"""Synthetic health-band readings for demos and tests.

Scenarios (one reading per minute, ending now):
  normal               resting HR 68-80, SpO2 96-99
  exercise             HR 125-140 but 90-120 steps/min  -> should NOT alert
  resting_tachycardia  HR 125-138 with ~0 steps          -> warning
  severe_tachycardia   HR 152-165 at rest                -> critical
  low_spo2             SpO2 drifting to 89-90%           -> warning
  fall                 sudden HR spike + fall flag       -> critical
"""

import random
import time

SCENARIOS = ("normal", "exercise", "resting_tachycardia", "severe_tachycardia", "low_spo2", "fall")


def generate(scenario: str = "normal", minutes: int = 6, end_ts: int = None, seed: int = None,
             align: bool = True) -> list:
    """align=True puts readings on whole minutes, so a new burst overwrites the
    previous burst (same DynamoDB keys) instead of interleaving with it."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario}; choose from {', '.join(SCENARIOS)}")
    rnd = random.Random(seed)
    end_ts = int(end_ts or time.time())
    if align:
        end_ts = end_ts // 60 * 60
    readings = []
    for i in range(minutes):
        ts = end_ts - (minutes - 1 - i) * 60
        hr, spo2, steps, fall = rnd.randint(68, 80), rnd.randint(96, 99), rnd.randint(0, 15), False
        if scenario == "exercise":
            hr, steps = rnd.randint(125, 140), rnd.randint(90, 120)
        elif scenario == "resting_tachycardia":
            hr, steps = rnd.randint(125, 138), rnd.randint(0, 6)
        elif scenario == "severe_tachycardia":
            hr, steps = rnd.randint(152, 165), rnd.randint(0, 4)
        elif scenario == "low_spo2":
            spo2 = 96 - i if i < 4 else rnd.randint(89, 90)
        elif scenario == "fall" and i == minutes - 1:
            hr, steps, fall = rnd.randint(110, 120), 0, True
        readings.append({"ts": ts, "hr": hr, "spo2": spo2, "steps": steps, "fall": fall,
                         "source": f"simulator:{scenario}"})
    return readings
