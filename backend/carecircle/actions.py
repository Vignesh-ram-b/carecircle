"""High-level actions shared by the AgentCore agent tools and the
deterministic fallback. Every action writes to the incident timeline, so the
dashboard (and the judges) can see exactly what happened and who did it.

Channels
  email      Amazon SES - always available (default). Alerts carry one-tap reply buttons.
  whatsapp   AWS End User Messaging Social - enabled when WA_PHONE_NUMBER_ID is set.
  sms        AWS End User Messaging SMS - escalation backup, when an origination identity is set.
  dashboard  replies to questions asked in the dashboard's "Ask CareCircle" box.
"""

from . import config, emails, messaging, store
from .detection import summarize_vitals


# ------------------------------------------------------------- channels
def whatsapp_enabled() -> bool:
    pid = str(config.get("WA_PHONE_NUMBER_ID") or "")
    return bool(pid) and "REPLACE" not in pid.upper()


def sms_enabled() -> bool:
    return bool(config.get("SMS_ORIGINATION_IDENTITY")) or config.dry_run()


def channels_for(contact: dict) -> list:
    """Every channel an *alert* goes out on for this contact."""
    chans = []
    if whatsapp_enabled() and contact.get("phone") and contact.get("whatsapp", True):
        chans.append("whatsapp")
    if contact.get("email"):
        chans.append("email")
    return chans


def reply_channel(contact: dict) -> str:
    """Where a conversational reply goes: back where they last talked to us."""
    last = contact.get("lastChannel")
    if last == "whatsapp" and whatsapp_enabled():
        return "whatsapp"
    if last in ("email", "dashboard"):
        return last
    chans = channels_for(contact)
    return chans[0] if chans else "sms"


def _who(contact: dict, phone: str) -> str:
    return contact.get("name") or phone


def _patient_for(incident: dict, contact: dict) -> dict:
    pid = (incident or {}).get("patientId") or contact.get("patientId", "")
    return store.get_patient(pid) or {"patientId": pid}


# ----------------------------------------------------------------- alerts
def send_alert(incident_id: str, phone: str, alert_summary: str, latest_reading: str = "",
               message: str = "", actor: str = "agent") -> dict:
    """First alert (or escalation alert) to one family member on every channel they have."""
    contact = store.get_contact(phone) or {"phone": phone}
    incident = store.get_incident(incident_id) if incident_id else {}
    patient = _patient_for(incident, contact)
    name = patient.get("name", "your family member")
    latest_reading = latest_reading or (incident or {}).get("reading", "")
    message = message or f"{alert_summary}. Please check on {name}."
    severity = (incident or {}).get("severity", "warning")
    emergency = patient.get("emergencyNumber") or config.get("EMERGENCY_NUMBER")

    results = []
    for channel in channels_for(contact):
        if channel == "whatsapp":
            res = _whatsapp(contact, phone, message, alert_summary, latest_reading, name)
            detail = f"to {_who(contact, phone)} ({res.get('mode')}): {alert_summary}"
        else:
            subject, text, html_body = emails.alert_email(
                name, alert_summary, latest_reading, message, incident_id, contact.get("phone", phone),
                severity, emergency)
            res = messaging.send_email(contact["email"], subject, text, html_body)
            if res["ok"] and incident_id and not (incident or {}).get("emailSubject"):
                store.update_incident(incident_id, emailSubject=subject)
                incident = {**(incident or {}), "emailSubject": subject}
            detail = f"to {_who(contact, phone)} <{contact['email']}>: {alert_summary}"
        store.add_timeline(incident_id, actor, f"{channel}_alert_sent" if res["ok"] else f"{channel}_failed",
                           detail, {"channel": channel, "to": store.normalize_phone(phone),
                                    "error": res.get("error")})
        results.append(res)

    if not results:
        store.add_timeline(incident_id, actor, "alert_failed",
                           f"{_who(contact, phone)} has no reachable channel (add an email)")
        return {"ok": False, "error": "contact has no email and WhatsApp is not enabled"}
    ok = any(r["ok"] for r in results)
    if ok and incident_id:
        store.mark_notified(incident_id, phone)
    return {"ok": ok, "channels": [r["channel"] for r in results],
            "errors": [r.get("error") for r in results if not r["ok"]]}


# -------------------------------------------------------- conversational
def send_message(incident_id: str, phone: str, text: str, channel: str = "auto",
                 actor: str = "agent") -> dict:
    """Conversational message (reply, trend answer, 'X is handling it')."""
    contact = store.get_contact(phone) or {"phone": phone}
    incident = store.get_incident(incident_id) if incident_id else {}
    patient = _patient_for(incident, contact)
    name = patient.get("name", "your family member")
    if channel in (None, "", "auto"):
        channel = reply_channel(contact)
    elif channel == "alert":  # the channel their alerts arrive on
        chans = channels_for(contact)
        channel = chans[0] if chans else reply_channel(contact)

    if channel == "whatsapp" and whatsapp_enabled():
        res = _whatsapp(contact, phone, text, text, (incident or {}).get("reading", ""), name)
    elif channel == "dashboard":
        store.add_chat(patient["patientId"], "agent", "CareCircle", text)
        res = {"ok": True, "channel": "dashboard"}
    elif channel == "sms":
        res = messaging.send_sms(phone, text)
    else:
        channel = "email"
        if not contact.get("email"):
            res = {"ok": False, "channel": "email", "error": "contact has no email"}
        else:
            subject = (incident or {}).get("emailSubject")
            subject = f"Re: {subject}" if subject else f"CareCircle update: {name}"
            text_body, html_body = emails.message_email(
                name, text, incident_id, contact.get("phone", phone),
                (incident or {}).get("status") in store.OPEN_STATUSES)
            res = messaging.send_email(contact["email"], subject, text_body, html_body)

    if incident_id or channel != "dashboard":
        store.add_timeline(incident_id, actor, f"{channel}_sent" if res["ok"] else f"{channel}_failed",
                           f"to {_who(contact, phone)}: {text}",
                           {"channel": channel, "to": store.normalize_phone(phone), "error": res.get("error")})
    res["channel"] = channel
    return res


