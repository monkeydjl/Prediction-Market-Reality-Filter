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
# Free tier is ~5 req/min; leave headroom for other processes / clock skew.
_REQUEST_INTERVAL_SECONDS = 13.0
_MAX_429_RETRIES = 6
_429_BACKOFF_SECONDS = 25.0

# Module-level timestamp of last request for rate limiting
_last_request_time: float = 0.0


class BalldontlieClientError(Exception):
    """balldontlie.io API error."""
    pass


def _enforce_rate_limit() -> None:
    """Sleep if needed to maintain ≥ interval between requests."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < _REQUEST_INTERVAL_SECONDS:
        time.sleep(_REQUEST_INTERVAL_SECONDS - elapsed)
    _last_request_time = time.monotonic()


def _get_games_page(
    api_key: str,
    season: int,
    cursor: int | None,
) -> tuple[list[dict[str, Any]], int | None]:
    """One paginated GET with 429 backoff. Returns (page_rows, next_cursor)."""
    params: dict[str, Any] = {"seasons[]": season, "per_page": 100}
    if cursor is not None:
        params["cursor"] = cursor

    last_status: int | None = None
    for attempt in range(_MAX_429_RETRIES + 1):
        _enforce_rate_limit()
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

        last_status = response.status_code
        if response.status_code == 401:
            raise BalldontlieClientError("API key invalid")
        if response.status_code == 429:
            if attempt >= _MAX_429_RETRIES:
                raise BalldontlieClientError("Rate limit exceeded")
            wait = _429_BACKOFF_SECONDS * (1 + attempt * 0.25)
            logger.warning(
                "balldontlie 429 on season=%s cursor=%s; sleep %.0fs (retry %s/%s)",
                season,
                cursor,
                wait,
                attempt + 1,
                _MAX_429_RETRIES,
            )
            time.sleep(wait)
            # Force next enforce to wait full interval after cooldown.
            global _last_request_time
            _last_request_time = time.monotonic()
            continue
        if response.status_code != 200:
            raise BalldontlieClientError(
                f"API error: {response.status_code} - {response.text[:200]}"
            )

        data = response.json()
        if not isinstance(data, dict):
            raise BalldontlieClientError("balldontlie.io returned non-object JSON")
        page = data.get("data") or []
        if not isinstance(page, list):
            page = []
        meta = data.get("meta") or {}
        next_cursor = meta.get("next_cursor")
        return page, next_cursor

    raise BalldontlieClientError(
        f"Rate limit exceeded (last_status={last_status})"
    )


def fetch_nba_games(season: int) -> list[dict[str, Any]]:
    """Fetch all NBA games for a season from balldontlie.io.

    Uses cursor-based pagination with per_page=100. Retries on HTTP 429.
    If rate limit persists mid-pagination after retries, returns any pages
    already collected (partial) so sync can still persist progress.

    Args:
        season: Season year (e.g., 2025 for 2025-26 season).

    Returns:
        List of raw game dicts from the API response (may be partial).

    Raises:
        BalldontlieClientError: If API key is missing or a non-429 failure
        occurs with zero pages collected.
    """
    api_key = config.settings.BALLDONTLIE_API_KEY
    if not api_key:
        raise BalldontlieClientError("BALLDONTLIE_API_KEY not configured")

    all_games: list[dict[str, Any]] = []
    cursor: int | None = None

    while True:
        try:
            page, next_cursor = _get_games_page(api_key, season, cursor)
        except BalldontlieClientError as exc:
            if all_games and "Rate limit" in str(exc):
                logger.warning(
                    "NBA season %s partial fetch: %s games before rate limit",
                    season,
                    len(all_games),
                )
                return all_games
            raise
        all_games.extend(page)
        if next_cursor is None:
            break
        cursor = next_cursor

    return all_games
