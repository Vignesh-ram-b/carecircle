"""Daily health summary over Amazon SES (EventBridge schedule, or on demand from the dashboard).

Stats are computed in code; Amazon Bedrock writes a short, warm narrative
for the family. If Bedrock fails, the email still goes out with the numbers.
"""

import html
import time

import boto3

from carecircle import config, messaging, store
from carecircle.detection import summarize_vitals


def _narrative(patient: dict, stats: dict, incidents: list) -> str:
    prompt = (
        "Write a 3-4 sentence daily health note for a family about their relative, based only on these "
        "health-band numbers. Be warm, plain and factual. Do not diagnose. If anything looked unusual, "
        "suggest discussing it with their doctor.\n"
        f"Person: {patient.get('name')}, age {patient.get('age', 'unknown')}\n"
        f"Last 24h stats: {stats}\n"
        f"Alerts in last 24h: {[{'type': i['type'], 'severity': i['severity'], 'status': i['status']} for i in incidents]}"
    )
    try:
        resp = boto3.client("bedrock-runtime", region_name=config.region()).converse(
            modelId=config.get("MODEL_ID"),
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 300, "temperature": 0.3},
        )
        return resp["output"]["message"]["content"][0]["text"].strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[summary] narrative unavailable: {exc}")
        return ""


def send_summary(patient_id: str) -> dict:
    patient = store.get_patient(patient_id)
    if not patient:
        return {"sent": 0, "error": "unknown patient"}
    readings = store.get_recent_vitals(patient_id, minutes=24 * 60)
    stats = summarize_vitals(readings)
    since = store.now() - 86400
    incidents = [i for i in store.list_incidents(patient_id, 50) if int(i.get("createdAt", 0)) >= since]
    narrative = _narrative(patient, stats, incidents)
    name = patient.get("name", "your family member")
    date = time.strftime("%d %b %Y", time.gmtime())

    rows = [
        ("Average heart rate", f"{stats['hr_avg']} bpm" if stats["hr_avg"] else "-"),
        ("Heart rate range", f"{stats['hr_min']}-{stats['hr_max']} bpm" if stats["hr_avg"] else "-"),
        ("Lowest SpO2", f"{stats['spo2_min']}%" if stats["spo2_min"] else "-"),
        ("Steps", f"{stats['steps_total']:,}"),
        ("Alerts", str(len(incidents))),
    ]
    alert_lines = [f"- {i['summary']} ({i['severity']}, {i['status'].lower()}"
                   f"{', handled by ' + i['acknowledgedBy'] if i.get('acknowledgedBy') else ''})"
                   for i in incidents]
    text = (f"CareCircle daily summary for {name} - {date}\n\n{narrative}\n\n"
            + "\n".join(f"{k}: {v}" for k, v in rows)
            + ("\n\nAlerts:\n" + "\n".join(alert_lines) if alert_lines else "")
            + "\n\nCareCircle is a monitoring aid, not a medical device.")
    html_body = f"""<div style="font-family:Arial,sans-serif;max-width:560px;color:#1f2937">
<h2 style="color:#0f766e;margin-bottom:4px">CareCircle · {html.escape(name)}</h2>
<div style="color:#6b7280;margin-bottom:16px">Daily summary · {date}</div>
<p style="line-height:1.5">{html.escape(narrative) if narrative else ''}</p>
<table style="border-collapse:collapse;width:100%">{''.join(
        f'<tr><td style="padding:6px 0;color:#6b7280">{k}</td><td style="padding:6px 0;text-align:right;font-weight:600">{html.escape(v)}</td></tr>'
        for k, v in rows)}</table>
{('<h3 style="margin-top:20px">Alerts</h3><ul>' + ''.join(f'<li>{html.escape(a[2:])}</li>' for a in alert_lines) + '</ul>') if alert_lines else ''}
<p style="color:#9ca3af;font-size:12px;margin-top:24px">CareCircle is a monitoring aid, not a medical device.</p></div>"""

    sent = 0
    for contact in store.get_contacts(patient_id):
        if contact.get("email") and contact.get("dailySummary", True):
            res = messaging.send_email(contact["email"], f"CareCircle: {name}'s day ({date})", text, html_body)
            sent += 1 if res["ok"] else 0
    return {"sent": sent, "stats": stats, "incidents": len(incidents)}


def handler(event, _context):
    patient_id = (event or {}).get("patientId")
    targets = [patient_id] if patient_id else [p["patientId"] for p in store.list_patients()]
    return {pid: send_summary(pid) for pid in targets}
