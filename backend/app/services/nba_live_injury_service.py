"""Optional, cached NBA availability snapshots (P1-B1).

Read-only and default-off. A failed or unconfigured provider is distinct from a
successful response carrying no absence for the requested team, so the caller can
preserve the static fallback only when live data was genuinely unavailable.

The role tiers and the impact formula stay in
``app.sports.basketball.nba_injury``; this module only fetches, validates, and
normalizes rows before handing them to that shared summarizer.
"""
from __future__ import annotations

import json
import time
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app.core.config import settings
from app.sports.basketball.nba_injury import ROLE_WEIGHTS, summarize_injury_impact

# Provider statuses treated as unavailable. Deliberately narrow: "questionable",
# "probable", and "day-to-day" describe a player expected to feature, and
# counting them would overstate the absence a role weight is meant to represent.
_OUT_STATUSES = frozenset({"out", "inactive", "suspended"})


@dataclass(frozen=True)
class LiveNbaInjuryImpact:
    """Result of looking up one team in a live availability snapshot."""

    available: bool
    impact: float | None = None


@dataclass(frozen=True)
class _CachedSnapshot:
    fetched_at: float
    teams: dict[str, tuple[dict[str, str], ...]]


_SNAPSHOT_CACHE: dict[str, _CachedSnapshot] = {}


def get_live_injury_impact(team_name: str) -> LiveNbaInjuryImpact:
    """Return a live injury impact, or ``available=False`` if it cannot be read.

    ``available=True, impact=None`` means the provider was reached but reported no
    absence for the requested team. That is intentionally not a known-healthy
    ``0.0``: the static table stays authoritative for teams the provider omits.
    """
    team_key = _team_key(team_name)
    url = _request_url()
    if not team_key or url is None:
        return LiveNbaInjuryImpact(available=False)

    snapshot = _snapshot(url)
    if snapshot is None:
        return LiveNbaInjuryImpact(available=False)

    rows = snapshot.get(team_key)
    return LiveNbaInjuryImpact(
        available=True,
        impact=summarize_injury_impact(list(rows)) if rows else None,
    )


def clear_live_injury_cache() -> None:
    """Clear process-local snapshots; used by tests and explicit diagnostics."""
    _SNAPSHOT_CACHE.clear()


def _request_url() -> str | None:
    """Configured endpoint, or None when disabled or unusable."""
    if not settings.NBA_LIVE_INJURIES_ENABLED:
        return None
    raw = str(settings.NBA_LIVE_INJURIES_URL or "").strip()
    key = str(settings.NBA_LIVE_INJURIES_API_KEY or "").strip()
    if not raw or not key:
        return None
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return raw


def _snapshot(url: str) -> dict[str, tuple[dict[str, str], ...]] | None:
    now = time.monotonic()
    try:
        ttl_seconds = max(0.0, float(settings.NBA_LIVE_INJURIES_CACHE_TTL_HOURS)) * 3600
        max_bytes = int(settings.NBA_LIVE_INJURIES_MAX_BYTES)
        timeout = max(0.1, float(settings.NBA_LIVE_INJURIES_TIMEOUT_S))
    except (TypeError, ValueError):
        return None
    if max_bytes <= 0:
        return None

    cached = _SNAPSHOT_CACHE.get(url)
    if cached is not None and now - cached.fetched_at < ttl_seconds:
        return cached.teams

    api_key = str(settings.NBA_LIVE_INJURIES_API_KEY or "").strip()
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            # One byte past the cap distinguishes "at the limit" from "too large".
            body = response.read(max_bytes + 1)
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    if len(body) > max_bytes:
        return None

    teams = _parse_snapshot(body)
    if teams is None:
        return None
    # Only valid snapshots are cached; a transient fault must not pin an
    # unavailable answer for the whole TTL.
    _SNAPSHOT_CACHE[url] = _CachedSnapshot(fetched_at=now, teams=teams)
    return teams


def _parse_snapshot(body: bytes) -> dict[str, tuple[dict[str, str], ...]] | None:
    """Validate the documented envelope into normalized rows per team key."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("errors"):
        return None
    entries = payload.get("teams")
    if not isinstance(entries, list):
        return None

    teams: dict[str, tuple[dict[str, str], ...]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        key = _team_key(entry.get("team"))
        if not key:
            return None
        absences = entry.get("absences")
        if absences is None:
            absences = []
        if not isinstance(absences, list):
            return None
        rows = [row for row in (_absence_row(item) for item in absences) if row]
        if key in teams:
            return None  # duplicate team blocks make the snapshot ambiguous
        teams[key] = tuple(rows)
    return teams


def _absence_row(item: Any) -> dict[str, str] | None:
    """Normalize one absence, or None when it does not count as unavailable."""
    if not isinstance(item, dict):
        return None
    status = str(item.get("status") or "").strip().lower()
    if status not in _OUT_STATUSES:
        return None
    role = str(item.get("role") or "").strip().lower()
    # Unrecognized tiers are dropped so the shared summarizer applies its own
    # documented bench default rather than this module inventing a weight.
    if role in ROLE_WEIGHTS:
        return {"status": "out", "role": role}
    return {"status": "out"}


def _team_key(name: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(name or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    tokens = "".join(char.lower() if char.isalnum() else " " for char in normalized).split()
    return " ".join(tokens)
