"""The Odds API integration for betting odds data.

API: https://the-odds-api.com/
Free tier: 500 requests/month
Covers: FIFA World Cup 2026 odds (1x2 markets)
"""

import httpx
import logging
from typing import Any
from datetime import datetime, timedelta

from app.core.config import settings

logger = logging.getLogger(__name__)

# API Configuration
ODDS_API_KEY = settings.ODDS_API_KEY if hasattr(settings, 'ODDS_API_KEY') else None
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORT = "soccer_fifa_world_cup"  # Sport key for World Cup
REGIONS = "us,eu"  # US and European bookmakers
MARKETS = "h2h"  # Head-to-head (1x2: home/draw/away)
ODDS_FORMAT = "decimal"  # Decimal odds (e.g., 2.10)

# In-memory quota tracker (updated from API response headers)
_quota_remaining: int | None = None
_quota_last_checked: datetime | None = None


def get_quota_status() -> dict[str, Any]:
    """Get current quota status (from last API response)."""
    return {
        "remaining": _quota_remaining,
        "last_checked": _quota_last_checked.isoformat() if _quota_last_checked else None,
        "enabled": bool(ODDS_API_KEY),
    }


def _update_quota_from_headers(headers: httpx.Headers) -> None:
    """Update in-memory quota tracker from API response headers."""
    global _quota_remaining, _quota_last_checked
    remaining = headers.get("x-requests-remaining")
    if remaining is not None:
        _quota_remaining = int(remaining)
        _quota_last_checked = datetime.now(timezone.utc)
        if _quota_remaining < 50:
            logger.warning("Odds API quota low: %d remaining", _quota_remaining)


async def fetch_match_odds(
    home_team: str,
    away_team: str,
    commence_time: str | datetime | None = None
) -> dict[str, Any] | None:
    """Fetch betting odds for a specific match from The Odds API.

    Args:
        home_team: Home team name
        away_team: Away team name
        commence_time: Match kickoff time (used to find the right fixture)

    Returns:
        {
            "home": 2.10,
            "draw": 3.20,
            "away": 3.50,
            "source": "pinnacle",
            "last_update": "2026-06-24T10:00:00Z",
            "bookmakers_count": 15
        }
        None if odds not available or API error
    """
    if not ODDS_API_KEY:
        logger.debug("Odds API key not configured, skipping fetch")
        return None

    # Respect ODDS_API_ENABLED config flag
    if hasattr(settings, 'ODDS_API_ENABLED') and not settings.ODDS_API_ENABLED:
        logger.debug("Odds API disabled in config (ODDS_API_ENABLED=false), skipping fetch")
        return None

    # Skip if quota is known to be exhausted
    if _quota_remaining is not None and _quota_remaining <= 0:
        logger.debug("Odds API quota exhausted, skipping fetch")
        return None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Fetch all upcoming World Cup matches with odds
            response = await client.get(
                f"{ODDS_API_BASE}/sports/{SPORT}/odds",
                params={
                    "apiKey": ODDS_API_KEY,
                    "regions": REGIONS,
                    "markets": MARKETS,
                    "oddsFormat": ODDS_FORMAT,
                }
            )

            # Update quota from headers regardless of status code
            _update_quota_from_headers(response.headers)

            if response.status_code == 422:
                # Quota exceeded or invalid sport key
                logger.warning("Odds API returned 422: %s", response.text[:200])
                return None

            if response.status_code == 401:
                logger.error("Odds API key invalid (401)")
                return None

            if response.status_code != 200:
                logger.warning("Odds API returned %d: %s", response.status_code, response.text[:200])
                return None

            data = response.json()

            # Find the matching fixture
            match = find_matching_fixture(
                data,
                home_team,
                away_team,
                commence_time
            )

            if not match:
                return None

            # Extract best odds (use sharpest bookmaker)
            odds = extract_best_odds(match)
            return odds

    except Exception as e:
        logger.debug("Odds API fetch error: %s", e)
        return None


