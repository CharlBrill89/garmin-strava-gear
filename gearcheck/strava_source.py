"""Pull running activities + their assigned gear from the Strava v3 API.

Tokens come from .env (written by strava_auth.py) and are refreshed in place when
they expire. Nothing token-shaped is ever printed.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, time as dtime, timezone
from typing import Any

import requests

from . import env_store
from .models import Activity

API = "https://www.strava.com/api/v3"
TOKEN_URL = "https://www.strava.com/oauth/token"

RUN_SPORT_TYPES = {"Run", "TrailRun", "VirtualRun"}

# Strava stores Garmin uploads with an external_id like "garmin_push_1234567890".
_GARMIN_REF = re.compile(r"garmin[_-](?:push|ping|connect)?[_-]?(\d{6,})", re.IGNORECASE)


class StravaError(RuntimeError):
    pass


class StravaClient:
    def __init__(self) -> None:
        env_store.load()
        self.client_id = env_store.get("STRAVA_CLIENT_ID")
        self.client_secret = env_store.get("STRAVA_CLIENT_SECRET")
        self.access_token = env_store.get("STRAVA_ACCESS_TOKEN")
        self.refresh_token = env_store.get("STRAVA_REFRESH_TOKEN")
        self.expires_at = int(env_store.get("STRAVA_EXPIRES_AT") or 0)

        missing = [
            name
            for name, value in (
                ("STRAVA_CLIENT_ID", self.client_id),
                ("STRAVA_CLIENT_SECRET", self.client_secret),
                ("STRAVA_REFRESH_TOKEN", self.refresh_token),
            )
            if not value
        ]
        if missing:
            raise StravaError(
                "Strava is not authorised yet (missing "
                + ", ".join(missing)
                + "). Run:  python strava_auth.py"
            )

        self.session = requests.Session()
        self._gear_cache: dict[str, str] = {}

    # -- auth ---------------------------------------------------------------

    def _ensure_token(self) -> None:
        if self.access_token and time.time() < self.expires_at - 120:
            return

        response = self.session.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
            timeout=30,
        )
        if response.status_code != 200:
            raise StravaError(
                f"Strava token refresh failed ({response.status_code}). "
                "Re-run  python strava_auth.py  to re-authorise."
            )
        payload = response.json()
        self.access_token = payload["access_token"]
        self.refresh_token = payload.get("refresh_token", self.refresh_token)
        self.expires_at = int(payload["expires_at"])
        env_store.set_values(
            {
                "STRAVA_ACCESS_TOKEN": self.access_token,
                "STRAVA_REFRESH_TOKEN": self.refresh_token,
                "STRAVA_EXPIRES_AT": str(self.expires_at),
            }
        )
        print("Strava: access token refreshed")

    def _get(self, path: str, **params: Any) -> Any:
        self._ensure_token()
        response = self.session.get(
            f"{API}{path}",
            headers={"Authorization": f"Bearer {self.access_token}"},
            params=params,
            timeout=30,
        )
        if response.status_code == 401:
            raise StravaError(
                "Strava rejected the token (401). Re-run  python strava_auth.py"
            )
        if response.status_code == 429:
            raise StravaError("Strava rate limit hit. Wait 15 minutes and retry.")
        if response.status_code >= 400:
            raise StravaError(f"Strava GET {path} failed ({response.status_code})")
        return response.json()

    # -- gear ---------------------------------------------------------------

    def _load_athlete_gear(self) -> None:
        """Prime the gear-id -> name cache from the athlete profile."""
        athlete = self._get("/athlete")
        for item in (athlete.get("shoes") or []) + (athlete.get("bikes") or []):
            gear_id = item.get("id")
            name = (item.get("nickname") or item.get("name") or "").strip()
            if gear_id and name:
                self._gear_cache[gear_id] = name

    def gear_name(self, gear_id: str | None) -> str | None:
        if not gear_id:
            return None
        if gear_id in self._gear_cache:
            return self._gear_cache[gear_id]
        # Retired gear is absent from the athlete profile; fetch it directly.
        try:
            gear = self._get(f"/gear/{gear_id}")
            name = (gear.get("nickname") or gear.get("name") or "").strip()
            if not name:
                name = f"{gear.get('brand_name', '')} {gear.get('model_name', '')}".strip()
            if gear.get("retired"):
                name = f"{name} (retired)"
        except StravaError:
            name = f"<unknown gear {gear_id}>"
        self._gear_cache[gear_id] = name or f"<unknown gear {gear_id}>"
        return self._gear_cache[gear_id]

    # -- activities ---------------------------------------------------------

    def fetch_runs(
        self, start: date, end: date
    ) -> tuple[list[Activity], list[dict[str, Any]]]:
        self._load_athlete_gear()

        after = int(
            datetime.combine(start, dtime.min, tzinfo=timezone.utc).timestamp()
        )
        before = int(
            datetime.combine(end, dtime.max, tzinfo=timezone.utc).timestamp()
        )

        raw: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self._get(
                "/athlete/activities", after=after, before=before, page=page, per_page=200
            )
            if not batch:
                break
            raw.extend(batch)
            page += 1
            if page > 25:  # ~5000 activities; a safety stop, not an expected path
                print("Strava: stopping at 25 pages")
                break

        activities: list[Activity] = []
        for item in raw:
            sport = item.get("sport_type") or item.get("type") or ""
            if sport not in RUN_SPORT_TYPES:
                continue
            started = datetime.strptime(
                item["start_date"], "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc)
            name = self.gear_name(item.get("gear_id"))
            external_id = item.get("external_id") or ""
            match = _GARMIN_REF.search(external_id)
            activities.append(
                Activity(
                    source="strava",
                    activity_id=str(item.get("id")),
                    name=item.get("name") or "(unnamed)",
                    start_utc=started,
                    distance_m=float(item.get("distance") or 0.0),
                    duration_s=float(item.get("elapsed_time") or 0.0),
                    sport=sport,
                    gear=[name] if name else [],
                    external_ref=match.group(1) if match else None,
                )
            )

        print(f"Strava: {len(activities)} runs (of {len(raw)} activities) in window")
        return activities, raw
