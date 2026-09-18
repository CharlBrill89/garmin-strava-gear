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
import webbrowser
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from gearcheck import env_store, garmin_source, html_report, matching, report, strava_csv
from gearcheck.models import Activity
from gearcheck.strava_source import StravaClient, StravaError

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
ALIAS_PATH = ROOT / "gear_aliases.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    window = parser.add_mutually_exclusive_group()
    window.add_argument(
        "--days",
        type=int,
        help="look back N days (default: the whole export with --strava-csv, else 90)",
    )
    window.add_argument(
        "--all",
        action="store_true",
        help="the full span of the Strava export (or everything since 2000 via the API)",
    )
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
    parser.add_argument(
        "--strava-csv",
        help=(
            "read Strava from a bulk-export activities.csv instead of the API "
            "(the API needs a paid Strava subscription as of June 2026)"
        ),
    )
    parser.add_argument(
        "--inspect-strava-csv",
        help="print the columns detected in an export CSV and exit; takes a path",
    )
    parser.add_argument(
        "--gear-names",
        action="store_true",
        help="list every distinct gear name on each platform, with how often they "
        "were paired together, then exit — for building gear_aliases.json",
    )
    parser.add_argument(
        "--garmin-check",
        action="store_true",
        help="log in to Garmin, print recent runs and their gear, and exit "
        "(verifies Garmin auth and gear parsing without needing Strava)",
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
    parser.add_argument(
        "--open",
        action="store_true",
        help="open the HTML report in your browser when done",
    )
    return parser.parse_args()


def resolve_window(
    args: argparse.Namespace, export_span: tuple[date, date] | None
) -> tuple[date, date]:
    """Work out [start, end].

    With an export and no explicit window, the export's own span is the only
    sensible default — it is the bounded dataset, so "everything" means it.
    Without an export, the API is open-ended and 90 days is the default.
    """
    today = datetime.now(timezone.utc).date()
    explicit = args.days is not None or args.start or args.end
    whole = args.all or (export_span is not None and not explicit)

    if whole and not args.start and not args.end:
        if export_span:
            start, end = export_span
            print(f"Window: whole Strava export, {start} to {end}")
        else:
            start, end = date(2000, 1, 1), today
            print(f"Window: everything since {start}")
        return start, end

    end = date.fromisoformat(args.end) if args.end else today
    if args.start:
        start = date.fromisoformat(args.start)
    elif whole:
        start = export_span[0] if export_span else date(2000, 1, 1)
    else:
        start = end - timedelta(days=args.days if args.days is not None else 90)
    if start > end:
        sys.exit("--start must be on or before --end")
    print(f"Window: {start} to {end}")
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
    alias_path = DATA_DIR / "garmin_gear_aliases.json"
    auto_aliases = (
        json.loads(alias_path.read_text(encoding="utf-8")) if alias_path.exists() else {}
    )
    return (
        _deserialise(json.loads(garmin_path.read_text(encoding="utf-8"))),
        _deserialise(json.loads(strava_path.read_text(encoding="utf-8"))),
        auto_aliases,
    )


def fetch_all(
    start: date, end: date, strava_csv_path: str | None = None
) -> tuple[list[Activity], list[Activity]]:
    env_store.load()

    # Resolve the Strava side first — no point prompting for a Garmin login if
    # the Strava source is misconfigured.
    if strava_csv_path:
        csv_path = Path(strava_csv_path)
        if not csv_path.exists():
            sys.exit(f"No such file: {csv_path}")
        strava_fetch = lambda: strava_csv.fetch_runs(csv_path, start, end)  # noqa: E731
    else:
        try:
            client_strava = StravaClient()
        except StravaError as exc:
            sys.exit(
                f"{exc}\n\nStrava's API has required a paid subscription since June 2026. "
                "Without one, use the free data export instead:\n"
                "  compare_gear.py --strava-csv path/to/activities.csv"
            )
        strava_fetch = lambda: client_strava.fetch_runs(start, end)  # noqa: E731

    client = garmin_source.connect()
    garmin_runs, _ = garmin_source.fetch_runs(client, start, end)
    auto_aliases = garmin_source.gear_name_aliases(client)
    strava_runs, _ = strava_fetch()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "garmin_runs.json").write_text(
        json.dumps(_serialise(garmin_runs), indent=2), encoding="utf-8"
    )
    (DATA_DIR / "strava_runs.json").write_text(
        json.dumps(_serialise(strava_runs), indent=2), encoding="utf-8"
    )
    (DATA_DIR / "garmin_gear_aliases.json").write_text(
        json.dumps(auto_aliases, indent=2), encoding="utf-8"
    )
    return garmin_runs, strava_runs, auto_aliases


