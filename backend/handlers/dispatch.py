"""Dispatch Lambda: routes every event to the AgentCore agent, with a
deterministic fallback so an alert is never lost.

Events:
  {"type": "concern_detected", "incidentId", "patientId"}
  {"type": "family_message",   "phone", "text", "patientId", "incidentId"?}
  {"type": "escalation_due",   "incidentId", "patientId"}
  {"type": "device_offline",   "incidentId", "patientId"}
"""

import time
import traceback

from carecircle import agent_client, fallback, store


def process(event: dict) -> dict:
    incident_id = event.get("incidentId")
    session_key = incident_id or f"chat-{store.normalize_phone(event.get('phone', ''))}"
    started = time.time()
    try:
        result = agent_client.invoke(event, session_key)
        summary = result.get("summary") or result.get("result") or str(result)
        store.add_timeline(incident_id, "agent", "agent_decision", summary,
                           {"latency_s": round(time.time() - started, 1)})
        return {"handled_by": "agent", "result": result}
    except Exception as exc:  # noqa: BLE001
        print(f"[dispatch] agent unavailable, using fallback: {exc}")
        if not isinstance(exc, RuntimeError):
            traceback.print_exc()
        store.add_timeline(incident_id, "system", "agent_fallback",
                           f"agent unavailable ({type(exc).__name__}) - deterministic fallback used")
        return {"handled_by": "fallback", "result": fallback.handle(event)}


def handler(event, _context):
    print("[dispatch] event", event)
    return process(event)
