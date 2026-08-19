"""Optional, cached API-Football league injury enrichment.

The provider is deliberately read-only and default-off.  A failed or unconfigured
provider is distinct from a successful provider response with no team absence, so
callers can preserve the static fallback only when live data was unavailable.
"""
from __future__ import annotations

import json
import time
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.config import settings
from app.sports.football.football_injury import summarize_injury_impact


@dataclass(frozen=True)
class LiveInjuryImpact:
    """Result of looking up one team in a live league injury snapshot."""

    available: bool
    impact: float | None = None


@dataclass(frozen=True)
class _CachedSnapshot:
    fetched_at: float
    rows: tuple[dict[str, Any], ...]


_SNAPSHOT_CACHE: dict[tuple[str, str], _CachedSnapshot] = {}


def get_live_injury_impact(
    competition: str | None,
    season: str | None,
    team_name: str,
) -> LiveInjuryImpact:
    """Return a live injury impact, or ``available=False`` if it cannot be read.

    ``available=True, impact=None`` means the provider was reached but supplied
    no reportable absence for the requested team.  It is intentionally not a
    known-healthy ``0.0`` signal.
    """
    team_key = _team_key(team_name)
    league_id = _league_id_for(competition)
    season_year = _season_year(season)
    if not team_key or not _is_configured() or not league_id or not season_year:
        return LiveInjuryImpact(available=False)

    snapshot = _snapshot(league_id, season_year)
    if snapshot is None:
        return LiveInjuryImpact(available=False)

    rows = [
        _injury_row(row)
        for row in snapshot
        if _team_key(_team_name(row)) == team_key
    ]
    return LiveInjuryImpact(
        available=True,
        impact=summarize_injury_impact(rows),
    )


def clear_live_injury_cache() -> None:
    """Clear process-local snapshots; used by tests and explicit diagnostics."""
    _SNAPSHOT_CACHE.clear()


def _is_configured() -> bool:
    return bool(
        settings.FOOTBALL_LIVE_INJURIES_ENABLED
        and str(settings.WORLD_CUP_API_FOOTBALL_API_KEY or "").strip()
        and str(settings.WORLD_CUP_API_FOOTBALL_BASE_URL or "").strip()
    )


def _league_id_for(competition: str | None) -> str | None:
    code = str(competition or "").strip().lower()
    if not code:
        return None
    mapping: dict[str, str] = {}
    for item in str(settings.FOOTBALL_LIVE_INJURIES_LEAGUE_IDS or "").split(","):
        key, separator, value = item.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if not separator or not key or not value.isdigit() or int(value) <= 0:
            continue
        mapping[key] = value
    return mapping.get(code)


def _season_year(season: str | None) -> str | None:
    value = str(season or "").strip()
    year = value[:4]
    return year if len(year) == 4 and year.isdigit() else None


def _snapshot(league_id: str, season_year: str) -> tuple[dict[str, Any], ...] | None:
    key = (league_id, season_year)
    now = time.monotonic()
    try:
        ttl_seconds = max(0.0, float(settings.FOOTBALL_LIVE_INJURIES_CACHE_TTL_HOURS)) * 3600
    except (TypeError, ValueError):
        return None
    cached = _SNAPSHOT_CACHE.get(key)
    if cached is not None and now - cached.fetched_at < ttl_seconds:
        return cached.rows

    base_url = str(settings.WORLD_CUP_API_FOOTBALL_BASE_URL or "").strip().rstrip("/")
    api_key = str(settings.WORLD_CUP_API_FOOTBALL_API_KEY or "").strip()
    try:
        max_bytes = int(settings.FOOTBALL_LIVE_INJURIES_MAX_BYTES)
        timeout = max(0.1, float(settings.FOOTBALL_LIVE_INJURIES_TIMEOUT_S))
    except (TypeError, ValueError):
        return None
    if max_bytes <= 0:
        return None

    url = f"{base_url}/injuries?{urlencode({'league': league_id, 'season': season_year})}"
    request = Request(
        url,
        headers={"Accept": "application/json", "x-apisports-key": api_key},
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
    if not isinstance(payload, dict) or payload.get("errors"):
        return None
    response_rows = payload.get("response")
    if not isinstance(response_rows, list):
        return None
    rows = tuple(row for row in response_rows if isinstance(row, dict))
    _SNAPSHOT_CACHE[key] = _CachedSnapshot(fetched_at=now, rows=rows)
    return rows


def _injury_row(row: dict[str, Any]) -> dict[str, str]:
    player = row.get("player")
    player_data = player if isinstance(player, dict) else {}
    status = str(
        row.get("status")
        or player_data.get("status")
        or ("injured" if player_data.get("reason") or player_data.get("type") else "")
    ).strip().lower()
    if status not in {"out", "injured", "suspended", "banned"}:
        return {"status": ""}
    role = str(player_data.get("role") or row.get("role") or "").strip().lower()
    return {
        "status": "out",
        "role": "starter" if role in {"starter", "starting"} else "bench",
    }


def _team_name(row: dict[str, Any]) -> str:
    team = row.get("team")
    if isinstance(team, dict):
        return str(team.get("name") or "")
    return str(team or "")


def _team_key(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(name or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    tokens = "".join(char.lower() if char.isalnum() else " " for char in normalized).split()
    # API-Football commonly omits these fixture-provider suffixes.
    return " ".join(token for token in tokens if token not in {"fc", "cf"})
