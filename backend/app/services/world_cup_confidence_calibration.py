"""Confidence calibration service using bucketed reliability curves.

This module implements a data-driven confidence calibration system that
replaces the heuristic confidence formulas with empirically-calibrated values.

Approach:
- Divide confidence into 5 buckets: [0-20%, 20-40%, 40-60%, 60-80%, 80-100%]
- For each bucket, compute the actual accuracy rate from MatchResult records
- Map raw confidence to calibrated confidence using the reliability curve
- Falls back to identity mapping when insufficient data (<5 samples per bucket)

This is more robust than Platt Scaling for small sample sizes (e.g., 64 WC matches).
"""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.world_cup_prediction import MatchResult, MatchPrediction
from app.utils.prediction_db import get_prediction_session, close_prediction_session

logger = logging.getLogger(__name__)

# 5 confidence buckets
BUCKET_BOUNDS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.01]
BUCKET_LABELS = ["0-20%", "20-40%", "40-60%", "60-80%", "80-100%"]
MIN_SAMPLES_PER_BUCKET = 5


def _get_bucket(confidence: float) -> int:
    """Get bucket index for a confidence value."""
    for i in range(len(BUCKET_BOUNDS) - 1):
        if BUCKET_BOUNDS[i] <= confidence < BUCKET_BOUNDS[i + 1]:
            return i
    return len(BUCKET_BOUNDS) - 2  # Last bucket


def compute_reliability_curve(engine_name: str | None = None) -> dict[str, Any]:
    """Compute the reliability curve from historical MatchResult data.

    Args:
        engine_name: Optional engine filter (e.g., "elo_odds", "hybrid", "integrated")

    Returns:
        {
            "buckets": [
                {"label": "0-20%", "count": 5, "actual_accuracy": 0.15, "avg_confidence": 0.12},
                ...
            ],
            "total_samples": 42,
            "is_reliable": bool,  # True if enough samples overall
        }
    """
    session = get_prediction_session()
    try:
        query = session.query(MatchResult).filter(
            MatchResult.outcome_correct.isnot(None),
            MatchResult.brier_score.isnot(None),
        )

        # Join with MatchPrediction to get engine and confidence
        query = query.join(
            MatchPrediction,
            MatchResult.match_id == MatchPrediction.match_id
        )

        if engine_name:
            query = query.filter(
                MatchPrediction.prediction_method.contains(engine_name)
            )

        results = query.all()

        if not results:
            return {
                "buckets": [
                    {"label": label, "count": 0, "actual_accuracy": None, "avg_confidence": None}
                    for label in BUCKET_LABELS
                ],
                "total_samples": 0,
                "is_reliable": False,
            }

        # Group by bucket
        buckets: list[dict[str, Any]] = [
            {"label": label, "count": 0, "correct": 0, "confidence_sum": 0.0}
            for label in BUCKET_LABELS
        ]

        for r in results:
            # Get confidence from the prediction
            pred = session.query(MatchPrediction).filter_by(match_id=r.match_id).first()
            if not pred or pred.confidence is None:
                continue

            conf = float(pred.confidence)
            bucket_idx = _get_bucket(conf)
            buckets[bucket_idx]["count"] += 1
            buckets[bucket_idx]["correct"] += int(r.outcome_correct or 0)
            buckets[bucket_idx]["confidence_sum"] += conf

        # Compute actual accuracy per bucket
        total_samples = sum(b["count"] for b in buckets)
        for b in buckets:
            if b["count"] > 0:
                b["actual_accuracy"] = round(b["correct"] / b["count"], 3)
                b["avg_confidence"] = round(b["confidence_sum"] / b["count"], 3)
            else:
                b["actual_accuracy"] = None
                b["avg_confidence"] = None
            del b["correct"]
            del b["confidence_sum"]

        # Check reliability: need at least MIN_SAMPLES_PER_BUCKET in at least 3 buckets
        reliable_buckets = sum(1 for b in buckets if b["count"] >= MIN_SAMPLES_PER_BUCKET)
        is_reliable = reliable_buckets >= 3

        return {
            "buckets": buckets,
            "total_samples": total_samples,
            "is_reliable": is_reliable,
            "engine_filter": engine_name,
        }

    finally:
        close_prediction_session(session)


def calibrate_confidence(
    raw_confidence: float,
    engine_name: str | None = None,
    reliability_cache: dict[str, Any] | None = None,
) -> float:
    """Calibrate a raw confidence score using the reliability curve.

    If insufficient data, returns the raw confidence unchanged (identity mapping).

    Args:
        raw_confidence: Raw confidence from the prediction engine (0-1)
        engine_name: Optional engine filter for engine-specific calibration
        reliability_cache: Pre-computed reliability curve to avoid DB queries

    Returns:
        Calibrated confidence (0-1)
    """
    # Get reliability curve
    if reliability_cache is None:
        reliability = compute_reliability_curve(engine_name)
    else:
        reliability = reliability_cache

    if not reliability.get("is_reliable"):
        # Not enough data — return raw confidence
        return round(raw_confidence, 3)

    bucket_idx = _get_bucket(raw_confidence)
    bucket = reliability["buckets"][bucket_idx]

    if bucket["count"] < MIN_SAMPLES_PER_BUCKET or bucket["actual_accuracy"] is None:
        # Not enough samples in this bucket — use interpolation from neighbors
        # Find nearest bucket with enough data
        for offset in range(1, len(BUCKET_LABELS)):
            for idx in [bucket_idx - offset, bucket_idx + offset]:
                if 0 <= idx < len(reliability["buckets"]):
                    b = reliability["buckets"][idx]
                    if b["count"] >= MIN_SAMPLES_PER_BUCKET and b["actual_accuracy"] is not None:
                        return round(b["actual_accuracy"], 3)
        # No neighbors have data either
        return round(raw_confidence, 3)

    # Use the actual accuracy of this bucket as calibrated confidence
    calibrated = bucket["actual_accuracy"]

    # Blend with raw confidence (50% empirical, 50% raw) to avoid overfitting
    # on small samples
    calibrated = 0.5 * calibrated + 0.5 * raw_confidence

    return round(max(0.05, min(0.99, calibrated)), 3)


def apply_confidence_calibration(
    prediction_result: dict[str, Any],
    engine_name: str | None = None,
) -> dict[str, Any]:
    """Apply confidence calibration to a prediction result.

    Modifies the prediction dict in-place, replacing the raw confidence
    with a calibrated value and storing the original.

    Args:
        prediction_result: Prediction dict with "confidence" key
        engine_name: Optional engine filter

    Returns:
        The modified prediction dict
    """
    raw_conf = prediction_result.get("confidence", 0.5)

    try:
        calibrated = calibrate_confidence(raw_conf, engine_name)
        prediction_result["raw_confidence"] = round(raw_conf, 3)
        prediction_result["confidence"] = calibrated
        prediction_result["calibration_info"] = {
            "raw": round(raw_conf, 3),
            "calibrated": calibrated,
            "method": "bucketed_reliability_curve",
        }
    except Exception as e:
        logger.warning("Confidence calibration failed, using raw: %s", e)

    return prediction_result
