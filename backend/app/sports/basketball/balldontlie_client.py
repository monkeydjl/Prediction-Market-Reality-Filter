# backend/app/sports/basketball/balldontlie_client.py
"""HTTP client for balldontlie.io NBA API.

Free tier: 5 req/min. Provides Teams, Players, Games endpoints.
Auth: Authorization header with API key (no Bearer prefix).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from app.core import config

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.balldontlie.io/v1"
_REQUEST_INTERVAL_SECONDS = 12.0  # 5 req/min → 12s between requests

# Module-level timestamp of last request for rate limiting
_last_request_time: float = 0.0


class BalldontlieClientError(Exception):
    """balldontlie.io API error."""
    pass


def _enforce_rate_limit() -> None:
    """Sleep if needed to maintain ≥ 12s between requests."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < _REQUEST_INTERVAL_SECONDS:
        time.sleep(_REQUEST_INTERVAL_SECONDS - elapsed)
    _last_request_time = time.monotonic()


def fetch_nba_games(season: int) -> list[dict[str, Any]]:
    """Fetch all NBA games for a season from balldontlie.io.

    Uses cursor-based pagination with per_page=100.

    Args:
        season: Season year (e.g., 2023 for 2023-24 season).

    Returns:
        List of raw game dicts from the API response.

    Raises:
        BalldontlieClientError: If API key is missing or request fails.
    """
    api_key = config.settings.BALLDONTLIE_API_KEY
    if not api_key:
        raise BalldontlieClientError("BALLDONTLIE_API_KEY not configured")

    all_games: list[dict[str, Any]] = []
    cursor: int | None = None

    while True:
        _enforce_rate_limit()
        params: dict[str, Any] = {"seasons[]": season, "per_page": 100}
        if cursor is not None:
            params["cursor"] = cursor

        try:
            response = httpx.get(
                f"{_BASE_URL}/games",
                headers={"Authorization": api_key},
                params=params,
                timeout=30.0,
            )
        except httpx.TimeoutException as exc:
            raise BalldontlieClientError("Request timeout") from exc
        except httpx.RequestError as exc:
            raise BalldontlieClientError(f"Request failed: {exc}") from exc

        if response.status_code == 401:
            raise BalldontlieClientError("API key invalid")
        if response.status_code == 429:
            raise BalldontlieClientError("Rate limit exceeded")
        if response.status_code != 200:
            raise BalldontlieClientError(
                f"API error: {response.status_code} - {response.text[:200]}"
            )

        data = response.json()
        if not isinstance(data, dict):
            raise BalldontlieClientError("balldontlie.io returned non-object JSON")

        all_games.extend(data.get("data", []))

        meta = data.get("meta", {})
        next_cursor = meta.get("next_cursor")
        if next_cursor is None:
            break
        cursor = next_cursor

    return all_games
