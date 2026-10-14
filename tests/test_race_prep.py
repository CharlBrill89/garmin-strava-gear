"""Half-split drift maths for the race prep report."""

import pytest

import race_prep as rp


def lap(km_pace_s: float, hr: float) -> dict:
    return {"distance": 1000.0, "duration": km_pace_s, "averageHR": hr}


def test_halves_split_laps_by_distance():
    laps = [lap(360, 140)] * 5 + [lap(360, 150)] * 5
    first, second = rp.halves_from_laps(laps)
    assert first["hr"] == pytest.approx(140)
    assert second["hr"] == pytest.approx(150)
    assert rp.fmt_pace(first["speed"]) == "6:00"
    # Same pace, HR up 10 beats: speed per beat falls by 1 - 140/150.
    assert rp.decoupling(first, second) == pytest.approx((1 - 140 / 150) * 100)


def test_too_few_laps_is_none():
    assert rp.halves_from_laps([lap(360, 140)] * 3) is None


def test_halves_from_details_time_series():
    details = {
        "metricDescriptors": [
            {"key": "directHeartRate", "metricsIndex": 0},
            {"key": "sumDistance", "metricsIndex": 1},
            {"key": "sumDuration", "metricsIndex": 2},
        ],
        "activityDetailMetrics": [
            {"metrics": [140 if t < 1800 else 150, t / 3.6 * 1000 / 1000, t]}
            for t in range(0, 3600, 60)
        ],
    }
    first, second = rp.halves_from_details(details)
    assert first["hr"] == pytest.approx(140)
    assert second["hr"] == pytest.approx(150)


def test_weekly_rows_group_by_monday():
    run = {"type": "running", "phase": "-", "max_hr": None, "elev": None}
    runs = [
        {**run, "date": "2026-10-05", "km": 10.0, "duration": 3000, "speed": 10000 / 3000, "hr": 150},
        {**run, "date": "2026-10-11", "km": 5.0, "duration": 1500, "speed": 5000 / 1500, "hr": None},
        {**run, "date": "2026-10-12", "km": 8.0, "duration": 2400, "speed": 8000 / 2400, "hr": 140},
    ]
    rows = rp.weekly_rows(runs)
    assert rows[0].startswith("| 2026-10-05 | 2 | 15.0 | 10.0 | 5:00 | 150 | 1.33 |")
    assert rows[1].startswith("| 2026-10-12 | 1 | 8.0 | 8.0 | 5:00 | 140 | 1.43 |")
