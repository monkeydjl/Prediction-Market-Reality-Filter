# backend/app/kernel/learning_service.py
"""Learning service — records predictions and outcomes, computes errors.

Phase 1 implements: record_prediction, record_outcome, compute_error,
engine_score. Calibration and weight updates are deferred to Phase 3.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.kernel.domain import (
    MatchIdentity, MatchOutcome, PredictionResult,
    PredictionError, EngineScore,
)
from app.kernel.kernel_db import (
    get_kernel_session,
    KernelPrediction, KernelMatchOutcome, KernelEngineScore,
    KernelCalibration, KernelPredictionHistory,
)
from app.core import config
from app.kernel.factor_registry import FactorRegistry

logger = logging.getLogger(__name__)

# Phase 3 hardcoded constants (not configurable — see spec Section 3.4)
_CALIBRATION_SLOPE_MIN = 0.0
_CALIBRATION_SLOPE_MAX = 2.0
_CALIBRATION_INTERCEPT_MIN = -0.5
_CALIBRATION_INTERCEPT_MAX = 0.5

# P1-V5: confidence bins for conditional calibration
_CONF_BUCKET_LOW = 0.45
_CONF_BUCKET_HIGH = 0.70
_CONF_BUCKET_PREFIX = "#c_"


def confidence_bucket(confidence: float | None) -> str:
    """Map confidence to low / mid / high (P1-V5)."""
    try:
        c = float(confidence) if confidence is not None else 0.5
    except (TypeError, ValueError):
        c = 0.5
    if c < _CONF_BUCKET_LOW:
        return "low"
    if c >= _CONF_BUCKET_HIGH:
        return "high"
    return "mid"


def competition_with_bucket(competition: str, bucket: str) -> str:
    return f"{competition}{_CONF_BUCKET_PREFIX}{bucket}"


_STAGE_PREFIX = "#s_"


def stage_bucket(stage: str | None = None, match_id: str | None = None) -> str:
    """Map match stage to coarse bucket for conditional calibration (P1-V5)."""
    text = f"{stage or ''} {match_id or ''}".lower()
    knockout_tokens = (
        "playoff", "knockout", "final", "semi", "quarter", "elim",
        "round_of", "r16", "r8", "wildcard", "postseason",
    )
    regular_tokens = (
        "regular", "group", "season", "rs", "regular_season", "league",
    )
    if any(t in text for t in knockout_tokens):
        return "knockout"
    if any(t in text for t in regular_tokens):
        return "regular"
    return "unknown"


def competition_with_stage(competition: str, stage_b: str) -> str:
    return f"{competition}{_STAGE_PREFIX}{stage_b}"


def _explanation_stage(explanation) -> str | None:
    if not explanation or not isinstance(explanation, list):
        return None
    for item in explanation:
        if not isinstance(item, dict):
            continue
        if item.get("factor") == "_meta" and item.get("stage"):
            return str(item["stage"])
    return None



def apply_linear_calibration(
    prob: float, slope: float, intercept: float,
) -> float:
    """Clamp calibrated probability to (0, 1)."""
    y = slope * float(prob) + float(intercept)
    return max(1e-4, min(1.0 - 1e-4, y))


class KernelLearningService:
    """Implements LearningService Protocol for Phase 1."""

    def __init__(self, factor_registry: FactorRegistry | None = None) -> None:
        self._factor_registry = factor_registry or FactorRegistry()

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
                existing.explanation = [
                    {
                        "factor": "_meta",
                        "stage": getattr(match, "stage", None),
                        "available": False,
                        "weight": 0.0,
                    }
                ] + [c.__dict__ for c in prediction.explanation]
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
                    explanation=[
                        {
                            "factor": "_meta",
                            "stage": getattr(match, "stage", None),
                            "available": False,
                            "weight": 0.0,
                        }
                    ] + [c.__dict__ for c in prediction.explanation],
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            session.commit()

            # Write to KernelPredictionHistory (Phase 3)
            history = KernelPredictionHistory(
                match_id=match.match_id,
                engine=prediction.engine_name,
                predicted_scores=prediction.predicted_scores,
                outcome_probabilities=prediction.outcome_probabilities,
                confidence=prediction.confidence,
                feature_version=prediction.feature_version,
                trigger="initial",
                created_at=now,
            )
            session.add(history)
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

            # Brier score — dynamically iterate outcome keys
            # (supports both football 3-way and basketball binary)
            probs = pred.outcome_probabilities
            outcome_keys = list(probs.keys())
            brier = sum(
                (probs.get(k, 0) - (1.0 if k == outcome.outcome else 0.0)) ** 2
                for k in outcome_keys
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
                .limit(config.settings.LEARNING_WINDOW_SIZE)
            )
            results = session.execute(query).all()
            if len(results) < config.settings.MIN_SAMPLES_FOR_CALIBRATION:
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


    def update_calibration_by_confidence(self, competition: str, engine: str) -> dict[str, int]:
        """Fit per-confidence-bin calibration rows (P1-V5).

        Stores bucket rows as competition key ``{competition}#c_{low|mid|high}``
        so existing KernelCalibration unique constraint is reused (no migration).
        Returns sample counts written per bucket.
        """
        session = get_kernel_session()
        written: dict[str, int] = {}
        try:
            query = (
                select(KernelPrediction, KernelMatchOutcome)
                .join(
                    KernelMatchOutcome,
                    KernelPrediction.match_id == KernelMatchOutcome.match_id,
                )
                .where(
                    KernelPrediction.competition == competition,
                    KernelPrediction.engine == engine,
                    KernelMatchOutcome.outcome.isnot(None),
                )
                .order_by(KernelMatchOutcome.finished_at.desc())
                .limit(config.settings.LEARNING_WINDOW_SIZE)
            )
            results = session.execute(query).all()
            buckets: dict[str, list] = {"low": [], "mid": [], "high": []}
            for pred, outcome in results:
                b = confidence_bucket(pred.confidence)
                buckets[b].append((pred, outcome))

            now = datetime.now(timezone.utc)
            min_n = max(5, config.settings.MIN_SAMPLES_FOR_CALIBRATION // 2)

            for bucket, rows in buckets.items():
                if len(rows) < min_n:
                    written[bucket] = 0
                    continue
                x = [r[0].outcome_probabilities.get("home_win", 0) for r in rows]
                y = [1.0 if r[1].outcome == "home_win" else 0.0 for r in rows]
                n = len(x)
                sum_x = sum(x)
                sum_y = sum(y)
                sum_xx = sum(xi * xi for xi in x)
                sum_xy = sum(xi * yi for xi, yi in zip(x, y))
                denominator = n * sum_xx - sum_x * sum_x
                if abs(denominator) < 1e-10:
                    written[bucket] = 0
                    continue
                slope = (n * sum_xy - sum_x * sum_y) / denominator
                intercept = (sum_y - slope * sum_x) / n
                slope = max(_CALIBRATION_SLOPE_MIN, min(_CALIBRATION_SLOPE_MAX, slope))
                intercept = max(
                    _CALIBRATION_INTERCEPT_MIN,
                    min(_CALIBRATION_INTERCEPT_MAX, intercept),
                )
                avg_confidence = sum_x / n
                avg_accuracy = sum_y / n
                key = competition_with_bucket(competition, bucket)
                existing = session.query(KernelCalibration).filter_by(
                    engine=engine, competition=key,
                ).first()
                if existing:
                    existing.slope = slope
                    existing.intercept = intercept
                    existing.sample_count = n
                    existing.avg_confidence = avg_confidence
                    existing.avg_accuracy = avg_accuracy
                    existing.last_updated = now
                else:
                    session.add(KernelCalibration(
                        engine=engine, competition=key,
                        slope=slope, intercept=intercept,
                        sample_count=n, avg_confidence=avg_confidence,
                        avg_accuracy=avg_accuracy, last_updated=now,
                    ))
                written[bucket] = n
            session.commit()
            return written
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


    def update_calibration_by_stage(self, competition: str, engine: str) -> dict[str, int]:
        """Fit per-stage-bucket calibration rows (P1-V5 category).

        Keys: ``{competition}#s_{regular|knockout|unknown}``.
        Stage is read from explanation ``_meta.stage`` or inferred from match_id.
        """
        session = get_kernel_session()
        written: dict[str, int] = {}
        try:
            query = (
                select(KernelPrediction, KernelMatchOutcome)
                .join(
                    KernelMatchOutcome,
                    KernelPrediction.match_id == KernelMatchOutcome.match_id,
                )
                .where(
                    KernelPrediction.competition == competition,
                    KernelPrediction.engine == engine,
                    KernelMatchOutcome.outcome.isnot(None),
                )
                .order_by(KernelMatchOutcome.finished_at.desc())
                .limit(config.settings.LEARNING_WINDOW_SIZE)
            )
            results = session.execute(query).all()
            buckets: dict[str, list] = {
                "regular": [], "knockout": [], "unknown": [],
            }
            for pred, outcome in results:
                st = _explanation_stage(pred.explanation)
                b = stage_bucket(st, pred.match_id)
                buckets[b].append((pred, outcome))

            now = datetime.now(timezone.utc)
            min_n = max(5, config.settings.MIN_SAMPLES_FOR_CALIBRATION // 2)

            for bucket, rows in buckets.items():
                if len(rows) < min_n:
                    written[bucket] = 0
                    continue
                x = [r[0].outcome_probabilities.get("home_win", 0) for r in rows]
                y = [1.0 if r[1].outcome == "home_win" else 0.0 for r in rows]
                n = len(x)
                sum_x = sum(x)
                sum_y = sum(y)
                sum_xx = sum(xi * xi for xi in x)
                sum_xy = sum(xi * yi for xi, yi in zip(x, y))
                denominator = n * sum_xx - sum_x * sum_x
                if abs(denominator) < 1e-10:
                    written[bucket] = 0
                    continue
                slope = (n * sum_xy - sum_x * sum_y) / denominator
                intercept = (sum_y - slope * sum_x) / n
                slope = max(_CALIBRATION_SLOPE_MIN, min(_CALIBRATION_SLOPE_MAX, slope))
                intercept = max(
                    _CALIBRATION_INTERCEPT_MIN,
                    min(_CALIBRATION_INTERCEPT_MAX, intercept),
                )
                avg_confidence = sum_x / n
                avg_accuracy = sum_y / n
                key = competition_with_stage(competition, bucket)
                existing = session.query(KernelCalibration).filter_by(
                    engine=engine, competition=key,
                ).first()
                if existing:
                    existing.slope = slope
                    existing.intercept = intercept
                    existing.sample_count = n
                    existing.avg_confidence = avg_confidence
                    existing.avg_accuracy = avg_accuracy
                    existing.last_updated = now
                else:
                    session.add(KernelCalibration(
                        engine=engine, competition=key,
                        slope=slope, intercept=intercept,
                        sample_count=n, avg_confidence=avg_confidence,
                        avg_accuracy=avg_accuracy, last_updated=now,
                    ))
                written[bucket] = n
            session.commit()
            return written
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_conditional_calibration(
        self,
        competition: str,
        engine: str,
        confidence: float | None,
        *,
        stage: str | None = None,
        match_id: str | None = None,
    ) -> dict | None:
        """Prefer stage then confidence-bin calibration; fall back to competition.

        Returns dict with slope, intercept, sample_count, source, bucket.
        """
        session = get_kernel_session()
        try:
            min_n = max(5, config.settings.MIN_SAMPLES_FOR_CALIBRATION // 2)
            conf_bucket = confidence_bucket(confidence)
            st_bucket = stage_bucket(stage, match_id)

            # 1) stage bucket
            if st_bucket != "unknown":
                skey = competition_with_stage(competition, st_bucket)
                srow = session.query(KernelCalibration).filter_by(
                    engine=engine, competition=skey,
                ).first()
                if srow is not None and srow.sample_count >= min_n:
                    return {
                        "slope": srow.slope,
                        "intercept": srow.intercept,
                        "sample_count": srow.sample_count,
                        "avg_accuracy": srow.avg_accuracy,
                        "bucket": st_bucket,
                        "source": "stage_bucket",
                        "competition_key": skey,
                    }

            # 2) confidence bucket
            key = competition_with_bucket(competition, conf_bucket)
            row = session.query(KernelCalibration).filter_by(
                engine=engine, competition=key,
            ).first()
            if row is not None and row.sample_count >= min_n:
                return {
                    "slope": row.slope,
                    "intercept": row.intercept,
                    "sample_count": row.sample_count,
                    "avg_accuracy": row.avg_accuracy,
                    "bucket": conf_bucket,
                    "source": "confidence_bucket",
                    "competition_key": key,
                }

            # 3) competition baseline
            base = session.query(KernelCalibration).filter_by(
                engine=engine, competition=competition,
            ).first()
            if base is None:
                return None
            return {
                "slope": base.slope,
                "intercept": base.intercept,
                "sample_count": base.sample_count,
                "avg_accuracy": base.avg_accuracy,
                "bucket": conf_bucket,
                "source": "competition",
                "competition_key": competition,
            }
        finally:
            session.close()

    def update_weights(self, competition: str) -> None:
        """EWMA weight adjustment per competition using per-factor accuracy."""
        if self._factor_registry is None:
            return

        session = get_kernel_session()
        try:
            # Query recent outcomes with predictions for this competition
            query = (
                select(KernelPrediction, KernelMatchOutcome)
                .join(KernelMatchOutcome, KernelPrediction.match_id == KernelMatchOutcome.match_id)
                .where(
                    KernelPrediction.competition == competition,
                    KernelMatchOutcome.outcome.isnot(None),
                )
                .order_by(KernelMatchOutcome.finished_at.desc())
                .limit(config.settings.LEARNING_WINDOW_SIZE)
            )
            results = session.execute(query).all()
            if len(results) < config.settings.MIN_SAMPLES_FOR_CALIBRATION:
                return

            # Dynamic factor collection — supports any number of factors
            # (football: elo+odds, basketball: elo+home_court+rest+form)
            factor_stats: dict[str, dict[str, int]] = {}  # {factor_id: {correct, total}}
            for pred, outcome in results:
                actual = outcome.outcome
                for item in pred.explanation or []:
                    if not isinstance(item, dict):
                        continue
                    factor = item.get("factor")
                    predicted = item.get("predicted_outcome")
                    if not factor or not predicted:
                        continue
                    if factor not in factor_stats:
                        factor_stats[factor] = {"correct": 0, "total": 0}
                    factor_stats[factor]["total"] += 1
                    if predicted == actual:
                        factor_stats[factor]["correct"] += 1

            # Skip if any factor has 0 samples
            if not factor_stats or any(s["total"] == 0 for s in factor_stats.values()):
                return

            # Compute accuracy per factor, normalize to target weights
            accuracies = {f: s["correct"] / s["total"] for f, s in factor_stats.items()}
            total_acc = sum(accuracies.values())
            if total_acc == 0:
                return
            target_weights = {f: acc / total_acc for f, acc in accuracies.items()}

            # EWMA update for each factor
            alpha = config.settings.EWMA_ALPHA
            for factor_id, target_w in target_weights.items():
                old_w = self._factor_registry.get_weight(factor_id, competition)
                new_w = max(config.settings.WEIGHT_FLOOR, min(config.settings.WEIGHT_CEILING,
                           alpha * target_w + (1 - alpha) * old_w))
                self._factor_registry.update_weight(factor_id, competition, new_w, source="ewma")

            # Normalize last factor to ensure weights sum to 1.0
            # (handles clamp rounding drift — same pattern as old code's
            # w_odds = 1.0 - w_elo for football's 2-factor case)
            factors = list(target_weights.keys())
            if len(factors) > 1:
                sum_w = sum(self._factor_registry.get_weight(f, competition) for f in factors[:-1])
                last_w = max(config.settings.WEIGHT_FLOOR, min(config.settings.WEIGHT_CEILING, 1.0 - sum_w))
                self._factor_registry.update_weight(factors[-1], competition, last_w, source="ewma")
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

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

            # Read confidence_calibration from KernelCalibration
            confidence_calibration = 0.0
            if competition is not None:
                cal = session.query(KernelCalibration).filter_by(
                    engine=engine, competition=competition,
                ).first()
                if cal:
                    confidence_calibration = cal.avg_accuracy / max(cal.avg_confidence, 1e-6)

            score = EngineScore(
                engine=engine, competition=competition,
                accuracy=round(accuracy, 4),
                avg_mae=round(avg_mae, 4),
                brier_score=round(avg_brier, 4),
                sample_count=count,
                confidence_calibration=round(confidence_calibration, 4),
                last_updated=datetime.now(timezone.utc),
            )

            # Persist to KernelEngineScore table
            existing = session.query(KernelEngineScore).filter_by(
                engine=engine, competition=competition,
            ).first()
            now = datetime.now(timezone.utc)
            if existing:
                existing.accuracy = score.accuracy
                existing.avg_mae = score.avg_mae
                existing.brier_score = score.brier_score
                existing.sample_count = count
                existing.confidence_calibration = score.confidence_calibration
                existing.last_updated = now
            else:
                row = KernelEngineScore(
                    engine=engine, competition=competition,
                    accuracy=score.accuracy, avg_mae=score.avg_mae,
                    brier_score=score.brier_score, sample_count=count,
                    confidence_calibration=score.confidence_calibration,
                    last_updated=now,
                )
                session.add(row)
            session.commit()

            return score
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
