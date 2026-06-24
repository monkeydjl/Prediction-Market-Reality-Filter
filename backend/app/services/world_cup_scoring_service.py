"""Post-match scoring service for World Cup predictions.

This module implements the feedback loop that was previously missing:
when a match finishes, it scores the prediction against the actual result,
writing MatchResult records with Brier score, MAE, and outcome accuracy.

These records are consumed by:
- /api/analytics/accuracy-stats endpoint
- /api/analytics/engine-stats endpoint
- Engine calibration feedback (future)
"""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.world_cup_prediction import (
    MatchFixture,
    MatchPrediction,
    MatchResult,
    PredictionHistory,
)
from app.utils.prediction_db import get_prediction_session, close_prediction_session

logger = logging.getLogger(__name__)


def _determine_outcome(home_score: int, away_score: int) -> str:
    """Determine match outcome string from final scores."""
    if home_score > away_score:
        return "home_win"
    elif home_score < away_score:
        return "away_win"
    else:
        return "draw"


def _calculate_brier_score(
    home_win_prob: float,
    draw_prob: float,
    away_win_prob: float,
    actual_outcome: str,
) -> float:
    """Calculate Brier score for a 3-way outcome prediction.

    Brier score = sum of (predicted_prob - actual_indicator)^2 for each outcome.
    Lower is better. Range: 0 (perfect) to 2 (worst).
    """
    if actual_outcome == "home_win":
        actual = [1.0, 0.0, 0.0]
    elif actual_outcome == "draw":
        actual = [0.0, 1.0, 0.0]
    else:
        actual = [0.0, 0.0, 1.0]

    predicted = [home_win_prob, draw_prob, away_win_prob]
    return round(sum((p - a) ** 2 for p, a in zip(predicted, actual)), 4)


def _get_predicted_outcome_prob(
    home_win_prob: float,
    draw_prob: float,
    away_win_prob: float,
    actual_outcome: str,
) -> float:
    """Get the probability that was assigned to the actual outcome."""
    if actual_outcome == "home_win":
        return home_win_prob
    elif actual_outcome == "draw":
        return draw_prob
    else:
        return away_win_prob


