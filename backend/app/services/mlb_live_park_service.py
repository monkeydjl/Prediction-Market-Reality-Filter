"""Optional, cached measured MLB park-factor snapshots (P1-M2).

Read-only and default-off. The code-local 30-team park table is a frozen
multi-year-ish level; this provider replaces it with a factor measured from
actual game results when one is configured and reachable.

The provider must publish home and road game counts together with the combined
runs scored in those games. The park factor is computed here from those counts
rather than read from the payload, so a pre-computed factor carrying no game
sample cannot be presented as a measured one.

Only the home team's park is relevant — both sides play in it — so unlike the
paired team-strength providers there is no cross-source comparison to protect
and no mixed-source hazard.
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

# Unit-sanity band for a park run factor, where 1.0 is league average. The most
# extreme real parks sit near 0.90 and 1.15, so a value beyond this band means
# the payload is not a ratio at all (runs per game, for instance) and the whole
# feed is untrustworthy.
_FACTOR_FLOOR = 0.70
_FACTOR_CEILING = 1.40


@dataclass(frozen=True)
class LiveMlbPark:
    """Result of looking one home park up in a measured snapshot."""

    available: bool
    park: dict[str, float] | None = None


@dataclass(frozen=True)
class _CachedSnapshot:
    fetched_at: float
    parks: dict[str, dict[str, float]]


_SNAPSHOT_CACHE: dict[str, _CachedSnapshot] = {}


def get_live_park_factor(season: str | int | None, team_name: str) -> LiveMlbPark:
    """Return a measured park factor, or ``available=False`` if it cannot be read.

    ``available=True, park=None`` means the provider was reached but carried no
    usable row for the requested home team — either it omitted the team or the
    team's game sample was too small to be meaningful. The static table stays
    authoritative in that case.
    """
    season_year = _season_year(season)
    team_key = _team_key(team_name)
    if not season_year or not team_key:
        return LiveMlbPark(available=False)

    url = _request_url(season_year)
    if url is None:
        return LiveMlbPark(available=False)

    snapshot = _snapshot(url)
    if snapshot is None:
        return LiveMlbPark(available=False)

    row = snapshot.get(team_key)
    return LiveMlbPark(available=True, park=dict(row) if row else None)


def clear_live_park_cache() -> None:
    """Clear process-local snapshots; used by tests and explicit diagnostics."""
    _SNAPSHOT_CACHE.clear()


def _season_year(season: str | int | None) -> str | None:
    """Leading year of an MLB season key such as ``2026``."""
    year = str(season if season is not None else "").strip()[:4]
    return year if len(year) == 4 and year.isdigit() else None


def _request_url(season_year: str) -> str | None:
    """Configured endpoint for one season, or None when disabled or unusable."""
    if not settings.MLB_LIVE_PARK_ENABLED:
        return None
    raw = str(settings.MLB_LIVE_PARK_URL or "").strip()
    key = str(settings.MLB_LIVE_PARK_API_KEY or "").strip()
    season_param = str(settings.MLB_LIVE_PARK_SEASON_PARAM or "").strip()
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
        ttl_seconds = max(0.0, float(settings.MLB_LIVE_PARK_CACHE_TTL_HOURS)) * 3600
        max_bytes = int(settings.MLB_LIVE_PARK_MAX_BYTES)
        timeout = max(0.1, float(settings.MLB_LIVE_PARK_TIMEOUT_S))
        min_games = max(0.0, float(settings.MLB_LIVE_PARK_MIN_GAMES))
    except (TypeError, ValueError):
        return None
    if max_bytes <= 0:
        return None

    # Keyed by the resolved URL, so the season and any configuration change get
    # their own entry instead of reusing a snapshot from a different endpoint.
    cached = _SNAPSHOT_CACHE.get(url)
    if cached is not None and now - cached.fetched_at < ttl_seconds:
        return cached.parks

    api_key = str(settings.MLB_LIVE_PARK_API_KEY or "").strip()
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

    parks = _parse_snapshot(body, min_games)
    if parks is None:
        return None
    # Only valid snapshots are cached; a transient fault must not pin an
    # unavailable answer for the whole TTL.
    _SNAPSHOT_CACHE[url] = _CachedSnapshot(fetched_at=now, parks=parks)
    return parks


def _parse_snapshot(body: bytes, min_games: float) -> dict[str, dict[str, float]] | None:
    """Validate the documented envelope into computed factors per team key.

    A structurally broken row rejects the whole snapshot: the contract is either
    honoured or it is not. A row that is well-formed but backed by too few games
    is different — that is real data with a sample too noisy to displace the
    static level, so it is dropped and the static table covers that park.
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("errors"):
        return None
    rows = payload.get("parks")
    if not isinstance(rows, list):
        return None

    parks: dict[str, dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        team_key = _team_key(row.get("team"))
        if not team_key or team_key in parks:
            return None  # missing or duplicate team makes the snapshot ambiguous
        home_games = _numeric(row.get("home_games"))
        road_games = _numeric(row.get("road_games"))
        home_runs = _numeric(row.get("home_runs"))
        road_runs = _numeric(row.get("road_runs"))
        if home_games is None or road_games is None:
            return None
        if home_runs is None or road_runs is None:
            return None
        if home_games <= 0 or road_games <= 0:
            return None
        # Road runs are the denominator, so zero of them cannot yield a ratio.
        if home_runs < 0 or road_runs <= 0:
            return None
        if min(home_games, road_games) < min_games:
            parks[team_key] = {}  # reached, sample too noisy to use
            continue
        factor = (home_runs / home_games) / (road_runs / road_games)
        if not _FACTOR_FLOOR <= factor <= _FACTOR_CEILING:
            return None  # not a league-average-relative ratio; the units are wrong
        parks[team_key] = {
            "park_factor": round(factor, 4),
            "home_games": round(home_games, 1),
            "road_games": round(road_games, 1),
        }
    return parks


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _team_key(name: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(name or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    tokens = "".join(char.lower() if char.isalnum() else " " for char in normalized).split()
    return " ".join(tokens)
