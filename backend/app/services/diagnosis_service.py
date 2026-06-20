"""diagnosis_service.py
====================
Disagreement Diagnosis (M2): turn a prediction's raw edge into a trust-weighted
adjusted edge and an act/watch/skip decision.

Philosophy 7: a divergence from the market is a hypothesis, not an edge. We trust
a divergence only in a category where our past resolved predictions actually beat
the market (positive skill); in a category where we scored at random, trust is 0 and
the adjusted edge collapses to 0. Until a category has enough resolved samples to
judge (dormant), trust falls back to a conservative default and the Decision Gate
caps the verdict at "watch" - an unproven segment never earns "act".

Pure functions: callers (prediction_store) pass in the segment's calibration stats
and the market liquidity, so this module imports no store and is trivially testable.
"""

from typing import Any

from app.core.config import settings
from app.services.calibration_service_event import skill_score
from app.utils.helpers import clamp01


def calibration_trust(
    segment_stats: dict[str, Any],
    *,
    min_samples: int,
    dormant_trust: float,
    qualified_floor: float = 0.0,
) -> float:
    """Trust in 0..1 for a divergence, from the segment's calibration history.

    Dormant (fewer than min_samples scored predictions in the segment) -> the
    conservative default. Otherwise trust = clamp(skill, floor, 1): a segment
    that historically beat the market earns high trust; one at or below random
    earns `qualified_floor`. The floor (>0) stops a worse-than-random segment
    from collapsing to trust 0 forever - at 0 the adjusted edge is always 0, so
    it only ever skips, skip rows are excluded from segment_skill, and its Brier
    can never improve (an absorbing state). A small floor keeps the penalty
    severe yet lets a large raw edge occasionally clear the watch gate, so the
    segment keeps sampling and can recover.
    """
    n = segment_stats.get("n") or 0
    mean_brier = segment_stats.get("mean_brier")
    if n < min_samples or mean_brier is None:
        return dormant_trust
    return round(max(qualified_floor, clamp01(skill_score(mean_brier))), 4)


def liquidity_factor(liquidity: float, *, floor: float) -> float:
    """Liquidity weight in 0..1. Unknown / non-positive liquidity -> 1.0 (do not
    penalize what we cannot measure; cross-platform units differ - refined later).
    Otherwise ramps linearly from 0 to 1 as liquidity approaches `floor`."""
    if liquidity is None or liquidity <= 0 or floor <= 0:
        return 1.0
    return round(clamp01(liquidity / floor), 4)


def decide(
    adjusted_edge: float,
    *,
    qualified: bool,
    act_edge: float,
    watch_edge: float,
) -> str:
    """act / watch / skip from the adjusted edge. "act" requires a qualified
    (non-dormant) segment, so an unproven segment caps at "watch" regardless of
    how large the divergence is."""
    magnitude = abs(adjusted_edge)
    if qualified and magnitude >= act_edge:
        return "act"
    if magnitude >= watch_edge:
        return "watch"
    return "skip"


def diagnose(
    raw_edge: float,
    segment_stats: dict[str, Any],
    liquidity: float,
) -> dict[str, Any]:
    """Diagnose one divergence. Returns the verdict plus the inputs behind it so
    a reviewer can see WHY (these are frozen with the prediction, not recomputed):

    trust           = calibration_trust(segment) in 0..1
    adjusted_edge   = raw_edge * trust * liquidity_factor(liquidity)
    decision        = act / watch / skip (act only for a qualified segment)
    liquidity_factor= the 0..1 liquidity weight applied
    qualified       = segment has >= min_samples resolved (act+watch) predictions
    segment_n       = that sample count
    segment_skill   = the segment's skill score (None when dormant)
    """
    min_samples = settings.CALIBRATION_FEEDBACK_MIN_SAMPLES
    trust = calibration_trust(
        segment_stats,
        min_samples=min_samples,
        dormant_trust=settings.DIAGNOSIS_DORMANT_TRUST,
        qualified_floor=settings.DIAGNOSIS_TRUST_FLOOR,
    )
    liq = liquidity_factor(liquidity, floor=settings.DIAGNOSIS_LIQUIDITY_FLOOR)
    adjusted_edge = round(raw_edge * trust * liq, 2)
    segment_n = segment_stats.get("n") or 0
    qualified = segment_n >= min_samples
    decision = decide(
        adjusted_edge,
        qualified=qualified,
        act_edge=settings.DECISION_ACT_EDGE,
        watch_edge=settings.DECISION_WATCH_EDGE,
    )
    return {
        "trust": trust,
        "adjusted_edge": adjusted_edge,
        "decision": decision,
        "liquidity_factor": liq,
        "qualified": qualified,
        "segment_n": segment_n,
        "segment_skill": segment_stats.get("skill"),
    }
