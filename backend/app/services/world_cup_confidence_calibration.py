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

from app.services.world_cup_quality_service import (
    build_quality_loop_report,
    MIN_BUCKET_SAMPLES,
    MIN_CALIBRATION_SAMPLES,
)
from app.utils.prediction_db import get_prediction_session, close_prediction_session

logger = logging.getLogger(__name__)

# 5 confidence buckets
BUCKET_BOUNDS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.01]
BUCKET_LABELS = ["0-20%", "20-40%", "40-60%", "60-80%", "80-100%"]
MIN_SAMPLES_PER_BUCKET = MIN_BUCKET_SAMPLES


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
        quality = build_quality_loop_report(session=session)
        stats = quality["by_engine"].get(engine_name) if engine_name else quality["overall"]
        if not stats:
            stats = quality["overall"]

        buckets = [
            {
                "label": bucket["label"],
                "count": bucket["count"],
                "actual_accuracy": bucket["accuracy"],
                "avg_confidence": bucket["avg_confidence"],
            }
            for bucket in stats["calibration_buckets"]
        ]
        total_samples = int(stats["samples"])
        reliable_buckets = sum(1 for bucket in buckets if bucket["count"] >= MIN_SAMPLES_PER_BUCKET)
        is_reliable = total_samples >= MIN_CALIBRATION_SAMPLES and reliable_buckets >= 1

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
    return build_confidence_calibration_info(
        raw_confidence,
        engine_name=engine_name,
        reliability_cache=reliability_cache,
    )["calibrated"]


def _public_bucket(bucket: dict[str, Any] | None) -> dict[str, Any] | None:
    if not bucket:
        return None
    return {
        "label": bucket.get("label"),
        "count": int(bucket.get("count") or 0),
        "actual_accuracy": bucket.get("actual_accuracy"),
        "avg_confidence": bucket.get("avg_confidence"),
    }


def build_confidence_calibration_info(
    raw_confidence: float,
    engine_name: str | None = None,
    reliability_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return calibrated confidence together with reliability metadata."""
    raw = max(0.0, min(1.0, float(raw_confidence or 0.0)))

    if reliability_cache is None:
        reliability = compute_reliability_curve(engine_name)
    else:
        reliability = reliability_cache

    bucket_idx = _get_bucket(raw)
    buckets = reliability.get("buckets") or []
    bucket = buckets[bucket_idx] if bucket_idx < len(buckets) else None
    bucket_is_reliable = (
        bucket is not None
        and int(bucket.get("count") or 0) >= MIN_SAMPLES_PER_BUCKET
        and bucket.get("actual_accuracy") is not None
    )
    info = {
        "raw": round(raw, 3),
        "calibrated": round(raw, 3),
        "method": "bucketed_reliability_curve",
        "engine_filter": reliability.get("engine_filter", engine_name),
        "total_samples": int(reliability.get("total_samples") or 0),
        "min_total_samples": MIN_CALIBRATION_SAMPLES,
        "min_bucket_samples": MIN_SAMPLES_PER_BUCKET,
        "is_reliable": bool(reliability.get("is_reliable")),
        "bucket_is_reliable": bucket_is_reliable,
        "is_reference_only": True,
        "bucket": _public_bucket(bucket),
        "applied_bucket": None,
        "reason": "insufficient_total_samples",
    }

    if not reliability.get("is_reliable"):
        return info

    if not bucket or bucket["count"] < MIN_SAMPLES_PER_BUCKET or bucket["actual_accuracy"] is None:
        # Not enough samples in this bucket; use the nearest reliable bucket.
        # Find nearest bucket with enough data
        for offset in range(1, len(BUCKET_LABELS)):
            for idx in [bucket_idx - offset, bucket_idx + offset]:
                if 0 <= idx < len(buckets):
                    b = buckets[idx]
                    if b["count"] >= MIN_SAMPLES_PER_BUCKET and b["actual_accuracy"] is not None:
                        info.update({
                            "calibrated": round(max(0.05, min(0.99, float(b["actual_accuracy"]))), 3),
                            "applied_bucket": _public_bucket(b),
                            "reason": "nearest_reliable_bucket",
                        })
                        return info
        # No neighbors have data either
        info["reason"] = "insufficient_bucket_samples"
        return info

    # Use the actual accuracy of this bucket as calibrated confidence
    calibrated = bucket["actual_accuracy"]

    # Blend with raw confidence (50% empirical, 50% raw) to avoid overfitting
    # on small samples
    calibrated = 0.5 * calibrated + 0.5 * raw

    info.update({
        "calibrated": round(max(0.05, min(0.99, calibrated)), 3),
        "applied_bucket": _public_bucket(bucket),
        "is_reference_only": False,
        "reason": "bucket_reliability_curve",
    })
    return info


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
        calibration_info = build_confidence_calibration_info(raw_conf, engine_name)
        prediction_result["raw_confidence"] = calibration_info["raw"]
        prediction_result["confidence"] = calibration_info["calibrated"]
        prediction_result["calibration_info"] = calibration_info
    except Exception as e:
        logger.warning("Confidence calibration failed, using raw: %s", e)

    return prediction_result
