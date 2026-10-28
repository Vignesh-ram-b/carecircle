"""AWS Communication Developer Services wrappers.

* WhatsApp - AWS End User Messaging Social   (boto3 client "socialmessaging")
* SMS      - AWS End User Messaging SMS      (boto3 client "pinpoint-sms-voice-v2")
* Email    - Amazon SES v2                   (boto3 client "sesv2")

Every function returns a small dict {"ok": bool, "channel": ..., "id"/"error": ...}
so callers (agent tools, fallback logic) can record what happened without
raising. Set DRY_RUN=true to log instead of sending (local tests, demos
without a WhatsApp number).
"""

import json
import re

import boto3

from . import config
from .store import normalize_phone

_clients = {}


def _client(name: str):
    if name not in _clients:
        _clients[name] = boto3.client(name, region_name=config.region())
    return _clients[name]


def _template_param(text: str, limit: int = 300) -> str:
    """Meta rejects template parameters containing newlines, tabs or more than
    four consecutive spaces."""
    text = re.sub(r"[\n\r\t]+", " ", str(text or ""))
    text = re.sub(r" {4,}", "   ", text).strip()
    return (text[: limit - 1] + "…") if len(text) > limit else (text or "-")


def _wa_send(payload: dict) -> dict:
    if config.dry_run():
        print("[DRY_RUN whatsapp]", json.dumps(payload, ensure_ascii=False))
        return {"ok": True, "channel": "whatsapp", "id": "dry-run", "dry_run": True}
    phone_number_id = config.get("WA_PHONE_NUMBER_ID")
    if not phone_number_id:
        return {"ok": False, "channel": "whatsapp", "error": "WA_PHONE_NUMBER_ID not configured"}
    try:
        resp = _client("socialmessaging").send_whatsapp_message(
            originationPhoneNumberId=phone_number_id,
            message=json.dumps(payload).encode("utf-8"),
            metaApiVersion=config.get("WA_META_API_VERSION"),
        )
        return {"ok": True, "channel": "whatsapp", "id": resp.get("messageId")}
    except Exception as exc:  # noqa: BLE001
        print(f"[whatsapp] send failed: {exc}")
        return {"ok": False, "channel": "whatsapp", "error": str(exc)}


def send_whatsapp_text(to_phone: str, text: str) -> dict:
    """Free-form text. Only delivered inside the 24h customer-service window."""
    return _wa_send({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": normalize_phone(to_phone),
        "type": "text",
        "text": {"preview_url": False, "body": str(text)[:4000]},
    })


def send_whatsapp_alert_template(to_phone: str, patient_name: str, alert_summary: str,
                                 latest_reading: str, language: str = None) -> dict:
    """Business-initiated alert using the pre-approved Utility template.

    Template body (see docs/whatsapp-templates.md):
      "CareCircle health alert for {{1}}: {{2}}. Latest reading: {{3}}. ..."
    with quick-reply buttons "I'm checking" and "Show trend".
    """
    return _wa_send({
        "messaging_product": "whatsapp",
        "to": normalize_phone(to_phone),
        "type": "template",
        "template": {
            "name": config.get("WA_TEMPLATE_ALERT"),
            "language": {"code": language or config.get("WA_TEMPLATE_LANG")},
            "components": [{
                "type": "body",
                "parameters": [
                    {"type": "text", "text": _template_param(patient_name, 60)},
                    {"type": "text", "text": _template_param(alert_summary, 300)},
                    {"type": "text", "text": _template_param(latest_reading, 120)},
                ],
            }],
        },
    })


def mark_whatsapp_read(wa_message_id: str) -> dict:
    """Shows the family member blue ticks so they know the system saw their reply."""
    return _wa_send({"messaging_product": "whatsapp", "message_id": wa_message_id, "status": "read"})


def send_sms(to_phone: str, text: str) -> dict:
    if config.dry_run():
        print(f"[DRY_RUN sms] to={to_phone}: {text}")
        return {"ok": True, "channel": "sms", "id": "dry-run", "dry_run": True}
    origination = config.get("SMS_ORIGINATION_IDENTITY")
    if not origination:
        return {"ok": False, "channel": "sms", "error": "SMS_ORIGINATION_IDENTITY not configured"}
    try:
        resp = _client("pinpoint-sms-voice-v2").send_text_message(
            DestinationPhoneNumber=normalize_phone(to_phone),
            OriginationIdentity=origination,
            MessageBody=str(text)[:1200],
            MessageType="TRANSACTIONAL",
        )
        return {"ok": True, "channel": "sms", "id": resp.get("MessageId")}
    except Exception as exc:  # noqa: BLE001
        print(f"[sms] send failed: {exc}")
        return {"ok": False, "channel": "sms", "error": str(exc)}


def send_email(to_email: str, subject: str, text_body: str, html_body: str = None) -> dict:
    if config.dry_run():
        print(f"[DRY_RUN email] to={to_email} subject={subject}\n{text_body}")
        return {"ok": True, "channel": "email", "id": "dry-run", "dry_run": True}
    sender = config.get("SES_FROM_EMAIL")
    if not sender:
        return {"ok": False, "channel": "email", "error": "SES_FROM_EMAIL not configured"}
    body = {"Text": {"Data": text_body, "Charset": "UTF-8"}}
    if html_body:
        body["Html"] = {"Data": html_body, "Charset": "UTF-8"}
    try:
        resp = _client("sesv2").send_email(
            FromEmailAddress=sender,
            Destination={"ToAddresses": [to_email]},
            Content={"Simple": {"Subject": {"Data": subject, "Charset": "UTF-8"}, "Body": body}},
        )
        return {"ok": True, "channel": "email", "id": resp.get("MessageId")}
    except Exception as exc:  # noqa: BLE001
        print(f"[email] send failed: {exc}")
        return {"ok": False, "channel": "email", "error": str(exc)}
