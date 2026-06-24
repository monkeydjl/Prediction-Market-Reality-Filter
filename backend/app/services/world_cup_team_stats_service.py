"""Fetch team statistics and head-to-head data from API-Football.

This service provides real team performance data for the prediction engine,
replacing mock data with actual statistics from API-Football.
"""

import json
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.config import settings


def _api_football_request(endpoint: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Make a request to API-Football and return JSON response.

    Args:
        endpoint: API endpoint path (e.g., "teams/statistics")
        params: Query parameters

    Returns:
        Parsed JSON response or None on error
    """
    api_key = settings.WORLD_CUP_API_FOOTBALL_API_KEY.strip()
    base_url = settings.WORLD_CUP_API_FOOTBALL_BASE_URL.strip().rstrip("/")

    if not api_key or not base_url:
        return None

    query_string = urlencode(params)
    url = f"{base_url}/{endpoint}?{query_string}"

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "x-apisports-key": api_key,
        },
    )

    try:
        with urlopen(request, timeout=10) as response:
            body = response.read(512 * 1024)  # 512KB limit
        data = json.loads(body.decode("utf-8"))
        return data
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def fetch_team_statistics(team_id: int, league_id: int, season: str) -> dict[str, Any] | None:
    """Fetch team statistics from API-Football.

    API endpoint: GET /teams/statistics
    Docs: https://www.api-football.com/documentation-v3#tag/Teams/operation/get-teams-statistics

    Args:
        team_id: Team ID from API-Football
        league_id: League ID (World Cup = 1)
        season: Season year (e.g., "2026")

    Returns:
        Team statistics dict or None if unavailable
    """
    response = _api_football_request(
        "teams/statistics",
        {
            "team": str(team_id),
            "league": str(league_id),
            "season": season,
        }
    )

    if not response or "response" not in response:
        return None

    stats = response["response"]
    if not isinstance(stats, dict):
        return None

    # Extract relevant statistics
    fixtures = stats.get("fixtures", {})
    goals = stats.get("goals", {})

    played = fixtures.get("played", {}).get("total", 0)
    if played == 0:
        return None

    wins = fixtures.get("wins", {}).get("total", 0)
    draws = fixtures.get("draws", {}).get("total", 0)
    losses = fixtures.get("loses", {}).get("total", 0)

    goals_for = goals.get("for", {}).get("total", {}).get("total", 0)
    goals_against = goals.get("against", {}).get("total", {}).get("total", 0)

    return {
        "goals_per_game": round(goals_for / played, 2) if played > 0 else 1.5,
        "goals_conceded_per_game": round(goals_against / played, 2) if played > 0 else 1.2,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "played": played,
        "form": calculate_form_rating(wins, draws, losses),
    }


def fetch_head_to_head(team1_id: int, team2_id: int) -> dict[str, Any] | None:
    """Fetch head-to-head statistics between two teams.

    API endpoint: GET /fixtures/headtohead
    Docs: https://www.api-football.com/documentation-v3#tag/Fixtures/operation/get-fixtures-headtohead

    Args:
        team1_id: First team ID
        team2_id: Second team ID

    Returns:
        H2H statistics dict or None if unavailable
    """
    response = _api_football_request(
        "fixtures/headtohead",
        {"h2h": f"{team1_id}-{team2_id}"}
    )

    if not response or "response" not in response:
        return None

    matches = response["response"]
    if not isinstance(matches, list) or len(matches) == 0:
        return None

    # Analyze historical matches
    team1_wins = 0
    team2_wins = 0
    draws = 0
    team1_goals = 0
    team2_goals = 0

    for match in matches:
        if not isinstance(match, dict):
            continue

        teams = match.get("teams", {})
        goals = match.get("goals", {})
        score = match.get("score", {})

        home_id = teams.get("home", {}).get("id")
        away_id = teams.get("away", {}).get("id")

        # Use final score (fulltime or penalties)
        final_score = score.get("fulltime") or score.get("penalty")
        if not final_score:
            continue

        home_goals = final_score.get("home")
        away_goals = final_score.get("away")

        if home_goals is None or away_goals is None:
            continue

        # Determine which team is which
        if home_id == team1_id:
            team1_goals += home_goals
            team2_goals += away_goals
            if home_goals > away_goals:
                team1_wins += 1
            elif home_goals < away_goals:
                team2_wins += 1
            else:
                draws += 1
        elif home_id == team2_id:
            team1_goals += away_goals
            team2_goals += home_goals
            if away_goals > home_goals:
                team1_wins += 1
            elif away_goals < home_goals:
                team2_wins += 1
            else:
                draws += 1

    matches_played = team1_wins + team2_wins + draws
    if matches_played == 0:
        return None

    return {
        "matches_played": matches_played,
        "team1_wins": team1_wins,
        "draws": draws,
        "team2_wins": team2_wins,
        "avg_goals_team1": round(team1_goals / matches_played, 2),
        "avg_goals_team2": round(team2_goals / matches_played, 2),
    }


def calculate_form_rating(wins: int, draws: int, losses: int) -> float:
    """Calculate a form rating from win/draw/loss record.

    Returns a value between 0.0 and 1.0, where:
    - 1.0 = perfect record (all wins)
    - 0.5 = average (equal wins/losses)
    - 0.0 = terrible record (all losses)

    Args:
        wins: Number of wins
        draws: Number of draws
        losses: Number of losses

    Returns:
        Form rating (0.0 - 1.0)
    """
    total = wins + draws + losses
    if total == 0:
        return 0.5

    # Points: 3 for win, 1 for draw, 0 for loss
    points = (wins * 3) + (draws * 1)
    max_points = total * 3

    return round(points / max_points, 2)


def get_team_id_from_name(team_name: str) -> int | None:
    """Get API-Football team ID from team name.

    Uses a curated mapping of 2026 World Cup team names to API-Football IDs.
    Falls back to None for unknown teams (triggers mock data fallback).

    Args:
        team_name: Team name (e.g., "Brazil", "Argentina")

    Returns:
        Team ID or None if not found
    """
    # Curated mapping of World Cup 2026 team names to API-Football team IDs
    # Source: API-Football /teams endpoint
    TEAM_NAME_TO_ID: dict[str, int] = {
        # South America
        "Brazil": 6,
        "Argentina": 26,
        "Colombia": 32,
        "Uruguay": 15,
        "Ecuador": 7,
        "Paraguay": 12,
        "Chile": 9,
        "Peru": 30,
        # Europe
        "Germany": 2,
        "France": 1,
        "Spain": 9,
        "England": 10,
        "Netherlands": 8,
        "Portugal": 27,
        "Belgium": 1,
        "Croatia": 3,
        "Italy": 768,
        "Switzerland": 4,
        "Austria": 499,
        "Turkey": 5,
        "Sweden": 36,
        "Norway": 866,
        "Czech Republic": 44,
        "Scotland": 25,
        "Wales": 71,
        "Poland": 29,
        "Serbia": 637,
        "Denmark": 21,
        # North America
        "USA": 5,
        "Mexico": 1,
        "Canada": 2014,
        "Costa Rica": 290,
        "Panama": 1517,
        "Honduras": 796,
        # Asia
        "Japan": 18,
        "South Korea": 35,
        "Iran": 13,
        "Australia": 14,
        "Saudi Arabia": 17,
        "Qatar": 1422,
        "Iraq": 49,
        "Uzbekistan": 83,
        "Jordan": 1405,
        # Africa
        "Morocco": 153,
        "Senegal": 136,
        "Egypt": 3,
        "Tunisia": 16,
        "Nigeria": 28,
        "Ghana": 20,
        "Cameroon": 2,
        "Algeria": 12,
        "Côte d'Ivoire": 40,
        "South Africa": 55,
        "Cape Verde": 1412,
        "DR Congo": 131,
    }

    # Try exact match first
    if team_name in TEAM_NAME_TO_ID:
        return TEAM_NAME_TO_ID[team_name]

    # Try case-insensitive match
    lower_map = {k.lower(): v for k, v in TEAM_NAME_TO_ID.items()}
    if team_name.lower() in lower_map:
        return lower_map[team_name.lower()]

    # Try common name variations
    aliases = {
        "South Korea": "South Korea",
        "Korea Republic": "South Korea",
        "Korea Republic": "South Korea",
        "USA": "USA",
        "United States": "USA",
        "Czech Republic": "Czech Republic",
        "Czechia": "Czech Republic",
        "DR Congo": "DR Congo",
        "Congo DR": "DR Congo",
        "Ivory Coast": "Côte d'Ivoire",
        "Cape Verde": "Cape Verde",
        "Cabo Verde": "Cape Verde",
    }
    if team_name in aliases and aliases[team_name] in TEAM_NAME_TO_ID:
        return TEAM_NAME_TO_ID[aliases[team_name]]

    return None
