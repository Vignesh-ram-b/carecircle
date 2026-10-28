"""Rule-based concern detection.

Deliberately simple and explainable: the rules decide *whether something looks
wrong*; the agent then decides *how serious it is and who to tell*.

Each reading: {"ts": epoch_s, "hr": bpm, "spo2": %, "steps": steps/min, "fall": bool}
"""

DEFAULT_THRESHOLDS = {
    "hr_high": 120,          # bpm, sustained while at rest
    "hr_critical": 150,      # bpm, sustained while at rest
    "hr_low": 45,            # bpm, sustained
    "spo2_low": 92,          # %, two consecutive readings
    "spo2_critical": 88,     # %
    "rest_steps": 30,        # steps/min at or below this counts as "at rest"
    "sustain_minutes": 4,    # how long HR must stay abnormal
    "min_readings": 3,       # readings needed inside the sustain window
}


def thresholds_for(patient: dict) -> dict:
    t = dict(DEFAULT_THRESHOLDS)
    t.update({k: v for k, v in (patient or {}).get("thresholds", {}).items() if v is not None})
    return t


def _num(values):
    return [v for v in values if isinstance(v, (int, float))]


def detect(readings: list, thresholds: dict = None, now_ts: int = None) -> list:
    """Return a list of concerns, most severe first."""
    t = dict(DEFAULT_THRESHOLDS)
    t.update(thresholds or {})
    readings = sorted([r for r in readings if r.get("ts") is not None], key=lambda r: r["ts"])
    if not readings:
        return []
    now_ts = now_ts or readings[-1]["ts"]
    concerns = []

    # 1. Fall -------------------------------------------------------------
    recent_falls = [r for r in readings if r.get("fall") and now_ts - r["ts"] <= 180]
    if recent_falls:
        r = recent_falls[-1]
        concerns.append({
            "type": "fall",
            "severity": "critical",
            "summary": "The band detected a possible fall",
            "reading": _fmt(r),
            "evidence": {"fall_ts": r["ts"], "hr": r.get("hr")},
        })

    # 2. Low SpO2 (two consecutive readings) -----------------------------
    spo2_readings = [r for r in readings if isinstance(r.get("spo2"), (int, float))]
    if len(spo2_readings) >= 2:
        last_two = spo2_readings[-2:]
        if all(r["spo2"] < t["spo2_low"] for r in last_two):
            low = min(r["spo2"] for r in last_two)
            concerns.append({
                "type": "low_spo2",
                "severity": "critical" if low < t["spo2_critical"] else "warning",
                "summary": f"Blood oxygen has dropped to {low}%",
                "reading": _fmt(last_two[-1]),
                "evidence": {"spo2_values": [r["spo2"] for r in last_two]},
            })

    # 3. Sustained heart-rate problems -----------------------------------
    window = [r for r in readings if now_ts - r["ts"] <= t["sustain_minutes"] * 60
              and isinstance(r.get("hr"), (int, float))]
    if len(window) >= t["min_readings"]:
        hrs = [r["hr"] for r in window]
        steps = _num([r.get("steps") for r in window])
        avg_steps = sum(steps) / len(steps) if steps else 0
        avg_hr = round(sum(hrs) / len(hrs))
        at_rest = avg_steps <= t["rest_steps"]

        if min(hrs) >= t["hr_high"]:
            if at_rest:
                concerns.append({
                    "type": "resting_tachycardia",
                    "severity": "critical" if avg_hr >= t["hr_critical"] else "warning",
                    "summary": f"Heart rate has stayed around {avg_hr} bpm for "
                               f"{t['sustain_minutes']}+ minutes while at rest",
                    "reading": _fmt(window[-1]),
                    "evidence": {"avg_hr": avg_hr, "max_hr": max(hrs),
                                 "avg_steps_per_min": round(avg_steps), "minutes": t["sustain_minutes"]},
                })
            # High HR while active is expected (walk/exercise) - not a concern.
        elif max(hrs) <= t["hr_low"]:
            concerns.append({
                "type": "bradycardia",
                "severity": "warning",
                "summary": f"Heart rate has stayed low, around {avg_hr} bpm",
                "reading": _fmt(window[-1]),
                "evidence": {"avg_hr": avg_hr, "min_hr": min(hrs)},
            })

    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(concerns, key=lambda c: order.get(c["severity"], 3))


def summarize_vitals(readings: list) -> dict:
    hrs = _num([r.get("hr") for r in readings])
    spo2 = _num([r.get("spo2") for r in readings])
    steps = _num([r.get("steps") for r in readings])
    return {
        "count": len(readings),
        "hr_avg": round(sum(hrs) / len(hrs)) if hrs else None,
        "hr_min": min(hrs) if hrs else None,
        "hr_max": max(hrs) if hrs else None,
        "spo2_min": min(spo2) if spo2 else None,
        "spo2_avg": round(sum(spo2) / len(spo2), 1) if spo2 else None,
        "steps_total": int(sum(steps)) if steps else 0,
        "falls": sum(1 for r in readings if r.get("fall")),
        "first_ts": readings[0]["ts"] if readings else None,
        "last_ts": readings[-1]["ts"] if readings else None,
    }


def _fmt(r: dict) -> str:
    parts = []
    if r.get("hr") is not None:
        parts.append(f"HR {r['hr']} bpm")
    if r.get("spo2") is not None:
        parts.append(f"SpO2 {r['spo2']}%")
    if r.get("steps") is not None:
        parts.append(f"{r['steps']} steps/min")
    return ", ".join(parts) or "no reading"
