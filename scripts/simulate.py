#!/usr/bin/env python3
"""Stream simulated health-band readings to the CareCircle API.

Examples:
    # one burst of 6 minutes of data (what the dashboard buttons do)
    python scripts/simulate.py --api https://xxxx.execute-api.ap-southeast-2.amazonaws.com --token T --scenario resting_tachycardia

    # live stream: one reading every 10 seconds, switching scenario after 2 minutes
    python scripts/simulate.py --api ... --token T --live --scenario normal --then resting_tachycardia --switch-after 120

Scenarios: normal, exercise, resting_tachycardia, severe_tachycardia, low_spo2, fall
"""

import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from carecircle.simulation import SCENARIOS, generate  # noqa: E402


def post(api: str, token: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        api.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
        headers={"content-type": "application/json", "x-api-token": token})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--patient", default="appa")
    ap.add_argument("--scenario", default="normal", choices=SCENARIOS)
    ap.add_argument("--minutes", type=int, default=6)
    ap.add_argument("--live", action="store_true", help="stream readings in real time")
    ap.add_argument("--interval", type=int, default=10, help="seconds between live readings")
    ap.add_argument("--then", choices=SCENARIOS, help="scenario to switch to in live mode")
    ap.add_argument("--switch-after", type=int, default=120)
    args = ap.parse_args()

    if not args.live:
        print(json.dumps(post(args.api, args.token, "/simulate",
                              {"patientId": args.patient, "scenario": args.scenario,
                               "minutes": args.minutes}), indent=2))
        return

    start, scenario = time.time(), args.scenario
    print(f"streaming '{scenario}' every {args.interval}s - Ctrl+C to stop")
    while True:
        if args.then and scenario != args.then and time.time() - start > args.switch_after:
            scenario = args.then
            print(f"--> switching to '{scenario}'")
        reading = generate(scenario, minutes=1, align=False)[0]
        result = post(args.api, args.token, "/vitals", {"patientId": args.patient, "readings": [reading]})
        print(time.strftime("%H:%M:%S"), f"HR {reading['hr']} SpO2 {reading['spo2']} steps {reading['steps']}",
              "->", result.get("concerns") or "ok", result.get("incidentsOpened") or "")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
