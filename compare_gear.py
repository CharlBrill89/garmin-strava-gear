#!/usr/bin/env python
"""Compare the shoes assigned to your runs on Garmin Connect vs Strava.

  python compare_gear.py --days 90
  python compare_gear.py --start 2026-01-01 --end 2026-06-30
  python compare_gear.py --days 90 --use-cache      # re-render from the last pull
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from gearcheck import env_store, garmin_source, matching, report
from gearcheck.models import Activity
from gearcheck.strava_source import StravaClient, StravaError

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
ALIAS_PATH = ROOT / "gear_aliases.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--days", type=int, default=90, help="look back N days (default 90)")
    parser.add_argument("--start", help="window start, YYYY-MM-DD (overrides --days)")
    parser.add_argument("--end", help="window end, YYYY-MM-DD (default today)")
    parser.add_argument(
        "--tolerance-seconds",
        type=int,
        default=180,
        help="start-time tolerance when matching by time (default 180)",
    )
    parser.add_argument(
        "--fuzzy-threshold",
        type=float,
        default=0.85,
        help="name similarity above which two gear names count as the same shoe (default 0.85)",
    )
    parser.add_argument("--out-dir", default=str(REPORT_DIR), help="where to write the report")
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help="re-use the previous API pull in data/ instead of calling the APIs",
    )
    parser.add_argument(
        "--problems-only",
        action="store_true",
        help="omit the full activity table from the markdown report",
    )
    return parser.parse_args()


def resolve_window(args: argparse.Namespace) -> tuple[date, date]:
    end = date.fromisoformat(args.end) if args.end else datetime.now(timezone.utc).date()
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=args.days)
    if start > end:
        sys.exit("--start must be on or before --end")
    return start, end


def _serialise(activities: list[Activity]) -> list[dict]:
    rows = []
    for activity in activities:
        row = asdict(activity)
        row["start_utc"] = activity.start_utc.isoformat()
        rows.append(row)
    return rows


def _deserialise(rows: list[dict]) -> list[Activity]:
    return [
        Activity(**{**row, "start_utc": datetime.fromisoformat(row["start_utc"])})
        for row in rows
    ]


def load_cached() -> tuple[list[Activity], list[Activity]]:
    garmin_path = DATA_DIR / "garmin_runs.json"
    strava_path = DATA_DIR / "strava_runs.json"
    if not (garmin_path.exists() and strava_path.exists()):
        sys.exit("No cache found in data/ — run once without --use-cache first.")
    return (
        _deserialise(json.loads(garmin_path.read_text(encoding="utf-8"))),
        _deserialise(json.loads(strava_path.read_text(encoding="utf-8"))),
    )


def fetch_all(start: date, end: date) -> tuple[list[Activity], list[Activity]]:
    env_store.load()

    try:
        strava = StravaClient()
    except StravaError as exc:
        sys.exit(str(exc))

    client = garmin_source.connect()
    garmin_runs, _ = garmin_source.fetch_runs(client, start, end)
    strava_runs, _ = strava.fetch_runs(start, end)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "garmin_runs.json").write_text(
        json.dumps(_serialise(garmin_runs), indent=2), encoding="utf-8"
    )
    (DATA_DIR / "strava_runs.json").write_text(
        json.dumps(_serialise(strava_runs), indent=2), encoding="utf-8"
    )
    return garmin_runs, strava_runs


def main() -> None:
    args = parse_args()
    start, end = resolve_window(args)
    print(f"Window: {start} to {end}")

    if args.use_cache:
        garmin_runs, strava_runs = load_cached()
        print(f"Cache: {len(garmin_runs)} Garmin runs, {len(strava_runs)} Strava runs")
    else:
        garmin_runs, strava_runs = fetch_all(start, end)

    aliases = matching.load_aliases(ALIAS_PATH)
    if aliases:
        print(f"Loaded {len(aliases)} gear name aliases from {ALIAS_PATH.name}")

    pairs = matching.build_pairs(
        garmin_runs,
        strava_runs,
        aliases,
        tolerance_s=args.tolerance_seconds,
        fuzzy_threshold=args.fuzzy_threshold,
    )

    out_dir = Path(args.out_dir)
    stamp = f"{start}_to_{end}"
    csv_path = out_dir / f"gear_comparison_{stamp}.csv"
    md_path = out_dir / f"gear_comparison_{stamp}.md"

    report.write_csv(pairs, csv_path)
    report.write_markdown(pairs, md_path, start, end, problems_only=args.problems_only)
    report.print_summary(pairs)

    print(f"\nWrote {csv_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
