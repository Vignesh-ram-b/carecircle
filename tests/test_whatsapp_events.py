import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from carecircle.whatsapp_events import parse_sns_event  # noqa: E402


def sns_event(entry: dict) -> dict:
    envelope = {
        "context": {"MetaWabaIds": [{"wabaId": "1", "arn": "arn:aws:social-messaging:us-east-1:1:waba/x"}],
                    "MetaPhoneNumberIds": [{"metaPhoneNumberId": "2", "arn": "arn"}]},
        "whatsAppWebhookEntry": json.dumps(entry),
        "aws_account_id": "123456789012",
        "message_timestamp": "2026-10-27T10:00:00Z",
        "messageId": "abc",
    }
    return {"Records": [{"Sns": {"Message": json.dumps(envelope)}}]}


def test_text_message():
    entry = {"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "contacts": [{"profile": {"name": "Vignesh"}, "wa_id": "919876543210"}],
        "messages": [{"from": "919876543210", "id": "wamid.1", "timestamp": "1761559200",
                      "type": "text", "text": {"body": "I'm checking"}}]}}]}
    out = parse_sns_event(sns_event(entry))
    assert out["messages"][0]["from"] == "+919876543210"
    assert out["messages"][0]["text"] == "I'm checking"
    assert out["messages"][0]["profile_name"] == "Vignesh"


def test_quick_reply_button():
    entry = {"id": "1", "changes": [{"field": "messages", "value": {
        "messages": [{"from": "919876543210", "id": "wamid.2", "timestamp": "1",
                      "type": "button", "button": {"text": "Show trend", "payload": "Show trend"}}]}}]}
    msg = parse_sns_event(sns_event(entry))["messages"][0]
    assert msg["text"] == "Show trend" and msg["button_payload"] == "Show trend"


def test_status_event():
    entry = {"id": "1", "changes": [{"field": "messages", "value": {
        "statuses": [{"id": "wamid.3", "status": "read", "timestamp": "1", "recipient_id": "919876543210"}]}}]}
    out = parse_sns_event(sns_event(entry))
    assert out["messages"] == [] and out["statuses"][0]["status"] == "read"
    assert out["statuses"][0]["recipient"] == "+919876543210"
