# backend/app/sports/baseball/mlb_stats_client.py
"""HTTP client for the official MLB Stats API.

Base URL: https://statsapi.mlb.com/api/v1
Authentication: None (official free API).
Rate limit: 1 req/s (polite usage, not API-enforced).

Endpoints used:
    schedule?startDate=...&endDate=...&sportId=1   — list games by date range
    game/{gamePk}/feedLive                         — full game feed (lineups, scoring)
    people/{personId}                              — player/pitcher stats
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://statsapi.mlb.com/api/v1"
_REQUEST_INTERVAL_SECONDS = 1.0  # 1 req/s polite rate limit

# Module-level timestamp of last request for rate limiting
_last_request_time: float = 0.0


class MLBStatsClientError(Exception):
    """MLB Stats API error."""
    pass


def _enforce_rate_limit() -> None:
    """Sleep if needed to maintain >= 1s between requests."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < _REQUEST_INTERVAL_SECONDS:
        time.sleep(_REQUEST_INTERVAL_SECONDS - elapsed)
    _last_request_time = time.monotonic()


def _request(path: str, params: dict[str, Any] | None = None) -> dict:
    """Issue a GET request to the MLB Stats API.

    Returns the parsed JSON payload (dict). Raises MLBStatsClientError
    on non-200 status, timeout, or network error.
    """
    _enforce_rate_limit()
    url = f"{_BASE_URL}{path}"
    try:
        response = httpx.get(url, params=params, timeout=30.0)
    except httpx.TimeoutException as exc:
        raise MLBStatsClientError(f"Request timeout: {url}") from exc
    except httpx.RequestError as exc:
        raise MLBStatsClientError(f"Request failed: {exc}") from exc

    if response.status_code != 200:
        raise MLBStatsClientError(
            f"MLB API error: {response.status_code} - {response.text[:200]}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise MLBStatsClientError("MLB API returned non-JSON response") from exc


def fetch_mlb_schedule(start_date: str, end_date: str) -> list[dict]:
    """Fetch MLB games in a date range.

    Args:
        start_date: Start date in YYYY-MM-DD format.
        end_date: End date in YYYY-MM-DD format (inclusive).

    Returns:
        List of raw game dicts from the schedule response. Each game dict
        contains ``gamePk``, ``status``, ``teams``, ``gameDate``, etc.
    """
    data = _request(
        "/schedule",
        params={
            "sportId": 1,  # MLB
            "startDate": start_date,
            "endDate": end_date,
        },
    )
    games: list[dict] = []
    for date_entry in data.get("dates", []):
        games.extend(date_entry.get("games", []))
    return games


def fetch_mlb_game_feed(game_pk: int) -> dict:
    """Fetch the full live feed for a single MLB game.

    Args:
        game_pk: MLB gamePk (e.g., 778812).

    Returns:
        Full game feed dict containing ``gameData`` (teams, players, venue)
        and ``liveData`` (plays, scoring, boxscore).
    """
    return _request(f"/game/{game_pk}/feedLive")


def fetch_mlb_pitcher(person_id: int) -> dict:
    """Fetch pitcher stats by person ID.

    Args:
        person_id: MLB person ID (e.g., 543037 for Gerrit Cole).

    Returns:
        Pitcher payload dict containing ``people`` array with stats
        (ERA, WHIP) under ``stats[].splits[].stat``.
    """
    return _request(
        f"/people/{person_id}",
        params={"hydrate": "stats(group=[pitching],type=[season])"},
    )
