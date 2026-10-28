"""Parse AWS End User Messaging Social events delivered through Amazon SNS.

SNS Message body:
{
  "context": {"MetaWabaIds": [...], "MetaPhoneNumberIds": [...]},
  "whatsAppWebhookEntry": "<JSON string in Meta webhook format>",
  "aws_account_id": "...", "message_timestamp": "...", "messageId": "..."
}
"""

import json

from .store import normalize_phone


def parse_sns_event(event: dict) -> dict:
    """Return {"messages": [...], "statuses": [...]} from a Lambda SNS event."""
    messages, statuses = [], []
    for record in event.get("Records", []):
        raw = record.get("Sns", {}).get("Message", "{}")
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError:
            continue
        m, s = parse_envelope(envelope)
        messages.extend(m)
        statuses.extend(s)
    return {"messages": messages, "statuses": statuses}


def parse_envelope(envelope: dict):
    entry = envelope.get("whatsAppWebhookEntry")
    if isinstance(entry, str):
        try:
            entry = json.loads(entry)
        except json.JSONDecodeError:
            return [], []
    if not isinstance(entry, dict):
        return [], []

    messages, statuses = [], []
    for change in entry.get("changes", []):
        value = change.get("value", {}) or {}
        names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts", [])}

        for msg in value.get("messages", []) or []:
            text, button_payload = _extract_text(msg)
            sender = msg.get("from", "")
            messages.append({
                "from": normalize_phone(sender),
                "profile_name": names.get(sender),
                "wa_message_id": msg.get("id"),
                "timestamp": int(msg.get("timestamp", 0) or 0),
                "type": msg.get("type"),
                "text": text,
                "button_payload": button_payload,
            })

        for st in value.get("statuses", []) or []:
            statuses.append({
                "recipient": normalize_phone(st.get("recipient_id", "")),
                "status": st.get("status"),
                "wa_message_id": st.get("id"),
                "timestamp": int(st.get("timestamp", 0) or 0),
                "errors": st.get("errors"),
            })
    return messages, statuses


def _extract_text(msg: dict):
    mtype = msg.get("type")
    if mtype == "text":
        return (msg.get("text") or {}).get("body", ""), None
    if mtype == "button":  # quick-reply button on a template message
        btn = msg.get("button") or {}
        return btn.get("text", ""), btn.get("payload")
    if mtype == "interactive":
        inter = msg.get("interactive") or {}
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        return reply.get("title", ""), reply.get("id")
    if mtype == "location":
        loc = msg.get("location") or {}
        return f"[shared location {loc.get('latitude')},{loc.get('longitude')}]", None
    if mtype in ("image", "audio", "video", "document", "sticker"):
        caption = (msg.get(mtype) or {}).get("caption", "")
        return f"[{mtype}] {caption}".strip(), None
    return "", None
