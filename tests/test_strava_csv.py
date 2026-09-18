"""Reading a Strava bulk-export activities.csv."""

from datetime import date
from pathlib import Path

import pytest

from gearcheck import strava_csv

# Mirrors the real export: 'Distance' twice (km early, metres later), 'Elapsed
# Time' twice, plus columns we ignore.
HEADER = (
    "Activity ID,Activity Date,Activity Name,Activity Type,Activity Description,"
    "Elapsed Time,Distance,Max Heart Rate,Relative Effort,Commute,Activity Gear,"
    "Filename,Athlete Weight,Bike Weight,Elapsed Time,Moving Time,Distance,"
    "Max Speed,Elevation Gain"
)
ROWS = [
    '1001,"Sep 1, 2026, 6:04:12 AM",Morning Run,Run,,2100,10.02,168,45,false,Nike Pegasus 41,activities/1001.fit.gz,,,2100,2050,10020.0,3.9,55',
    '1002,"Sep 2, 2026, 5:58:00 AM",Easy Run,Run,,1800,8.00,155,30,false,,activities/1002.fit.gz,,,1800,1780,8000.0,3.7,40',
    '1003,"Sep 3, 2026, 6:15:30 AM",Trail Loop,Trail Run,,3600,"12,000.5",160,60,false,Hoka Speedgoat 5,activities/1003.fit.gz,,,3600,3500,12000.5,3.2,320',
    '1004,"Sep 4, 2026, 7:00:00 PM",Evening Ride,Ride,,3000,25.00,150,50,false,Canyon Endurace,activities/1004.fit.gz,,,3000,2900,25000.0,9.1,210',
    '1005,"Aug 1, 2026, 6:00:00 AM",Old Run,Run,,1500,5.00,150,20,false,Nike Pegasus 41,activities/1005.fit.gz,,,1500,1450,5000.0,3.5,20',
]


@pytest.fixture
def export(tmp_path: Path) -> Path:
    path = tmp_path / "activities.csv"
    path.write_text(HEADER + "\n" + "\n".join(ROWS) + "\n", encoding="utf-8")
    return path


def test_picks_metres_column_filters_runs_and_window(export: Path):
    runs, _ = strava_csv.fetch_runs(export, date(2026, 9, 1), date(2026, 9, 30), quiet=True)
    assert [r.activity_id for r in runs] == ["1001", "1002", "1003"]  # no Ride, no August
    assert runs[0].distance_m == 10020.0  # the metres column, not the km one
    assert runs[2].distance_m == 12000.5  # thousands separator handled
    assert runs[0].duration_s == 2100
    assert runs[1].gear == []
    assert runs[2].gear == ["Hoka Speedgoat 5"]
    assert runs[0].start_utc.isoformat() == "2026-09-01T06:04:12+00:00"


def test_single_distance_column_in_kilometres_is_scaled(tmp_path: Path):
    path = tmp_path / "km.csv"
    path.write_text(
        "Activity ID,Activity Date,Activity Name,Activity Type,Elapsed Time,Distance,Activity Gear\n"
        '2001,"Sep 1, 2026, 6:04:12 AM",Morning Run,Run,2100,10.02,Nike Pegasus 41\n'
        '2002,"Sep 2, 2026, 6:04:12 AM",Long Run,Run,5400,21.10,Nike Pegasus 41\n',
        encoding="utf-8",
    )
    runs, _ = strava_csv.fetch_runs(path, date(2026, 9, 1), date(2026, 9, 30), quiet=True)
    assert [r.distance_m for r in runs] == [10020.0, 21100.0]


def test_missing_gear_column_is_a_clear_error(tmp_path: Path):
    path = tmp_path / "nogear.csv"
    path.write_text('Activity ID,Activity Date,Distance\n1,"Sep 1, 2026, 6:04:12 AM",10.0\n')
    with pytest.raises(strava_csv.StravaCsvError, match="no 'gear' column"):
        strava_csv.fetch_runs(path, date(2026, 9, 1), date(2026, 9, 30), quiet=True)


def test_latest_activity_date(export: Path):
    assert strava_csv.latest_activity_date(export) == date(2026, 9, 4)


def test_inspect_reports_duplicates_and_units(export: Path):
    text = strava_csv.inspect(export)
    assert "[6] 'Distance', [16] 'Distance'" in text
    assert "looks like metres" in text
    assert "Ride=1" in text
