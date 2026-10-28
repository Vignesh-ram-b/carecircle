"""Deterministic safety net.

If the AgentCore runtime is not deployed yet, times out, or errors, family
members must still be told. This module handles the same events as the agent
with fixed rules. It is intentionally boring.
"""

from . import actions, config, store

ACK_WORDS = ("1", "ok", "okay", "on it", "checking", "i'm checking", "im checking",
             "coming", "on my way", "seen", "yes", "நான் பார்க்கிறேன்", "சரி")
TREND_WORDS = ("2", "trend", "show trend", "status", "how is", "update", "now", "?",
               "போக்கைக் காட்டு")
RESOLVE_WORDS = ("resolved", "false alarm", "he is fine", "she is fine", "all good", "fine now",
                 "all fine")


def handle(event: dict) -> dict:
    kind = event.get("type")
    if kind in ("concern_detected", "device_offline"):
        return _concern(event)
    if kind == "family_message":
        return _family_message(event)
    if kind == "escalation_due":
        return _escalate(event)
    return {"handled": False, "reason": f"unknown event {kind}"}


def _contacts_for_level(patient_id: str, level: int) -> list:
    contacts = store.get_contacts(patient_id)
    priorities = sorted({c.get("priority", 1) for c in contacts})
    if not priorities:
        return []
    if level >= len(priorities):
        return contacts  # last resort: everyone
    return [c for c in contacts if c.get("priority", 1) == priorities[level]]


def _concern(event: dict) -> dict:
    incident = store.get_incident(event["incidentId"])
    patient = store.get_patient(incident["patientId"]) or {}
    name = patient.get("name", "your family member")
    critical = incident["severity"] == "critical"
    contacts = store.get_contacts(incident["patientId"]) if critical \
        else _contacts_for_level(incident["patientId"], 0)
    message = (f"{incident['summary']}. Please check on {name}."
               + (" This looks urgent." if critical else ""))
    for c in contacts:
        actions.send_alert(incident["incidentId"], c["phone"], incident["summary"],
                           incident["reading"], message, actor="fallback")
    actions.schedule_escalation(incident["incidentId"], minutes=2 if critical else None, actor="fallback")
    return {"handled": True, "notified": [c["phone"] for c in contacts]}


def _family_message(event: dict) -> dict:
    phone, text = event["phone"], (event.get("text") or "").strip().lower()
    incident_id = event.get("incidentId")
    channel = event.get("channel", "auto")
    action = event.get("action")  # set by email buttons; state change already applied
    contact = store.get_contact(phone) or {}
    patient_id = contact.get("patientId") or event.get("patientId")
    who = contact.get("name", phone)
    patient = store.get_patient(patient_id) or {}
    pname = patient.get("name", "them")

    if incident_id and (action == "ack" or (not action and any(text == w or text.startswith(w) for w in ACK_WORDS))):
        if action != "ack":
            actions.acknowledge(incident_id, phone)
        reply = f"Thank you {who}. I've noted that you're checking on {pname} and stopped alerting others."
        _tell_others(incident_id, phone, f"{who} is checking on {pname}. No action needed from you for now.")
    elif incident_id and (action == "resolve" or (not action and any(w in text for w in RESOLVE_WORDS))):
        if action != "resolve":
            actions.resolve(incident_id, note=f"marked resolved by {who}", actor="family")
        reply = "Glad to hear it. I've closed this alert and will keep watching."
        _tell_others(incident_id, phone, f"{who} confirmed {pname} is fine. The alert is closed.")
    elif action == "trend" or any(w in text for w in TREND_WORDS):
        reply = actions.trend_text(patient_id, 60)
    else:
        reply = (f"I'm CareCircle. Ask me how {pname} is doing, or use the buttons in the alert "
                 "to say you're checking or that all is fine.")
    actions.send_message(incident_id, phone, reply, channel=channel, actor="fallback")
    return {"handled": True}


def _tell_others(incident_id: str, phone: str, text: str):
    incident = store.get_incident(incident_id) or {}
    me = store.normalize_phone(phone)
    for other in incident.get("notified", []):
        if other != me:
            actions.send_message(incident_id, other, text, channel="alert", actor="fallback")


def _escalate(event: dict) -> dict:
    incident = store.get_incident(event["incidentId"])
    if not incident or incident.get("status") != "OPEN":
        return {"handled": False, "reason": "incident not open"}
    patient = store.get_patient(incident["patientId"]) or {}
    name = patient.get("name", "your family member")
    level = int(incident.get("escalationLevel", 0)) + 1
    store.update_incident(incident["incidentId"], escalationLevel=level)
    contacts = _contacts_for_level(incident["patientId"], level)
    text = (f"URGENT: nobody has responded yet about {name}. {incident['summary']}. "
            f"Latest: {incident['reading']}. Please check on {name} now.")
    for c in contacts:
        actions.send_alert(incident["incidentId"], c["phone"], "No response yet - " + incident["summary"],
                           incident["reading"], text, actor="fallback")
        actions.sms_to_contact(incident["incidentId"], c["phone"], text, actor="fallback")
    if level < 3:
        actions.schedule_escalation(incident["incidentId"], actor="fallback")
    return {"handled": True, "level": level}


_ = config
