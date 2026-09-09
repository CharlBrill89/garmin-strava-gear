"""Render the comparison as CSV + a markdown report."""

from __future__ import annotations

import csv
from collections import Counter
from datetime import date
from pathlib import Path

from .matching import (
    DIFFERENT,
    MATCH,
    MISSING_BOTH,
    MISSING_GARMIN,
    MISSING_STRAVA,
    NAME_VARIANT,
    NO_GARMIN_ACTIVITY,
    NO_STRAVA_ACTIVITY,
    PROBLEM_VERDICTS,
    Pair,
)

VERDICT_LABELS = {
    MATCH: "Match",
    NAME_VARIANT: "Match (name differs)",
    DIFFERENT: "Different shoe",
    MISSING_STRAVA: "Missing on Strava",
    MISSING_GARMIN: "Missing on Garmin",
    MISSING_BOTH: "Missing on both",
    NO_STRAVA_ACTIVITY: "No Strava activity",
    NO_GARMIN_ACTIVITY: "No Garmin activity",
}

CSV_COLUMNS = [
    "date",
    "start_utc",
    "name",
    "distance_km",
    "duration_min",
    "garmin_gear",
    "strava_gear",
    "verdict",
    "detail",
    "match_method",
    "garmin_activity_id",
    "strava_activity_id",
    "garmin_url",
    "strava_url",
]

GARMIN_ACTIVITY_URL = "https://connect.garmin.com/modern/activity/{}"
STRAVA_ACTIVITY_URL = "https://www.strava.com/activities/{}"


def _row(pair: Pair) -> dict[str, object]:
    anchor = pair.anchor
    return {
        "date": anchor.start_utc.date().isoformat(),
        "start_utc": anchor.start_utc.strftime("%Y-%m-%d %H:%M:%S"),
        "name": (pair.garmin or pair.strava).name,  # type: ignore[union-attr]
        "distance_km": round(anchor.distance_km, 2),
        "duration_min": round(anchor.duration_s / 60, 1),
        "garmin_gear": pair.garmin.gear_label if pair.garmin else "",
        "strava_gear": pair.strava.gear_label if pair.strava else "",
        "verdict": VERDICT_LABELS[pair.verdict],
        "detail": pair.detail,
        "match_method": pair.match_method,
        "garmin_activity_id": pair.garmin.activity_id if pair.garmin else "",
        "strava_activity_id": pair.strava.activity_id if pair.strava else "",
        "garmin_url": GARMIN_ACTIVITY_URL.format(pair.garmin.activity_id) if pair.garmin else "",
        "strava_url": STRAVA_ACTIVITY_URL.format(pair.strava.activity_id) if pair.strava else "",
    }


