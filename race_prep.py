"""Summarise recent runs and recovery markers ahead of a race.

Pulls the last N weeks of runs from Garmin Connect (reusing the gear check's
cached login), works out pace, HR and first-half vs second-half HR drift for
each, and lists daily resting HR, overnight HRV and sleep. Prints Markdown
tables and saves them to data/race_prep.md (gitignored: it's personal data).

    python race_prep.py --ill-from 2026-09-23
"""

from __future__ import annotations

import argparse
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from gearcheck import env_store, garmin_source

OUT_PATH = Path(__file__).resolve().parent / "data" / "race_prep.md"
DRIFT_MIN_KM = 8.0


def fmt_pace(speed_mps: float | None) -> str:
    if not speed_mps:
        return "-"
    secs = round(1000 / speed_mps)
    return f"{secs // 60}:{secs % 60:02d}"


def fmt_num(value: float | None, digits: int = 0) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def halves_from_laps(laps: list[dict[str, Any]]) -> tuple[dict, dict] | None:
    """Duration-weighted pace and HR for each half of a run, by distance.

    Laps straddling the midpoint are counted wholly in the half holding most
    of their distance; with 1 km auto-laps that's close enough for drift.
    """
    laps = [l for l in laps if l.get("distance") and l.get("duration") and l.get("averageHR")]
    if len(laps) < 4:
        return None
    total = sum(l["distance"] for l in laps)
    first, second, covered = [], [], 0.0
    for lap in laps:
        middle = covered + lap["distance"] / 2
        (first if middle <= total / 2 else second).append(lap)
        covered += lap["distance"]
    if not first or not second:
        return None

    def summarise(group: list[dict[str, Any]]) -> dict[str, float]:
        dist = sum(l["distance"] for l in group)
        dur = sum(l["duration"] for l in group)
        hr = sum(l["averageHR"] * l["duration"] for l in group) / dur
        return {"speed": dist / dur, "hr": hr}

    return summarise(first), summarise(second)


def halves_from_details(details: dict[str, Any]) -> tuple[dict, dict] | None:
    """Same as halves_from_laps, from the per-sample time series."""
    index = {d.get("key"): d.get("metricsIndex") for d in details.get("metricDescriptors") or []}
    hr_i, dist_i = index.get("directHeartRate"), index.get("sumDistance")
    time_i = index.get("sumDuration", index.get("sumElapsedDuration"))
    if None in (hr_i, dist_i, time_i):
        return None
    rows = []
    for sample in details.get("activityDetailMetrics") or []:
        m = sample.get("metrics") or []
        try:
            hr, dist, t = m[hr_i], m[dist_i], m[time_i]
        except IndexError:
            continue
        if hr and dist is not None and t is not None:
            rows.append((t, dist, hr))
    if len(rows) < 20:
        return None
    rows.sort()
    mid = rows[-1][1] / 2
    first = [r for r in rows if r[1] <= mid]
    second = [r for r in rows if r[1] > mid]
    if len(first) < 2 or len(second) < 2:
        return None

    def summarise(group: list[tuple[float, float, float]]) -> dict[str, float]:
        dt = group[-1][0] - group[0][0]
        return {"speed": (group[-1][1] - group[0][1]) / dt if dt else 0.0,
                "hr": mean(r[2] for r in group)}

    return summarise(first), summarise(second)


def decoupling(first: dict, second: dict) -> float:
    """Pa:HR decoupling in %: how much speed-per-beat fell in the second half."""
    ef1 = first["speed"] / first["hr"]
    ef2 = second["speed"] / second["hr"]
    return (ef1 - ef2) / ef1 * 100


def fetch_halves(client, activity_id: str) -> tuple[dict, dict] | None:
    try:
        splits = client.get_activity_splits(activity_id) or {}
        halves = halves_from_laps(splits.get("lapDTOs") or [])
        if halves:
            return halves
        return halves_from_details(client.get_activity_details(activity_id) or {})
    except Exception as exc:  # noqa: BLE001 - one bad activity shouldn't stop the report
        print(f"  ! splits unavailable for {activity_id}: {exc}")
        return None


def daily_markers(client, day: date) -> dict[str, Any]:
    iso = day.isoformat()
    row: dict[str, Any] = {"date": iso}
    try:
        row["rhr"] = (client.get_user_summary(iso) or {}).get("restingHeartRate")
    except Exception:  # noqa: BLE001
        pass
    try:
        hrv = (client.get_hrv_data(iso) or {}).get("hrvSummary") or {}
        row["hrv"] = hrv.get("lastNightAvg")
        row["hrv_week"] = hrv.get("weeklyAvg")
        row["hrv_status"] = hrv.get("status")
        base = hrv.get("baseline") or {}
        if base.get("balancedLow") and base.get("balancedUpper"):
            row["hrv_base"] = f"{base['balancedLow']}-{base['balancedUpper']}"
    except Exception:  # noqa: BLE001
        pass
    try:
        sleep = (client.get_sleep_data(iso) or {}).get("dailySleepDTO") or {}
        if sleep.get("sleepTimeSeconds"):
            row["sleep_h"] = sleep["sleepTimeSeconds"] / 3600
        row["sleep_score"] = (((sleep.get("sleepScores") or {}).get("overall")) or {}).get("value")
    except Exception:  # noqa: BLE001
        pass
    return row


