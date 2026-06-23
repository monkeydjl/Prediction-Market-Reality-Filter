"""Service to fetch and manage World Cup match data.

This module handles:
- Fetching match fixtures from API-Football
- Fetching team statistics
- Populating and updating the prediction database
"""

import json
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.world_cup_prediction import MatchFixture, MatchPrediction
from app.utils.prediction_db import get_prediction_session, close_prediction_session


def _clean(value: str | None) -> str:
    """Clean and validate string value."""
    if not value:
        return ""
    return str(value).strip()


def fetch_world_cup_fixtures(
    season: str = "2026",
    league_id: str | None = None
) -> list[dict[str, Any]]:
    """Fetch World Cup fixtures from API-Football.

    Args:
        season: Tournament year (default: 2026)
        league_id: API-Football league ID (default: from settings)

    Returns:
        List of fixture dictionaries
    """

    api_key = _clean(settings.WORLD_CUP_API_FOOTBALL_API_KEY)
    base_url = _clean(settings.WORLD_CUP_API_FOOTBALL_BASE_URL).rstrip("/")
    league = league_id or _clean(settings.WORLD_CUP_API_FOOTBALL_LEAGUE_ID)

    if not api_key or not base_url or not league:
        raise ValueError("API-Football configuration missing")

    url = f"{base_url}/fixtures?league={league}&season={season}"
    request = Request(
        url,
        headers={"Accept": "application/json", "x-apisports-key": api_key}
    )

    try:
        with urlopen(request, timeout=30) as response:
            body = response.read(5 * 1024 * 1024)  # 5MB limit
        data = json.loads(body.decode("utf-8"))

        fixtures = data.get("response", [])
        return fixtures

    except (HTTPError, URLError, TimeoutError) as e:
        raise RuntimeError(f"Failed to fetch fixtures: {e}")


def parse_fixture(fixture_data: dict[str, Any]) -> dict[str, Any] | None:
    """Parse API-Football fixture into our format.

    Args:
        fixture_data: Raw fixture data from API

    Returns:
        Parsed fixture dict or None if invalid
    """

    fixture = fixture_data.get("fixture", {})
    teams = fixture_data.get("teams", {})
    league = fixture_data.get("league", {})

    fixture_id = str(fixture.get("id", ""))
    if not fixture_id:
        return None

    home_team = teams.get("home", {}).get("name", "")
    away_team = teams.get("away", {}).get("name", "")
    if not home_team or not away_team:
        return None

    # Parse kickoff time
    timestamp = fixture.get("timestamp")
    if timestamp:
        kickoff_utc = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    else:
        date_str = fixture.get("date", "")
        try:
            kickoff_utc = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        except:
            return None

    # Determine stage from round info
    round_info = league.get("round", "").lower()
    if "group" in round_info:
        stage = "group_stage"
        # Extract group letter (e.g., "Group A" -> "A")
        group = None
        for char in round_info.upper():
            if char in "ABCDEFGH":
                group = char
                break
    elif "final" in round_info and "semi" not in round_info and "quarter" not in round_info:
        stage = "final"
        group = None
    elif "semi" in round_info or "semi-final" in round_info:
        stage = "semifinal"
        group = None
    elif "quarter" in round_info:
        stage = "quarterfinal"
        group = None
    elif "16" in round_info or "round of 16" in round_info:
        stage = "round_of_16"
        group = None
    else:
        stage = "unknown"
        group = None

    # Match status
    status_short = fixture.get("status", {}).get("short", "")
    if status_short in {"TBD", "NS"}:
        match_status = "scheduled"
    elif status_short in {"1H", "HT", "2H", "ET", "P", "LIVE"}:
        match_status = "in_play"
    elif status_short in {"FT", "AET", "PEN"}:
        match_status = "finished"
    else:
        match_status = "scheduled"

    return {
        "match_id": f"wc2026-{fixture_id}",
        "fixture_id": fixture_id,
        "home_team": home_team,
        "away_team": away_team,
        "kickoff_utc": kickoff_utc,
        "venue": fixture.get("venue", {}).get("name", ""),
        "stage": stage,
        "group": group,
        "status": match_status
    }


def save_fixtures_to_db(fixtures: list[dict[str, Any]]) -> dict[str, int]:
    """Save or update fixtures in database.

    Args:
        fixtures: List of parsed fixtures

    Returns:
        Stats: {"created": int, "updated": int, "skipped": int}
    """

    session = get_prediction_session()
    stats = {"created": 0, "updated": 0, "skipped": 0}

    try:
        for fixture_dict in fixtures:
            match_id = fixture_dict["match_id"]

            # Check if exists
            existing = session.query(MatchFixture).filter_by(match_id=match_id).first()

            if existing:
                # Update if status changed or other fields updated
                if existing.status != fixture_dict["status"]:
                    existing.status = fixture_dict["status"]
                    existing.updated_at = datetime.utcnow()
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1
            else:
                # Create new
                fixture = MatchFixture(**fixture_dict)
                session.add(fixture)
                stats["created"] += 1

        session.commit()

    except Exception as e:
        session.rollback()
        raise RuntimeError(f"Failed to save fixtures: {e}")
    finally:
        close_prediction_session(session)

    return stats


def get_remaining_matches(session: Session | None = None) -> list[MatchFixture]:
    """Get all remaining (not finished) matches.

    Args:
        session: Database session (creates one if None)

    Returns:
        List of MatchFixture objects
    """

    should_close = session is None
    if session is None:
        session = get_prediction_session()

    try:
        now = datetime.utcnow()
        matches = session.query(MatchFixture).filter(
            MatchFixture.status.in_(["scheduled", "in_play"]),
            MatchFixture.kickoff_utc >= now
        ).order_by(MatchFixture.kickoff_utc).all()

        return matches

    finally:
        if should_close:
            close_prediction_session(session)


def sync_world_cup_fixtures() -> dict[str, Any]:
    """Sync World Cup fixtures from API-Football to database.

    Returns:
        Result summary with stats
    """

    try:
        # Get season from settings
        season = _clean(settings.WORLD_CUP_API_FOOTBALL_SEASON) or "2026"

        # Fetch from API
        raw_fixtures = fetch_world_cup_fixtures(season=season)

        # Parse fixtures
        parsed = []
        for raw in raw_fixtures:
            fixture = parse_fixture(raw)
            if fixture:
                parsed.append(fixture)

        # Save to database
        stats = save_fixtures_to_db(parsed)

        # Get remaining matches count
        session = get_prediction_session()
        remaining = get_remaining_matches(session)
        close_prediction_session(session)

        return {
            "status": "ok",
            "fixtures_synced": stats["created"] + stats["updated"],
            "fixtures_fetched": len(raw_fixtures),
            "fixtures_parsed": len(parsed),
            "created": stats["created"],
            "updated": stats["updated"],
            "skipped": stats["skipped"],
            "remaining_matches": len(remaining),
            "season": season
        }

    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }
