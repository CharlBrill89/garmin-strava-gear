"""Gear-name equivalence and activity pairing."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gearcheck import matching
from gearcheck.models import Activity

# Left, right, expected-same. Several of these came from real data.
NAME_CASES = [
    ("Nike Pegasus 40", "Nike Pegasus 41", False),  # version bump is a real mismatch
    ("Nike Air Zoom Pegasus 41", "Nike Pegasus 41", True),
    ("Nike Air Zoom Pegasus 41", "Pegasus 41", True),
    ("Hoka Clifton 9", "Clifton", True),
    ("Hoka Clifton 9", "Hoka Bondi 9", False),
    ("Saucony Endorphin Speed 4", "Nike Pegasus 40", False),
    ("Saucony Endorphin Speed 4", "Endorphin Spd 4", True),
    ("Nike Pegasus 40", "Nike Vaporfly 3", False),
    ("Asics Novablast 4", "ASICS  Novablast  4", True),
    ("Brooks Ghost 15", "Brooks Ghost 15 (retired)", True),
    # Only the model number distinguishes these; stripping numbers before the
    # subset test once merged them.
    ("New Balance 880", "New Balance FuelCell Rebel v4", False),
    ("New Balance 880", "New Balance 1080", False),
    ("Saucony Peregrine 12 wide", "Saucony Peregrine", True),
    ("Reebok Floatride Energy", "Reebok Floatrider Energy", True),
    ("NB FuelCell Rebel v4", "New Balance FuelCell Rebel v4", True),
    ("Adizero", "Adidas Adizero SL M Adizero", True),
    ("Altra Experience Flow", "Altra Experience Flow Altras", True),
    ("New Balance 880", "HOKA Clifton 9", False),
]


@pytest.mark.parametrize(("left", "right", "expected"), NAME_CASES)
def test_same_shoe(left, right, expected):
    got, _why = matching.same_shoe(left, right, 0.85)
    assert got is expected


def test_version_difference_only_reported_for_same_model_line():
    _, why = matching.same_shoe("Nike Pegasus 40", "Nike Pegasus 41", 0.85)
    assert "different version" in why
    _, why = matching.same_shoe("Nike Pegasus 40", "Nike Vaporfly 3", 0.85)
    assert why == ""  # shares only a brand; not a version difference


# --- pairing ----------------------------------------------------------------

BASE = datetime(2026, 6, 1, 6, 0, 0, tzinfo=timezone.utc)


def _g(i, gear, dist=10000, day=0):
    return Activity("garmin", f"g{i}", "Morning Run", BASE + timedelta(days=day),
                    dist, dist / 3.0, "running", gear)


def _s(i, gear, dist=10000, day=0, drift_s=0, ext=None, name="Morning Run"):
    return Activity("strava", f"s{i}", name, BASE + timedelta(days=day, seconds=drift_s),
                    dist, dist / 3.0, "Run", gear, external_ref=ext)


def test_every_verdict_on_synthetic_data():
    garmin = [
        _g(1, ["Nike Pegasus 40"], day=0),
        _g(2, ["Hoka Clifton 9"], day=1),
        _g(3, [], day=2),
        _g(4, ["Saucony Endorphin Speed 4"], day=3),
        _g(5, [], day=4),
        _g(6, ["Nike Air Zoom Pegasus 41"], day=5),
        _g(7, ["Asics Novablast 4"], day=6),
        _g(8, ["Nike Pegasus 40"], day=7),
    ]
    strava = [
        _s(1, ["Nike Pegasus 40"], day=0, ext="g1"),
        _s(2, [], day=1),
        _s(3, ["Hoka Clifton 9"], day=2),
        _s(4, ["Nike Pegasus 40"], day=3),
        _s(5, [], day=4),
        _s(6, ["Nike Pegasus 41"], day=5),
        _s(8, ["Nike Pegasus 40"], day=7, drift_s=95),
        _s(99, ["Hoka Clifton 9"], dist=5000, day=20, name="Strava-only"),
    ]
    pairs = matching.build_pairs(garmin, strava, aliases={})
    got = {
        (p.garmin.activity_id if p.garmin else None,
         p.strava.activity_id if p.strava else None): p.verdict
        for p in pairs
    }
    assert got == {
        ("g1", "s1"): matching.MATCH,
        ("g2", "s2"): matching.MISSING_STRAVA,
        ("g3", "s3"): matching.MISSING_GARMIN,
        ("g4", "s4"): matching.DIFFERENT,
        ("g5", "s5"): matching.MISSING_BOTH,
        ("g6", "s6"): matching.NAME_VARIANT,
        ("g7", None): matching.NO_STRAVA_ACTIVITY,
        ("g8", "s8"): matching.MATCH,
        (None, "s99"): matching.NO_GARMIN_ACTIVITY,
    }
    by_key = {(p.garmin.activity_id if p.garmin else None): p for p in pairs}
    assert by_key["g1"].match_method == "external_id"
    assert by_key["g8"].match_method.startswith("start_time")


def test_distance_and_duration_both_disagreeing_is_not_the_same_run():
    garmin = [Activity("garmin", "g1", "Run", BASE, 10000, 3000, "running", ["A"])]
    strava = [Activity("strava", "s1", "Run", BASE, 8000, 2000, "Run", ["A"])]
    matched, only_g, only_s = matching.match_activities(garmin, strava)
    assert matched == [] and len(only_g) == 1 and len(only_s) == 1


def test_treadmill_distances_disagree_but_duration_pairs_them():
    # Real case: 1.00 km vs 0.82 km (18% apart), both 6 minutes, same start.
    garmin = [Activity("garmin", "g1", "Treadmill Running", BASE, 1000, 360, "treadmill_running", [])]
    strava = [Activity("strava", "s1", "Afternoon Run", BASE, 820, 361, "Run", ["Reebok"])]
    matched, only_g, only_s = matching.match_activities(garmin, strava)
    assert len(matched) == 1 and not only_g and not only_s
    _, _, method = matched[0]
    assert "by duration" in method


def test_agreeing_distance_does_not_mention_duration():
    garmin = [_g(1, ["A"])]
    strava = [_s(1, ["A"], drift_s=30)]
    (pair,) = matching.match_activities(garmin, strava)[0]
    assert pair[2] == "start_time (+/-30s)"


# --- aliases ----------------------------------------------------------------

def test_aliases_rewrite_gear_and_keep_raw():
    aliases = matching.aliases_from_pairs({"Newbies": "New Balance 880"})
    garmin = [_g(1, ["Newbies"])]
    strava = [_s(1, ["New Balance 880"])]
    (pair,) = matching.build_pairs(garmin, strava, aliases)
    assert pair.verdict == matching.MATCH
    assert pair.garmin.gear == ["New Balance 880"]
    assert pair.garmin.gear_raw == ["Newbies"]


def test_load_aliases_skips_comments_and_rejects_bad_shapes(tmp_path: Path):
    good = tmp_path / "a.json"
    good.write_text('{"_comment": "x", "Hoka Clifton 9": ["Clifton 9", "HOKA ONE ONE Clifton 9"]}')
    lookup = matching.load_aliases(good)
    assert lookup[matching.normalise("clifton 9")] == "Hoka Clifton 9"
    assert matching.normalise("x") not in lookup

    bad = tmp_path / "b.json"
    bad.write_text('{"Hoka Clifton 9": "Clifton 9"}')
    with pytest.raises(ValueError):
        matching.load_aliases(bad)
