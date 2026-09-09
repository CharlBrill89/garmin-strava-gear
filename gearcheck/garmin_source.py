"""Pull running activities + their assigned gear from Garmin Connect.

Auth: prompts for username/password interactively the first time, then reuses the
cached session tokens (default ~/.garminconnect) on every later run. Credentials
are held in memory only for the duration of the login call — never written to disk,
never echoed.
"""

from __future__ import annotations

import getpass
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
)

from .models import Activity

DEFAULT_TOKENSTORE = "~/.garminconnect"

# Garmin's parent "running" type covers these leaf typeKeys.
RUNNING_TYPE_KEYS = {
    "running",
    "trail_running",
    "treadmill_running",
    "indoor_running",
    "track_running",
    "virtual_run",
    "obstacle_run",
    "ultra_run",
}


def _tokenstore_path() -> str:
    return os.getenv("GARMINTOKENS") or DEFAULT_TOKENSTORE


def connect(quiet: bool = False) -> Garmin:
    """Return a logged-in client, reusing cached tokens when possible."""
    tokenstore = _tokenstore_path()
    expanded = Path(tokenstore).expanduser()

    if expanded.exists():
        try:
            client = Garmin()
            client.login(tokenstore)
            if not quiet:
                print(f"Garmin: reused cached session for {client.display_name}")
            return client
        except Exception as exc:  # noqa: BLE001 - any failure means fall back to login
            print(f"Garmin: cached tokens unusable ({type(exc).__name__}), re-authenticating.")

    print("\nGarmin Connect login (credentials are not stored anywhere).")
    email = input("  Garmin email: ").strip()
    password = getpass.getpass("  Garmin password: ")
    if not email or not password:
        raise SystemExit("Garmin email and password are both required.")

    def prompt_mfa() -> str:
        return input("  Garmin MFA code: ").strip()

    client = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    try:
        client.login(tokenstore)
    except GarminConnectAuthenticationError as exc:
        raise SystemExit(f"Garmin authentication failed: {exc}") from exc
    finally:
        del password

    print(f"Garmin: logged in as {client.display_name}; tokens cached at {tokenstore}")
    return client


def _gear_display_name(gear: dict[str, Any]) -> str:
    """Reproduce the label Garmin Connect shows for a gear item."""
    for key in ("displayName", "customMakeModel"):
        value = (gear.get(key) or "").strip()
        if value:
            return value
    make = (gear.get("gearMakeName") or "").strip()
    model = (gear.get("gearModelName") or "").strip()
    combined = f"{make} {model}".strip()
    return combined or f"<unnamed gear {gear.get('uuid', '?')}>"


def _parse_start(raw: dict[str, Any]) -> datetime | None:
    """Garmin returns startTimeGMT as 'YYYY-MM-DD HH:MM:SS' in UTC."""
    value = raw.get("startTimeGMT")
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def fetch_runs(
    client: Garmin,
    start: date,
    end: date,
    gear_delay_s: float = 0.25,
) -> tuple[list[Activity], list[dict[str, Any]]]:
    """Fetch running activities in [start, end] plus the gear assigned to each.

    Returns (normalised activities, raw activity payloads).
    """
    raw = client.get_activities_by_date(
        start.isoformat(), end.isoformat(), activitytype="running"
    )
    print(f"Garmin: {len(raw)} running activities between {start} and {end}")

    activities: list[Activity] = []
    for index, item in enumerate(raw, start=1):
        type_key = (item.get("activityType") or {}).get("typeKey", "")
        if type_key and type_key not in RUNNING_TYPE_KEYS:
            continue

        started = _parse_start(item)
        if started is None:
            print(f"  ! skipping activity {item.get('activityId')}: unparseable start time")
            continue

        activity_id = str(item.get("activityId"))
        try:
            gear_items = client.get_activity_gear(activity_id) or []
        except Exception as exc:  # noqa: BLE001 - one bad activity shouldn't kill the run
            print(f"  ! gear lookup failed for {activity_id}: {exc}")
            gear_items = []
        if isinstance(gear_items, dict):
            gear_items = [gear_items]

        item["_gear"] = gear_items
        activities.append(
            Activity(
                source="garmin",
                activity_id=activity_id,
                name=item.get("activityName") or "(unnamed)",
                start_utc=started,
                distance_m=float(item.get("distance") or 0.0),
                duration_s=float(item.get("duration") or 0.0),
                sport=type_key or "running",
                gear=[_gear_display_name(g) for g in gear_items],
            )
        )

        print(f"\r  gear {index}/{len(raw)}", end="", flush=True)
        if gear_delay_s:
            time.sleep(gear_delay_s)  # be polite to Garmin's API

    print()
    return activities, raw
