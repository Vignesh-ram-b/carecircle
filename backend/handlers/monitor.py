"""Monitor Lambda (EventBridge, every minute).

1. Escalates incidents nobody acknowledged in time.
2. Optionally opens a `device_offline` incident when the band goes silent
   (enabled when OFFLINE_MINUTES > 0).
"""

from carecircle import config, service, store


def handler(_event, _context):
    now = store.now()
    escalated, offline = [], []

    for inc in store.list_open_incidents():
        due = inc.get("nextEscalationAt")
        if inc.get("status") == "OPEN" and due and int(due) <= now:
            store.remove_incident_field(inc["incidentId"], "nextEscalationAt")  # avoid double fire
            store.add_timeline(inc["incidentId"], "monitor", "escalation_due",
                               "no acknowledgement received in time")
            service.dispatch_async({"type": "escalation_due", "incidentId": inc["incidentId"],
                                    "patientId": inc["patientId"]})
            escalated.append(inc["incidentId"])

    offline_minutes = config.get_int("OFFLINE_MINUTES", 0)
    if offline_minutes > 0:
        for patient in store.list_patients():
            if patient.get("offlineAlerts") is False:
                continue
            latest = store.get_latest_vital(patient["patientId"])
            if not latest or now - int(latest["ts"]) < offline_minutes * 60:
                continue
            if store.find_open_incident(patient["patientId"], "device_offline"):
                continue
            mins = (now - int(latest["ts"])) // 60
            inc = store.create_incident(patient["patientId"], {
                "type": "device_offline", "severity": "warning",
                "summary": f"No data from the health band for {mins} minutes",
                "reading": f"last reading HR {latest.get('hr')} bpm",
            })
            service.dispatch_async({"type": "device_offline", "incidentId": inc["incidentId"],
                                    "patientId": patient["patientId"]})
            offline.append(inc["incidentId"])

    return {"escalated": escalated, "offline": offline}
