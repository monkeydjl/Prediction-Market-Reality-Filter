"""Main prediction engine that orchestrates rule-based and AI predictions."""

import logging
from datetime import datetime, timezone
from typing import Any

from app.services.world_cup_engines.world_cup_rule_engine import predict_score_rule_based
from app.services.world_cup_engines.world_cup_ai_engine import predict_score_ai

logger = logging.getLogger(__name__)


def fuse_predictions(
    rule_pred: dict[str, Any],
    ai_pred: dict[str, Any] | None,
    rule_weight: float = 0.80,
    ai_weight: float = 0.20
) -> dict[str, Any]:
    """Combine rule-based and AI predictions with weighted fusion.

    The AI engine now calibrates the rule-engine prediction rather than
    producing an independent score.  Therefore the rule engine receives a
    higher weight (0.80) and the AI acts as a fine-tuning overlay (0.20).

    If the AI is not confident in its adjustment
    (``confidence_in_adjustment < 0.4``) the rule-engine result is used
    directly without applying any AI delta.

    Args:
        rule_pred: Prediction from rule engine
        ai_pred: Prediction from AI engine (or None if AI failed)
        rule_weight: Weight for rule-based prediction (default 0.80)
        ai_weight: Weight for AI prediction (default 0.20)

    Returns:
        Fused prediction with combined score and confidence
    """

    if ai_pred is None:
        # AI failed, use rule-only
        return {
            "predicted_score": rule_pred["predicted_score"],
            "outcome_probabilities": rule_pred["outcome_probabilities"],
            "confidence": rule_pred["confidence"] * 0.9,  # Slightly lower without AI
            "prediction_method": "rule_only",
            "rule_score": rule_pred["predicted_score"],
            "ai_score": None,
            "ai_reasoning": None,
            "key_factors": []
        }

    # If the AI is not confident in its adjustment, trust the rule engine
    # directly instead of applying a dubious delta.
    confidence_in_adjustment = ai_pred.get("confidence_in_adjustment", 1.0)
    if confidence_in_adjustment < 0.4:
        logger.info(
            "AI confidence_in_adjustment=%.2f < 0.4, using rule-only result",
            confidence_in_adjustment,
        )
        return {
            "predicted_score": rule_pred["predicted_score"],
            "outcome_probabilities": rule_pred["outcome_probabilities"],
            "confidence": rule_pred["confidence"],
            "prediction_method": "rule_dominant",
            "rule_score": rule_pred["predicted_score"],
            "ai_score": None,
            "ai_reasoning": ai_pred.get("reasoning"),
            "key_factors": []
        }

    # Normalize weights
    total_weight = rule_weight + ai_weight
    rule_w = rule_weight / total_weight
    ai_w = ai_weight / total_weight

    # Fuse scores
    fused_home = rule_pred["predicted_score"]["home"] * rule_w + ai_pred["predicted_score"]["home"] * ai_w
    fused_away = rule_pred["predicted_score"]["away"] * rule_w + ai_pred["predicted_score"]["away"] * ai_w

    # Recalculate outcome probabilities from fused scores
    from app.services.world_cup_engines.world_cup_rule_engine import calculate_outcome_probabilities
    outcome_probs = calculate_outcome_probabilities(fused_home, fused_away)

    # Calculate confidence based on agreement between rule and AI
    score_diff_home = abs(rule_pred["predicted_score"]["home"] - ai_pred["predicted_score"]["home"])
    score_diff_away = abs(rule_pred["predicted_score"]["away"] - ai_pred["predicted_score"]["away"])
    avg_diff = (score_diff_home + score_diff_away) / 2.0

    # High agreement -> high confidence, low agreement -> lower confidence
    agreement_factor = max(0.5, 1.0 - (avg_diff / 3.0))  # Max diff of 3 goals
    base_confidence = (rule_pred["confidence"] + ai_pred["confidence"]) / 2.0
    final_confidence = base_confidence * agreement_factor

    return {
        "predicted_score": {
            "home": round(fused_home, 2),
            "away": round(fused_away, 2)
        },
        "outcome_probabilities": outcome_probs,
        "confidence": round(final_confidence, 3),
        "prediction_method": "hybrid",
        "rule_score": rule_pred["predicted_score"],
        "ai_score": ai_pred["predicted_score"],
        "ai_reasoning": ai_pred.get("reasoning"),
        "key_factors": ai_pred.get("key_factors", [])
    }


async def predict_match_score(
    home_team: str,
    away_team: str,
    kickoff_utc: str | datetime,
    stage: str,
    factors: dict[str, Any]
) -> dict[str, Any]:
    """Generate match score prediction using hybrid approach.

    Args:
        home_team: Home team name
        away_team: Away team name
        kickoff_utc: Match kickoff time
        stage: Tournament stage (group_stage, round_of_16, etc.)
        factors: All prediction factors (team stats, context, h2h)

    Returns:
        Complete prediction including score, probabilities, confidence, and metadata
    """

    # Convert datetime to string if needed
    if isinstance(kickoff_utc, datetime):
        kickoff_str = kickoff_utc.isoformat()
    else:
        kickoff_str = kickoff_utc

    # Run rule-based prediction (synchronous)
    rule_pred = predict_score_rule_based(factors)

    # Run AI prediction (asynchronous) - AI calibrates the rule-engine result
    ai_pred = await predict_score_ai(home_team, away_team, kickoff_str, stage, factors, rule_pred)

    # Use data-driven fusion weights when sufficient Brier history exists;
    # otherwise fall back to the hardcoded 0.80/0.20 defaults.
    rule_w, ai_w = 0.80, 0.20
    try:
        from app.services.world_cup_dynamic_weights import get_dynamic_rule_ai_weights
        rule_w, ai_w = get_dynamic_rule_ai_weights()
    except Exception:
        pass  # Insufficient data or import issue — use defaults

    # Fuse predictions
    fused = fuse_predictions(rule_pred, ai_pred, rule_weight=rule_w, ai_weight=ai_w)

    # Propagate key_factors from sports signals when the AI engine didn't
    # produce any (which is the common case — the AI always returns []).
    if not fused.get("key_factors") and factors.get("key_factors"):
        fused["key_factors"] = factors["key_factors"]

    # Add metadata
    fused["factors"] = factors
    fused["timestamp"] = datetime.now(timezone.utc).isoformat()

    return fused
