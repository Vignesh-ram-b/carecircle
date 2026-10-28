"""Tools the CareCircle agent can call (Strands Agents @tool functions).

Every side effect goes through carecircle.actions, which records it on the
incident timeline - so every agent decision is auditable.
"""

import json

from strands import tool

from carecircle import actions, store


def _j(obj) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)


@tool
def get_incident(incident_id: str) -> str:
    """Get an incident: type, preliminary severity, status, summary, latest reading,
    evidence, who was notified, escalation level and the full timeline of what has
    happened so far (messages sent, family replies, acknowledgements).

    Args:
        incident_id: The incident ID, e.g. "inc-20261027120000-ab12cd34".
    """
    inc = store.get_incident(incident_id)
    return _j(inc) if inc else _j({"error": "incident not found"})


@tool
def get_patient_profile(patient_id: str) -> str:
    """Get the monitored person's profile: name, age, city, relevant notes written by
    the family (e.g. usual resting heart rate, medications to mention to a doctor),
    doctor contact, local emergency number and alert thresholds.

    Args:
        patient_id: The patient ID, e.g. "appa".
    """
    p = store.get_patient(patient_id)
    return _j(p) if p else _j({"error": "patient not found"})


@tool
def get_recent_vitals(patient_id: str, minutes: int = 60) -> str:
    """Get health-band statistics for the last N minutes: heart-rate average/min/max,
    SpO2 min/average, total steps, falls, and up to 12 evenly spaced samples
    (ts, hr, spo2, steps/min, fall). Use this to tell real concerns from exercise or
    sensor glitches, and to answer family questions about trends.

    Args:
        patient_id: The patient ID.
        minutes: Look-back window in minutes (5-1440). Default 60.
    """
    minutes = max(5, min(int(minutes), 1440))
    return _j(actions.vitals_brief(patient_id, minutes))


@tool
def list_family_contacts(patient_id: str) -> str:
    """List family contacts in escalation order (priority 1 first) with name, relation,
    phone (their contact ID), email, preferred language, the channels alerts reach them on,
    and the channel they last used to talk to CareCircle (replies go back there).

    Args:
        patient_id: The patient ID.
    """
    contacts = []
    for c in store.get_contacts(patient_id):
        contacts.append({
            "name": c.get("name"), "relation": c.get("relation"), "priority": c.get("priority"),
            "phone": c.get("phone"), "email": c.get("email"), "language": c.get("language", "en"),
            "alert_channels": actions.channels_for(c), "reply_channel": actions.reply_channel(c),
        })
    return _j(contacts)


@tool
def send_alert(phone: str, alert_summary: str, latest_reading: str, message: str,
               incident_id: str) -> str:
    """Send a health ALERT to one family member on every channel they have (email with
    one-tap reply buttons "I'm checking" / "Show trend" / "All fine now"; WhatsApp too if
    enabled). Use for first alerts and escalation alerts. Marks the contact as notified.

    Args:
        phone: The contact's phone number (their contact ID), e.g. "+919876543210".
        alert_summary: One plain sentence of what was measured, e.g.
            "heart rate has stayed around 130 bpm for 5 minutes while resting".
        latest_reading: Short reading text, e.g. "HR 131 bpm, SpO2 97%, 2 steps/min".
        message: 1-3 short sentences: what to do now (call them, check on them, and for
            critical incidents the emergency number). Kind and specific.
        incident_id: The incident ID.
    """
    return _j(actions.send_alert(incident_id, phone, alert_summary, latest_reading, message))


@tool
def send_message(phone: str, message: str, incident_id: str = "", channel: str = "auto") -> str:
    """Send a conversational message to a family member: a reply to their question, a
    trend explanation, or "Priya is checking on Appa". With channel "auto" it goes back
    on the channel they last used (email, dashboard chat or WhatsApp).

    Args:
        phone: The contact's phone number (their contact ID).
        message: The message. Short, plain text, real numbers only.
        incident_id: The related incident ID, if any.
        channel: "auto" (default) replies where they last talked to you - use it when
            answering the person who just messaged. Use "alert" to reach other family
            members (e.g. "Priya is checking on Appa") on the channel their alerts arrive on.
            You can also force "email", "dashboard", "whatsapp" or "sms".
    """
    return _j(actions.send_message(incident_id or None, phone, message, channel or "auto"))


@tool
def send_sms(phone: str, message: str, incident_id: str = "") -> str:
    """Send an SMS (AWS End User Messaging SMS) as a backup channel for urgent
    escalations. If SMS is not configured the attempt is logged and skipped.

    Args:
        phone: Phone number in E.164 format.
        message: Under 300 characters, plain text, no links.
        incident_id: The related incident ID, if any.
    """
    return _j(actions.sms_to_contact(incident_id or None, phone, message))


@tool
def send_email(to_email: str, subject: str, body: str, incident_id: str = "") -> str:
    """Send a free-form email (Amazon SES), e.g. a written incident summary a family
    member can forward to the doctor.

    Args:
        to_email: Recipient email address.
        subject: Email subject.
        body: Plain-text body.
        incident_id: The related incident ID, if any.
    """
    return _j(actions.email_to(incident_id or None, to_email, subject, body))


@tool
def update_incident(incident_id: str, severity: str = "", status: str = "", note: str = "",
                    acknowledged_by_phone: str = "") -> str:
    """Update an incident and record your reasoning on its timeline.

    Args:
        incident_id: The incident ID.
        severity: Your assessed severity: "info", "warning" or "critical" (optional).
        status: "ACKNOWLEDGED" when a family member says they are handling it,
            "RESOLVED" when they confirm the person is fine,
            "FALSE_ALARM" when data shows it was not a real concern (optional).
        note: One-sentence explanation of your assessment or decision.
        acknowledged_by_phone: Phone of the family member who acknowledged (with ACKNOWLEDGED).
    """
    status = (status or "").upper()
    if status == "ACKNOWLEDGED":
        actions.acknowledge(incident_id, acknowledged_by_phone or "unknown", actor="agent")
    elif status in ("RESOLVED", "FALSE_ALARM"):
        actions.resolve(incident_id, status=status, note=note)
    if severity in ("info", "warning", "critical"):
        store.update_incident(incident_id, severity=severity)
    if note:
        store.add_timeline(incident_id, "agent", "assessment", note)
    return _j({"ok": True})


@tool
def schedule_escalation(incident_id: str, minutes: int = 5) -> str:
    """Escalate automatically if nobody acknowledges within `minutes`. The system
    will call you again with an `escalation_due` event. Use after first alerts.

    Args:
        incident_id: The incident ID.
        minutes: Minutes to wait (2-30). Use 2-3 for critical, 5-10 for warnings.
    """
    actions.schedule_escalation(incident_id, max(2, min(int(minutes), 30)))
    return _j({"ok": True})


ALL_TOOLS = [get_incident, get_patient_profile, get_recent_vitals, list_family_contacts,
             send_alert, send_message, send_sms, send_email, update_incident, schedule_escalation]
