"""Ingest pipeline: store readings -> detect concerns -> open incidents -> hand off to the agent."""

import json

import boto3

from . import config, detection, store

_lambda = None


def dispatch_async(event: dict):
    """Hand an event to the dispatch Lambda (which calls the AgentCore agent).
    Asynchronous, so the band/API never waits on the model."""
    global _lambda
    fn = config.get("DISPATCH_FUNCTION")
    if not fn:
        from handlers import dispatch  # local / test mode
        return dispatch.process(event)
    if _lambda is None:
        _lambda = boto3.client("lambda", region_name=config.region())
    _lambda.invoke(FunctionName=fn, InvocationType="Event", Payload=json.dumps(event).encode())
    return {"queued": True}


def ingest(patient_id: str, readings: list) -> dict:
    patient = store.get_patient(patient_id)
    if not patient:
        raise KeyError(f"unknown patient {patient_id}")

    stored = store.put_vitals(patient_id, readings)
    window = store.get_recent_vitals(patient_id, minutes=15)
    concerns = detection.detect(window, detection.thresholds_for(patient))

    opened, updated = [], []
    for concern in concerns:
        existing = store.find_open_incident(patient_id, concern["type"])
        if existing:
            # Same problem still going on: don't spam the family, just record it.
            store.update_incident(existing["incidentId"], reading=concern["reading"])
            updated.append(existing["incidentId"])
            continue
        incident = store.create_incident(patient_id, concern)
        opened.append(incident["incidentId"])
        dispatch_async({"type": "concern_detected", "incidentId": incident["incidentId"],
                        "patientId": patient_id})

    return {"stored": stored, "concerns": [c["type"] for c in concerns],
            "incidentsOpened": opened, "incidentsUpdated": updated}
