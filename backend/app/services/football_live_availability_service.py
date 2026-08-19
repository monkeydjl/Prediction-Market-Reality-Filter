"""Optional configured player-availability impact provider for football.

The provider supplies reportable absences plus player minutes and market-value
shares in a normalized competition-season snapshot. It is read-only and
default-off; malformed snapshots are rejected as a whole.
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
from app.sports.football.football_injury import summarize_injury_impact

_ALLOWED_ROLES = {"star", "starter", "rotation", "bench"}


@dataclass(frozen=True)
class LiveAvailabilityImpact:
    """Result that distinguishes provider availability from no reportable absence."""

    available: bool
    impact: float | None = None


@dataclass(frozen=True)
class _CachedSnapshot:
    fetched_at: float
    teams: dict[str, tuple[dict[str, Any], ...]]


_SNAPSHOT_CACHE: dict[tuple[str, str], _CachedSnapshot] = {}


def get_live_availability_impact(
    competition: str | None,
    season: str | None,
    team_name: str,
) -> LiveAvailabilityImpact:
    """Return a contextual impact, or provider unavailability.

    ``available=True, impact=None`` means a valid snapshot contained no
    reportable absence for the requested team. It is not a healthy ``0.0``.
    """
    competition_code = str(competition or "").strip().lower()
    season_year = _season_year(season)
    team_key = _team_key(team_name)
    if not competition_code or not season_year or not team_key or not _is_configured():
        return LiveAvailabilityImpact(available=False)

    snapshot = _snapshot(competition_code, season_year)
    if snapshot is None:
        return LiveAvailabilityImpact(available=False)
    return LiveAvailabilityImpact(
        available=True,
        impact=summarize_injury_impact(list(snapshot.get(team_key, ()))),
    )


def clear_live_availability_cache() -> None:
    """Clear cached provider snapshots for deterministic tests."""
    _SNAPSHOT_CACHE.clear()


def _is_configured() -> bool:
    return bool(
        settings.FOOTBALL_LIVE_AVAILABILITY_ENABLED
        and str(settings.FOOTBALL_LIVE_AVAILABILITY_URL or "").strip()
        and str(settings.FOOTBALL_LIVE_AVAILABILITY_API_KEY or "").strip()
    )


def _season_year(season: str | None) -> str | None:
    value = str(season or "").strip()
    year = value[:4]
    return year if len(year) == 4 and year.isdigit() else None


def _snapshot(
    competition: str,
    season_year: str,
) -> dict[str, tuple[dict[str, Any], ...]] | None:
    key = (competition, season_year)
    now = time.monotonic()
    try:
        ttl_seconds = max(
            0.0, float(settings.FOOTBALL_LIVE_AVAILABILITY_CACHE_TTL_HOURS)
        ) * 3600
    except (TypeError, ValueError):
        return None
    cached = _SNAPSHOT_CACHE.get(key)
    if cached is not None and now - cached.fetched_at < ttl_seconds:
        return cached.teams

    url = _request_url(competition, season_year)
    if url is None:
        return None
    try:
        timeout = max(0.1, float(settings.FOOTBALL_LIVE_AVAILABILITY_TIMEOUT_S))
        max_bytes = int(settings.FOOTBALL_LIVE_AVAILABILITY_MAX_BYTES)
    except (TypeError, ValueError):
        return None
    if max_bytes <= 0:
        return None

    api_key = str(settings.FOOTBALL_LIVE_AVAILABILITY_API_KEY or "").strip()
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
    raw_url = str(settings.FOOTBALL_LIVE_AVAILABILITY_URL or "").strip()
    season_param = str(settings.FOOTBALL_LIVE_AVAILABILITY_SEASON_PARAM or "").strip()
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


def _parse_snapshot(payload: Any) -> dict[str, tuple[dict[str, Any], ...]] | None:
    if not isinstance(payload, dict) or payload.get("errors"):
        return None
    rows = payload.get("teams")
    if not isinstance(rows, list):
        return None
    teams: dict[str, tuple[dict[str, Any], ...]] = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        team_key = _team_key(row.get("team"))
        absences = row.get("absences")
        if not team_key or team_key in teams or not isinstance(absences, list):
            return None
        parsed_absences = _parse_absences(absences)
        if parsed_absences is None:
            return None
        teams[team_key] = tuple(parsed_absences)
    return teams


def _parse_absences(rows: list[Any]) -> list[dict[str, Any]] | None:
    absences: list[dict[str, Any]] = []
    players: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            return None
        player_key = _player_key(row.get("player"))
        status = str(row.get("status") or "").strip().lower()
        role = str(row.get("role") or "").strip().lower()
        minutes_share = _share(row.get("minutes_share"))
        market_value_share = _share(row.get("market_value_share"))
        if (
            not player_key
            or player_key in players
            or status != "out"
            or role not in _ALLOWED_ROLES
            or minutes_share is None
            or market_value_share is None
        ):
            return None
        players.add(player_key)
        absences.append(
            {
                "status": "out",
                "role": role,
                "minutes_share": minutes_share,
                "market_value_share": market_value_share,
            }
        )
    return absences


def _share(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        return None
    return round(parsed, 6)


def _team_key(name: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(name or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    tokens = "".join(char.lower() if char.isalnum() else " " for char in normalized).split()
    return " ".join(token for token in tokens if token not in {"fc", "cf"})


def _player_key(name: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(name or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(
        "".join(char.lower() if char.isalnum() else " " for char in normalized).split()
    )
