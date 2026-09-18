"""Read runs + gear from a Strava bulk-export `activities.csv`.

Since 1 June 2026 the Strava API needs a paid subscription, but data export
stayed free. This reads the export instead, producing the same Activity objects
the API path produces.

Strava does not document the CSV's columns and they have drifted between
exports, so nothing here is positional: columns are found by name, units are
inferred from the data and reported, and anything unrecognised is surfaced
rather than guessed at.
"""

from __future__ import annotations

import csv
import statistics
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .models import Activity

# Header spellings seen across exports, normalised (lowercase, single-spaced).
COLUMN_ALIASES = {
    "id": ["activity id"],
    "date": ["activity date", "date"],
    "name": ["activity name", "name"],
    "type": ["activity type", "type"],
    "gear": ["activity gear", "gear"],
    "distance": ["distance"],
    "elapsed": ["elapsed time"],
    "moving": ["moving time"],
}

DATE_FORMATS = [
    "%b %d, %Y, %I:%M:%S %p",   # Sep 1, 2026, 6:04:12 AM
    "%b %d, %Y, %H:%M:%S",      # Sep 1, 2026, 06:04:12
    "%d %b %Y, %H:%M:%S",       # 1 Sep 2026, 06:04:12
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%d/%m/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
]


class StravaCsvError(RuntimeError):
    pass


# The only files worth pulling out of a bulk-export archive. The rest is
# media and settings, typically tens of megabytes of it.
EXPORT_MEMBERS = ("activities.csv", "shoes.csv", "bikes.csv")


def resolve_export(source: Path, extract_dir: Path) -> Path:
    """Accept the export .zip, a folder it was unzipped into, or activities.csv
    itself; return the path to activities.csv.

    A zip is unpacked into extract_dir (just the CSVs), so the next run can use
    the extracted copy without the flag.
    """
    source = source.expanduser()
    if not source.exists():
        raise StravaCsvError(f"No such file or folder: {source}")

    if source.is_dir():
        candidate = source / "activities.csv"
        if not candidate.exists():
            raise StravaCsvError(f"{source} has no activities.csv in it")
        return candidate

    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as archive:
            names = set(archive.namelist())
            if "activities.csv" not in names:
                raise StravaCsvError(
                    f"{source.name} has no activities.csv — is it a Strava bulk export?"
                )
            extract_dir.mkdir(parents=True, exist_ok=True)
            for member in EXPORT_MEMBERS:
                if member in names:
                    archive.extract(member, extract_dir)
        print(f"Strava: unpacked {source.name} → {extract_dir}")
        return extract_dir / "activities.csv"

    return source


def _norm(text: str) -> str:
    return " ".join(text.replace("﻿", "").strip().lower().split())


