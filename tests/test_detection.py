import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from carecircle.detection import detect  # noqa: E402
from carecircle.simulation import generate  # noqa: E402


def types(scenario):
    return [c["type"] for c in detect(generate(scenario, minutes=6, end_ts=1_800_000_000, seed=7))]


def test_normal_has_no_concerns():
    assert types("normal") == []


def test_exercise_is_not_an_alert():
    # High heart rate while walking is expected - the key false-alarm case.
    assert types("exercise") == []


def test_resting_tachycardia_warning():
    concerns = detect(generate("resting_tachycardia", 6, 1_800_000_000, seed=7))
    assert [c["type"] for c in concerns] == ["resting_tachycardia"]
    assert concerns[0]["severity"] == "warning"


def test_severe_tachycardia_is_critical():
    concerns = detect(generate("severe_tachycardia", 6, 1_800_000_000, seed=7))
    assert concerns[0]["type"] == "resting_tachycardia"
    assert concerns[0]["severity"] == "critical"


def test_low_spo2():
    assert "low_spo2" in types("low_spo2")


def test_fall_is_critical_and_first():
    concerns = detect(generate("fall", 6, 1_800_000_000, seed=7))
    assert concerns[0]["type"] == "fall" and concerns[0]["severity"] == "critical"


def test_short_spike_does_not_alert():
    base = generate("normal", 6, 1_800_000_000, seed=1)
    base[-1]["hr"] = 140  # one high reading only
    assert detect(base) == []


def test_patient_threshold_override():
    readings = generate("resting_tachycardia", 6, 1_800_000_000, seed=7)
    assert detect(readings, {"hr_high": 140}) == []
