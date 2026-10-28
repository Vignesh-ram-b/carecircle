"""End-to-end flow against mocked DynamoDB (moto), with the agent unavailable
so the deterministic fallback runs, and messaging in DRY_RUN mode.

Default mode is email-first (no WhatsApp number configured)."""

import json
import os
import sys

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

ENV = {
    "AWS_REGION": "us-east-1", "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test",
    "CC_SKIP_SSM": "1", "DRY_RUN": "true", "API_TOKEN": "secret",
    "VITALS_TABLE": "v", "PATIENTS_TABLE": "p", "CONTACTS_TABLE": "c", "INCIDENTS_TABLE": "i",
    "AGENT_ARN_PARAM": "/carecircle/missing",
    "PUBLIC_BASE_URL": "https://api.test", "SES_FROM_EMAIL": "alerts@test.dev",
}


@pytest.fixture()
def aws(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("DISPATCH_FUNCTION", raising=False)
    monkeypatch.delenv("WA_PHONE_NUMBER_ID", raising=False)
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(TableName="v", BillingMode="PAY_PER_REQUEST",
                         KeySchema=[{"AttributeName": "patientId", "KeyType": "HASH"},
                                    {"AttributeName": "ts", "KeyType": "RANGE"}],
                         AttributeDefinitions=[{"AttributeName": "patientId", "AttributeType": "S"},
                                               {"AttributeName": "ts", "AttributeType": "N"}])
        ddb.create_table(TableName="p", BillingMode="PAY_PER_REQUEST",
                         KeySchema=[{"AttributeName": "patientId", "KeyType": "HASH"}],
                         AttributeDefinitions=[{"AttributeName": "patientId", "AttributeType": "S"}])
        ddb.create_table(TableName="c", BillingMode="PAY_PER_REQUEST",
                         KeySchema=[{"AttributeName": "phone", "KeyType": "HASH"}],
                         AttributeDefinitions=[{"AttributeName": "phone", "AttributeType": "S"},
                                               {"AttributeName": "patientId", "AttributeType": "S"}],
                         GlobalSecondaryIndexes=[{"IndexName": "byPatient",
                                                  "KeySchema": [{"AttributeName": "patientId", "KeyType": "HASH"}],
                                                  "Projection": {"ProjectionType": "ALL"}}])
        ddb.create_table(TableName="i", BillingMode="PAY_PER_REQUEST",
                         KeySchema=[{"AttributeName": "incidentId", "KeyType": "HASH"}],
                         AttributeDefinitions=[{"AttributeName": "incidentId", "AttributeType": "S"},
                                               {"AttributeName": "patientId", "AttributeType": "S"},
                                               {"AttributeName": "createdAt", "AttributeType": "N"}],
                         GlobalSecondaryIndexes=[{"IndexName": "byPatient",
                                                  "KeySchema": [{"AttributeName": "patientId", "KeyType": "HASH"},
                                                                {"AttributeName": "createdAt", "KeyType": "RANGE"}],
                                                  "Projection": {"ProjectionType": "ALL"}}])
        import carecircle.store as store
        store._resource = None
        store.put_patient({"patientId": "appa", "name": "Appa", "age": 72})
        store.put_contact({"phone": "+919800000001", "name": "Vignesh", "relation": "son",
                           "priority": 1, "patientId": "appa", "email": "v@example.com"})
        store.put_contact({"phone": "+919800000002", "name": "Priya", "relation": "daughter",
                           "priority": 2, "patientId": "appa", "email": "p@example.com"})
        yield store


def api(path, body=None, method="POST", token="secret", query=None):
    from handlers import api as api_handler
    event = {"rawPath": path, "requestContext": {"http": {"method": method}},
             "headers": {"x-api-token": token}, "queryStringParameters": query,
             "body": json.dumps(body) if body else None}
    resp = api_handler.handler(event, None)
    return resp["statusCode"], json.loads(resp["body"]) if resp["headers"]["content-type"].startswith("application/json") else resp["body"]


def inbound(phone_digits, text):
    from handlers import inbound as inbound_handler
    entry = {"id": "1", "changes": [{"field": "messages", "value": {
        "messages": [{"from": phone_digits, "id": "wamid.x", "timestamp": "1", "type": "text", "text": {"body": text}}]}}]}
    envelope = {"whatsAppWebhookEntry": json.dumps(entry)}
    return inbound_handler.handler({"Records": [{"Sns": {"Message": json.dumps(envelope)}}]}, None)


def act(url_or_params, method="POST"):
    """Simulate tapping an email button."""
    from urllib.parse import parse_qs, urlparse
    if isinstance(url_or_params, str):
        q = parse_qs(urlparse(url_or_params).query)
        params = {k: v[0] for k, v in q.items()}
    else:
        params = url_or_params
    if method == "GET":
        return api("/act", method="GET", token="", query=params)
    return api("/act", params, token="")


def link(incident_id, phone, action):
    from carecircle import emails
    return emails.action_url(incident_id, phone, action)


def events_of(store, incident_id):
    return [e["event"] for e in store.get_incident(incident_id)["timeline"]]


def test_auth_required(aws):
    status, _ = api("/simulate", {"patientId": "appa"}, token="wrong")
    assert status == 401


def test_dashboard_served_without_token(aws):
    status, body = api("/", method="GET", token="")
    assert status == 200 and "CareCircle" in body and "Ask CareCircle" in body


def test_exercise_does_not_alert(aws):
    status, body = api("/simulate", {"patientId": "appa", "scenario": "exercise"})
    assert status == 200 and body["incidentsOpened"] == []


def test_warning_sends_email_alert_with_buttons(aws, capsys):
    store = aws
    _, body = api("/simulate", {"patientId": "appa", "scenario": "resting_tachycardia"})
    incident_id = body["incidentsOpened"][0]
    inc = store.get_incident(incident_id)
    assert inc["notified"] == ["+919800000001"]          # warning -> priority 1 only
    assert "email_alert_sent" in events_of(store, incident_id)
    assert inc["emailSubject"].startswith("⚠️ CareCircle alert: Appa")
    out = capsys.readouterr().out
    assert "to=v@example.com" in out and "https://api.test/act?" in out
    assert "to=p@example.com" not in out

    # Same concern again: no duplicate incident, no extra alert
    _, again = api("/simulate", {"patientId": "appa", "scenario": "resting_tachycardia"})
    assert again["incidentsOpened"] == [] and again["incidentsUpdated"] == [incident_id]


def test_email_button_acknowledges(aws, capsys):
    store = aws
    _, body = api("/simulate", {"patientId": "appa", "scenario": "resting_tachycardia"})
    incident_id = body["incidentsOpened"][0]
    url = link(incident_id, "+919800000001", "ack")

    # GET (what the email link opens) only renders a page - it must not change state
    status, page = act(url, "GET")
    assert status == 200 and "fetch(" in page
    assert store.get_incident(incident_id)["status"] == "OPEN"

    status, result = act(url)
    assert status == 200 and result["ok"]
    inc = store.get_incident(incident_id)
    assert inc["status"] == "ACKNOWLEDGED" and inc["acknowledgedBy"] == "Vignesh"
    assert "nextEscalationAt" not in inc
    assert "Thank you Vignesh" in capsys.readouterr().out   # reply email

    # Tapping again is harmless
    status, again = act(url)
    assert status == 200


def test_tampered_link_rejected(aws):
    _, body = api("/simulate", {"patientId": "appa", "scenario": "resting_tachycardia"})
    from urllib.parse import parse_qs, urlparse
    params = {k: v[0] for k, v in parse_qs(urlparse(link(body["incidentsOpened"][0], "+919800000001", "ack")).query).items()}
    params["c"] = "+919800000002"                       # someone else's ID, same signature
    status, result = act(params)
    assert status == 400 and not result["ok"]


def test_trend_button(aws):
    _, body = api("/simulate", {"patientId": "appa", "scenario": "resting_tachycardia"})
    status, result = act(link(body["incidentsOpened"][0], "+919800000001", "trend"))
    assert status == 200 and "heart rate avg" in result["detail"]


def test_resolve_button(aws):
    store = aws
    _, body = api("/simulate", {"patientId": "appa", "scenario": "low_spo2"})
    incident_id = body["incidentsOpened"][0]
    act(link(incident_id, "+919800000001", "resolve"))
    assert store.get_incident(incident_id)["status"] == "RESOLVED"
    status, result = act(link(incident_id, "+919800000001", "ack"))
    assert result["title"] == "Already handled"


def test_escalation_reaches_next_contact(aws, capsys):
    store = aws
    _, body = api("/simulate", {"patientId": "appa", "scenario": "low_spo2"})
    incident_id = body["incidentsOpened"][0]
    store.update_incident(incident_id, nextEscalationAt=store.now() - 1)

    from handlers import monitor
    result = monitor.handler({}, None)
    assert result["escalated"] == [incident_id]
    inc = store.get_incident(incident_id)
    assert inc["escalationLevel"] == 1
    assert "+919800000002" in inc["notified"]
    assert "sms_sent" in events_of(store, incident_id)
    assert "to=p@example.com" in capsys.readouterr().out


def test_fall_alerts_everyone_and_coordinates(aws, capsys):
    store = aws
    _, body = api("/simulate", {"patientId": "appa", "scenario": "fall"})
    incident_id = body["incidentsOpened"][0]
    inc = store.get_incident(incident_id)
    assert inc["severity"] == "critical"
    assert set(inc["notified"]) == {"+919800000001", "+919800000002"}
    assert "call 108" in capsys.readouterr().out

    act(link(incident_id, "+919800000002", "ack"))      # Priya taps "I'm checking"
    out = capsys.readouterr().out
    assert store.get_incident(incident_id)["acknowledgedBy"] == "Priya"
    assert "to=v@example.com" in out and "Priya is checking on Appa" in out


def test_ask_from_dashboard(aws):
    store = aws
    api("/simulate", {"patientId": "appa", "scenario": "normal"})
    status, body = api("/ask", {"patientId": "appa", "question": "How is Appa now?"})
    assert status == 200 and body["askedAs"] == "Vignesh"
    chat = store.get_chat("appa")
    assert [m["role"] for m in chat] == ["family", "agent"]
    assert "heart rate avg" in chat[1]["text"]
    _, status_body = api("/status", method="GET", query={"patientId": "appa"})
    assert len(status_body["chat"]) == 2


def test_status_reports_channels(aws):
    api("/simulate", {"patientId": "appa", "scenario": "normal"})
    status, body = api("/status", method="GET", query={"patientId": "appa"})
    assert status == 200 and body["latest"]["hr"]
    assert body["channels"]["email"] and not body["channels"]["whatsapp"]
    assert body["contacts"][0]["channels"] == ["email"]
    assert "phone" not in body["contacts"][0]


def test_summary_email(aws, capsys):
    api("/simulate", {"patientId": "appa", "scenario": "normal"})
    status, body = api("/summary", {"patientId": "appa"})
    assert status == 200 and body["sent"] == 2
    assert "CareCircle daily summary" in capsys.readouterr().out


def test_whatsapp_mode_when_enabled(aws, monkeypatch, capsys):
    store = aws
    monkeypatch.setenv("WA_PHONE_NUMBER_ID", "phone-number-id-abc")
    _, body = api("/simulate", {"patientId": "appa", "scenario": "resting_tachycardia"})
    incident_id = body["incidentsOpened"][0]
    out = capsys.readouterr().out
    assert '"type": "template"' in out and "to=v@example.com" in out    # WhatsApp + email
    assert {"whatsapp_alert_sent", "email_alert_sent"} <= set(events_of(store, incident_id))

    inbound("919800000001", "1")                                    # WhatsApp reply
    assert store.get_incident(incident_id)["status"] == "ACKNOWLEDGED"
    assert '"type": "text"' in capsys.readouterr().out             # reply inside 24h window


def test_back_to_back_scenarios_do_not_mix(aws):
    # Demo pattern: press "Normal" then "Racing heart" seconds later - must still alert.
    api("/simulate", {"patientId": "appa", "scenario": "normal"})
    _, body = api("/simulate", {"patientId": "appa", "scenario": "resting_tachycardia"})
    assert body["concerns"] == ["resting_tachycardia"] and body["incidentsOpened"]
