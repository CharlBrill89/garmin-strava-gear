"""Pair Garmin activities with their Strava counterparts, then compare gear."""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .models import Activity

# Verdicts
MATCH = "match"
NAME_VARIANT = "name_variant"
DIFFERENT = "different_shoe"
MISSING_STRAVA = "missing_on_strava"
MISSING_GARMIN = "missing_on_garmin"
MISSING_BOTH = "missing_on_both"
NO_STRAVA_ACTIVITY = "no_strava_activity"
NO_GARMIN_ACTIVITY = "no_garmin_activity"

PROBLEM_VERDICTS = {
    DIFFERENT,
    MISSING_STRAVA,
    MISSING_GARMIN,
    MISSING_BOTH,
    NO_STRAVA_ACTIVITY,
    NO_GARMIN_ACTIVITY,
}

_PUNCT = re.compile(r"[^a-z0-9]+")


@dataclass
class Pair:
    garmin: Activity | None
    strava: Activity | None
    verdict: str
    detail: str
    match_method: str

    @property
    def anchor(self) -> Activity:
        return self.garmin or self.strava  # type: ignore[return-value]


def load_aliases(path: Path) -> dict[str, str]:
    """Optional user-supplied equivalences: {"canonical name": ["variant", ...]}."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    lookup: dict[str, str] = {}
    for name, variants in raw.items():
        if name.startswith("_"):
            continue  # "_comment" and friends
        if not isinstance(variants, list):
            raise ValueError(
                f"{path.name}: '{name}' must map to a list of alternative spellings"
            )
        lookup[normalise(name)] = name
        for variant in variants:
            lookup[normalise(str(variant))] = name
    return lookup


def aliases_from_pairs(mapping: dict[str, str]) -> dict[str, str]:
    """Turn {nickname: real name} into the lookup shape load_aliases returns."""
    return {normalise(nickname): real for nickname, real in mapping.items() if real}


def normalise(name: str) -> str:
    return _PUNCT.sub(" ", name.lower()).strip()


def canonical(name: str, aliases: dict[str, str]) -> str:
    return aliases.get(normalise(name), name)


def _split_tokens(name: str) -> tuple[set[str], set[str]]:
    """Return (word tokens, numeric tokens) for a normalised gear name."""
    tokens = set(normalise(name).split())
    numbers = {t for t in tokens if t.isdigit()}
    return tokens - numbers, numbers


def _partial_ratio(shorter: str, longer: str) -> float:
    """Best similarity of `shorter` against any equal-length window of `longer`.

    Lets "Endorphin Spd 4" match "Saucony Endorphin Speed 4" without the brand
    prefix dragging the whole-string ratio down.
    """
    if len(shorter) < 6:
        return 0.0  # too short for a window match to mean anything
    span = len(shorter)
    return max(
        (
            difflib.SequenceMatcher(None, shorter, longer[i : i + span]).ratio()
            for i in range(len(longer) - span + 1)
        ),
        default=0.0,
    )


def same_shoe(left: str, right: str, threshold: float) -> tuple[bool, str]:
    """Decide whether two gear names denote the same physical shoe.

    Garmin tends to store the full make+model ("Nike Air Zoom Pegasus 41") while
    Strava shows whatever nickname you typed ("Pegasus 41"), so a pure string
    comparison over-reports mismatches. But version numbers are load-bearing:
    "Pegasus 40" and "Pegasus 41" are genuinely different shoes, and plain string
    similarity rates them at 0.93 — high enough to wrongly merge them. So numeric
    tokens are compared exactly first, and only then do we look at the words.
    """
    l_words, l_nums = _split_tokens(left)
    r_words, r_nums = _split_tokens(right)

    if l_nums and r_nums and l_nums != r_nums:
        # Only call it a version difference when the model line itself matches.
        # Two unrelated shoes that merely share a brand ("Nike Pegasus 40" vs
        # "Nike Vaporfly 3") are a different shoe, not a different version.
        shared = l_words & r_words
        if len(shared) >= 2 or (shared and shared in (l_words, r_words)):
            return False, (
                f"same model line, different version "
                f"({'/'.join(sorted(l_nums))} vs {'/'.join(sorted(r_nums))})"
            )
        return False, ""

    if l_words and r_words and (l_words <= r_words or r_words <= l_words):
        return True, "one name is a shortened form of the other"

    l_norm, r_norm = normalise(left), normalise(right)
    shorter, longer = sorted((l_norm, r_norm), key=len)
    ratio = max(
        difflib.SequenceMatcher(None, l_norm, r_norm).ratio(),
        _partial_ratio(shorter, longer),
    )
    if ratio >= threshold:
        return True, f"names are {ratio:.0%} similar"
    return False, ""


def match_activities(
    garmin: list[Activity],
    strava: list[Activity],
    tolerance_s: int = 180,
) -> tuple[list[tuple[Activity, Activity, str]], list[Activity], list[Activity]]:
    """Pair 1:1. Prefer Strava's external_id (which embeds the Garmin id), then
    fall back to nearest start time within tolerance, sanity-checked on distance.
    """
    by_garmin_id = {a.activity_id: a for a in garmin}
    used_garmin: set[str] = set()
    used_strava: set[str] = set()
    pairs: list[tuple[Activity, Activity, str]] = []

    # Pass 1: id embedded in Strava's external_id
    for s in strava:
        if s.external_ref and s.external_ref in by_garmin_id:
            g = by_garmin_id[s.external_ref]
            if g.activity_id not in used_garmin:
                pairs.append((g, s, "external_id"))
                used_garmin.add(g.activity_id)
                used_strava.add(s.activity_id)

    # Pass 2: nearest start time, with a distance sanity check
    remaining_strava = [s for s in strava if s.activity_id not in used_strava]
    for g in sorted(garmin, key=lambda a: a.start_utc):
        if g.activity_id in used_garmin:
            continue
        best: Activity | None = None
        best_delta = None
        for s in remaining_strava:
            if s.activity_id in used_strava:
                continue
            delta = abs((s.start_utc - g.start_utc).total_seconds())
            if delta > tolerance_s:
                continue
            # Uploads of the same run agree on distance to well within 5%.
            if g.distance_m and s.distance_m:
                spread = abs(g.distance_m - s.distance_m) / max(g.distance_m, s.distance_m)
                if spread > 0.05:
                    continue
            if best_delta is None or delta < best_delta:
                best, best_delta = s, delta
        if best is not None:
            pairs.append((g, best, f"start_time (+/-{int(best_delta)}s)"))
            used_garmin.add(g.activity_id)
            used_strava.add(best.activity_id)

    unmatched_garmin = [a for a in garmin if a.activity_id not in used_garmin]
    unmatched_strava = [a for a in strava if a.activity_id not in used_strava]
    return pairs, unmatched_garmin, unmatched_strava


def compare_gear(
    garmin: Activity, strava: Activity, aliases: dict[str, str], fuzzy_threshold: float
) -> tuple[str, str]:
    g_names = [canonical(n, aliases) for n in garmin.gear]
    s_names = [canonical(n, aliases) for n in strava.gear]

    if not g_names and not s_names:
        return MISSING_BOTH, "No gear assigned on either platform"
    if not s_names:
        return MISSING_STRAVA, f"Garmin has '{garmin.gear_label}', Strava has none"
    if not g_names:
        return MISSING_GARMIN, f"Strava has '{strava.gear_label}', Garmin has none"

    g_norm = {normalise(n) for n in g_names}
    s_norm = {normalise(n) for n in s_names}
    if g_norm & s_norm:
        if g_norm == s_norm:
            return MATCH, ""
        extra = ", ".join(sorted(g_norm ^ s_norm))
        return NAME_VARIANT, f"Overlapping but not identical gear sets ({extra})"

    # No exact overlap — is it the same shoe named differently, or a real mismatch?
    reasons: list[str] = []
    for g_name in g_names:
        for s_name in s_names:
            is_same, why = same_shoe(g_name, s_name, fuzzy_threshold)
            if is_same:
                return (
                    NAME_VARIANT,
                    f"Likely same shoe: '{garmin.gear_label}' vs "
                    f"'{strava.gear_label}' — {why}",
                )
            if why:
                reasons.append(why)

    detail = f"Garmin '{garmin.gear_label}' vs Strava '{strava.gear_label}'"
    if reasons:
        detail += f" — {reasons[0]}"
    return DIFFERENT, detail


def build_pairs(
    garmin: list[Activity],
    strava: list[Activity],
    aliases: dict[str, str],
    tolerance_s: int = 180,
    fuzzy_threshold: float = 0.85,
) -> list[Pair]:
    matched, only_garmin, only_strava = match_activities(garmin, strava, tolerance_s)

    pairs = [
        Pair(g, s, *compare_gear(g, s, aliases, fuzzy_threshold), match_method=method)
        for g, s, method in matched
    ]
    pairs += [
        Pair(
            g,
            None,
            NO_STRAVA_ACTIVITY,
            "No Strava activity matched this Garmin run",
            "unmatched",
        )
        for g in only_garmin
    ]
    pairs += [
        Pair(
            None,
            s,
            NO_GARMIN_ACTIVITY,
            "No Garmin activity matched this Strava run",
            "unmatched",
        )
        for s in only_strava
    ]

    pairs.sort(key=lambda p: p.anchor.start_utc, reverse=True)
    return pairs
