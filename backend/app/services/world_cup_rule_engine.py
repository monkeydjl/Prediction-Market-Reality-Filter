"""Rule-based prediction engine using statistical models.

This module implements the core mathematical models for predicting match scores:
- Poisson distribution for expected goals
- ELO-based team strength ratings
- Home advantage, form, and fatigue adjustments
"""

import math
from typing import Any


def poisson_probability(expected_goals: float, actual_goals: int) -> float:
    """Calculate Poisson probability for a given goal count."""
    return (math.exp(-expected_goals) * (expected_goals ** actual_goals)) / math.factorial(actual_goals)


def calculate_outcome_probabilities(home_xg: float, away_xg: float, max_goals: int = 8) -> dict[str, float]:
    """Calculate win/draw/loss probabilities from expected goals using Poisson.

    Returns probabilities as decimals (0.0 to 1.0), not percentages.
    """
    home_win = 0.0
    draw = 0.0
    away_win = 0.0

    for home_goals in range(max_goals + 1):
        home_prob = poisson_probability(home_xg, home_goals)
        for away_goals in range(max_goals + 1):
            away_prob = poisson_probability(away_xg, away_goals)
            joint_prob = home_prob * away_prob

            if home_goals > away_goals:
                home_win += joint_prob
            elif home_goals < away_goals:
                away_win += joint_prob
            else:
                draw += joint_prob

    return {
        "home_win": round(home_win, 4),
        "draw": round(draw, 4),
        "away_win": round(away_win, 4)
    }


def calculate_expected_goals(
    team_attack: float,
    team_defense: float,
    opponent_attack: float,
    opponent_defense: float,
    is_home: bool = False,
    form_factor: float = 1.0,
    fatigue_factor: float = 1.0,
    injury_impact: float = 0.0
) -> float:
    """Calculate expected goals for a team.

    Args:
        team_attack: Team's attack rating (goals per game)
        team_defense: Team's defense rating (goals conceded per game)
        opponent_attack: Opponent's attack rating
        opponent_defense: Opponent's defense rating
        is_home: Whether team is playing at home
        form_factor: Recent form multiplier (0.5 - 1.5)
        fatigue_factor: Fatigue multiplier based on days since last match (0.8 - 1.1)
        injury_impact: Injury impact modifier (-0.3 to 0)

    Returns:
        Expected goals (typically 0.5 - 4.0)
    """
    # Base expected goals: team attack vs opponent defense
    base_xg = (team_attack + opponent_defense) / 2.0

    # Home advantage: +0.3 expected goals
    if is_home:
        base_xg += 0.3

    # Apply modifiers
    xg = base_xg * form_factor * fatigue_factor
    xg += injury_impact

    # Clamp to reasonable range
    return max(0.1, min(xg, 5.0))


def predict_score_rule_based(factors: dict[str, Any]) -> dict[str, Any]:
    """Generate score prediction using rule-based models.

    Args:
        factors: Dictionary containing team factors, context, head-to-head

    Returns:
        {
            "predicted_score": {"home": float, "away": float},
            "outcome_probabilities": {"home_win": float, "draw": float, "away_win": float},
            "confidence": float
        }
    """
    home = factors.get("home_team", {})
    away = factors.get("away_team", {})
    context = factors.get("context", {})

    # Extract team metrics
    home_attack = home.get("goals_per_game", 1.5)
    home_defense = home.get("goals_conceded_per_game", 1.2)
    away_attack = away.get("goals_per_game", 1.4)
    away_defense = away.get("goals_conceded_per_game", 1.3)

    # Form factors (recent performance)
    home_form = 1.0 + (home.get("recent_form", 0.5) - 0.5)  # 0.5-1.5 range
    away_form = 1.0 + (away.get("recent_form", 0.5) - 0.5)

    # Fatigue factors (days since last match)
    home_rest_days = home.get("days_since_last_match", 7)
    away_rest_days = away.get("days_since_last_match", 7)
    home_fatigue = 1.0 if home_rest_days >= 4 else (0.85 + home_rest_days * 0.0375)
    away_fatigue = 1.0 if away_rest_days >= 4 else (0.85 + away_rest_days * 0.0375)

    # Injury impacts
    home_injury = home.get("injury_impact", 0.0)
    away_injury = away.get("injury_impact", 0.0)

    # Calculate expected goals
    home_xg = calculate_expected_goals(
        home_attack, home_defense, away_attack, away_defense,
        is_home=True,
        form_factor=home_form,
        fatigue_factor=home_fatigue,
        injury_impact=home_injury
    )

    away_xg = calculate_expected_goals(
        away_attack, away_defense, home_attack, home_defense,
        is_home=False,
        form_factor=away_form,
        fatigue_factor=away_fatigue,
        injury_impact=away_injury
    )

    # Adjust for stakes (must-win situations increase attack)
    stakes = context.get("stakes", "medium")
    if stakes == "must_win":
        home_xg *= 1.15
        away_xg *= 1.15
    elif stakes == "high":
        home_xg *= 1.08
        away_xg *= 1.08

    # Calculate outcome probabilities
    outcome_probs = calculate_outcome_probabilities(home_xg, away_xg)

    # Confidence based on data quality
    confidence = 0.70  # Base confidence for rule-based model
    if home.get("recent_form") and away.get("recent_form"):
        confidence += 0.05
    if factors.get("head_to_head", {}).get("matches_played", 0) >= 5:
        confidence += 0.05

    return {
        "predicted_score": {
            "home": round(home_xg, 2),
            "away": round(away_xg, 2)
        },
        "outcome_probabilities": outcome_probs,
        "confidence": min(confidence, 0.85),
        "expected_goals": {
            "home": round(home_xg, 2),
            "away": round(away_xg, 2)
        }
    }
