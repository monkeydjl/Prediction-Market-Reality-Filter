"""Betting market analysis derived from Poisson score probability matrices.

Converts the score probability matrix into common betting market outputs:
- 1X2 (match winner) — already exists, but included for completeness
- Double chance (1X, 12, X2)
- Over/Under total goals (0.5, 1.5, 2.5, 3.5)
- Both teams to score (BTTS Yes/No)
- Asian handicap (0, +0.5, -0.5, +1, -1)
- Correct score top 3
"""

from typing import Any


def analyze_betting_markets(
    score_matrix: dict[str, float],
    outcome_probs: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Derive betting market analysis from a score probability matrix.

    Args:
        score_matrix: Dict like {"0-0": 0.08, "1-0": 0.12, ...}
        outcome_probs: Optional pre-computed 1X2 probabilities

    Returns:
        Dict with all betting market analyses
    """
    if not score_matrix:
        return {}

    # 1X2 (from matrix if not provided)
    if outcome_probs:
        home_win = outcome_probs.get("home_win", 0.0)
        draw = outcome_probs.get("draw", 0.0)
        away_win = outcome_probs.get("away_win", 0.0)
    else:
        home_win = draw = away_win = 0.0
        for score, prob in score_matrix.items():
            h, a = map(int, score.split("-"))
            if h > a:
                home_win += prob
            elif h < a:
                away_win += prob
            else:
                draw += prob

    # Double chance
    double_chance = {
        "1X": round(home_win + draw, 4),    # Home or Draw
        "12": round(home_win + away_win, 4),  # Home or Away (no draw)
        "X2": round(draw + away_win, 4),    # Draw or Away
    }

    # Over/Under total goals
    over_under = {}
    for line in [0.5, 1.5, 2.5, 3.5]:
        over = 0.0
        under = 0.0
        for score, prob in score_matrix.items():
            h, a = map(int, score.split("-"))
            total = h + a
            if total > line:
                over += prob
            else:
                under += prob
        over_under[f"O{line}"] = round(over, 4)
        over_under[f"U{line}"] = round(under, 4)

    # Both teams to score
    btts_yes = 0.0
    btts_no = 0.0
    for score, prob in score_matrix.items():
        h, a = map(int, score.split("-"))
        if h > 0 and a > 0:
            btts_yes += prob
        else:
            btts_no += prob
    btts = {
        "yes": round(btts_yes, 4),
        "no": round(btts_no, 4),
    }

    # Asian handicap
    handicaps = {}
    for handicap in [0, 0.5, -0.5, 1, -1, 1.5, -1.5]:
        home_win_h = 0.0
        away_win_h = 0.0
        push = 0.0
        for score, prob in score_matrix.items():
            h, a = map(int, score.split("-"))
            # Apply handicap to home team
            adjusted_home = h + handicap
            if adjusted_home > a:
                home_win_h += prob
            elif adjusted_home < a:
                away_win_h += prob
            else:
                push += prob
        handicaps[f"H{handicap:+.1f}"] = {
            "home": round(home_win_h, 4),
            "away": round(away_win_h, 4),
            "push": round(push, 4),
        }

    # Top 3 correct scores
    sorted_scores = sorted(score_matrix.items(), key=lambda x: x[1], reverse=True)
    top_3 = [{"score": s, "probability": round(p, 4)} for s, p in sorted_scores[:3]]

    # Implied odds (1/probability)
    implied_odds = {
        "home_win": round(1.0 / max(home_win, 0.001), 2),
        "draw": round(1.0 / max(draw, 0.001), 2),
        "away_win": round(1.0 / max(away_win, 0.001), 2),
    }

    return {
        "1x2": {
            "home_win": round(home_win, 4),
            "draw": round(draw, 4),
            "away_win": round(away_win, 4),
            "implied_odds": implied_odds,
        },
        "double_chance": double_chance,
        "over_under": over_under,
        "btts": btts,
        "asian_handicap": handicaps,
        "top_3_correct_scores": top_3,
    }
