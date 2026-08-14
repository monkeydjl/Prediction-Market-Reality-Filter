"""calibration_feedback_service.py
==================================
Closed-loop calibration feedback for the event layer.

Turns the Brier history of already-resolved events into two adjustments to a new
event's published probability:

  1. Component weighting - the published estimate today is a single LLM number.
     Each resolved event records what the market baseline, the LLM, and (when
     enabled) the cross-validation model each predicted. Scoring those against
     the settled outcome gives a per-component Brier history, so the signals can
     be fused weighted by how accurate each has been (weight inversely
     proportional to mean Brier).

  2. Base-rate shrinkage - a category whose resolved events have been
     historically overconfident (high Brier) has its estimate pulled back toward
     that category's base-rate prior.

Dormant by design. Each breakdown is only used once it has at least
`min_samples` resolved samples; below that it contributes nothing, so the
adjusted probability equals the LLM estimate and the published value is
unchanged. With no resolved history (the common case today) the whole module is
a no-op. The caller (analyze_event) additionally gates the entire feature behind
settings.CALIBRATION_FEEDBACK_ENABLED.

Pure and deterministic except for `_load_resolved_records`, the single seam that
reads the event store; the math functions take plain lists so they stay
network-free and trivially testable.

Event vocabulary only - no trading terms.
"""

import logging
import math
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# Weighting: weight = 1 / (mean_brier + EPS). A small EPS keeps a perfect
# (brier 0) component from dividing by zero and bounds how far it can dominate.
_EPS = 0.01
# Shrinkage caps how far an overconfident category is pulled to its prior: even
# a fully random category (mean brier >= 0.25) only moves halfway, never past
# the prior. Keeps the feedback a correction, not a takeover.
_MAX_SHRINK = 0.5
# Brier of a coin-flip against a binary outcome; the worst we scale shrinkage to.
_RANDOM_BRIER = 0.25
# Half-life (in days) for exponential time decay weighting of resolved events.
# Events resolved 45 days ago contribute half the weight of fresh ones.
_HALF_LIFE_DAYS = 45


