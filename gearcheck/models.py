"""Platform-neutral shape that both Garmin and Strava activities are mapped into."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Activity:
    source: str  # "garmin" | "strava"
    activity_id: str
    name: str
    start_utc: datetime
    distance_m: float
    duration_s: float
    sport: str
    gear: list[str] = field(default_factory=list)  # display names, may be empty
    external_ref: str | None = None  # Strava external_id / Garmin id parsed from it

    @property
    def gear_label(self) -> str:
        return " + ".join(self.gear) if self.gear else ""

    @property
    def distance_km(self) -> float:
        return self.distance_m / 1000.0
