"""Optional, configured true-style provider for football club competitions.

The provider must expose a normalized season snapshot with possession, shots,
and PPDA: ``{"teams": [{"team": "Arsenal", "possession_pct": 57.2,
"shots_per90": 15.1, "ppda": 9.3}]}``.
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


@dataclass(frozen=True)
class LiveStyleResult:
    """A lookup result that distinguishes source availability from missing data."""

    available: bool
    style: dict[str, float] | None = None


@dataclass(frozen=True)
class _CachedSnapshot:
    fetched_at: float
    teams: dict[str, dict[str, float]]


_SNAPSHOT_CACHE: dict[tuple[str, str], _CachedSnapshot] = {}


def get_live_style(
    competition: str | None,
    season: str | None,
    team_name: str,
) -> LiveStyleResult:
    """Return a true-style lookup or report that the provider is unavailable."""
    competition_code = str(competition or "").strip().lower()
    season_year = _season_year(season)
    team_key = _team_key(team_name)
    if not competition_code or not season_year or not team_key or not _is_configured():
        return LiveStyleResult(available=False)

    snapshot = _snapshot(competition_code, season_year)
    if snapshot is None:
        return LiveStyleResult(available=False)
    return LiveStyleResult(available=True, style=snapshot.get(team_key))


def clear_live_style_cache() -> None:
    """Clear cached provider snapshots for deterministic tests."""
    _SNAPSHOT_CACHE.clear()


def _is_configured() -> bool:
    return bool(
        settings.FOOTBALL_LIVE_STYLE_ENABLED
        and str(settings.FOOTBALL_LIVE_STYLE_URL or "").strip()
        and str(settings.FOOTBALL_LIVE_STYLE_API_KEY or "").strip()
    )


def _season_year(season: str | None) -> str | None:
    value = str(season or "").strip()
    year = value[:4]
    return year if len(year) == 4 and year.isdigit() else None


def _snapshot(competition: str, season_year: str) -> dict[str, dict[str, float]] | None:
    key = (competition, season_year)
    now = time.monotonic()
    try:
        ttl_seconds = max(0.0, float(settings.FOOTBALL_LIVE_STYLE_CACHE_TTL_HOURS)) * 3600
    except (TypeError, ValueError):
        return None
    cached = _SNAPSHOT_CACHE.get(key)
    if cached is not None and now - cached.fetched_at < ttl_seconds:
        return cached.teams

    url = _request_url(competition, season_year)
    if url is None:
        return None
    try:
        timeout = max(0.1, float(settings.FOOTBALL_LIVE_STYLE_TIMEOUT_S))
        max_bytes = int(settings.FOOTBALL_LIVE_STYLE_MAX_BYTES)
    except (TypeError, ValueError):
        return None
    if max_bytes <= 0:
        return None

    api_key = str(settings.FOOTBALL_LIVE_STYLE_API_KEY or "").strip()
    request = Request(
        url,
        headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    if len(body) > max_bytes:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    teams = _parse_snapshot(payload)
    if teams is None:
        return None
    _SNAPSHOT_CACHE[key] = _CachedSnapshot(fetched_at=now, teams=teams)
    return teams


def _request_url(competition: str, season_year: str) -> str | None:
    raw_url = str(settings.FOOTBALL_LIVE_STYLE_URL or "").strip()
    season_param = str(settings.FOOTBALL_LIVE_STYLE_SEASON_PARAM or "").strip()
    if not raw_url or not season_param or season_param == "competition":
        return None
    split = urlsplit(raw_url)
    if split.scheme not in {"http", "https"} or not split.netloc:
        return None
    query_items = [
        (name, value)
        for name, value in parse_qsl(split.query, keep_blank_values=True)
        if name not in {"competition", season_param}
    ]
    query_items.extend((("competition", competition), (season_param, season_year)))
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query_items), ""))


def _parse_snapshot(payload: Any) -> dict[str, dict[str, float]] | None:
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
            return None
        possession = _metric(row.get("possession_pct"), 20.0, 80.0)
        shots = _metric(row.get("shots_per90"), 1.0, 40.0)
        ppda = _metric(row.get("ppda"), 1.0, 40.0)
        if possession is None or shots is None or ppda is None:
            return None
        teams[team_key] = {
            "possession_pct": round(possession, 2),
            "shots_per90": round(shots, 2),
            "ppda": round(ppda, 2),
        }
    return teams


def _metric(value: Any, lower: float, upper: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or not lower <= numeric <= upper:
        return None
    return numeric


def _team_key(name: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(name or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    tokens = "".join(char.lower() if char.isalnum() else " " for char in normalized).split()
    return " ".join(token for token in tokens if token not in {"fc", "cf"})
