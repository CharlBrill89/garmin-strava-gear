"""Garmin gear resolution and window handling, against a fake client."""

from datetime import date

import pytest

from gearcheck import garmin_source as gs

ACTIVITIES = [
    {"activityId": 1, "activityName": "Morning Run", "startTimeGMT": "2026-09-01 06:04:12",
     "distance": 10020.0, "duration": 2100.0, "activityType": {"typeKey": "running"}},
    {"activityId": 2, "activityName": "Easy Run", "startTimeGMT": "2026-09-02 05:58:00",
     "distance": 8000.0, "duration": 1800.0, "activityType": {"typeKey": "running"}},
    {"activityId": 3, "activityName": "Trail Loop", "startTimeGMT": "2026-09-03 06:15:30",
     "distance": 12000.0, "duration": 3600.0, "activityType": {"typeKey": "trail_running"}},
    {"activityId": 4, "activityName": "Ride", "startTimeGMT": "2026-09-04 07:00:00",
     "distance": 25000.0, "duration": 3000.0, "activityType": {"typeKey": "cycling"}},
    {"activityId": 5, "activityName": "Bad Date", "startTimeGMT": "",
     "distance": 5000.0, "duration": 1500.0, "activityType": {"typeKey": "running"}},
    # Evening run: 01:17 UTC on the 1st is the previous local evening, which is
    # how Garmin's local-date filter loses it without padding.
    {"activityId": 6, "activityName": "Late Run", "startTimeGMT": "2026-09-01 01:17:00",
     "distance": 5030.0, "duration": 1600.0, "activityType": {"typeKey": "running"}},
]

GEAR = [
    {"uuid": "u-peg", "displayName": "Nike Pegasus 41", "customMakeModel": "Nike Air Zoom Pegasus 41", "gearTypeName": "Shoes"},
    {"uuid": "u-hoka", "customMakeModel": "Hoka Speedgoat 5", "gearTypeName": "Shoes"},
    {"uuid": "u-nb", "displayName": "Newbies", "customMakeModel": "New Balance 880", "gearTypeName": "Shoes"},
    {"uuid": "u-bike", "gearMakeName": "Canyon", "gearModelName": "Endurace", "gearTypeName": "Bike"},
]
GEAR_ACTS = {
    "u-peg": [{"activityId": 1}],
    "u-hoka": [{"activityId": 3}],
    "u-nb": [{"activityId": 6}],
    "u-bike": [{"activityId": 4}],
}


class FakeClient:
    def __init__(self, truncate=None, fail_gear=False, window_padded=True):
        self.calls = {"gear_acts": 0, "per_activity": 0}
        self.truncate, self.fail_gear = truncate, fail_gear
        self.requested: list[tuple[str, str]] = []

    def get_activities_by_date(self, s, e, activitytype=None, sortorder=None):
        self.requested.append((s, e))
        return list(ACTIVITIES)

    def get_user_profile(self):
        return {"id": 987654, "userData": {}}

    def get_gear(self, n):
        if self.fail_gear:
            raise RuntimeError("gear endpoint 500")
        return list(GEAR)

    def get_gear_activities(self, uuid, limit=1000):
        self.calls["gear_acts"] += 1
        if self.truncate == uuid:
            return [{"activityId": i} for i in range(limit)]
        return GEAR_ACTS.get(uuid, [])

    def get_activity_gear(self, activity_id):
        self.calls["per_activity"] += 1
        return [g for g in GEAR
                if any(a["activityId"] == int(activity_id) for a in GEAR_ACTS[g["uuid"]])]


WINDOW = (date(2026, 9, 1), date(2026, 9, 30))
EXPECTED = {"1": ["Nike Pegasus 41"], "2": [], "3": ["Hoka Speedgoat 5"], "6": ["Newbies"]}


def _gear_by_id(runs):
    return {r.activity_id: r.gear for r in runs}


def test_bulk_strategy_uses_gear_endpoints_not_per_activity():
    c = FakeClient()
    runs, _ = gs.fetch_runs(c, *WINDOW, gear_delay_s=0)
    assert _gear_by_id(runs) == EXPECTED
    assert c.calls["per_activity"] == 0
    assert c.calls["gear_acts"] == len(GEAR)


def test_per_activity_strategy_agrees_with_bulk():
    c = FakeClient()
    runs, _ = gs.fetch_runs(c, *WINDOW, gear_delay_s=0, bulk_gear=False)
    assert _gear_by_id(runs) == EXPECTED
    assert c.calls["per_activity"] == len(EXPECTED)


@pytest.mark.parametrize("client", [FakeClient(truncate="u-peg"), FakeClient(fail_gear=True)])
def test_bulk_falls_back_when_unreliable(client):
    runs, _ = gs.fetch_runs(client, *WINDOW, gear_delay_s=0)
    assert _gear_by_id(runs) == EXPECTED
    assert client.calls["per_activity"] == len(EXPECTED)


def test_query_is_padded_for_timezone_edges_then_filtered_on_utc():
    c = FakeClient()
    gs.fetch_runs(c, date(2026, 9, 2), date(2026, 9, 3), gear_delay_s=0)
    assert c.requested == [("2026-09-01", "2026-09-04")]
    runs, _ = gs.fetch_runs(c, date(2026, 9, 2), date(2026, 9, 3), gear_delay_s=0)
    assert sorted(r.activity_id for r in runs) == ["2", "3"]  # padding rows filtered out


def test_gear_name_aliases_map_nickname_to_make_model():
    assert gs.gear_name_aliases(FakeClient()) == {
        "Newbies": "New Balance 880",
        "Nike Pegasus 41": "Nike Air Zoom Pegasus 41",
    }
    assert gs.gear_name_aliases(FakeClient(fail_gear=True)) == {}


def test_gear_display_name_precedence():
    assert gs._gear_display_name({"displayName": "Nick", "customMakeModel": "Real"}) == "Nick"
    assert gs._gear_display_name({"customMakeModel": "Real"}) == "Real"
    assert gs._gear_display_name({"gearMakeName": "Canyon", "gearModelName": "Endurace"}) == "Canyon Endurace"
