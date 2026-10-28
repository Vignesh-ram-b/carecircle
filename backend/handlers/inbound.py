"""Inbound WhatsApp Lambda (subscribed to the SNS topic that AWS End User
Messaging Social publishes WABA events to)."""

from carecircle import messaging, service, store
from carecircle.whatsapp_events import parse_sns_event


def handler(event, _context):
    parsed = parse_sns_event(event)
    handled = 0

    for msg in parsed["messages"]:
        contact = store.get_contact(msg["from"])
        if not contact:
            print(f"[inbound] message from unknown number {msg['from']} ignored")
            continue
        store.touch_contact_inbound(msg["from"])
        if msg.get("wa_message_id"):
            messaging.mark_whatsapp_read(msg["wa_message_id"])

        incident = store.find_open_incident(contact["patientId"])
        incident_id = incident["incidentId"] if incident else None
        store.add_timeline(incident_id, contact.get("name", msg["from"]), "family_reply",
                           msg.get("text") or f"[{msg.get('type')}]",
                           {"channel": "whatsapp", "from": msg["from"]})
        service.dispatch_async({
            "type": "family_message",
            "channel": "whatsapp",
            "phone": msg["from"],
            "text": msg.get("text", ""),
            "buttonPayload": msg.get("button_payload"),
            "patientId": contact["patientId"],
            "incidentId": incident_id,
        })
        handled += 1

    # Delivery receipts: show "read by <name>" on the incident timeline.
    for st in parsed["statuses"]:
        if st.get("status") not in ("read", "failed"):
            continue
        contact = store.get_contact(st["recipient"])
        if not contact:
            continue
        incident = store.find_open_incident(contact["patientId"])
        if incident and (st["status"] == "failed" or st["recipient"] in incident.get("notified", [])):
            already = any(e.get("event") == "whatsapp_read" and e.get("to") == st["recipient"]
                          for e in incident.get("timeline", []))
            if st["status"] == "read" and already:
                continue
            store.add_timeline(incident["incidentId"], "whatsapp",
                               "whatsapp_read" if st["status"] == "read" else "whatsapp_delivery_failed",
                               f"{contact.get('name')} {'read the alert' if st['status'] == 'read' else 'delivery failed'}",
                               {"to": st["recipient"], "errors": st.get("errors")})

    return {"messages": handled, "statuses": len(parsed["statuses"])}