def build_report(runs: list[dict[str, Any]], days: list[dict[str, Any]], ill_from: date | None) -> str:
    out = ["## Runs", "",
           "| Date | Phase | Type | km | Pace /km | Avg HR | Max HR | Elev + m | m/beat | 1st half pace/HR | 2nd half pace/HR | Decoupling |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        h = r.get("halves")
        out.append("| " + " | ".join([
            r["date"], r["phase"], r["type"], f"{r['km']:.1f}", fmt_pace(r["speed"]),
            fmt_num(r["hr"]), fmt_num(r["max_hr"]), fmt_num(r["elev"]),
            fmt_num(r["speed"] * 60 / r["hr"], 2) if r["hr"] and r["speed"] else "-",
            f"{fmt_pace(h[0]['speed'])} @ {h[0]['hr']:.0f}" if h else "-",
            f"{fmt_pace(h[1]['speed'])} @ {h[1]['hr']:.0f}" if h else "-",
            f"{decoupling(*h):+.1f}%" if h else "-",
        ]) + " |")

    if ill_from:
        out += ["", "## Pre vs post illness (outdoor runs with HR)", "",
                "| Phase | Runs | km/week | Avg pace | Avg HR | m/beat | Avg decoupling (>8 km) |",
                "|---|---|---|---|---|---|---|"]
        for phase in ("pre", "post"):
            group = [r for r in runs if r["phase"] == phase and r["hr"] and r["speed"]]
            if not group:
                continue
            span = [date.fromisoformat(r["date"]) for r in group]
            weeks = max(((max(span) - min(span)).days + 1) / 7, 1)
            secs = sum(r["duration"] for r in group)
            speed = sum(r["km"] for r in group) * 1000 / secs
            hr = sum(r["hr"] * r["duration"] for r in group) / secs
            drifts = [decoupling(*r["halves"]) for r in group if r.get("halves") and r["km"] >= DRIFT_MIN_KM]
            out.append(f"| {phase} | {len(group)} | {sum(r['km'] for r in group) / weeks:.1f} | "
                       f"{fmt_pace(speed)} | {hr:.0f} | {speed * 60 / hr:.2f} | "
                       f"{(f'{mean(drifts):+.1f}%' if drifts else '-')} |")

    out += ["", "## Daily recovery markers", "",
            "| Date | Resting HR | HRV last night | HRV 7d avg | HRV baseline | HRV status | Sleep h | Sleep score |",
            "|---|---|---|---|---|---|---|---|"]
    for d in days:
        out.append(f"| {d['date']} | {fmt_num(d.get('rhr'))} | {fmt_num(d.get('hrv'))} | "
                   f"{fmt_num(d.get('hrv_week'))} | {d.get('hrv_base') or '-'} | "
                   f"{d.get('hrv_status') or '-'} | {fmt_num(d.get('sleep_h'), 1)} | "
                   f"{fmt_num(d.get('sleep_score'))} |")
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weeks", type=int, default=8)
    parser.add_argument("--ill-from", type=date.fromisoformat,
                        help="first day of illness (YYYY-MM-DD); splits runs into pre/post")
    args = parser.parse_args()

    env_store.load()
    client = garmin_source.connect()
    end = date.today()
    start = end - timedelta(weeks=args.weeks)

    raw = client.get_activities_by_date(start.isoformat(), end.isoformat(), activitytype="running")
    raw.sort(key=lambda a: a.get("startTimeLocal") or "")
    print(f"Garmin: {len(raw)} runs since {start}")

    runs = []
    for item in raw:
        started = datetime.fromisoformat((item.get("startTimeLocal") or "").replace(" ", "T")).date()
        km = (item.get("distance") or 0) / 1000
        run = {
            "date": started.isoformat(),
            "phase": "-" if not args.ill_from else ("pre" if started < args.ill_from else "post"),
            "type": (item.get("activityType") or {}).get("typeKey", "running"),
            "km": km,
            "duration": item.get("duration") or 0,
            "speed": item.get("averageSpeed"),
            "hr": item.get("averageHR"),
            "max_hr": item.get("maxHR"),
            "elev": item.get("elevationGain"),
        }
        if km >= DRIFT_MIN_KM and run["hr"]:
            run["halves"] = fetch_halves(client, str(item.get("activityId")))
            time.sleep(0.3)
        runs.append(run)

    days = []
    for offset in range((end - start).days + 1):
        days.append(daily_markers(client, start + timedelta(days=offset)))
        time.sleep(0.2)
        print(f"\r  recovery data {offset + 1}/{(end - start).days + 1}", end="", flush=True)
    print()

    report = build_report(runs, days, args.ill_from)
    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(report, encoding="utf-8")
    print(report)
    print(f"Saved to {OUT_PATH}")


if __name__ == "__main__":
    main()
