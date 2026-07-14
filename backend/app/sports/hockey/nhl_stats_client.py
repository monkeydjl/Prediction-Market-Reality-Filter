# backend/app/sports/hockey/nhl_stats_client.py
"""HTTP client for the official NHL Stats API.

Base URL: https://api-web.nhle.com
Authentication: None (official free API).
Rate limit: 1 req/s (polite usage, not API-enforced).

Endpoints used:
    /v1/schedule/{season}             — list games by season
    /v1/game/{id}/feed/live           — full game feed (scoring, lines)
    /v1/roster/{teamId}/current       — current team roster (goalies + sv%)
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api-web.nhle.com"
_REQUEST_INTERVAL_SECONDS = 1.0  # 1 req/s polite rate limit

# Module-level timestamp of last request for rate limiting
_last_request_time: float = 0.0


class NHLStatsClientError(Exception):
    """NHL Stats API error."""
    pass


def _enforce_rate_limit() -> None:
    """Sleep if needed to maintain >= 1s between requests."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < _REQUEST_INTERVAL_SECONDS:
        time.sleep(_REQUEST_INTERVAL_SECONDS - elapsed)
    _last_request_time = time.monotonic()


def _request(path: str, params: dict[str, Any] | None = None) -> dict:
    """Issue a GET request to the NHL Stats API.

    Returns the parsed JSON payload (dict). Raises NHLStatsClientError
    on non-200 status, timeout, or network error.
    """
    _enforce_rate_limit()
    url = f"{_BASE_URL}{path}"
    try:
        response = httpx.get(url, params=params, timeout=30.0)
    except httpx.TimeoutException as exc:
        raise NHLStatsClientError(f"Request timeout: {url}") from exc
    except httpx.RequestError as exc:
        raise NHLStatsClientError(f"Request failed: {exc}") from exc

    if response.status_code != 200:
        raise NHLStatsClientError(
            f"NHL API error: {response.status_code} - {response.text[:200]}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise NHLStatsClientError("NHL API returned non-JSON response") from exc


def fetch_nhl_schedule(season: str) -> list[dict]:
    """Fetch NHL games for a season.

    Args:
        season: Season key in NHL format (e.g., "20232024" for 2023-24).

    Returns:
        List of raw game dicts. Each game dict contains ``id``,
        ``gameState``, ``homeTeam``, ``awayTeam``, ``gameDate``, etc.
    """
    data = _request(f"/v1/schedule/{season}")
    games: list[dict] = []
    for week in data.get("gameWeek", []):
        games.extend(week.get("games", []))
    return games


def fetch_nhl_game_feed(game_id: int) -> dict:
    """Fetch the full live feed for a single NHL game.

    Args:
        game_id: NHL game ID (e.g., 2023020001).

    Returns:
        Full game feed dict containing ``homeTeam``, ``awayTeam``,
        ``scoringPlays``, ``rosters``, etc.
    """
    return _request(f"/v1/game/{game_id}/feed/live")


def fetch_nhl_team_roster(team_id: int) -> dict:
    """Fetch the current roster for an NHL team.

    Args:
        team_id: NHL team ID (e.g., 1 for New Jersey Devils).

    Returns:
        Roster dict containing ``forwards``, ``defensemen``, and
        ``goalies`` arrays. Each goalie has ``svPct`` (save percentage).
    """
    return _request(f"/v1/roster/{team_id}/current")