def print_gear_names(
    garmin_runs: list[Activity], strava_runs: list[Activity], auto_aliases: dict[str, str]
) -> None:
    """Show what each platform calls things, and which names travel together."""
    from collections import Counter

    garmin_names = Counter(n for a in garmin_runs for n in a.gear)
    strava_names = Counter(n for a in strava_runs for n in a.gear)

    print("\nGarmin gear names (activities using each):")
    for name, count in garmin_names.most_common():
        hint = f"   [Garmin's make/model: {auto_aliases[name]}]" if name in auto_aliases else ""
        print(f"  {count:>4}  {name}{hint}")

    print("\nStrava gear names (activities using each):")
    for name, count in strava_names.most_common():
        print(f"  {count:>4}  {name}")

    # Pair raw names on time-matched activities, no aliasing, to show which
    # spellings co-occur — the strongest evidence for what maps to what.
    matched, _, _ = matching.match_activities(garmin_runs, strava_runs)
    together: Counter[tuple[str, str]] = Counter()
    for g, s, _ in matched:
        if g.gear and s.gear:
            together[(g.gear_label, s.gear_label)] += 1

    print("\nHow they pair up on the same runs (Garmin  ->  Strava):")
    for (g, s), count in together.most_common():
        print(f"  {count:>4}  {g:<30} ->  {s}")

    print(
        "\nBuild gear_aliases.json as {\"canonical name\": [\"spelling\", ...]} — see "
        "gear_aliases.example.json. Every spelling from either platform that means the "
        "same shoe goes in one list."
    )


def main() -> None:
    args = parse_args()

    if args.inspect_strava_csv:
        path = Path(args.inspect_strava_csv)
        if not path.exists():
            sys.exit(f"No such file: {path}")
        print(strava_csv.inspect(path))
        return

    export_span = None
    if args.strava_csv and not args.use_cache:
        csv_path = Path(args.strava_csv)
        if not csv_path.exists():
            sys.exit(f"No such file: {csv_path}")
        export_span = strava_csv.activity_date_range(csv_path)

    start, end = resolve_window(args, export_span)

    # A Strava export is a snapshot. Runs after its newest activity can't be
    # compared, so don't fetch them from Garmin only to flag them as unmatched.
    if export_span:
        latest = export_span[1]
        if end > latest:
            print(f"Strava export ends {latest}; window end moved from {end} to match.")
            end = latest
        if start > latest:
            sys.exit(f"Window starts {start}, but the Strava export ends {latest}.")

    if args.garmin_check:
        client = garmin_source.connect()
        runs, _ = garmin_source.fetch_runs(client, start, end)
        print(f"\n{len(runs)} running activities:\n")
        print(f"{'date':<11} {'km':>6}  {'activity':<28} gear")
        print("-" * 78)
        for run in sorted(runs, key=lambda a: a.start_utc, reverse=True):
            name = run.name[:27]
            print(
                f"{run.start_utc:%Y-%m-%d} {run.distance_km:6.2f}  "
                f"{name:<28} {run.gear_label or '(none assigned)'}"
            )
        without = sum(1 for r in runs if not r.gear)
        print(f"\n{len(runs) - without} with gear, {without} without.")
        return

    if args.use_cache:
        garmin_runs, strava_runs, auto_aliases = load_cached()
        print(f"Cache: {len(garmin_runs)} Garmin runs, {len(strava_runs)} Strava runs")
    else:
        garmin_runs, strava_runs, auto_aliases = fetch_all(start, end, args.strava_csv)

    if args.gear_names:
        print_gear_names(garmin_runs, strava_runs, auto_aliases)
        return

    # Garmin's own nickname -> make/model mapping, then the user's file on top.
    aliases = matching.aliases_from_pairs(auto_aliases)
    for nickname, real in auto_aliases.items():
        print(f"Garmin gear alias: {nickname!r} -> {real!r}")
    file_aliases = matching.load_aliases(ALIAS_PATH)
    if file_aliases:
        print(f"Loaded {len(file_aliases)} gear name aliases from {ALIAS_PATH.name}")
    aliases.update(file_aliases)

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
    html_path = out_dir / f"gear_comparison_{stamp}.html"

    report.write_csv(pairs, csv_path)
    report.write_markdown(pairs, md_path, start, end, problems_only=args.problems_only)
    html_report.write_html(pairs, html_path, start, end)
    report.print_summary(pairs)

    print(f"\nWrote {csv_path}")
    print(f"Wrote {md_path}")
    print(f"Wrote {html_path}")
    if args.open:
        webbrowser.open(html_path.resolve().as_uri())


if __name__ == "__main__":
    main()