def score_finished_match(match_id: str, session: Session | None = None) -> dict[str, Any] | None:
    """Score a single finished match's prediction against the actual result.

    Writes a MatchResult record with accuracy metrics. Idempotent — if a
    MatchResult already exists for this match, it is updated.

    Args:
        match_id: Match ID to score
        session: Optional DB session (creates one if None)

    Returns:
        Scoring summary dict, or None if match is not finished / not found
    """
    should_close = session is None
    if session is None:
        session = get_prediction_session()

    try:
        match = session.query(MatchFixture).filter_by(match_id=match_id).first()
        if not match:
            return None

        # Only score finished matches with actual scores
        if match.status != "finished":
            return None
        if match.home_score is None or match.away_score is None:
            return None

        actual_home = int(match.home_score)
        actual_away = int(match.away_score)
        actual_outcome = _determine_outcome(actual_home, actual_away)

        # Get the last prediction before match finished
        prediction = session.query(MatchPrediction).filter_by(match_id=match_id).first()
        if not prediction:
            # No prediction exists — record result with nulls
            existing_result = session.query(MatchResult).filter_by(match_id=match_id).first()
            if existing_result:
                return None  # Already scored
            result = MatchResult(
                match_id=match_id,
                final_home_score=actual_home,
                final_away_score=actual_away,
                outcome=actual_outcome,
                finished_at=datetime.utcnow(),
                predicted_home_score=None,
                predicted_away_score=None,
                predicted_outcome_prob=None,
                score_mae=None,
                outcome_correct=None,
                brier_score=None,
                home_error=None,
                away_error=None,
                confidence_calibrated=None,
            )
            session.add(result)
            session.commit()
            return {
                "match_id": match_id,
                "status": "scored_no_prediction",
                "actual_score": {"home": actual_home, "away": actual_away},
                "outcome": actual_outcome,
            }

        # Calculate metrics
        pred_home = float(prediction.predicted_home_score or 0)
        pred_away = float(prediction.predicted_away_score or 0)
        home_win_prob = float(prediction.home_win_prob or 0.33)
        draw_prob = float(prediction.draw_prob or 0.34)
        away_win_prob = float(prediction.away_win_prob or 0.33)

        pred_outcome = _determine_outcome(round(pred_home), round(pred_away))
        outcome_correct = 1 if pred_outcome == actual_outcome else 0

        score_mae = round(
            (abs(pred_home - actual_home) + abs(pred_away - actual_away)) / 2, 4
        )
        brier = _calculate_brier_score(home_win_prob, draw_prob, away_win_prob, actual_outcome)
        predicted_outcome_prob = _get_predicted_outcome_prob(
            home_win_prob, draw_prob, away_win_prob, actual_outcome
        )

        home_error = round(pred_home - actual_home, 4)
        away_error = round(pred_away - actual_away, 4)

        # Confidence calibration: 1 if high confidence (>0.6) and correct,
        # or low confidence (<0.4) and wrong
        confidence = float(prediction.confidence or 0.5)
        if (confidence >= 0.6 and outcome_correct) or (confidence < 0.4 and not outcome_correct):
            confidence_calibrated = 1
        else:
            confidence_calibrated = 0

        # Upsert MatchResult
        existing = session.query(MatchResult).filter_by(match_id=match_id).first()
        if existing:
            existing.final_home_score = actual_home
            existing.final_away_score = actual_away
            existing.outcome = actual_outcome
            existing.finished_at = datetime.utcnow()
            existing.predicted_home_score = pred_home
            existing.predicted_away_score = pred_away
            existing.predicted_outcome_prob = predicted_outcome_prob
            existing.score_mae = score_mae
            existing.outcome_correct = outcome_correct
            existing.brier_score = brier
            existing.home_error = home_error
            existing.away_error = away_error
            existing.confidence_calibrated = confidence_calibrated
            action = "updated"
        else:
            result = MatchResult(
                match_id=match_id,
                final_home_score=actual_home,
                final_away_score=actual_away,
                outcome=actual_outcome,
                finished_at=datetime.utcnow(),
                predicted_home_score=pred_home,
                predicted_away_score=pred_away,
                predicted_outcome_prob=predicted_outcome_prob,
                score_mae=score_mae,
                outcome_correct=outcome_correct,
                brier_score=brier,
                home_error=home_error,
                away_error=away_error,
                confidence_calibrated=confidence_calibrated,
            )
            session.add(result)
            action = "created"

        session.commit()
        logger.info(
            "Scored match %s: actual=%d-%d, predicted=%.1f-%.1f, outcome_correct=%d, brier=%.4f, mae=%.4f",
            match_id, actual_home, actual_away, pred_home, pred_away,
            outcome_correct, brier, score_mae,
        )

        return {
            "match_id": match_id,
            "status": "scored",
            "action": action,
            "actual_score": {"home": actual_home, "away": actual_away},
            "predicted_score": {"home": pred_home, "away": pred_away},
            "outcome": actual_outcome,
            "outcome_correct": bool(outcome_correct),
            "brier_score": brier,
            "score_mae": score_mae,
            "predicted_outcome_prob": predicted_outcome_prob,
            "confidence": confidence,
            "confidence_calibrated": bool(confidence_calibrated),
        }

    except Exception as e:
        session.rollback()
        logger.error("Failed to score match %s: %s", match_id, e, exc_info=True)
        return None

    finally:
        if should_close:
            close_prediction_session(session)


def score_all_finished_matches() -> dict[str, Any]:
    """Score all finished matches that haven't been scored yet.

    This is the main entry point for the reconciliation loop — call it
    on startup or on a schedule to catch up on any matches that finished
    while the service was down.

    Returns:
        Summary of scoring results
    """
    session = get_prediction_session()
    try:
        # Find all finished matches
        finished = session.query(MatchFixture).filter(
            MatchFixture.status == "finished",
            MatchFixture.home_score.isnot(None),
            MatchFixture.away_score.isnot(None),
        ).all()

        scored = 0
        skipped = 0
        errors = 0

        for match in finished:
            # Check if already scored
            existing = session.query(MatchResult).filter_by(match_id=match.match_id).first()
            if existing and existing.brier_score is not None:
                skipped += 1
                continue

            result = score_finished_match(match.match_id, session=session)
            if result:
                scored += 1
            else:
                errors += 1

        logger.info(
            "Scoring reconciliation: %d scored, %d skipped, %d errors (total finished: %d)",
            scored, skipped, errors, len(finished),
        )

        return {
            "status": "ok",
            "total_finished": len(finished),
            "scored": scored,
            "skipped": skipped,
            "errors": errors,
        }

    finally:
        close_prediction_session(session)
