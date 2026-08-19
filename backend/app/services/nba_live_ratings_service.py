"""Optional, cached dynamic-season NBA efficiency snapshots (P1-B4).

Read-only and default-off. The static 30-team ORtg/DRtg table is a soft
multi-year level; this provider replaces it with the current season's actual
efficiency when one is configured and reachable.

The provider must publish points, points allowed, and **true possession counts**.
ORtg/DRtg are computed here from those counts rather than read from the payload,
so a pre-computed rating carrying no possession sample cannot be presented as a
possession-derived efficiency. Points per game, raw totals, and estimated
possessions are not valid substitutes.
"""
from __future__ import annotations

import json
import math
import time
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.core.config import settings

# Unit-sanity band for points per 100 possessions. No NBA team lands outside it
# over a meaningful sample, so a value beyond the band means the payload is not
# in points-per-100 at all and the whole feed is untrustworthy.
_RATING_FLOOR = 80.0
_RATING_CEILING = 140.0


@dataclass(frozen=True)
class LiveNbaRatings:
    """Result of looking up one team in a live efficiency snapshot."""

    available: bool
    ratings: dict[str, float] | None = None


@dataclass(frozen=True)
class _CachedSnapshot:
    fetched_at: float
    teams: dict[str, dict[str, float]]


_SNAPSHOT_CACHE: dict[str, _CachedSnapshot] = {}


def get_live_team_ratings(season: str | None, team_name: str) -> LiveNbaRatings:
    """Return live ORtg/DRtg, or ``available=False`` if they cannot be read.

    ``available=True, ratings=None`` means the provider was reached but carried no
    usable row for the requested team — either it omitted the team or the team's
    possession sample was too small to be meaningful. The static table stays
    authoritative in that case.
    """
    season_year = _season_year(season)
    team_key = _team_key(team_name)
    if not season_year or not team_key:
        return LiveNbaRatings(available=False)

    url = _request_url(season_year)
    if url is None:
        return LiveNbaRatings(available=False)

    snapshot = _snapshot(url)
    if snapshot is None:
        return LiveNbaRatings(available=False)

    row = snapshot.get(team_key)
    return LiveNbaRatings(available=True, ratings=dict(row) if row else None)


def clear_live_ratings_cache() -> None:
    """Clear process-local snapshots; used by tests and explicit diagnostics."""
    _SNAPSHOT_CACHE.clear()


def _season_year(season: str | None) -> str | None:
    """Leading start year of an NBA season key such as ``2024-25``."""
    year = str(season or "").strip()[:4]
    return year if len(year) == 4 and year.isdigit() else None


def _request_url(season_year: str) -> str | None:
    """Configured endpoint for one season, or None when disabled or unusable."""
    if not settings.NBA_LIVE_RATINGS_ENABLED:
        return None
    raw = str(settings.NBA_LIVE_RATINGS_URL or "").strip()
    key = str(settings.NBA_LIVE_RATINGS_API_KEY or "").strip()
    season_param = str(settings.NBA_LIVE_RATINGS_SEASON_PARAM or "").strip()
    if not raw or not key or not season_param:
        return None
    split = urlsplit(raw)
    if split.scheme not in {"http", "https"} or not split.netloc:
        return None
    query_items = [
        (name, value)
        for name, value in parse_qsl(split.query, keep_blank_values=True)
        if name != season_param
    ]
    query_items.append((season_param, season_year))
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query_items), ""))


def _snapshot(url: str) -> dict[str, dict[str, float]] | None:
    now = time.monotonic()
    try:
        ttl_seconds = max(0.0, float(settings.NBA_LIVE_RATINGS_CACHE_TTL_HOURS)) * 3600
        max_bytes = int(settings.NBA_LIVE_RATINGS_MAX_BYTES)
        timeout = max(0.1, float(settings.NBA_LIVE_RATINGS_TIMEOUT_S))
        min_possessions = max(0.0, float(settings.NBA_LIVE_RATINGS_MIN_POSSESSIONS))
    except (TypeError, ValueError):
        return None
    if max_bytes <= 0:
        return None

    # Keyed by the resolved URL, so the season and any configuration change get
    # their own entry instead of reusing a snapshot from a different endpoint.
    cached = _SNAPSHOT_CACHE.get(url)
    if cached is not None and now - cached.fetched_at < ttl_seconds:
        return cached.teams

    api_key = str(settings.NBA_LIVE_RATINGS_API_KEY or "").strip()
    request = Request(
        url,
        headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            # One byte past the cap distinguishes "at the limit" from "too large".
            body = response.read(max_bytes + 1)
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    if len(body) > max_bytes:
        return None

    teams = _parse_snapshot(body, min_possessions)
    if teams is None:
        return None
    # Only valid snapshots are cached; a transient fault must not pin an
    # unavailable answer for the whole TTL.
    _SNAPSHOT_CACHE[url] = _CachedSnapshot(fetched_at=now, teams=teams)
    return teams


def _parse_snapshot(
    body: bytes, min_possessions: float
) -> dict[str, dict[str, float]] | None:
    """Validate the documented envelope into computed ratings per team key.

    A structurally broken row rejects the whole snapshot: the contract is either
    honoured or it is not. A row that is well-formed but backed by too few
    possessions is different — that is real data with an unusable sample, so it is
    dropped and the static table covers that team.
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("errors"):
        return None
    rows = payload.get("teams")
    if not isinstance(rows, list):
        return None

    teams: dict[str, dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        team_key = _team_key(row.get("team"))
        if not team_key or team_key in teams:
            return None  # missing or duplicate team makes the snapshot ambiguous
        possessions = _numeric(row.get("possessions"))
        points = _numeric(row.get("points"))
        allowed = _numeric(row.get("points_allowed"))
        if possessions is None or points is None or allowed is None:
            return None
        if possessions <= 0 or points < 0 or allowed < 0:
            return None
        if possessions < min_possessions:
            teams[team_key] = {}  # reached, sample too small to use
            continue
        ortg = 100.0 * points / possessions
        drtg = 100.0 * allowed / possessions
        if not _in_band(ortg) or not _in_band(drtg):
            return None  # not points per 100 possessions; the units are wrong
        teams[team_key] = {
            "ortg": round(ortg, 2),
            "drtg": round(drtg, 2),
            "possessions": round(possessions, 1),
        }
    return teams


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _in_band(rating: float) -> bool:
    return _RATING_FLOOR <= rating <= _RATING_CEILING


def _team_key(name: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(name or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    tokens = "".join(char.lower() if char.isalnum() else " " for char in normalized).split()
    return " ".join(tokens)
