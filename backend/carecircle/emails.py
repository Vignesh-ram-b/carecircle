"""Email bodies (Amazon SES) and signed one-tap action links.

Alert emails carry three buttons - "I'm checking", "Show trend", "All fine now".
Each is a link to the CareCircle API signed with HMAC, so tapping it is the
email equivalent of replying on WhatsApp: no login, no inbound mail server.
"""

import hashlib
import hmac
import html
from urllib.parse import urlencode

from . import config

ACTIONS = {
    "ack": "I'm checking",
    "trend": "Show trend",
    "resolve": "All fine now",
}


def _secret() -> bytes:
    return str(config.get("ACTION_SECRET") or config.get("API_TOKEN") or "carecircle-dev").encode()


def sign(incident_id: str, phone: str, action: str) -> str:
    msg = f"{incident_id or '-'}|{phone}|{action}".encode()
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:32]


def verify(incident_id: str, phone: str, action: str, signature: str) -> bool:
    return hmac.compare_digest(sign(incident_id, phone, action), str(signature or ""))


def action_url(incident_id: str, phone: str, action: str) -> str:
    base = (config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    query = urlencode({"i": incident_id or "-", "c": phone, "a": action,
                       "s": sign(incident_id, phone, action)})
    return f"{base}/act?{query}"


def _button(url: str, label: str, primary: bool) -> str:
    bg, fg = ("#0f766e", "#ffffff") if primary else ("#ffffff", "#0f766e")
    return (f'<a href="{html.escape(url)}" style="display:inline-block;margin:4px 6px 4px 0;padding:12px 18px;'
            f'border-radius:10px;border:2px solid #0f766e;background:{bg};color:{fg};font-weight:700;'
            f'text-decoration:none;font-family:Arial,sans-serif;font-size:15px">{html.escape(label)}</a>')


def _wrap(inner: str) -> str:
    return (f'<div style="font-family:Arial,sans-serif;max-width:560px;color:#1f2937;line-height:1.5">{inner}'
            '<p style="color:#9ca3af;font-size:12px;margin-top:24px">CareCircle is a monitoring aid, not a '
            'medical device. In an emergency call your local emergency number.</p></div>')


def alert_email(patient_name: str, alert_summary: str, latest_reading: str, message: str,
                incident_id: str, phone: str, severity: str = "warning", emergency: str = ""):
    color = "#b91c1c" if severity == "critical" else "#b45309"
    links = {a: action_url(incident_id, phone, a) for a in ACTIONS}
    buttons = "".join(_button(links[a], label, a == "ack") for a, label in ACTIONS.items())
    emergency_html = (f'<p style="background:#fee2e2;color:#b91c1c;padding:10px 12px;border-radius:8px;font-weight:700">'
                      f'If {html.escape(patient_name)} is unresponsive, call {html.escape(emergency)} now.</p>'
                      if severity == "critical" and emergency else "")
    body = _wrap(
        f'<div style="font-size:12px;font-weight:700;letter-spacing:.06em;color:{color};text-transform:uppercase">'
        f'CareCircle health alert · {html.escape(severity)}</div>'
        f'<h2 style="margin:6px 0 4px">{html.escape(patient_name)}: {html.escape(alert_summary)}</h2>'
        f'<div style="color:#6b7280;margin-bottom:14px">Latest reading: {html.escape(latest_reading)}</div>'
        f'<p>{html.escape(message)}</p>{emergency_html}'
        f'<p style="margin:18px 0 6px;font-weight:700">Tap to reply:</p>{buttons}'
        f'<p style="color:#6b7280;font-size:13px">If nobody responds, CareCircle will alert the next family member.</p>')
    text = (f"CareCircle health alert ({severity}) - {patient_name}: {alert_summary}\n"
            f"Latest reading: {latest_reading}\n\n{message}\n\n"
            + (f"If {patient_name} is unresponsive, call {emergency} now.\n\n" if severity == "critical" and emergency else "")
            + "\n".join(f"{label}: {links[a]}" for a, label in ACTIONS.items()))
    subject = f"⚠️ CareCircle alert: {patient_name} - {alert_summary}"
    return subject[:180], text, body


def message_email(patient_name: str, message: str, incident_id: str = None, phone: str = "",
                  incident_open: bool = False):
    buttons = ""
    if incident_id and incident_open:
        buttons = ('<p style="margin:18px 0 6px;font-weight:700">Quick replies:</p>'
                   + "".join(_button(action_url(incident_id, phone, a), label, a == "ack")
                             for a, label in ACTIONS.items()))
    body = _wrap(f'<div style="font-size:12px;font-weight:700;letter-spacing:.06em;color:#0f766e;'
                 f'text-transform:uppercase">CareCircle · {html.escape(patient_name)}</div>'
                 f'<p style="font-size:16px;white-space:pre-line">{html.escape(message)}</p>{buttons}')
    return message, body


def action_page(title: str, detail: str, ok: bool = True) -> str:
    color = "#0f766e" if ok else "#b91c1c"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CareCircle</title>
<style>body{{margin:0;background:#f4f6f5;font-family:Arial,sans-serif;color:#14211d}}
.c{{max-width:460px;margin:12vh auto;background:#fff;border:1px solid #e2e8e5;border-radius:16px;padding:28px 24px}}
h1{{color:{color};font-size:22px;margin:0 0 10px}}p{{line-height:1.5;white-space:pre-line}}
small{{color:#6b7280}}</style></head><body><div class="c"><h1>{html.escape(title)}</h1>
<p id="d">{html.escape(detail)}</p><small>CareCircle · monitoring aid, not a medical device</small></div></body></html>"""


def confirm_page(params: dict, label: str) -> str:
    """GET /act returns this page; it POSTs the action from the browser.
    Mail-scanner bots that pre-fetch links (GET only, no JavaScript) therefore
    cannot acknowledge an alert by accident."""
    import json as _json
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CareCircle</title>
<style>body{{margin:0;background:#f4f6f5;font-family:Arial,sans-serif;color:#14211d}}
.c{{max-width:460px;margin:12vh auto;background:#fff;border:1px solid #e2e8e5;border-radius:16px;padding:28px 24px}}
h1{{color:#0f766e;font-size:22px;margin:0 0 10px}}p{{line-height:1.5;white-space:pre-line}}
button{{font:inherit;font-weight:700;background:#0f766e;color:#fff;border:0;border-radius:10px;padding:12px 18px;cursor:pointer}}
small{{color:#6b7280}}</style></head><body><div class="c"><h1 id="t">Sending “{html.escape(label)}”…</h1>
<p id="d"></p><noscript><p>JavaScript is off.</p></noscript>
<button id="b" style="display:none">Send “{html.escape(label)}”</button>
<p><small>CareCircle · monitoring aid, not a medical device</small></p></div>
<script>
const params = {_json.dumps(params)};
async function go() {{
  document.getElementById("b").style.display = "none";
  try {{
    const r = await fetch("act", {{ method: "POST", headers: {{ "content-type": "application/json" }}, body: JSON.stringify(params) }});
    const j = await r.json();
    document.getElementById("t").textContent = j.title || "Done";
    document.getElementById("d").textContent = j.detail || "";
  }} catch (e) {{
    document.getElementById("t").textContent = "Could not reach CareCircle";
    document.getElementById("b").style.display = "inline-block";
  }}
}}
document.getElementById("b").onclick = go;
go();
</script></body></html>"""
