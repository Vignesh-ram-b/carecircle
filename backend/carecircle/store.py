"""DynamoDB data access.

Tables (names come from config):
  VITALS_TABLE     pk patientId (S), sk ts (N, epoch seconds), TTL attribute `expiresAt`
  PATIENTS_TABLE   pk patientId (S)
  CONTACTS_TABLE   pk phone (S, E.164), GSI byPatient (patientId)
  INCIDENTS_TABLE  pk incidentId (S), GSI byPatient (patientId, createdAt)
"""

import time
import uuid
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key

from . import config

OPEN_STATUSES = ("OPEN", "ACKNOWLEDGED")

_resource = None


def _ddb():
    global _resource
    if _resource is None:
        _resource = boto3.resource("dynamodb", region_name=config.region())
    return _resource


def _table(key: str):
    return _ddb().Table(config.get(key))


def now() -> int:
    return int(time.time())


# ---------------------------------------------------------------- conversion
def to_ddb(value):
    if isinstance(value, float):
        return Decimal(str(round(value, 4)))
    if isinstance(value, dict):
        return {k: to_ddb(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [to_ddb(v) for v in value]
    return value


def from_ddb(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: from_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_ddb(v) for v in value]
    return value


def normalize_phone(phone: str) -> str:
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    return f"+{digits}" if digits else ""


# ------------------------------------------------------------------- vitals
def put_vitals(patient_id: str, readings: list) -> int:
    table = _table("VITALS_TABLE")
    expires = now() + 30 * 86400
    count = 0
    with table.batch_writer(overwrite_by_pkeys=["patientId", "ts"]) as batch:
        for r in readings:
            item = {
                "patientId": patient_id,
                "ts": int(r.get("ts") or now()),
                "hr": r.get("hr"),
                "spo2": r.get("spo2"),
                "steps": r.get("steps"),
                "fall": bool(r.get("fall", False)),
                "source": r.get("source", "band"),
                "expiresAt": expires,
            }
            batch.put_item(Item=to_ddb(item))
            count += 1
    return count


def get_recent_vitals(patient_id: str, minutes: int = 30) -> list:
    since = now() - int(minutes) * 60
    items, kwargs = [], {
        "KeyConditionExpression": Key("patientId").eq(patient_id) & Key("ts").gte(since),
        "ScanIndexForward": True,
    }
    while True:
        resp = _table("VITALS_TABLE").query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return [from_ddb(i) for i in items]


def get_latest_vital(patient_id: str):
    resp = _table("VITALS_TABLE").query(
        KeyConditionExpression=Key("patientId").eq(patient_id),
        ScanIndexForward=False,
        Limit=1,
    )
    items = resp.get("Items", [])
    return from_ddb(items[0]) if items else None


# ----------------------------------------------------------------- patients
def get_patient(patient_id: str):
    item = _table("PATIENTS_TABLE").get_item(Key={"patientId": patient_id}).get("Item")
    return from_ddb(item) if item else None


def put_patient(patient: dict):
    _table("PATIENTS_TABLE").put_item(Item=to_ddb(patient))


def list_patients() -> list:
    resp = _table("PATIENTS_TABLE").scan()
    return [from_ddb(i) for i in resp.get("Items", [])]


def put_patient_keep_chat(patient: dict):
    """Re-seeding a patient must not wipe the dashboard chat history."""
    existing = get_patient(patient["patientId"]) or {}
    if existing.get("chat") and "chat" not in patient:
        patient = {**patient, "chat": existing["chat"]}
    put_patient(patient)


# ----------------------------------------------------------------- contacts
def put_contact(contact: dict):
    contact = dict(contact)
    contact["phone"] = normalize_phone(contact["phone"])
    _table("CONTACTS_TABLE").put_item(Item=to_ddb(contact))


def get_contact(phone: str):
    item = _table("CONTACTS_TABLE").get_item(Key={"phone": normalize_phone(phone)}).get("Item")
    return from_ddb(item) if item else None


def get_contacts(patient_id: str) -> list:
    resp = _table("CONTACTS_TABLE").query(
        IndexName="byPatient", KeyConditionExpression=Key("patientId").eq(patient_id)
    )
    contacts = [from_ddb(i) for i in resp.get("Items", [])]
    return sorted(contacts, key=lambda c: c.get("priority", 99))


def touch_contact_inbound(phone: str):
    """Record when the contact last messaged us on WhatsApp: WhatsApp allows
    free-form replies for 24 hours after the user's last message."""
    _table("CONTACTS_TABLE").update_item(
        Key={"phone": normalize_phone(phone)},
        UpdateExpression="SET lastInboundAt = :t, lastChannel = :c",
        ExpressionAttributeValues={":t": now(), ":c": "whatsapp"},
    )


def set_last_channel(phone: str, channel: str):
    """Remember where the contact last talked to us, so replies go back there."""
    _table("CONTACTS_TABLE").update_item(
        Key={"phone": normalize_phone(phone)},
        UpdateExpression="SET lastChannel = :c",
        ExpressionAttributeValues={":c": channel},
    )


# --------------------------------------------------------- dashboard chat
def add_chat(patient_id: str, role: str, name: str, text: str):
    entry = {"ts": now(), "role": role, "name": name, "text": str(text)[:2000]}
    _table("PATIENTS_TABLE").update_item(
        Key={"patientId": patient_id},
        UpdateExpression="SET #c = list_append(if_not_exists(#c, :empty), :e)",
        ExpressionAttributeNames={"#c": "chat"},
        ExpressionAttributeValues={":e": [to_ddb(entry)], ":empty": []},
    )


def get_chat(patient_id: str, limit: int = 20) -> list:
    patient = get_patient(patient_id) or {}
    return (patient.get("chat") or [])[-limit:]


def in_whatsapp_window(contact: dict) -> bool:
    last = contact.get("lastInboundAt") or 0
    return now() - int(last) < 23 * 3600  # keep a 1h safety margin


# ---------------------------------------------------------------- incidents
def create_incident(patient_id: str, concern: dict) -> dict:
    ts = now()
    incident = {
        "incidentId": f"inc-{time.strftime('%Y%m%d%H%M%S', time.gmtime(ts))}-{uuid.uuid4().hex[:8]}",
        "patientId": patient_id,
        "type": concern["type"],
        "severity": concern.get("severity", "warning"),
        "status": "OPEN",
        "summary": concern.get("summary", ""),
        "reading": concern.get("reading", ""),
        "evidence": concern.get("evidence", {}),
        "createdAt": ts,
        "updatedAt": ts,
        "escalationLevel": 0,
        "notified": [],
        "timeline": [
            {"ts": ts, "actor": "detector", "event": "concern_detected",
             "detail": concern.get("summary", "")}
        ],
    }
    _table("INCIDENTS_TABLE").put_item(Item=to_ddb(incident))
    return incident


def get_incident(incident_id: str):
    item = _table("INCIDENTS_TABLE").get_item(Key={"incidentId": incident_id}).get("Item")
    return from_ddb(item) if item else None


def add_timeline(incident_id: str, actor: str, event: str, detail: str = "", extra: dict = None):
    if not incident_id:
        return
    entry = {"ts": now(), "actor": actor, "event": event, "detail": str(detail)[:1500]}
    if extra:
        entry.update(extra)
    _table("INCIDENTS_TABLE").update_item(
        Key={"incidentId": incident_id},
        UpdateExpression="SET #tl = list_append(if_not_exists(#tl, :empty), :e), updatedAt = :t",
        ExpressionAttributeNames={"#tl": "timeline"},
        ExpressionAttributeValues={":e": [to_ddb(entry)], ":empty": [], ":t": now()},
    )


def update_incident(incident_id: str, **fields):
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        return
    fields["updatedAt"] = now()
    names, values, sets = {}, {}, []
    for i, (k, v) in enumerate(fields.items()):
        names[f"#f{i}"] = k
        values[f":v{i}"] = to_ddb(v)
        sets.append(f"#f{i} = :v{i}")
    _table("INCIDENTS_TABLE").update_item(
        Key={"incidentId": incident_id},
        UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def remove_incident_field(incident_id: str, field: str):
    _table("INCIDENTS_TABLE").update_item(
        Key={"incidentId": incident_id},
        UpdateExpression="REMOVE #f",
        ExpressionAttributeNames={"#f": field},
    )


def mark_notified(incident_id: str, phone: str):
    incident = get_incident(incident_id) or {}
    notified = set(incident.get("notified", []))
    notified.add(normalize_phone(phone))
    update_incident(incident_id, notified=sorted(notified))


def list_incidents(patient_id: str, limit: int = 20) -> list:
    resp = _table("INCIDENTS_TABLE").query(
        IndexName="byPatient",
        KeyConditionExpression=Key("patientId").eq(patient_id),
        ScanIndexForward=False,
        Limit=limit,
    )
    return [from_ddb(i) for i in resp.get("Items", [])]


def find_open_incident(patient_id: str, incident_type: str = None):
    for inc in list_incidents(patient_id, limit=25):
        if inc.get("status") in OPEN_STATUSES and (incident_type is None or inc.get("type") == incident_type):
            return inc
    return None


def list_open_incidents() -> list:
    items, kwargs = [], {"FilterExpression": Attr("status").is_in(list(OPEN_STATUSES))}
    while True:
        resp = _table("INCIDENTS_TABLE").scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return [from_ddb(i) for i in items]
