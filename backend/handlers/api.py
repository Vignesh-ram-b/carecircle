"""HTTP API Lambda (API Gateway HTTP API, payload v2).

  GET  /                      demo dashboard (HTML, asks for the API token)
  POST /vitals                band/phone app uploads readings     {patientId, readings:[...]} or one reading
  GET  /status?patientId=     latest vitals, 60-min history, incidents, chat
  POST /simulate              {patientId, scenario, minutes}  -> synthetic readings through the real pipeline
  POST /ask                   {patientId, question, phone?}   -> ask the agent from the dashboard
  POST /summary               {patientId}                     -> send the SES daily summary now
  POST /incidents/{id}/resolve                                 -> close an incident (demo reset)
  GET|POST /act?i&c&a&s       one-tap email buttons (HMAC-signed, no token needed)

All other routes require header `x-api-token` (or ?token=).
"""

import base64
import hmac
import json
import os

from carecircle import actions, config, emails, service, simulation, store

_DASHBOARD = os.path.join(os.path.dirname(__file__), "dashboard.html")


def _resp(status: int, body, content_type="application/json"):
    return {
        "statusCode": status,
        "headers": {"content-type": content_type, "cache-control": "no-store"},
        "body": body if isinstance(body, str) else json.dumps(body, default=str, ensure_ascii=False),
    }


def _html(status: int, body: str):
    return _resp(status, body, "text/html; charset=utf-8")


def _authorized(event) -> bool:
    expected = config.get("API_TOKEN") or ""
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    supplied = headers.get("x-api-token") or (event.get("queryStringParameters") or {}).get("token") or ""
    return bool(expected) and hmac.compare_digest(str(supplied), str(expected))


def _body(event) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    return json.loads(raw)


# ------------------------------------------------------------ email buttons
def do_action(params: dict) -> dict:
    """Apply a signed email-button action. Returns {ok, title, detail}."""
    incident_id, phone, action = params.get("i"), params.get("c", ""), params.get("a")
    incident_id = None if incident_id in (None, "", "-") else incident_id
    if action not in emails.ACTIONS or not emails.verify(incident_id, phone, action, params.get("s")):
        return {"ok": False, "title": "This link is not valid", "detail": "Please use the buttons in the latest CareCircle email."}
    contact = store.get_contact(phone)
    if not contact:
        return {"ok": False, "title": "Unknown contact", "detail": ""}
    patient = store.get_patient(contact["patientId"]) or {}
    pname = patient.get("name", "your family member")
    who = contact.get("name", phone)
    incident = store.get_incident(incident_id) if incident_id else None
    label = emails.ACTIONS[action]

    store.set_last_channel(phone, "email")
    store.add_timeline(incident_id, who, "family_reply", f"tapped “{label}” in the alert email",
                       {"channel": "email", "from": store.normalize_phone(phone)})

    # Safety-critical state changes happen immediately and deterministically;
    # the agent is then told, so it can reply and coordinate the rest of the family.
    open_now = bool(incident and incident.get("status") in store.OPEN_STATUSES)
    if action == "ack" and incident and incident.get("status") == "OPEN":
        actions.acknowledge(incident_id, phone)
    elif action == "resolve" and open_now:
        actions.resolve(incident_id, note=f"{who} confirmed {pname} is fine", actor="family")

    if action != "trend" and incident and not open_now:
        return {"ok": True, "title": "Already handled",
                "detail": f"This alert is already {incident.get('status', '').lower().replace('_', ' ')}"
                          + (f" by {incident['acknowledgedBy']}" if incident.get("acknowledgedBy") else "") + "."}

    service.dispatch_async({"type": "family_message", "channel": "email", "action": action,
                            "actionApplied": action in ("ack", "resolve"), "text": label,
                            "phone": store.normalize_phone(phone), "patientId": contact["patientId"],
                            "incidentId": incident_id})

    if action == "ack":
        return {"ok": True, "title": f"Thank you, {who}",
                "detail": f"CareCircle has stopped escalating and will let the family know you're checking on {pname}."}
    if action == "resolve":
        return {"ok": True, "title": "Alert closed",
                "detail": f"Thanks for confirming {pname} is fine. CareCircle keeps watching."}
    return {"ok": True, "title": f"{pname} · last 60 minutes",
            "detail": actions.trend_text(contact["patientId"], 60) + "\n\nCareCircle will also email you a short explanation."}


