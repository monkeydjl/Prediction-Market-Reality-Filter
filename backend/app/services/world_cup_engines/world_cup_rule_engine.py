"""Rule-based prediction engine using statistical models.

This module implements the core mathematical models for predicting match scores:
- Poisson distribution for expected goals
- ELO-based team strength ratings
- Home advantage, form, and fatigue adjustments
- Market value and sentiment factor integration
- Head-to-head historical adjustment
- Dixon-Coles draw correction
"""

import math
from typing import Any


def poisson_probability(expected_goals: float, actual_goals: int) -> float:
    """Calculate Poisson probability for a given goal count."""
    return (math.exp(-expected_goals) * (expected_goals ** actual_goals)) / math.factorial(actual_goals)


def calculate_outcome_probabilities(home_xg: float, away_xg: float, max_goals: int = 8) -> dict[str, float]:
    """Calculate win/draw/loss probabilities from expected goals using Poisson.

    Includes Dixon-Coles correction for low-scoring games (draw underestimation).

    Returns probabilities as decimals (0.0 to 1.0), not percentages.
    """
    home_win = 0.0
    draw = 0.0
    away_win = 0.0

    # Dixon-Coles correction parameter
    # Adjusts for the fact that Poisson underestimates draws
    # rho < 1 increases probability of 0-0, 1-1, 1-0, 0-1 results
    rho = 0.96  # Slight draw boost

    for home_goals in range(max_goals + 1):
        home_prob = poisson_probability(home_xg, home_goals)
        for away_goals in range(max_goals + 1):
            away_prob = poisson_probability(away_xg, away_goals)
            joint_prob = home_prob * away_prob

            # Apply Dixon-Coles correction for low scores
            if home_goals <= 1 and away_goals <= 1:
                if home_goals == 0 and away_goals == 0:
                    joint_prob *= (1.0 - home_xg * away_xg * (1.0 - rho))
                elif home_goals == 1 and away_goals == 0:
                    joint_prob *= (1.0 + away_xg * (1.0 - rho))
                elif home_goals == 0 and away_goals == 1:
                    joint_prob *= (1.0 + home_xg * (1.0 - rho))
                elif home_goals == 1 and away_goals == 1:
                    joint_prob *= (1.0 - (1.0 - rho))

            if home_goals > away_goals:
                home_win += joint_prob
            elif home_goals < away_goals:
                away_win += joint_prob
            else:
                draw += joint_prob

    # Normalize to ensure sum is exactly 1.0
    total = home_win + draw + away_win
    if total > 0:
        home_win = home_win / total
        draw = draw / total
        away_win = away_win / total

    return {
        "home_win": round(max(0.0, home_win), 4),
        "draw": round(max(0.0, draw), 4),
        "away_win": round(max(0.0, away_win), 4)
    }