def _to_float(raw: str) -> float | None:
    """Parse a Strava numeric cell, tolerating thousands separators."""
    text = (raw or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_date(raw: str) -> datetime | None:
    """Activity Date in the export is UTC."""
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _locate(header: list[str]) -> dict[str, list[int]]:
    """Map each logical field to every column index carrying that name.

    Exports have repeated headers — 'Distance' appears twice, in different
    units — so this deliberately keeps all matches instead of the last one.
    """
    normalised = [_norm(h) for h in header]
    found: dict[str, list[int]] = {}
    for field, aliases in COLUMN_ALIASES.items():
        found[field] = [i for i, h in enumerate(normalised) if h in aliases]
    return found


def _pick_distance_column(
    rows: list[list[str]], candidates: list[int]
) -> tuple[int | None, float, str]:
    """Choose the distance column and its scale factor to metres.

    Returns (index, multiplier, explanation). Where two 'Distance' columns
    exist, one is kilometres and one is metres; the larger typical value is the
    metres one. With only one, the magnitude decides: a run measured in
    kilometres is single/double digits, in metres it's thousands.
    """
    stats: list[tuple[int, float]] = []
    for index in candidates:
        values = [v for v in (_to_float(r[index]) if index < len(r) else None for r in rows) if v]
        if values:
            stats.append((index, statistics.median(values)))

    if not stats:
        return None, 1.0, "no numeric distance values found"

    index, median = max(stats, key=lambda pair: pair[1])
    if median < 500:
        return index, 1000.0, f"column {index} looks like kilometres (median {median:.1f})"
    return index, 1.0, f"column {index} looks like metres (median {median:.0f})"


def _read_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise StravaCsvError(f"{path} is empty") from exc
        return header, [row for row in reader if any(cell.strip() for cell in row)]


def inspect(path: Path) -> str:
    """Human-readable summary of what we detected — for verifying a real export."""
    header, rows = _read_rows(path)
    found = _locate(header)
    lines = [f"{path.name}: {len(rows)} data rows, {len(header)} columns", "", "Detected columns:"]
    for field, indexes in found.items():
        if indexes:
            names = ", ".join(f"[{i}] {header[i]!r}" for i in indexes)
            lines.append(f"  {field:<9} -> {names}")
        else:
            lines.append(f"  {field:<9} -> NOT FOUND")

    index, multiplier, why = _pick_distance_column(rows, found["distance"])
    lines.append(f"\nDistance: {why}; multiplier to metres = {multiplier:g}")

    if found["date"] and rows:
        sample = rows[0][found["date"][0]]
        lines.append(f"Date sample: {sample!r} -> {parse_date(sample)}")

    if found["type"]:
        types: dict[str, int] = {}
        for row in rows:
            key = row[found["type"][0]] if found["type"][0] < len(row) else ""
            types[key] = types.get(key, 0) + 1
        top = sorted(types.items(), key=lambda kv: -kv[1])[:8]
        lines.append("Activity types: " + ", ".join(f"{k or '(blank)'}={v}" for k, v in top))

    if found["gear"]:
        gear_idx = found["gear"][0]
        gears: dict[str, int] = {}
        for row in rows:
            key = row[gear_idx].strip() if gear_idx < len(row) else ""
            gears[key] = gears.get(key, 0) + 1
        top = sorted(gears.items(), key=lambda kv: -kv[1])[:12]
        lines.append("Gear values: " + ", ".join(f"{k or '(blank)'}={v}" for k, v in top))

    unknown = [h for i, h in enumerate(header) if not any(i in v for v in found.values())]
    if unknown:
        lines.append(f"\nUnused columns ({len(unknown)}): {', '.join(unknown[:15])}")
    return "\n".join(lines)


def activity_date_range(path: Path) -> tuple[date, date] | None:
    """(oldest, newest) activity in the export — the span it can answer for."""
    header, rows = _read_rows(path)
    found = _locate(header)
    if not found["date"]:
        return None
    index = found["date"][0]
    dates = [
        parsed.date()
        for row in rows
        if index < len(row) and (parsed := parse_date(row[index])) is not None
    ]
    return (min(dates), max(dates)) if dates else None


def latest_activity_date(path: Path) -> date | None:
    """The newest activity in the export — nothing after it can be compared."""
    span = activity_date_range(path)
    return span[1] if span else None


def fetch_runs(
    path: Path, start: date, end: date, quiet: bool = False
) -> tuple[list[Activity], list[dict[str, Any]]]:
    header, rows = _read_rows(path)
    found = _locate(header)

    for required in ("date", "gear", "distance"):
        if not found[required]:
            raise StravaCsvError(
                f"{path.name} has no '{required}' column. Columns present: "
                + ", ".join(header)
                + "\nRun with --inspect-strava-csv to see what was detected."
            )

    dist_idx, dist_mult, dist_why = _pick_distance_column(rows, found["distance"])
    if dist_idx is None:
        raise StravaCsvError(f"{path.name}: could not read any distance values")
    if not quiet:
        print(f"Strava CSV: distance {dist_why}")

    def cell(row: list[str], field: str) -> str:
        indexes = found[field]
        if not indexes or indexes[0] >= len(row):
            return ""
        return row[indexes[0]].strip()

    activities: list[Activity] = []
    raw: list[dict[str, Any]] = []
    skipped_unparsed = 0

    for row in rows:
        sport = cell(row, "type")
        if "run" not in sport.lower():
            continue

        started = parse_date(cell(row, "date"))
        if started is None:
            skipped_unparsed += 1
            continue
        if not (start <= started.date() <= end):
            continue

        distance = _to_float(row[dist_idx]) if dist_idx < len(row) else None
        elapsed = None
        for index in found["elapsed"] + found["moving"]:
            if index < len(row):
                elapsed = _to_float(row[index])
                if elapsed:
                    break

        gear = cell(row, "gear")
        activity_id = cell(row, "id") or f"csv-{started:%Y%m%d%H%M%S}"
        raw.append(dict(zip(header, row)))
        activities.append(
            Activity(
                source="strava",
                activity_id=activity_id,
                name=cell(row, "name") or "(unnamed)",
                start_utc=started,
                distance_m=(distance or 0.0) * dist_mult,
                duration_s=elapsed or 0.0,
                sport=sport or "Run",
                gear=[gear] if gear else [],
            )
        )

    if skipped_unparsed and not quiet:
        print(
            f"Strava CSV: {skipped_unparsed} rows had an unreadable date and were skipped. "
            "Run --inspect-strava-csv to check the date format."
        )
    if not quiet:
        print(f"Strava CSV: {len(activities)} runs in window (from {len(rows)} rows)")
    return activities, raw