def write_csv(pairs: list[Pair], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for pair in pairs:
            writer.writerow(_row(pair))


def summarise(pairs: list[Pair]) -> dict[str, object]:
    verdicts = Counter(p.verdict for p in pairs)
    problems = [p for p in pairs if p.verdict in PROBLEM_VERDICTS]

    # Which shoe is most often the one that goes missing or gets misassigned?
    culprits: Counter[str] = Counter()
    for pair in problems:
        if pair.verdict == MISSING_STRAVA and pair.garmin:
            culprits[f"{pair.garmin.gear_label} (not carried to Strava)"] += 1
        elif pair.verdict == MISSING_GARMIN and pair.strava:
            culprits[f"{pair.strava.gear_label} (not set on Garmin)"] += 1
        elif pair.verdict == DIFFERENT and pair.garmin and pair.strava:
            culprits[f"{pair.garmin.gear_label} -> {pair.strava.gear_label}"] += 1
        elif pair.verdict == MISSING_BOTH:
            culprits["(no shoe assigned anywhere)"] += 1

    return {
        "total": len(pairs),
        "verdicts": verdicts,
        "problem_count": len(problems),
        "culprits": culprits,
    }


def priority_needed(verdict: str) -> bool:
    """True when a batch represents real reconciliation work."""
    return verdict in {DIFFERENT, MISSING_STRAVA, MISSING_GARMIN}


def _action_plan(pairs: list[Pair]) -> list[str]:
    """Group the problems into batches of identical work, newest first.

    Every row in a batch needs the same decision, so deciding once and then
    working the list beats reasoning about each activity separately.
    """
    problems = [p for p in pairs if p.verdict in PROBLEM_VERDICTS]
    if not problems:
        return []

    batches: dict[tuple[str, str, str], list[Pair]] = {}
    for pair in problems:
        key = (
            pair.verdict,
            pair.garmin.gear_label if pair.garmin else "",
            pair.strava.gear_label if pair.strava else "",
        )
        batches.setdefault(key, []).append(pair)

    decisions = sum(
        1 for (verdict, _, _) in batches if priority_needed(verdict)
    )
    actionable = sum(
        len(g) for (verdict, _, _), g in batches.items() if priority_needed(verdict)
    )
    lines = [
        "",
        "## Action plan",
        "",
        f"**{actionable} activities to fix, across {decisions} decisions.** Each batch "
        "below needs one call on which platform is right, then it's just clicking "
        "through the links. Batches needing no decision are last.",
        "",
    ]

    # Actionable work first; "no gear anywhere" is bulk but needs no decision,
    # so it goes last however large it is.
    priority = {
        DIFFERENT: 0,
        MISSING_STRAVA: 1,
        MISSING_GARMIN: 1,
        NO_STRAVA_ACTIVITY: 2,
        NO_GARMIN_ACTIVITY: 2,
        MISSING_BOTH: 3,
    }
    order = sorted(
        batches.items(),
        key=lambda kv: (priority.get(kv[0][0], 9), -len(kv[1]), kv[0]),
    )
    for index, ((verdict, garmin_gear, strava_gear), group) in enumerate(order, start=1):
        group.sort(key=lambda p: p.anchor.start_utc, reverse=True)
        first = group[-1].anchor.start_utc.date()
        last = group[0].anchor.start_utc.date()
        span = f"{first}" if first == last else f"{first} → {last}"

        lines.append(f"### {index}. {VERDICT_LABELS[verdict]} — {len(group)} activit"
                     f"{'y' if len(group) == 1 else 'ies'} ({span})")
        lines.append("")

        if verdict == DIFFERENT:
            lines += [
                f"Garmin says **{garmin_gear}**, Strava says **{strava_gear}**.",
                "",
                f"- If Garmin is right → set Strava to *{garmin_gear}* "
                "(Strava supports bulk editing: My Activities → select → Edit).",
                f"- If Strava is right → set Garmin to *{strava_gear}* "
                "(Garmin is one activity at a time).",
            ]
        elif verdict == MISSING_STRAVA:
            lines.append(f"Garmin has **{garmin_gear}**; Strava has nothing. "
                         "Bulk-edit these on Strava in one pass.")
        elif verdict == MISSING_GARMIN:
            lines.append(f"Strava has **{strava_gear}**; Garmin has nothing. "
                         "Set gear on each Garmin activity.")
        elif verdict == MISSING_BOTH:
            lines.append("No gear on either side — nothing to reconcile unless you "
                         "want to backfill what you wore.")
        else:
            lines.append("These didn't pair up, so check whether they're the same run "
                         "recorded twice or genuinely one-sided.")

        lines += ["", "| Date | Activity | km | Garmin | Strava |", "|---|---|---:|---|---|"]
        for pair in group:
            row = _row(pair)
            g = (
                f"[open]({GARMIN_ACTIVITY_URL.format(pair.garmin.activity_id)})"
                if pair.garmin
                else "—"
            )
            s = (
                f"[open]({STRAVA_ACTIVITY_URL.format(pair.strava.activity_id)})"
                if pair.strava
                else "—"
            )
            name = str(row["name"]).replace("|", "\\|")
            lines.append(f"| {row['date']} | {name} | {row['distance_km']} | {g} | {s} |")
        lines.append("")

    return lines


def _table(rows: list[dict[str, object]]) -> list[str]:
    header = "| Date | Activity | km | Garmin gear | Strava gear | Verdict | Detail |"
    lines = [header, "|---|---|---:|---|---|---|---|"]
    for row in rows:
        name = str(row["name"]).replace("|", "\\|")
        detail = str(row["detail"]).replace("|", "\\|")
        lines.append(
            f"| {row['date']} | {name} | {row['distance_km']} | "
            f"{row['garmin_gear'] or '—'} | {row['strava_gear'] or '—'} | "
            f"{row['verdict']} | {detail or '—'} |"
        )
    return lines


def write_markdown(
    pairs: list[Pair], path: Path, start: date, end: date, problems_only: bool = False
) -> str:
    stats = summarise(pairs)
    verdicts: Counter[str] = stats["verdicts"]  # type: ignore[assignment]
    culprits: Counter[str] = stats["culprits"]  # type: ignore[assignment]
    total = stats["total"]
    problem_count = stats["problem_count"]

    lines = [
        "# Garmin vs Strava gear comparison",
        "",
        f"Window: **{start} to {end}**  ",
        f"Activities compared: **{total}**  ",
        f"Mismatched or missing: **{problem_count}**"
        + (f" ({problem_count / total:.0%})" if total else ""),
        "",
        "## Breakdown",
        "",
        "| Verdict | Count |",
        "|---|---:|",
    ]
    for verdict, label in VERDICT_LABELS.items():
        if verdicts.get(verdict):
            lines.append(f"| {label} | {verdicts[verdict]} |")

    lines += ["", "## Most common offenders", ""]
    if culprits:
        lines += ["| Shoe / mismatch | Activities |", "|---|---:|"]
        lines += [f"| {name} | {count} |" for name, count in culprits.most_common()]
    else:
        lines.append("Nothing mismatched — every activity agrees on gear.")

    lines += _action_plan(pairs)

    problem_pairs = [p for p in pairs if p.verdict in PROBLEM_VERDICTS]
    if problem_pairs:
        lines += ["", "## Activities needing attention", ""]
        lines += _table([_row(p) for p in problem_pairs])

    variants = [p for p in pairs if p.verdict == NAME_VARIANT]
    if variants:
        lines += [
            "",
            "## Name variants — counted as matching, worth a glance",
            "",
            "The same shoe is spelled differently on each platform. If any of these are "
            "actually two different shoes, add them to `gear_aliases.json` or lower "
            "`--fuzzy-threshold`.",
            "",
        ]
        lines += _table([_row(p) for p in variants])

    if not problems_only:
        lines += ["", "## All activities", ""]
        lines += _table([_row(p) for p in pairs])

    text = "\n".join(lines) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def print_summary(pairs: list[Pair]) -> None:
    stats = summarise(pairs)
    verdicts: Counter[str] = stats["verdicts"]  # type: ignore[assignment]
    culprits: Counter[str] = stats["culprits"]  # type: ignore[assignment]

    print(f"\nCompared {stats['total']} activities; {stats['problem_count']} need attention.")
    for verdict, label in VERDICT_LABELS.items():
        if verdicts.get(verdict):
            print(f"  {label:<22} {verdicts[verdict]}")
    if culprits:
        print("\nMost common offenders:")
        for name, count in culprits.most_common(10):
            print(f"  {count:>3}x  {name}")