def _finite_float(value: Any) -> float | None:
    """Parse to a finite float, or None.

    Returns the number rather than a bool: a `_finite(value) -> bool` predicate
    cannot narrow its argument, so every caller had to call float() a second
    time on a value the checker still saw as Optional.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _time_weight(resolved_at: str, half_life_days: int = _HALF_LIFE_DAYS) -> float:
    """Exponential time decay weight for a resolved event.

    Returns 0.5 ** (days_since_resolution / half_life_days).
    Returns 1.0 if the timestamp is missing or unparseable (fail-safe so that
    records without resolved_at are weighted equally, preserving old behavior).
    """
    if not resolved_at or not isinstance(resolved_at, str):
        return 1.0
    try:
        resolved_dt = datetime.fromisoformat(resolved_at)
        if resolved_dt.tzinfo is None:
            resolved_dt = resolved_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days = max(0.0, (now - resolved_dt).total_seconds() / 86400.0)
        return 0.5 ** (days / half_life_days)
    except (ValueError, TypeError, OverflowError):
        return 1.0


def briers_by_component(resolved_records: list[dict[str, Any]]) -> dict[str, list[float]]:
    """Per-component Brier history across resolved events.

    Each record may carry `calibration_components` (what market / llm /
    cross_validation each predicted, 0-100) recorded at analyze time, and an
    `outcome.actual_outcome` (0-100) attached at resolve time. The Brier for a
    component is ((predicted - actual)/100)^2, weighted by exponential time decay
    based on `resolved_at` (half-life of 45 days). Records lacking components
    (older events) or a finite outcome are skipped, so a component only
    accumulates samples once it has actually been recorded - this is what keeps
    the feedback dormant until enough new events resolve.
    """
    out: dict[str, list[float]] = defaultdict(list)
    for record in resolved_records:
        components = record.get("calibration_components")
        outcome = record.get("outcome")
        if not isinstance(components, dict) or not isinstance(outcome, dict):
            continue
        actual = outcome.get("actual_outcome")
        actual_value = _finite_float(actual)
        if actual_value is None:
            continue
        weight = _time_weight(record.get("resolved_at", ""))
        for key, predicted in components.items():
            predicted_value = _finite_float(predicted)
            if predicted_value is not None:
                brier = ((predicted_value - actual_value) / 100.0) ** 2
                out[key].append(brier * weight)
    return dict(out)


def category_briers(resolved_records: list[dict[str, Any]], category: str) -> list[float]:
    """Brier history of the published estimate for one base-rate category.

    Reads the main `calibration.brier_score` (the published estimate's accuracy)
    and the event's base_rate_category (stored under legacy_analysis, as the
    /calibration route reads it). Unlike component history this is available on
    all resolved events - old and new - so category shrinkage can activate
    independently of component recording. Each brier is weighted by exponential
    time decay based on `resolved_at` (half-life of 45 days).
    """
    briers: list[float] = []
    for record in resolved_records:
        legacy = record.get("legacy_analysis") or {}
        record_category = legacy.get("base_rate_category") or "unknown"
        if record_category != category:
            continue
        calibration = record.get("calibration") or {}
        brier = _finite_float(calibration.get("brier_score"))
        if brier is not None:
            weight = _time_weight(record.get("resolved_at", ""))
            briers.append(brier * weight)
    return briers


def component_weights(
    resolved_records: list[dict[str, Any]],
    min_samples: int,
) -> dict[str, float]:
    """Normalized fusion weights per component, inversely proportional to mean
    Brier. Only components with at least `min_samples` history are included.

    Returns an empty dict when fewer than two components qualify - with 0 or 1
    qualifying signal there is nothing to weight against, so the caller keeps the
    LLM estimate as-is (current behavior). Weights sum to 1 over the included
    components.
    """
    qualified = {
        key: briers
        for key, briers in briers_by_component(resolved_records).items()
        if len(briers) >= min_samples
    }
    if len(qualified) < 2:
        return {}
    raw = {key: 1.0 / (mean(briers) + _EPS) for key, briers in qualified.items()}
    total = sum(raw.values())
    if total <= 0:
        return {}
    return {key: weight / total for key, weight in raw.items()}


def category_shrinkage(
    resolved_records: list[dict[str, Any]],
    category: str,
    min_samples: int,
) -> float:
    """Shrinkage factor (0.._MAX_SHRINK) for a category by its Brier history.

    0 when the category has fewer than `min_samples` resolved events (dormant).
    Otherwise scales linearly from 0 (perfect history) to _MAX_SHRINK (mean
    Brier at or beyond random).
    """
    briers = category_briers(resolved_records, category)
    if len(briers) < min_samples:
        return 0.0
    return _clamp(mean(briers) / _RANDOM_BRIER, 0.0, 1.0) * _MAX_SHRINK


def fuse_components(
    components: dict[str, float],
    weights: dict[str, float],
) -> float:
    """Weighted fusion of the current event's component probabilities.

    Only components present in both the event and the weight map are fused.
    Falls back to the LLM estimate when fewer than two overlap (no meaningful
    fusion) or the overlapping weights are degenerate - so a dormant or
    single-signal feedback leaves the published estimate unchanged.
    """
    present = {key: components[key] for key in components if key in weights}
    if len(present) < 2:
        return components["llm"]
    weight_sum = sum(weights[key] for key in present)
    if weight_sum <= 0:
        return components["llm"]
    return sum(components[key] * weights[key] for key in present) / weight_sum


def shrink_to_prior(value: float, prior: float, factor: float) -> float:
    """Pull `value` toward `prior` by `factor` (0 = unchanged, 1 = the prior)."""
    return value * (1.0 - factor) + prior * factor


def adjust_probability(
    components: dict[str, float],
    category: str,
    prior: float,
    *,
    resolved_records: list[dict[str, Any]] | None = None,
    min_samples: int | None = None,
) -> tuple[float, dict[str, Any]]:
    """Adjust an event's published probability using calibration history.

    `components` must contain at least "llm" (the anchored LLM estimate that is
    published today) and typically "market"; "cross_validation" when enabled.
    Fuses them by component Brier history, then shrinks the result toward the
    category prior by the category's Brier history. Returns (adjusted_pct, info).

    When history is insufficient (the dormant default) the weights are empty and
    the shrinkage is 0, so the adjusted value equals components["llm"] - making
    enabling the feature a no-op until enough outcomes accumulate.
    """
    if min_samples is None:
        min_samples = settings.CALIBRATION_FEEDBACK_MIN_SAMPLES
    if resolved_records is None:
        resolved_records = _load_resolved_records()

    weights = component_weights(resolved_records, min_samples)
    fused = fuse_components(components, weights)
    shrink = category_shrinkage(resolved_records, category, min_samples)
    final = shrink_to_prior(fused, prior, shrink)
    info = {
        "weights": {key: round(weight, 4) for key, weight in weights.items()},
        "shrinkage": round(shrink, 4),
        "fused": round(fused, 2),
        "samples": len(resolved_records),
    }
    return round(_clamp(final, 0.0, 100.0), 2), info


def _load_resolved_records() -> list[dict[str, Any]]:
    """Read every resolved event's record dict from the store (the one seam that
    does I/O). Best-effort: a store read failure degrades to no feedback rather
    than breaking analysis."""
    try:
        from app.memory.event_store import list_resolved_events

        return [(entry.get("record") or {}) for entry in list_resolved_events()]
    except Exception:
        logger.warning(
            "Failed to load resolved records for calibration feedback",
            exc_info=True,
        )
        return []
