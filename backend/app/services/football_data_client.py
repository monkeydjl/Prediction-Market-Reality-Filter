"""Parameterized Football-Data.org client.

Generic client for the Football-Data.org v4 API. Supports any competition
code (WC, CL, PL, etc.) without hardcoding. Does NOT import world_cup_*
modules — it is a clean, independent client.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)


class FootballDataClientError(Exception):
    """Football-Data.org API error."""
    pass


def fetch_competition_fixtures(
    competition: str,
    season: int = 2026,
) -> list[dict[str, Any]]:
    """Fetch fixtures for a competition from Football-Data.org.

    Args:
        competition: Football-Data.org competition code (e.g., "CL", "PL", "WC").
        season: Season year (default: 2026).

    Returns:
        List of raw match dicts from the API response.

    Raises:
        FootballDataClientError: If API key is missing or request fails.
    """
    api_key = settings.FOOTBALL_DATA_API_KEY
    if not api_key:
        raise FootballDataClientError("FOOTBALL_DATA_API_KEY not configured")

    base_url = str(settings.FOOTBALL_DATA_BASE_URL or "").rstrip("/")
    url = f"{base_url}/competitions/{competition}/matches"

    data = _football_data_get(url, params={"season": season})
    matches = data.get("matches", [])
    return matches


def parse_fixture(
    match_data: dict[str, Any],
    stage_mapping: dict[str, str] | None = None,
    match_id_prefix: str = "fd-",
) -> dict[str, Any] | None:
    """Parse a raw Football-Data.org match dict into internal fixture format.

    Args:
        match_data: Raw match dict from the API.
        stage_mapping: Maps API stage names to internal canonical names.
            If None, all stages map to "regular_season".
        match_id_prefix: Prefix for the internal match_id (e.g., "ucl-", "epl-").

    Returns:
        Parsed fixture dict or None if match_data is malformed.
    """
    match_id = match_data.get("id")
    if not match_id:
        return None

    home_team = match_data.get("homeTeam", {}).get("name", "")
    away_team = match_data.get("awayTeam", {}).get("name", "")

    if not home_team or not away_team:
        return None

    utc_date = match_data.get("utcDate", "")
    try:
        kickoff_utc = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None

    stage_raw = (match_data.get("stage", "") or "").upper()
    if stage_mapping and stage_raw in stage_mapping:
        stage = stage_mapping[stage_raw]
    elif stage_mapping is None:
        stage = "regular_season"
    else:
        stage = stage_raw.lower()

    group = match_data.get("group")

    status_raw = match_data.get("status", "")
    status_mapping = {
        "TIMED": "scheduled",
        "SCHEDULED": "scheduled",
        "IN_PLAY": "in_play",
        "LIVE": "in_play",
        "PAUSED": "in_play",
        "FINISHED": "finished",
        "AWARDED": "finished",
        "POSTPONED": "postponed",
        "CANCELLED": "cancelled",
        "SUSPENDED": "suspended",
    }
    match_status = status_mapping.get(status_raw, "scheduled")

    venue_name = match_data.get("venue", "")

    score = match_data.get("score", {})
    fulltime = score.get("fullTime", {})
    home_score = fulltime.get("home")
    away_score = fulltime.get("away")

    return {
        "match_id": f"{match_id_prefix}{match_id}",
        "fixture_id": str(match_id),
        "home_team": home_team,
        "away_team": away_team,
        "kickoff_utc": kickoff_utc,
        "venue": venue_name or "Unknown",
        "stage": stage,
        "group": group,
        "status": match_status,
        "home_score": home_score,
        "away_score": away_score,
    }


def _football_data_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """HTTP GET with X-Auth-Token header.

    Reuses the same authentication and error-handling pattern as the
    existing football_data_source.py but lives in a separate module.
    """
    api_key = settings.FOOTBALL_DATA_API_KEY
    if not api_key:
        raise FootballDataClientError("FOOTBALL_DATA_API_KEY not configured")

    try:
        response = httpx.get(
            url,
            headers={"X-Auth-Token": api_key},
            params=params,
            timeout=30.0,
        )

        if response.status_code == 403:
            raise FootballDataClientError("API key invalid or access forbidden")
        if response.status_code == 429:
            raise FootballDataClientError("Rate limit exceeded (10 requests/minute)")
        if response.status_code != 200:
            raise FootballDataClientError(
                f"API error: {response.status_code} - {response.text[:200]}"
            )

        data = response.json()
        if not isinstance(data, dict):
            raise FootballDataClientError("Football-Data.org returned non-object JSON")
        return data
    except httpx.TimeoutException as exc:
        raise FootballDataClientError("Request timeout") from exc
    except httpx.RequestError as exc:
        raise FootballDataClientError(f"Request failed: {exc}") from exc
