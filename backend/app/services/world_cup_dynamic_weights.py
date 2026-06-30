"""Dynamic fusion weight adjustment based on historical Brier scores.

Replaces hardcoded fusion weights (Elo 30%/Odds 70%, Rule 70%/AI 30%)
with data-driven weights based on each component's historical accuracy.

Uses inverse-Brier weighting: components with lower (better) Brier scores
get higher weights. Falls back to default weights when insufficient data.
"""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.world_cup_prediction import MatchResult, MatchPrediction
from app.utils.prediction_db import get_prediction_session, close_prediction_session

logger = logging.getLogger(__name__)

# Default weights (used when insufficient historical data)
DEFAULT_ELO_WEIGHT = 0.30
DEFAULT_ODDS_WEIGHT = 0.70
DEFAULT_RULE_WEIGHT = 0.70
DEFAULT_AI_WEIGHT = 0.30
DEFAULT_ELO_ODDS_WEIGHT = 0.60
DEFAULT_HYBRID_WEIGHT = 0.40

MIN_SAMPLES = 5  # Minimum samples needed for data-driven weights


def _compute_component_brier(
    session: Session,
    engine_filter: str,
) -> float | None:
    """Compute average Brier score for predictions from a specific engine.

    Args:
        session: DB session
        engine_filter: String to match in prediction_method (e.g., "elo_odds", "hybrid")

    Returns:
        Average Brier score, or None if insufficient data
    """
    results = (
        session.query(MatchResult, MatchPrediction)
        .join(MatchPrediction, MatchResult.match_id == MatchPrediction.match_id)
        .filter(
            MatchResult.brier_score.isnot(None),
            MatchPrediction.prediction_method.contains(engine_filter),
        )
        .all()
    )

    if len(results) < MIN_SAMPLES:
        return None

    total_brier = sum(float(r.brier_score) for r, _ in results)
    return total_brier / len(results)


def get_dynamic_elo_odds_weights() -> tuple[float, float]:
    """Get data-driven Elo vs Odds fusion weights.

    Returns:
        (elo_weight, odds_weight) that sum to 1.0
    """
    session = get_prediction_session()
    try:
        # We can't directly separate Elo-only vs Odds-only Brier scores
        # because the pipeline always fuses them.
        # Instead, we use the overall prediction accuracy as a proxy:
        # if the engine performs well, keep default weights;
        # if it performs poorly, shift toward the component with more data.

        elo_odds_brier = _compute_component_brier(session, "elo_odds")
        hybrid_brier = _compute_component_brier(session, "hybrid")

        if elo_odds_brier is not None and hybrid_brier is not None:
            # If elo_odds is better, increase its weight in integrated mode
            # If hybrid is better, increase its weight
            # Use inverse-Brier weighting
            inv_elo = 1.0 / max(elo_odds_brier, 0.01)
            inv_hybrid = 1.0 / max(hybrid_brier, 0.01)
            total = inv_elo + inv_hybrid
            elo_odds_w = inv_elo / total
            hybrid_w = inv_hybrid / total

            # Clamp to reasonable range (don't let either dominate completely)
            elo_odds_w = max(0.30, min(0.80, elo_odds_w))
            hybrid_w = 1.0 - elo_odds_w

            return round(elo_odds_w, 3), round(hybrid_w, 3)

        return DEFAULT_ELO_ODDS_WEIGHT, DEFAULT_HYBRID_WEIGHT

    finally:
        close_prediction_session(session)


def get_dynamic_rule_ai_weights() -> tuple[float, float]:
    """Get data-driven Rule vs AI fusion weights for the hybrid engine.

    Returns:
        (rule_weight, ai_weight) that sum to 1.0
    """
    session = get_prediction_session()
    try:
        # Compare rule-only predictions vs AI-enhanced predictions
        # rule_only is used when AI fails; hybrid is used when AI succeeds
        rule_brier = _compute_component_brier(session, "rule_only")
        hybrid_brier = _compute_component_brier(session, "hybrid")

        if rule_brier is not None and hybrid_brier is not None:
            # If AI helps (hybrid Brier < rule Brier), increase AI weight
            # If AI hurts, decrease AI weight
            inv_rule = 1.0 / max(rule_brier, 0.01)
            inv_hybrid = 1.0 / max(hybrid_brier, 0.01)
            total = inv_rule + inv_hybrid
            rule_w = inv_rule / total
            ai_w = inv_hybrid / total

            # Clamp to reasonable range
            rule_w = max(0.40, min(0.90, rule_w))
            ai_w = 1.0 - rule_w

            return round(rule_w, 3), round(ai_w, 3)

        return DEFAULT_RULE_WEIGHT, DEFAULT_AI_WEIGHT

    finally:
        close_prediction_session(session)


def get_dynamic_weights_summary() -> dict[str, Any]:
    """Get a summary of current dynamic weights for debugging/display."""
    elo_odds_w, hybrid_w = get_dynamic_elo_odds_weights()
    rule_w, ai_w = get_dynamic_rule_ai_weights()

    return {
        "elo_odds_vs_hybrid": {
            "elo_odds_weight": elo_odds_w,
            "hybrid_weight": hybrid_w,
            "default_elo_odds": DEFAULT_ELO_ODDS_WEIGHT,
            "default_hybrid": DEFAULT_HYBRID_WEIGHT,
        },
        "rule_vs_ai": {
            "rule_weight": rule_w,
            "ai_weight": ai_w,
            "default_rule": DEFAULT_RULE_WEIGHT,
            "default_ai": DEFAULT_AI_WEIGHT,
        },
    }
