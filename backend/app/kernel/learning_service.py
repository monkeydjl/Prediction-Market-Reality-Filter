# backend/app/kernel/learning_service.py
"""Learning service — records predictions and outcomes, computes errors.

Phase 1 implements: record_prediction, record_outcome, compute_error,
engine_score. Calibration and weight updates are deferred to Phase 3.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, func

from app.kernel.domain import (
    MatchIdentity, MatchOutcome, PredictionResult,
    PredictionError, EngineScore,
)
from app.kernel.kernel_db import (
    get_kernel_session,
    KernelPrediction, KernelMatchOutcome, KernelEngineScore,
    KernelCalibration,
)

logger = logging.getLogger(__name__)

# Phase 3 hardcoded constants (not configurable — see spec Section 3.4)
_CALIBRATION_SLOPE_MIN = 0.0
_CALIBRATION_SLOPE_MAX = 2.0
_CALIBRATION_INTERCEPT_MIN = -0.5
_CALIBRATION_INTERCEPT_MAX = 0.5

# Defaults used until config.py has the Phase 3 settings (added in Task 7)
_DEFAULT_LEARNING_WINDOW_SIZE = 30
_DEFAULT_MIN_SAMPLES_FOR_CALIBRATION = 10


class KernelLearningService:
    """Implements LearningService Protocol for Phase 1."""

    def record_prediction(self, match: MatchIdentity,
                          prediction: PredictionResult) -> None:
        session = get_kernel_session()
        try:
            existing = session.get(KernelPrediction, match.match_id)
            now = datetime.now(timezone.utc)
            if existing:
                existing.engine = prediction.engine_name
                existing.predicted_scores = prediction.predicted_scores
                existing.outcome_probabilities = prediction.outcome_probabilities
                existing.confidence = prediction.confidence
                existing.feature_version = prediction.feature_version
                existing.explanation = [c.__dict__ for c in prediction.explanation]
                existing.updated_at = now
            else:
                record = KernelPrediction(
                    match_id=match.match_id,
                    sport=match.season.competition.sport.code,
                    competition=match.season.competition.code,
                    season=match.season.season_key,
                    engine=prediction.engine_name,
                    predicted_scores=prediction.predicted_scores,
                    outcome_probabilities=prediction.outcome_probabilities,
                    confidence=prediction.confidence,
                    feature_version=prediction.feature_version,
                    explanation=[c.__dict__ for c in prediction.explanation],
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def record_outcome(self, outcome: MatchOutcome) -> None:
        session = get_kernel_session()
        try:
            existing = session.get(KernelMatchOutcome, outcome.match_id)
            if existing:
                existing.home_score = outcome.home_score
                existing.away_score = outcome.away_score
                existing.outcome = outcome.outcome
                existing.finished_at = outcome.finished_at
                # Reset error columns — they will be recomputed by compute_error().
                # Without this, stale error data from a previous prediction
                # would persist after the outcome is updated.
                existing.engine = None
                existing.score_mae = None
                existing.outcome_correct = None
                existing.brier_score = None
            else:
                record = KernelMatchOutcome(
                    match_id=outcome.match_id,
                    home_score=outcome.home_score,
                    away_score=outcome.away_score,
                    outcome=outcome.outcome,
                    finished_at=outcome.finished_at,
                    created_at=datetime.now(timezone.utc),
                )
                session.add(record)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def compute_error(self, match_id: str) -> PredictionError | None:
        session = get_kernel_session()
        try:
            pred = session.get(KernelPrediction, match_id)
            outcome = session.get(KernelMatchOutcome, match_id)
            if pred is None or outcome is None:
                return None

            # Score MAE
            pred_home = pred.predicted_scores.get("home", 0)
            pred_away = pred.predicted_scores.get("away", 0)
            score_mae = (abs(pred_home - outcome.home_score) +
                         abs(pred_away - outcome.away_score)) / 2.0

            # Outcome correct
            predicted_outcome = max(
                pred.outcome_probabilities,
                key=pred.outcome_probabilities.get,
            ) if pred.outcome_probabilities else None
            outcome_correct = (predicted_outcome == outcome.outcome)

            # Brier score
            probs = pred.outcome_probabilities
            brier = sum(
                (probs.get(k, 0) - (1.0 if k == outcome.outcome else 0.0)) ** 2
                for k in ["home_win", "draw", "away_win"]
            )

            # Confidence calibrated
            confidence_calibrated = (
                (outcome_correct and pred.confidence >= 0.5) or
                (not outcome_correct and pred.confidence < 0.5)
            )

            error = PredictionError(
                match_id=match_id, engine=pred.engine,
                score_mae=round(score_mae, 4),
                outcome_correct=outcome_correct,
                brier_score=round(brier, 4),
                confidence_calibrated=confidence_calibrated,
            )

            # Update outcome record with error
            outcome.engine = pred.engine
            outcome.score_mae = error.score_mae
            outcome.outcome_correct = 1 if outcome_correct else 0
            outcome.brier_score = error.brier_score
            session.commit()

            return error
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_calibration(self, competition: str, engine: str) -> None:
        """Fit linear regression calibration model and persist to DB."""
        session = get_kernel_session()
        try:
            # Query recent predictions with outcomes for this competition+engine
            query = (
                select(KernelPrediction, KernelMatchOutcome)
                .join(KernelMatchOutcome, KernelPrediction.match_id == KernelMatchOutcome.match_id)
                .where(
                    KernelPrediction.competition == competition,
                    KernelPrediction.engine == engine,
                    KernelMatchOutcome.outcome.isnot(None),
                )
                .order_by(KernelMatchOutcome.finished_at.desc())
                .limit(_DEFAULT_LEARNING_WINDOW_SIZE)
            )
            results = session.execute(query).all()
            if len(results) < _DEFAULT_MIN_SAMPLES_FOR_CALIBRATION:
                return

            x = [r[0].outcome_probabilities.get("home_win", 0) for r in results]
            y = [1.0 if r[1].outcome == "home_win" else 0.0 for r in results]

            n = len(x)
            sum_x = sum(x)
            sum_y = sum(y)
            sum_xx = sum(xi * xi for xi in x)
            sum_xy = sum(xi * yi for xi, yi in zip(x, y))
            denominator = n * sum_xx - sum_x * sum_x
            if abs(denominator) < 1e-10:
                return

            slope = (n * sum_xy - sum_x * sum_y) / denominator
            intercept = (sum_y - slope * sum_x) / n

            slope = max(_CALIBRATION_SLOPE_MIN, min(_CALIBRATION_SLOPE_MAX, slope))
            intercept = max(_CALIBRATION_INTERCEPT_MIN, min(_CALIBRATION_INTERCEPT_MAX, intercept))

            avg_confidence = sum_x / n
            avg_accuracy = sum_y / n

            # Upsert calibration
            existing = session.query(KernelCalibration).filter_by(
                engine=engine, competition=competition,
            ).first()
            now = datetime.now(timezone.utc)
            if existing:
                existing.slope = slope
                existing.intercept = intercept
                existing.sample_count = n
                existing.avg_confidence = avg_confidence
                existing.avg_accuracy = avg_accuracy
                existing.last_updated = now
            else:
                cal = KernelCalibration(
                    engine=engine, competition=competition,
                    slope=slope, intercept=intercept,
                    sample_count=n, avg_confidence=avg_confidence,
                    avg_accuracy=avg_accuracy, last_updated=now,
                )
                session.add(cal)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_weights(self, competition: str) -> None:
        """Deferred to Phase 3."""
        logger.info("update_weights deferred to Phase 3")

    def engine_score(self, engine: str,
                     competition: str | None = None) -> EngineScore | None:
        session = get_kernel_session()
        try:
            query = select(
                KernelMatchOutcome,
            ).where(
                KernelMatchOutcome.engine == engine,
                KernelMatchOutcome.outcome_correct.isnot(None),
            )
            if competition is not None:
                # Join with predictions to filter by competition
                query = query.join(
                    KernelPrediction,
                    KernelMatchOutcome.match_id == KernelPrediction.match_id,
                ).where(KernelPrediction.competition == competition)

            results = session.execute(query).scalars().all()
            if not results:
                return None

            count = len(results)
            correct = sum(1 for r in results if r.outcome_correct)
            accuracy = correct / count if count > 0 else 0.0
            avg_mae = sum(r.score_mae or 0 for r in results) / count
            avg_brier = sum(r.brier_score or 0 for r in results) / count

            return EngineScore(
                engine=engine, competition=competition,
                accuracy=round(accuracy, 4),
                avg_mae=round(avg_mae, 4),
                brier_score=round(avg_brier, 4),
                sample_count=count,
                confidence_calibration=0.0,  # Phase 3
                last_updated=datetime.now(timezone.utc),
            )
        finally:
            session.close()
