"""Prediction calibration service (Phase 3: Prediction Outcome Calibration).

Pure-function layer that enriches the existing prediction snapshot (frozen at
``freeze_prediction`` time) with calibration context and resolution buckets.

The existing ``predictions`` table already records the core commitment
(ai_probability, market_probability, raw_edge, decision, segment info) and
``score_prediction`` already computes the Brier score. Phase 3 adds:

1. **Snapshot context** — captured at freeze time alongside the existing
   commitment fields:
   - ``snapshot_question`` (event title at freeze time)
   - ``snapshot_recommendation`` (YES/NO/WAIT/AVOID from
     actionable_recommendation.direction — distinct from the Decision Gate
     ``decision`` field which is act/watch/skip)
   - ``snapshot_confidence`` (high/medium/low from
     actionable_recommendation.confidence)
   - ``snapshot_evidence_strength`` (from evidence_profile.strength)
   - ``snapshot_conflict_score`` (from evidence_profile.conflict)
   - ``snapshot_market_quality_score`` (from market_quality.score, when Phase 2
     is enabled and the source is prediction_market)
   - ``snapshot_source_platform`` (the market platform name, e.g. Polymarket)

2. **Resolution buckets** — computed at resolve time (in ``score_prediction``):
   - ``direction_correct`` (bool | None) — whether the YES/NO recommendation
     matched the settled outcome. None when recommendation was WAIT/AVOID
     (no direction to check) or missing.
   - ``edge_bucket`` (str) — half-open intervals [0,5), [5,10), [10,20),
     [20,+inf). Uses absolute value for bucketing per spec; preserves sign for
     direction analysis via the raw_edge column.
   - ``confidence_bucket`` (str) — low/medium/high (mirrors
     snapshot_confidence, but computed at resolve time so it is always present
     even for pre-Phase-3 predictions that lack snapshot_confidence).

3. **Aggregate summary** — ``calibration_bucket_summary()`` groups resolved
   predictions by edge_bucket × confidence_bucket, reporting count, mean
   Brier, and direction_correct_rate per cell. This is the calibration
   diagnostic that tells operators which (edge, confidence) combinations the
   engine is actually good at.

Brier score convention (per spec § Brier Score Direction Convention):
    brier_score = (estimated_probability_yes - outcome_indicator) ^ 2
    where outcome_indicator = 1.0 if YES, 0.0 if NO

The existing ``calibration_service_event.brier_score`` already implements this
convention (``((predicted/100) - (actual/100))^2`` where actual is 0-100 with
100=YES, 0=NO). Phase 3 does NOT change the Brier computation — it only adds
the bucket and direction_correct fields on top.

Direction vocabulary: ``direction_correct`` checks ``snapshot_recommendation``
(YES/NO/WAIT/AVOID from actionable_recommendation), NOT the Decision Gate
``decision`` (act/watch/skip). The two are independent — a prediction can be
decision=act with recommendation=WAIT (no actionable edge), or decision=watch
with recommendation=YES (edge exists but segment not qualified). Phase 3
scores the RECOMMENDATION direction, not the gate verdict.

Pure functions: no I/O, no LLM, no settings reads. The orchestrator
(``freeze_prediction`` / ``score_prediction``) extracts scalar config values
and passes them explicitly. Default OFF
(``PREDICTION_CALIBRATION_ENABLED=false``): when disabled, snapshot columns
stay NULL and buckets are not computed — byte-identical to pre-Phase-3.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Edge bucket boundaries (half-open intervals [low, high)).
# Per spec § Edge Bucket Boundaries: a value on a boundary (e.g. edge=5.0)
# belongs to the UPPER bucket ("5-10", not "0-5").
_EDGE_BUCKETS: tuple[tuple[float, float, str], ...] = (
    (0.0, 5.0, "0-5"),
    (5.0, 10.0, "5-10"),
    (10.0, 20.0, "10-20"),
    (20.0, float("inf"), "20+"),
)

# Confidence bucket vocabulary (validated passthrough — no remapping).
_VALID_CONFIDENCE_BUCKETS = ("high", "medium", "low")

# Outcome threshold (0-100): >= this means YES occurred.
# Per spec: outcome_indicator = 1.0 if YES, 0.0 if NO. We use 50.0 as the
# decision boundary — outcomes at exactly 50.0 are treated as YES (the
# "partial" case is recorded as actual_outcome on the prediction row but the
# direction check needs a binary signal).
_OUTCOME_YES_THRESHOLD = 50.0

# Directions that have a checkable stance (YES/NO). WAIT/AVOID are
# non-directional — direction_correct is None for them.
_DIRECTIONAL = ("YES", "NO")


def compute_edge_bucket(raw_edge: float | None) -> str:
    """Map an absolute edge value to a half-open bucket label.

    Per spec § Edge Bucket Boundaries: uses ``abs(raw_edge)`` for bucketing.
    Boundary values belong to the UPPER bucket (e.g. edge=5.0 → "5-10").
    Returns ``""`` when raw_edge is None or not finite (missing / corrupt
    data — no bucket to assign).

    The sign of raw_edge is preserved on the prediction row (raw_edge column)
    for direction analysis; the bucket is sign-agnostic so that a +13 and -13
    edge land in the same "10-20" bucket.
    """
    if raw_edge is None:
        return ""
    try:
        magnitude = abs(float(raw_edge))
    except (TypeError, ValueError):
        return ""
    if not _is_finite(magnitude):
        return ""
    for low, high, label in _EDGE_BUCKETS:
        if low <= magnitude < high:
            return label
    # magnitude >= inf is impossible (filtered above), but guard anyway
    return "20+"


def compute_confidence_bucket(confidence: str | None) -> str:
    """Validate and normalize a confidence label to a bucket string.

    Returns the label as-is when it is in {high, medium, low}. Returns
    ``"unknown"`` for None / empty / unrecognized values — so pre-Phase-3
    predictions (which lack snapshot_confidence) still get a bucket for
    aggregate grouping.
    """
    if not isinstance(confidence, str):
        return "unknown"
    normalized = confidence.strip().lower()
    if normalized in _VALID_CONFIDENCE_BUCKETS:
        return normalized
    return "unknown"


def compute_direction_correct(
    recommendation: str | None,
    actual_outcome: float | None,
) -> bool | None:
    """Check whether the YES/NO recommendation matched the settled outcome.

    Returns:
        True  — recommendation was YES and outcome was YES (>= threshold)
        True  — recommendation was NO and outcome was NO (< threshold)
        False — recommendation was YES but outcome was NO, or vice versa
        None  — recommendation was WAIT/AVOID/empty (no direction to check),
                or actual_outcome is None (not yet resolved)

    Per spec § Brier Score Direction Convention: ``direction_correct`` is a
    SEPARATE field from Brier — it records direction accuracy, not probability
    accuracy. A system can be well-calibrated (low Brier) but direction-wrong
    (boundary case where 0.51 vs 0.49 flips direction).
    """
    if not isinstance(recommendation, str):
        return None
    direction = recommendation.strip().upper()
    if direction not in _DIRECTIONAL:
        return None
    if actual_outcome is None:
        return None
    try:
        outcome = float(actual_outcome)
    except (TypeError, ValueError):
        return None
    if not _is_finite(outcome):
        return None
    outcome_yes = outcome >= _OUTCOME_YES_THRESHOLD
    if direction == "YES":
        return outcome_yes
    if direction == "NO":
        return not outcome_yes
    return None


def build_prediction_snapshot(
    record: dict[str, Any] | None,
) -> dict[str, Any]:
    """Extract the Phase 3 snapshot context fields from an event record.

    Called by ``freeze_prediction`` when ``PREDICTION_CALIBRATION_ENABLED`` is
    true. Returns a dict with the snapshot_* keys; the caller writes them into
    the new prediction columns alongside the existing commitment fields.

    Missing fields default to empty string / None — the snapshot is best-effort
    and never raises. A prediction can be frozen before
    actionable_recommendation exists (signal=WATCHLIST), in which case
    snapshot_recommendation is "" and direction_correct will be None at resolve
    time.
    """
    if not isinstance(record, dict):
        return _empty_snapshot()

    event_title = str(record.get("event_title") or "")
    source = record.get("source")
    if not isinstance(source, dict):
        source = {}
    source_platform = str(source.get("platform") or "")

    recommendation = record.get("actionable_recommendation")
    if isinstance(recommendation, dict):
        snapshot_recommendation = str(recommendation.get("direction") or "")
        snapshot_confidence = str(recommendation.get("confidence") or "")
    else:
        snapshot_recommendation = ""
        snapshot_confidence = ""

    evidence = record.get("evidence")
    if isinstance(evidence, dict):
        evidence_strength = _safe_float(evidence.get("strength"))
        conflict_score = _safe_float(evidence.get("conflict"))
    else:
        evidence_strength = None
        conflict_score = None

    market_quality = record.get("market_quality")
    if isinstance(market_quality, dict):
        market_quality_score = _safe_float(market_quality.get("score"))
    else:
        market_quality_score = None

    return {
        "snapshot_question": event_title,
        "snapshot_recommendation": snapshot_recommendation,
        "snapshot_confidence": snapshot_confidence,
        "snapshot_evidence_strength": evidence_strength,
        "snapshot_conflict_score": conflict_score,
        "snapshot_market_quality_score": market_quality_score,
        "snapshot_source_platform": source_platform,
    }


def build_resolution_buckets(
    snapshot_recommendation: str | None,
    snapshot_confidence: str | None,
    raw_edge: float | None,
    actual_outcome: float | None,
) -> dict[str, Any]:
    """Compute the Phase 3 resolution buckets at score_prediction time.

    Returns a dict with ``direction_correct`` (bool | None),
    ``edge_bucket`` (str), and ``confidence_bucket`` (str). Called by
    ``score_prediction`` when ``PREDICTION_CALIBRATION_ENABLED`` is true.

    ``snapshot_recommendation`` and ``snapshot_confidence`` come from the
    frozen prediction row (captured at freeze time). ``raw_edge`` is the
    frozen raw edge. ``actual_outcome`` is the settled outcome (0-100).
    """
    return {
        "direction_correct": compute_direction_correct(
            snapshot_recommendation, actual_outcome
        ),
        "edge_bucket": compute_edge_bucket(raw_edge),
        "confidence_bucket": compute_confidence_bucket(snapshot_confidence),
    }


def _empty_snapshot() -> dict[str, Any]:
    return {
        "snapshot_question": "",
        "snapshot_recommendation": "",
        "snapshot_confidence": "",
        "snapshot_evidence_strength": None,
        "snapshot_conflict_score": None,
        "snapshot_market_quality_score": None,
        "snapshot_source_platform": "",
    }


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not _is_finite(result):
        return None
    return result


def _is_finite(value: float) -> bool:
    import math

    return math.isfinite(value)
