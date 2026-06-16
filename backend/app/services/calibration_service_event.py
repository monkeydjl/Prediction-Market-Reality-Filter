"""calibration_service_event.py
================================
Event-layer probability calibration.

Scores how accurate an event's probability estimate was against its settled
outcome, and aggregates those scores across events. This is the event-layer
parallel of the market-layer `calibration_service` - it does NOT import or
depend on that module (the two layers stay decoupled).

Scoring convention (same as market-layer calibration_service):
  Brier score = ((predicted/100) - (actual/100))^2
    0.00 = perfect, 0.25 = random guessing, 1.00 = fully wrong
  Skill score = 1 - brier/0.25
    >0 beats random, <0 is worse than random
  Grade bands mirror the market layer for consistency.

Pure and deterministic: numbers in, dicts out. No I/O, no network. The caller
(the resolve endpoint) supplies the latest estimate from the audit trajectory
and the outcome; this module only does the math.

Event vocabulary only - no trading terms.
"""

from collections import defaultdict
import math
from typing import Any

from app.utils.market_utils import safe_float


def _clamp_pct(value: Any) -> float:
    """Clamp a 0-100 probability (defensive: upstream should already be in range,
    but calibration reads from stored records, so a bad value must not produce a
    non-finite Brier or an out-of-range grade)."""
    return max(0.0, min(100.0, safe_float(value, 50.0)))


def brier_score(predicted_pct: float, actual_pct: float) -> float:
    """Brier score for two 0-100 probabilities.

    0.0 = perfect, 0.25 = a random 50/50 guess against a binary outcome,
    1.0 = a fully wrong confident prediction.
    """
    return ((predicted_pct / 100.0) - (actual_pct / 100.0)) ** 2


def skill_score(brier: float) -> float:
    """Rescale Brier so >0 beats random (0.25) and <0 is worse than random."""
    return 1.0 - brier / 0.25


def grade(brier: float) -> str:
    """Letter grade for a Brier score, mirroring the market-layer bands."""
    if brier <= 0.05:
        return "EXCELLENT"
    if brier <= 0.10:
        return "GOOD"
    if brier <= 0.15:
        return "ACCEPTABLE"
    if brier <= 0.20:
        return "POOR"
    return "RANDOM_LEVEL"


def score_event(
    estimated: float,
    actual_outcome: float,
    trajectory_observations: int,
    trajectory_span_hours: float | None,
) -> dict[str, Any]:
    """Compute the calibration snapshot for one resolved event.

    `estimated` is the latest probability estimate (0-100) that is being
    scored; `actual_outcome` is the settled outcome (0-100). The trajectory_*
    fields are carried through as context (not used in the score) so a reviewer
    can tell how much tracking history the score rests on. Returns a
    Calibration-shaped dict; it is validated against the Calibration model when
    attached to a record.
    """
    # Defensive: estimated comes from the audit trajectory / record baseline,
    # actual_outcome from the resolve request. Both should already be 0-100, but
    # a stray bad value would otherwise produce a non-finite or misleading Brier.
    estimated = _clamp_pct(estimated)
    actual_outcome = _clamp_pct(actual_outcome)
    brier = brier_score(estimated, actual_outcome)
    return {
        "brier_score": round(brier, 4),
        "skill_score": round(skill_score(brier), 4),
        "grade": grade(brier),
        "estimated_probability": round(estimated, 2),
        "actual_outcome": round(actual_outcome, 2),
        "trajectory_observations": int(trajectory_observations),
        "trajectory_span_hours": trajectory_span_hours,
    }


def summarize(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate per-event calibration across all resolved events.

    `events` is a list of dicts each carrying a `calibration` dict, a `source`
    descriptor, and optionally a `base_rate_category`. Returns an overall block
    (brier / skill / grade / n) plus two parallel breakdowns: by_source (keyed
    by the source's platform) and by_base_rate_category (keyed by the event's
    base-rate category, falling back to "unknown"). Empty input yields a
    no_data overall and empty breakdowns, so the endpoint is safe before any
    events are resolved.
    """
    rows = []
    for event in events:
        calibration = event.get("calibration")
        if not isinstance(calibration, dict):
            continue
        brier = calibration.get("brier_score")
        if brier is None:
            continue
        try:
            brier_value = float(brier)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(brier_value):
            # A non-finite Brier (NaN/inf) would poison the aggregate; skip it.
            continue
        source = event.get("source") or {}
        platform = source.get("platform") or source.get("type") or "unknown"
        category = str(event.get("base_rate_category") or "unknown")
        rows.append((brier_value, str(platform), category))

    if not rows:
        return {"overall": _empty_overall(), "by_source": {}, "by_base_rate_category": {}}

    all_briers = [b for b, _, _ in rows]
    overall = _aggregate(all_briers)

    by_source: dict[str, list[float]] = defaultdict(list)
    by_category: dict[str, list[float]] = defaultdict(list)
    for brier, platform, category in rows:
        by_source[platform].append(brier)
        by_category[category].append(brier)
    return {
        "overall": overall,
        "by_source": {
            platform: _aggregate(briers) for platform, briers in by_source.items()
        },
        "by_base_rate_category": {
            category: _aggregate(briers) for category, briers in by_category.items()
        },
    }


def _aggregate(briers: list[float]) -> dict[str, Any]:
    avg = sum(briers) / len(briers)
    return {
        "brier_score": round(avg, 4),
        "skill_score": round(skill_score(avg), 4),
        "grade": grade(avg),
        "n": len(briers),
    }


def _empty_overall() -> dict[str, Any]:
    return {"brier_score": None, "skill_score": None, "grade": "no_data", "n": 0}