def _whatsapp(contact, phone, text, alert_summary, latest_reading, patient_name) -> dict:
    """Free-form text inside the 24h window, otherwise the approved alert template."""
    if store.in_whatsapp_window(contact):
        res = messaging.send_whatsapp_text(phone, text)
        res["mode"] = "text"
    else:
        res = messaging.send_whatsapp_alert_template(
            phone, patient_name=patient_name, alert_summary=alert_summary or text,
            latest_reading=latest_reading or "see dashboard", language=contact.get("language"))
        res["mode"] = "template"
    return res


# Backwards-compatible name used by older code paths
def whatsapp_to_contact(incident_id, phone, text, alert_summary="", latest_reading="", actor="agent"):
    return send_message(incident_id, phone, text, channel="whatsapp", actor=actor)


def sms_to_contact(incident_id: str, phone: str, text: str, actor: str = "agent") -> dict:
    contact = store.get_contact(phone) or {"phone": phone}
    if not sms_enabled():
        store.add_timeline(incident_id, actor, "sms_skipped",
                           f"SMS not configured - {_who(contact, phone)} not texted")
        return {"ok": False, "channel": "sms", "error": "SMS not configured"}
    result = messaging.send_sms(phone, text)
    store.add_timeline(
        incident_id, actor, "sms_sent" if result["ok"] else "sms_failed",
        f"to {_who(contact, phone)}: {text}",
        {"channel": "sms", "to": store.normalize_phone(phone), "error": result.get("error")},
    )
    return result


def email_to(incident_id: str, to_email: str, subject: str, body: str, html: str = None,
             actor: str = "agent") -> dict:
    result = messaging.send_email(to_email, subject, body, html)
    store.add_timeline(
        incident_id, actor, "email_sent" if result["ok"] else "email_failed",
        f"to {to_email}: {subject}", {"channel": "email", "error": result.get("error")},
    )
    return result


# ------------------------------------------------------- incident state
def schedule_escalation(incident_id: str, minutes: int = None, actor: str = "agent"):
    minutes = int(minutes or config.get_int("ACK_TIMEOUT_MIN", 5))
    store.update_incident(incident_id, nextEscalationAt=store.now() + minutes * 60)
    store.add_timeline(incident_id, actor, "escalation_scheduled",
                       f"will escalate if nobody acknowledges within {minutes} min")


def acknowledge(incident_id: str, phone: str, actor: str = "family"):
    contact = store.get_contact(phone) or {}
    store.update_incident(incident_id, status="ACKNOWLEDGED",
                          acknowledgedBy=contact.get("name", phone), acknowledgedAt=store.now())
    try:
        store.remove_incident_field(incident_id, "nextEscalationAt")
    except Exception:  # noqa: BLE001
        pass
    store.add_timeline(incident_id, actor, "acknowledged",
                       f"{contact.get('name', phone)} is checking in - escalation stopped")


def resolve(incident_id: str, status: str = "RESOLVED", note: str = "", actor: str = "agent"):
    store.update_incident(incident_id, status=status, resolvedAt=store.now())
    try:
        store.remove_incident_field(incident_id, "nextEscalationAt")
    except Exception:  # noqa: BLE001
        pass
    store.add_timeline(incident_id, actor, status.lower(), note)


# ------------------------------------------------------------- vitals
def vitals_brief(patient_id: str, minutes: int = 60) -> dict:
    readings = store.get_recent_vitals(patient_id, minutes)
    stats = summarize_vitals(readings)
    stats["window_minutes"] = minutes
    # Keep the payload small for the model: one sample every few minutes.
    step = max(1, len(readings) // 12)
    stats["samples"] = [
        {k: r.get(k) for k in ("ts", "hr", "spo2", "steps", "fall")} for r in readings[::step]
    ][-12:]
    return stats


def trend_text(patient_id: str, minutes: int = 60) -> str:
    s = vitals_brief(patient_id, minutes)
    if not s["count"]:
        return f"No readings from the band in the last {minutes} minutes."
    return (f"Last {minutes} min: heart rate avg {s['hr_avg']} bpm "
            f"(range {s['hr_min']}-{s['hr_max']}), lowest SpO2 {s['spo2_min']}%, "
            f"{s['steps_total']} steps, falls detected: {s['falls']}.")
