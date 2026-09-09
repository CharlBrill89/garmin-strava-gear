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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
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


def _in_mintty() -> bool:
    """Git Bash / MSYS2 run Windows Python without a real console attached."""
    return os.name == "nt" and bool(os.environ.get("MSYSTEM"))


def _warn_about_terminal() -> None:
    if _in_mintty():
        print(
            "  Note: this looks like Git Bash, where hidden password prompts often\n"
            "  swallow pasted text. If the paste doesn't register, either run this\n"
            "  from PowerShell, or prefix the command with 'winpty'.\n"
        )


def _read_password() -> str:
    """Prompt without echo, degrading gracefully where that isn't possible."""
    try:
        return getpass.getpass("  Garmin password: ")
    except Exception as exc:  # noqa: BLE001 - no console, redirected stdin, etc.
        print(f"  (hidden input unavailable: {exc})")
        print("  WARNING: the password will be visible as you type it.")
        return input("  Garmin password: ")


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

    # GARMIN_EMAIL / GARMIN_PASSWORD let the shell supply the credentials, which
    # is the reliable way in on Git Bash: bash's `read -s` accepts pasted text,
    # whereas Python's getpass has no real console there and drops it. The value
    # stays in the shell's memory — nothing is written to disk either way.
    email = (os.getenv("GARMIN_EMAIL") or "").strip()
    password = os.getenv("GARMIN_PASSWORD") or ""

    if email:
        print(f"  email from GARMIN_EMAIL: {email}")
    else:
        email = input("  Garmin email: ").strip()

    if password:
        print("  password from GARMIN_PASSWORD (not shown)")
    else:
        _warn_about_terminal()
        password = _read_password()

    if not email or not password:
        raise SystemExit("Garmin email and password are both required.")

    def prompt_mfa() -> str:
        return input("  Garmin MFA code: ").strip()

    client = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    try:
        client.login(tokenstore)
    except GarminConnectTooManyRequestsError as exc:
        raise SystemExit(
            "\nGarmin rate-limited this IP address (HTTP 429), so your password was\n"
            "never checked — this is not a credential problem.\n\n"
            "The limit is per-IP and lifts on its own, usually within an hour. Each\n"
            "further attempt restarts the clock, so don't retry in a loop.\n\n"
            "  - Wait, then try once more.\n"
            "  - A different network (phone hotspot) has a different IP and often works.\n"
            "  - Once a login succeeds, tokens are cached and later runs won't hit\n"
            f"    the login endpoint at all (cache: {tokenstore}).\n"
            f"\nOriginal error: {exc}"
        ) from exc
    except GarminConnectAuthenticationError as exc:
        message = str(exc)
        if "429" in message or "too many" in message.lower():
            raise SystemExit(
                "\nGarmin rate-limited this IP address (HTTP 429). Your password was\n"
                "never checked. Wait a while and try once — repeated attempts extend\n"
                f"the block.\n\nOriginal error: {exc}"
            ) from exc
        raise SystemExit(f"\nGarmin authentication failed: {exc}") from exc
    finally:
        del password

    print(f"Garmin: logged in as {client.display_name}; tokens cached at {tokenstore}")
    if os.getenv("GARMIN_PASSWORD"):
        print("  Tokens are cached now, so you can clear the variable: unset GARMIN_PASSWORD")
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


def _profile_number(client: Garmin) -> str:
    """The userProfilePk that the gear endpoints key off."""
    profile = client.get_user_profile() or {}
    for key in ("id", "profileId", "userProfilePk", "userProfileId"):
        value = profile.get(key)
        if value:
            return str(value)
    user_data = profile.get("userData") or {}
    for key in ("id", "profileId", "userProfilePk"):
        value = user_data.get(key)
        if value:
            return str(value)
    raise KeyError(f"no profile id in user settings (keys: {sorted(profile)[:12]})")


# Ceiling used when asking for a gear's activity list. If a shoe comes back with
# exactly this many, the list was probably truncated and older activities in our
# window may be missing — which would look like "missing on Garmin" when it isn't.
GEAR_ACTIVITY_LIMIT = 1000


def gear_name_aliases(client: Garmin) -> dict[str, str]:
    """Map each Garmin gear nickname to its real make/model.

    Garmin keeps both: displayName is what you nicknamed the shoe ('Newbies')
    and customMakeModel is what it actually is ('New Balance 880'). Only the
    nickname shows on an activity, so a nickname that looks nothing like the
    Strava name would otherwise read as a completely different shoe.
    """
    try:
        items = client.get_gear(_profile_number(client)) or []
    except Exception as exc:  # noqa: BLE001 - aliases are a nicety, not required
        print(f"  (could not read gear names for aliasing: {exc})")
        return {}

    aliases: dict[str, str] = {}
    for gear in items:
        nickname = (gear.get("displayName") or "").strip()
        make_model = (gear.get("customMakeModel") or "").strip()
        if nickname and make_model and nickname.casefold() != make_model.casefold():
            aliases[nickname] = make_model
    return aliases