def calculate_expected_goals(
    team_attack: float,
    team_defense: float,
    opponent_attack: float,
    opponent_defense: float,
    is_home: bool = False,
    form_factor: float = 1.0,
    fatigue_factor: float = 1.0,
    injury_impact: float = 0.0,
    market_value_factor: float = 1.0,
    sentiment_factor: float = 1.0,
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
        market_value_factor: Squad quality multiplier based on market value (0.85 - 1.15)
        sentiment_factor: Morale/momentum multiplier based on sentiment (0.90 - 1.10)

    Returns:
        Expected goals (typically 0.5 - 4.0)
    """
    # Base expected goals: team attack vs opponent defense
    base_xg = (team_attack + opponent_defense) / 2.0

    # Home advantage: +0.3 expected goals
    if is_home:
        base_xg += 0.3

    # Apply modifiers
    xg = base_xg * form_factor * fatigue_factor * market_value_factor * sentiment_factor
    xg += injury_impact

    # Clamp to reasonable range
    return max(0.1, min(xg, 5.0))


def _determine_must_win(
    stage: str,
    home_team: dict[str, Any],
    away_team: dict[str, Any],
    context: dict[str, Any],
) -> tuple[bool, bool]:
    """Determine if either team is in a must-win situation.

    A must-win situation occurs when:
    - Knockout stage (both teams must win to advance)
    - Group stage final match with team needing a win to advance

    Returns:
        (home_must_win, away_must_win)
    """
    # In knockout stages, both teams must win (no draws allowed)
    if stage in {"round_of_16", "quarterfinal", "semifinal", "final"}:
        return True, True

    # Group stage: check standings if available
    if stage == "group_stage":
        home_standing = context.get("home_team_standing", {})
        away_standing = context.get("away_team_standing", {})

        # Check if this is the final group match
        matches_played = home_standing.get("matches_played", 0)
        if matches_played >= 2:  # Final group match (3rd of 3)
            home_points = home_standing.get("points", 0)
            away_points = away_standing.get("points", 0)

            # If team has 0-3 points and is in bottom 2, they likely need a win
            home_must = home_points <= 3
            away_must = away_points <= 3

            return home_must, away_must

    return False, False


def predict_score_rule_based(factors: dict[str, Any]) -> dict[str, Any]:
    """Generate score prediction using rule-based models.

    Integrates all available factors:
    - Team statistics (attack, defense, form, fatigue, injuries)
    - Market value (squad quality proxy)
    - Sentiment (morale/momentum signal)
    - Head-to-head history
    - Tournament context (stakes, must-win situations)
    - Dixon-Coles draw correction

    Args:
        factors: Dictionary containing team factors, context, head-to-head

    Returns:
        {
            "predicted_score": {"home": float, "away": float},
            "outcome_probabilities": {"home_win": float, "draw": float, "away_win": float},
            "confidence": float,
            "expected_goals": {"home": float, "away": float},
            "score_probability_matrix": dict,
            "top_5_scores": list,
        }
    """
    home = factors.get("home_team", {})
    away = factors.get("away_team", {})
    context = factors.get("context", {})
    h2h = factors.get("head_to_head", {})

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

    # Market value factors (squad quality multiplier)
    # Map 0-1 rating to 0.85-1.15 multiplier
    home_mv = home.get("market_value_rating", 0.5)
    away_mv = away.get("market_value_rating", 0.5)
    home_mv_factor = 0.85 + home_mv * 0.30  # 0.85 to 1.15
    away_mv_factor = 0.85 + away_mv * 0.30

    # Sentiment factors (morale/momentum multiplier)
    # Map 0-1 rating to 0.90-1.10 multiplier
    home_sent = home.get("sentiment_rating", 0.5)
    away_sent = away.get("sentiment_rating", 0.5)
    home_sent_factor = 0.90 + home_sent * 0.20  # 0.90 to 1.10
    away_sent_factor = 0.90 + away_sent * 0.20

    # Calculate expected goals
    # NOTE: World Cup matches are played on neutral ground - no home advantage
    home_xg = calculate_expected_goals(
        home_attack, home_defense, away_attack, away_defense,
        is_home=False,  # No home advantage in World Cup (neutral venue)
        form_factor=home_form,
        fatigue_factor=home_fatigue,
        injury_impact=home_injury,
        market_value_factor=home_mv_factor,
        sentiment_factor=home_sent_factor,
    )

    away_xg = calculate_expected_goals(
        away_attack, away_defense, home_attack, home_defense,
        is_home=False,  # No home advantage in World Cup (neutral venue)
        form_factor=away_form,
        fatigue_factor=away_fatigue,
        injury_impact=away_injury,
        market_value_factor=away_mv_factor,
        sentiment_factor=away_sent_factor,
    )

    # Head-to-head adjustment
    # If teams have history, blend expected goals with H2H averages
    h2h_played = h2h.get("matches_played", 0)
    if h2h_played >= 3:
        h2h_home_goals = h2h.get("avg_goals_home", 1.5)
        h2h_away_goals = h2h.get("avg_goals_away", 1.5)
        # Weight H2H by sample size (max 30% influence at 10+ games)
        h2h_weight = min(0.30, h2h_played / 10 * 0.30)
        home_xg = home_xg * (1 - h2h_weight) + h2h_home_goals * h2h_weight
        away_xg = away_xg * (1 - h2h_weight) + h2h_away_goals * h2h_weight

    # Determine must-win situations
    stage = context.get("tournament_stage", "group_stage")
    home_must_win, away_must_win = _determine_must_win(stage, home, away, context)

    # Adjust for stakes
    stakes = context.get("stakes", "medium")
    if home_must_win and away_must_win:
        # Both teams must win → more attacking play from both sides
        home_xg *= 1.15
        away_xg *= 1.15
    elif home_must_win:
        # Only home must win → home attacks more, away counters
        home_xg *= 1.12
        away_xg *= 1.05
    elif away_must_win:
        # Only away must win → away attacks more, home counters
        away_xg *= 1.12
        home_xg *= 1.05
    elif stakes == "high":
        home_xg *= 1.08
        away_xg *= 1.08

    # Group stage final round adjustments
    # Teams that have already qualified or been eliminated play differently.
    # Applied after expected goals calculation and before Dixon-Coles correction.
    group_status = factors.get("group_status", {})
    home_group_status = group_status.get("home")
    away_group_status = group_status.get("away")

    # Track multipliers for factor breakdown reporting
    home_xg_mult = 1.0
    home_concede_mult = 1.0
    away_xg_mult = 1.0
    away_concede_mult = 1.0
    confidence_multiplier = 1.0

    if home_group_status == "qualified":
        # Already qualified: rotation lineup reduces attack, weaker defense
        home_xg *= 0.85
        away_xg *= 1.10
        confidence_multiplier *= 0.85
        home_xg_mult = 0.85
        home_concede_mult = 1.10
    elif home_group_status == "eliminated":
        # Already eliminated: lack of motivation
        home_xg *= 0.80
        away_xg *= 1.20
        confidence_multiplier *= 0.80
        home_xg_mult = 0.80
        home_concede_mult = 1.20

    if away_group_status == "qualified":
        away_xg *= 0.85
        home_xg *= 1.10
        confidence_multiplier *= 0.85
        away_xg_mult = 0.85
        away_concede_mult = 1.10
    elif away_group_status == "eliminated":
        away_xg *= 0.80
        home_xg *= 1.20
        confidence_multiplier *= 0.80
        away_xg_mult = 0.80
        away_concede_mult = 1.20

    # Calculate outcome probabilities (with Dixon-Coles correction)
    outcome_probs = calculate_outcome_probabilities(home_xg, away_xg)

    # Build score probability matrix
    max_goals = 8
    score_matrix: dict[str, float] = {}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p_h = poisson_probability(home_xg, h)
            p_a = poisson_probability(away_xg, a)
            score_matrix[f"{h}-{a}"] = round(p_h * p_a, 6)

    top_scores = sorted(score_matrix.items(), key=lambda x: x[1], reverse=True)[:5]

    # Confidence based on data quality
    confidence = 0.70  # Base confidence for rule-based model
    if home.get("recent_form") and away.get("recent_form"):
        confidence += 0.05
    if h2h_played >= 5:
        confidence += 0.05
    if home.get("market_value_rating", 0) > 0 and away.get("market_value_rating", 0) > 0:
        confidence += 0.03  # Market value data available
    if home.get("sentiment_confidence", 0) > 0.5 or away.get("sentiment_confidence", 0) > 0.5:
        confidence += 0.02  # Sentiment data available

    # Apply group stage final round confidence adjustment
    confidence *= confidence_multiplier

    return {
        "predicted_score": {
            "home": round(home_xg, 2),
            "away": round(away_xg, 2)
        },
        "outcome_probabilities": outcome_probs,
        "confidence": min(confidence, 0.88),
        "expected_goals": {
            "home": round(home_xg, 2),
            "away": round(away_xg, 2)
        },
        "score_probability_matrix": score_matrix,
        "top_5_scores": [{"score": s, "probability": p} for s, p in top_scores],
        "factor_breakdown": {
            "market_value_factor": {
                "home": round(home_mv_factor, 3),
                "away": round(away_mv_factor, 3),
            },
            "sentiment_factor": {
                "home": round(home_sent_factor, 3),
                "away": round(away_sent_factor, 3),
            },
            "h2h_adjustment": {
                "weight": round(h2h_weight, 3) if h2h_played >= 3 else 0,
                "games": h2h_played,
            },
            "must_win": {
                "home": home_must_win,
                "away": away_must_win,
            },
            "group_status_adjustment": {
                "home": {
                    "status": home_group_status,
                    "xg_multiplier": round(home_xg_mult, 3),
                    "concede_multiplier": round(home_concede_mult, 3),
                },
                "away": {
                    "status": away_group_status,
                    "xg_multiplier": round(away_xg_mult, 3),
                    "concede_multiplier": round(away_concede_mult, 3),
                },
                "confidence_multiplier": round(confidence_multiplier, 3),
            },
        }
    }