def find_matching_fixture(
    fixtures: list[dict],
    home_team: str,
    away_team: str,
    commence_time: str | datetime | None
) -> dict | None:
    """Find the fixture matching the given teams and time.

    Args:
        fixtures: List of fixtures from API
        home_team: Home team name
        away_team: Away team name
        commence_time: Match kickoff time

    Returns:
        Matching fixture or None
    """
    # Normalize team names for matching
    home_normalized = normalize_team_name(home_team)
    away_normalized = normalize_team_name(away_team)

    # Convert commence_time to datetime if string
    target_time = None
    if isinstance(commence_time, str):
        try:
            target_time = datetime.fromisoformat(commence_time.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            pass
    elif isinstance(commence_time, datetime):
        target_time = commence_time

    for fixture in fixtures:
        fixture_home = normalize_team_name(fixture.get("home_team", ""))
        fixture_away = normalize_team_name(fixture.get("away_team", ""))

        # Check team names match
        teams_match = (
            fixture_home == home_normalized and fixture_away == away_normalized
        )

        if not teams_match:
            continue

        # If time provided, check it's within 6 hours window
        if target_time:
            fixture_time_str = fixture.get("commence_time")
            if fixture_time_str:
                try:
                    fixture_time = datetime.fromisoformat(
                        fixture_time_str.replace('Z', '+00:00')
                    )
                    time_diff = abs((fixture_time - target_time).total_seconds())
                    if time_diff > 6 * 3600:  # 6 hours tolerance
                        continue
                except (ValueError, TypeError):
                    pass

        return fixture

    return None


def normalize_team_name(name: str) -> str:
    """Normalize team name for matching.

    Args:
        name: Raw team name

    Returns:
        Normalized name (lowercase, no spaces/special chars)
    """
    return name.lower().replace(" ", "").replace("-", "").replace("'", "")


def extract_best_odds(fixture: dict) -> dict[str, Any]:
    """Extract best available odds from fixture data.

    Uses the sharpest bookmaker (Pinnacle preferred) or averages
    across multiple bookmakers.

    Args:
        fixture: Fixture data from API with bookmaker odds

    Returns:
        {
            "home": float,
            "draw": float,
            "away": float,
            "source": str,
            "last_update": str,
            "bookmakers_count": int
        }
    """
    bookmakers = fixture.get("bookmakers", [])

    if not bookmakers:
        return {
            "home": 2.5,
            "draw": 3.2,
            "away": 3.0,
            "source": "default",
            "last_update": datetime.now(timezone.utc).isoformat(),
            "bookmakers_count": 0
        }

    # Priority: Pinnacle (sharpest) > Average of all
    pinnacle = None
    all_odds = []

    for bookmaker in bookmakers:
        bookmaker_name = bookmaker.get("key", "").lower()
        markets = bookmaker.get("markets", [])

        for market in markets:
            if market.get("key") != "h2h":
                continue

            outcomes = market.get("outcomes", [])
            if len(outcomes) != 3:
                continue

            # Parse odds
            odds_dict = {}
            for outcome in outcomes:
                outcome_name = outcome.get("name", "").lower()
                price = outcome.get("price")

                if "draw" in outcome_name or outcome_name == "draw":
                    odds_dict["draw"] = price
                elif outcome_name == fixture.get("home_team", "").lower():
                    odds_dict["home"] = price
                elif outcome_name == fixture.get("away_team", "").lower():
                    odds_dict["away"] = price

            if len(odds_dict) == 3:
                all_odds.append({
                    "bookmaker": bookmaker.get("title", bookmaker_name),
                    "odds": odds_dict,
                    "last_update": bookmaker.get("last_update")
                })

                if "pinnacle" in bookmaker_name:
                    pinnacle = all_odds[-1]

    # Use Pinnacle if available
    if pinnacle:
        return {
            "home": pinnacle["odds"]["home"],
            "draw": pinnacle["odds"]["draw"],
            "away": pinnacle["odds"]["away"],
            "source": "pinnacle",
            "last_update": pinnacle["last_update"],
            "bookmakers_count": len(all_odds)
        }

    # Otherwise, average across all bookmakers
    if all_odds:
        avg_home = sum(o["odds"]["home"] for o in all_odds) / len(all_odds)
        avg_draw = sum(o["odds"]["draw"] for o in all_odds) / len(all_odds)
        avg_away = sum(o["odds"]["away"] for o in all_odds) / len(all_odds)

        return {
            "home": round(avg_home, 2),
            "draw": round(avg_draw, 2),
            "away": round(avg_away, 2),
            "source": f"average_{len(all_odds)}_bookmakers",
            "last_update": all_odds[0]["last_update"],
            "bookmakers_count": len(all_odds)
        }

    # Fallback
    return {
        "home": 2.5,
        "draw": 3.2,
        "away": 3.0,
        "source": "fallback",
        "last_update": datetime.now(timezone.utc).isoformat(),
        "bookmakers_count": 0
    }


async def get_available_quota() -> dict[str, int] | None:
    """Check remaining API quota.

    Returns:
        {
            "requests_remaining": 450,
            "requests_used": 50
        }
        None if API key not configured or error
    """
    if not ODDS_API_KEY:
        return None

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Make a lightweight request to check quota
            response = await client.get(
                f"{ODDS_API_BASE}/sports",
                params={"apiKey": ODDS_API_KEY}
            )

            # Quota info is in response headers
            remaining = response.headers.get("x-requests-remaining")
            used = response.headers.get("x-requests-used")

            if remaining and used:
                return {
                    "requests_remaining": int(remaining),
                    "requests_used": int(used)
                }

    except Exception:
        pass

    return None


# Team name mapping (API names -> Our names)
TEAM_NAME_MAPPING = {
    "united states": "usa",
    "korea republic": "south korea",
    "iran islamic republic": "iran",
    # Add more mappings as needed
}


def map_team_name(api_name: str) -> str:
    """Map API team name to our internal name.

    Args:
        api_name: Team name from The Odds API

    Returns:
        Our internal team name
    """
    normalized = normalize_team_name(api_name)
    return TEAM_NAME_MAPPING.get(normalized, api_name)