def _gear_map_bulk(client: Garmin, activity_ids: set[str]) -> dict[str, list[str]] | None:
    """Build activity_id -> gear names with one call per gear item.

    Far cheaper than one call per activity (a handful of shoes vs dozens of
    runs), which matters because Garmin rate-limits aggressively. Returns None
    if anything looks unreliable, so the caller falls back to the exact path.
    """
    try:
        profile_number = _profile_number(client)
        gear_items = client.get_gear(profile_number) or []
    except Exception as exc:  # noqa: BLE001
        print(f"  (bulk gear lookup unavailable: {exc}; using per-activity lookup)")
        return None

    if not gear_items:
        return None

    mapping: dict[str, list[str]] = {}
    for gear in gear_items:
        uuid = gear.get("uuid")
        if not uuid:
            continue
        name = _gear_display_name(gear)
        try:
            used_in = client.get_gear_activities(uuid, limit=GEAR_ACTIVITY_LIMIT) or []
        except Exception as exc:  # noqa: BLE001
            print(f"  (gear activity list failed for {name}: {exc}; using per-activity lookup)")
            return None

        if len(used_in) >= GEAR_ACTIVITY_LIMIT:
            print(
                f"  ('{name}' returned {len(used_in)} activities — the list may be "
                "truncated, so falling back to the exact per-activity lookup)"
            )
            return None

        for item in used_in:
            activity_id = str(item.get("activityId") or "")
            if activity_id in activity_ids:
                mapping.setdefault(activity_id, []).append(name)
        time.sleep(0.2)

    print(f"  gear resolved from {len(gear_items)} gear items ({len(gear_items) + 2} API calls)")
    return mapping


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
    bulk_gear: bool = True,
    pad_days: int = 1,
) -> tuple[list[Activity], list[dict[str, Any]]]:
    """Fetch running activities in [start, end] plus the gear assigned to each.

    Garmin filters get_activities_by_date by *local* date while we compare on
    UTC, so a run logged late in the evening lands on the previous local day and
    would fall outside the window — silently reported as having no Garmin
    counterpart. Query a padded range and filter on UTC afterwards.

    With bulk_gear (the default), gear is resolved with one call per gear item
    rather than one per activity, falling back automatically if that looks
    unreliable. Returns (normalised activities, raw activity payloads).
    """
    query_start = start - timedelta(days=pad_days)
    query_end = end + timedelta(days=pad_days)
    raw = client.get_activities_by_date(
        query_start.isoformat(), query_end.isoformat(), activitytype="running"
    )
    print(
        f"Garmin: {len(raw)} running activities between {query_start} and {query_end} "
        f"(padded {pad_days}d for timezone edges)"
    )

    # Keep only runs with a usable start time, then resolve gear for those.
    candidates: list[tuple[dict[str, Any], str, datetime]] = []
    for item in raw:
        type_key = (item.get("activityType") or {}).get("typeKey", "")
        if type_key and type_key not in RUNNING_TYPE_KEYS:
            continue
        started = _parse_start(item)
        if started is None:
            print(f"  ! skipping activity {item.get('activityId')}: unparseable start time")
            continue
        if not (start <= started.date() <= end):
            continue  # pulled in only by the padding; outside the UTC window
        candidates.append((item, str(item.get("activityId")), started))

    activity_ids = {activity_id for _, activity_id, _ in candidates}
    gear_map: dict[str, list[str]] | None = None
    if bulk_gear:
        gear_map = _gear_map_bulk(client, activity_ids)

    activities: list[Activity] = []
    for index, (item, activity_id, started) in enumerate(candidates, start=1):
        if gear_map is not None:
            gear_names = gear_map.get(activity_id, [])
        else:
            try:
                gear_items = client.get_activity_gear(activity_id) or []
            except Exception as exc:  # noqa: BLE001 - one bad activity shouldn't kill the run
                print(f"  ! gear lookup failed for {activity_id}: {exc}")
                gear_items = []
            if isinstance(gear_items, dict):
                gear_items = [gear_items]
            item["_gear"] = gear_items
            gear_names = [_gear_display_name(g) for g in gear_items]
            print(f"\r  gear {index}/{len(candidates)}", end="", flush=True)
            if gear_delay_s:
                time.sleep(gear_delay_s)  # be polite to Garmin's API

        activities.append(
            Activity(
                source="garmin",
                activity_id=activity_id,
                name=item.get("activityName") or "(unnamed)",
                start_utc=started,
                distance_m=float(item.get("distance") or 0.0),
                duration_s=float(item.get("duration") or 0.0),
                sport=(item.get("activityType") or {}).get("typeKey") or "running",
                gear=gear_names,
            )
        )

    if gear_map is None:
        print()
    return activities, raw
