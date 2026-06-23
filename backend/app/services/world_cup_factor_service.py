"""Service to calculate prediction factors from team data and context.

This module extracts and transforms raw team statistics into normalized
factors used by the prediction engines.
"""

from datetime import datetime, timedelta
from typing import Any


def calculate_team_factors(
    team_name: str,
    team_stats: dict[str, Any],
    is_home: bool = False
) -> dict[str, Any]:
    """Calculate normalized factors for a team.

    Args:
        team_name: Team name
        team_stats: Raw statistics from API
        is_home: Whether team is playing at home

    Returns:
        Dictionary of normalized factors
    """

    # Extract basic stats
    goals_per_game = team_stats.get("goals_per_game", 1.5)
    goals_conceded_per_game = team_stats.get("goals_conceded_per_game", 1.2)
    wins = team_stats.get("wins", 0)
    draws = team_stats.get("draws", 0)
    losses = team_stats.get("losses", 0)
    matches_played = wins + draws + losses

    # Calculate recent form (win rate with recency weighting)
    recent_form = 0.5  # Default neutral
    if matches_played > 0:
        recent_form = (wins + 0.5 * draws) / matches_played

    # Defense rating (inverse of goals conceded, normalized)
    # Assume avg team concedes ~1.2 goals per game
    defense_rating = max(0.0, min(1.0, 1.0 - (goals_conceded_per_game - 0.8) / 2.0))

    # Injury impact (negative modifier)
    injury_impact = team_stats.get("injury_impact", 0.0)

    # Fatigue level based on days since last match
    last_match_date = team_stats.get("last_match_date")
    days_since_last_match = 7  # Default
    if last_match_date:
        try:
            last_date = datetime.fromisoformat(last_match_date.replace('Z', '+00:00'))
            days_since_last_match = (datetime.utcnow() - last_date).days
        except:
            pass

    # Home advantage modifier
    home_advantage = 0.10 if is_home else 0.0

    return {
        "fifa_ranking": team_stats.get("fifa_ranking"),
        "recent_form": round(recent_form, 3),
        "goals_per_game": round(goals_per_game, 2),
        "goals_conceded_per_game": round(goals_conceded_per_game, 2),
        "defense_rating": round(defense_rating, 3),
        "injury_impact": round(injury_impact, 3),
        "days_since_last_match": days_since_last_match,
        "home_advantage": home_advantage,
        "matches_played": matches_played,
        "wins": wins,
        "draws": draws,
        "losses": losses
    }


def calculate_head_to_head_factors(h2h_data: dict[str, Any] | None) -> dict[str, Any]:
    """Calculate head-to-head factors from historical matchups.

    Args:
        h2h_data: Historical head-to-head data

    Returns:
        Normalized h2h factors
    """

    if not h2h_data:
        return {
            "matches_played": 0,
            "home_wins": 0,
            "draws": 0,
            "away_wins": 0,
            "avg_goals_home": 1.5,
            "avg_goals_away": 1.5
        }

    return {
        "matches_played": h2h_data.get("matches_played", 0),
        "home_wins": h2h_data.get("home_wins", 0),
        "draws": h2h_data.get("draws", 0),
        "away_wins": h2h_data.get("away_wins", 0),
        "avg_goals_home": round(h2h_data.get("avg_goals_home", 1.5), 2),
        "avg_goals_away": round(h2h_data.get("avg_goals_away", 1.5), 2)
    }


def calculate_context_factors(
    stage: str,
    home_team_standing: dict[str, Any] | None = None,
    away_team_standing: dict[str, Any] | None = None,
    weather: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Calculate contextual factors for the match.

    Args:
        stage: Tournament stage
        home_team_standing: Current group standing for home team
        away_team_standing: Current group standing for away team
        weather: Weather conditions

    Returns:
        Context factors including stakes level
    """

    # Determine stakes level based on stage and standings
    stakes = "medium"

    if stage in {"final", "semifinal"}:
        stakes = "high"
    elif stage in {"quarterfinal", "round_of_16"}:
        stakes = "medium"
    elif stage == "group_stage":
        # Check if must-win situation based on standings
        if home_team_standing or away_team_standing:
            # Simplified logic: if team is bottom of group in last match, it's must-win
            stakes = "medium"
            # Could add more sophisticated logic here

    return {
        "tournament_stage": stage,
        "stakes": stakes,
        "weather": weather.get("condition", "clear") if weather else "clear",
        "temperature_c": weather.get("temperature", 25) if weather else 25
    }


def build_prediction_factors(
    home_team_name: str,
    away_team_name: str,
    home_team_stats: dict[str, Any],
    away_team_stats: dict[str, Any],
    stage: str,
    h2h_data: dict[str, Any] | None = None,
    home_standing: dict[str, Any] | None = None,
    away_standing: dict[str, Any] | None = None,
    weather: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build complete factor dictionary for prediction engines.

    Args:
        home_team_name: Home team name
        away_team_name: Away team name
        home_team_stats: Home team statistics
        away_team_stats: Away team statistics
        stage: Tournament stage
        h2h_data: Head-to-head historical data
        home_standing: Home team's current group standing
        away_standing: Away team's current group standing
        weather: Weather conditions

    Returns:
        Complete factors dictionary ready for prediction
    """

    return {
        "home_team": calculate_team_factors(home_team_name, home_team_stats, is_home=True),
        "away_team": calculate_team_factors(away_team_name, away_team_stats, is_home=False),
        "head_to_head": calculate_head_to_head_factors(h2h_data),
        "context": calculate_context_factors(stage, home_standing, away_standing, weather)
    }
