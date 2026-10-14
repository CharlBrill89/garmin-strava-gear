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