def _act(event, method):
    if method == "GET":
        params = event.get("queryStringParameters") or {}
        label = emails.ACTIONS.get(params.get("a"), "reply")
        return _html(200, emails.confirm_page({k: params.get(k) for k in ("i", "c", "a", "s")}, label))
    try:
        result = do_action(_body(event))
    except (ValueError, KeyError):
        result = {"ok": False, "title": "Bad request", "detail": ""}
    return _resp(200 if result["ok"] else 400, result)


# ------------------------------------------------------------------ router
def handler(event, _context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")

    if method == "GET" and path in ("/", "/dashboard"):
        with open(_DASHBOARD, encoding="utf-8") as fh:
            return _html(200, fh.read())
    if path == "/act":
        return _act(event, method)

    if not _authorized(event):
        return _resp(401, {"error": "missing or invalid x-api-token"})

    try:
        if method == "POST" and path == "/vitals":
            body = _body(event)
            readings = body.get("readings") or [{k: body.get(k) for k in ("ts", "hr", "spo2", "steps", "fall")}]
            return _resp(200, service.ingest(body["patientId"], readings))

        if method == "POST" and path == "/simulate":
            body = _body(event)
            readings = simulation.generate(body.get("scenario", "normal"), int(body.get("minutes", 6)))
            result = service.ingest(body["patientId"], readings)
            result["scenario"] = body.get("scenario", "normal")
            return _resp(200, result)

        if method == "POST" and path == "/ask":
            body = _body(event)
            pid, question = body["patientId"], str(body.get("question", "")).strip()[:500]
            if not question:
                return _resp(400, {"error": "empty question"})
            contacts = store.get_contacts(pid)
            contact = next((c for c in contacts if c["phone"] == store.normalize_phone(body.get("phone", ""))),
                           contacts[0] if contacts else None)
            if not contact:
                return _resp(400, {"error": "add a family contact first"})
            incident = store.find_open_incident(pid)
            store.set_last_channel(contact["phone"], "dashboard")
            store.add_chat(pid, "family", contact.get("name", "Family"), question)
            if incident:
                store.add_timeline(incident["incidentId"], contact.get("name", "Family"), "family_reply",
                                   question, {"channel": "dashboard"})
            service.dispatch_async({"type": "family_message", "channel": "dashboard", "text": question,
                                    "phone": contact["phone"], "patientId": pid,
                                    "incidentId": incident["incidentId"] if incident else None})
            return _resp(200, {"queued": True, "askedAs": contact.get("name")})

        if method == "GET" and path == "/status":
            pid = (event.get("queryStringParameters") or {}).get("patientId")
            patient = store.get_patient(pid)
            if not patient:
                return _resp(404, {"error": "unknown patient - run scripts/seed.py"})
            contacts = []
            for c in store.get_contacts(pid):
                contacts.append({"name": c.get("name"), "relation": c.get("relation"),
                                 "priority": c.get("priority"), "channels": actions.channels_for(c)})
            return _resp(200, {
                "patient": {k: patient.get(k) for k in ("patientId", "name", "age", "city")},
                "contacts": contacts,
                "latest": store.get_latest_vital(pid),
                "history": store.get_recent_vitals(pid, 60),
                "incidents": store.list_incidents(pid, 10),
                "chat": (patient.get("chat") or [])[-20:],
                "agentConfigured": bool(config.agent_runtime_arn()),
                "channels": {"email": bool(config.get("SES_FROM_EMAIL")) or config.dry_run(),
                             "whatsapp": actions.whatsapp_enabled(), "sms": actions.sms_enabled()},
            })

        if method == "POST" and path == "/summary":
            from handlers import summary
            return _resp(200, summary.send_summary(_body(event)["patientId"]))

        if method == "POST" and path.startswith("/incidents/") and path.endswith("/resolve"):
            incident_id = path.split("/")[2]
            actions.resolve(incident_id, note="closed from dashboard", actor="dashboard")
            return _resp(200, {"resolved": incident_id})

        return _resp(404, {"error": f"no route {method} {path}"})
    except KeyError as exc:
        return _resp(400, {"error": f"bad request: missing {exc}"})
    except ValueError as exc:
        return _resp(400, {"error": str(exc)})
