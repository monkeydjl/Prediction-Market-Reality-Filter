"""Strategy-layer Guardrails (P0-8 §6.3 minimum set).

Pure-function layer that applies *global* risk controls AFTER the per-event
overlay merge has produced ``final_displayed_direction``. Unlike the per-event
overlays (decision_quality / market_quality / source_reliability), guardrails
are policy-level constraints that fire regardless of how good a single
event's evidence looks — they exist to bound systemic risk.

This is a fail-closed layer: when a guardrail fires, the recommendation is
downgraded (escalated in severity, never de-escalated). The fired guardrail
is appended to ``final_downgrade_reason`` so the operator can see *why* the
final direction changed.

Like the other overlays, this is a pure, synchronous, deterministic function
with no LLM / I/O dependencies. The caller (event_intelligence_service)
extracts scalar config values and passes them explicitly. The function never
mutates the input record.

Rules implemented (Phase 1 minimum set):

1. ``llm_degraded_blocks_act``
   When ``llm_telemetry.degraded_mode=True`` (LLM fell back to deterministic),
   any YES/NO recommendation is forced to WAIT. The system should not commit
   to a strong stance when the underlying analysis degraded.

2. ``uncalibrated_category_blocks_act``
   When the event's category is not "qualified" (``prediction_store.segments``
   reports ``qualified=False`` or the category is absent), any YES/NO is
   forced to WAIT. Unproven segments must accumulate samples before they
   earn the right to recommend action.

3. ``high_conflict_blocks_act``
   When ``decision_quality.conflict_score >= threshold``, any YES/NO is
   forced to WAIT. High evidence conflict means the system cannot confidently
   pick a side, regardless of how the market overlay scored.

Rules deferred to P1 (require state — daily counters):

- ``daily_llm_cost_cap`` — needs a date-keyed cost counter
- ``daily_yes_count_cap`` — needs a date-keyed YES counter
- ``per_source_exposure_cap`` — needs a per-source daily counter

These are intentionally excluded from the P0 minimum set.

The function returns a 3-tuple ``(direction, reason, fired)``:

- ``direction`` — the (possibly downgraded) final direction. ``None`` when
  the input ``final_direction`` is ``None`` (no overlay produced a direction).
- ``reason`` — the new ``final_downgrade_reason``. Existing reason text is
  preserved; guardrail reason is appended with `` | `` separator.
- ``fired`` — list of fired guardrail names (for metrics instrumentation).
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Direction severity ordering (most-strict wins). YES/NO are "strong"
# directions that can be downgraded; WAIT/AVOID cannot be downgraded further
# by these guardrails (they are already conservative).
_STRONG_DIRECTIONS = ("YES", "NO")

# Separator for combining multiple downgrade reasons. The same separator is
# used by merge_quality_overlays for 3-way overlay reason combination.
_REASON_SEPARATOR = " | "


def evaluate_guardrails(
    *,
    final_direction: str | None,
    final_downgrade_reason: str | None,
    record: dict[str, Any],
    enabled: bool,
    llm_degraded_blocks_act: bool,
    uncalibrated_category_blocks_act: bool,
    high_conflict_blocks_act: bool,
    high_conflict_threshold: float,
    qualified_categories: set[str] | None = None,
) -> tuple[str | None, str | None, list[str]]:
    """Apply the strategy-layer guardrails to the post-merge final direction.

    Pure function: no LLM, no I/O, no settings reads, no mutation of inputs.
    When ``enabled=False``, returns the inputs unchanged (byte-identical to
    pre-guardrail behavior) so the feature flag is safe to leave off.

    Args:
        final_direction: The post-merge ``final_displayed_direction``. ``None``
            when no overlay produced a direction.
        final_downgrade_reason: The post-merge ``final_downgrade_reason``.
            Preserved and extended; never overwritten.
        record: The full event record dict. Read-only. Used to extract
            ``llm_telemetry``, ``decision_quality``, and category.
        enabled: Master feature flag. ``False`` = no-op.
        llm_degraded_blocks_act: When True, rule 1 is active.
        uncalibrated_category_blocks_act: When True, rule 2 is active.
        high_conflict_blocks_act: When True, rule 3 is active.
        high_conflict_threshold: conflict_score >= this triggers rule 3.
        qualified_categories: Set of qualified category names (from
            ``prediction_store.calibration_summary().segments``). When a
            category is missing from this set OR ``qualified=False``, rule 2
            fires. Pass ``None`` to skip the qualification check (treat all
            categories as qualified — useful when calibration data is empty).

    Returns:
        ``(new_final_direction, new_final_downgrade_reason, fired_rules)``.
        ``fired_rules`` is a list of rule names (e.g. ``["llm_degraded_blocks_act"]``)
        for metrics instrumentation. Empty when no guardrail fired.
    """
    if not enabled:
        return final_direction, final_downgrade_reason, []

    if final_direction is None:
        # No overlay produced a direction — guardrails have nothing to gate.
        return final_direction, final_downgrade_reason, []

    if final_direction not in _STRONG_DIRECTIONS:
        # WAIT/AVOID already conservative — guardrails cannot escalate further.
        return final_direction, final_downgrade_reason, []

    fired: list[str] = []
    new_direction = final_direction
    new_reasons: list[str] = []

    # Preserve existing reason text (do not drop it).
    if final_downgrade_reason:
        new_reasons.append(final_downgrade_reason)

    # Rule 1: LLM degraded mode blocks strong actions.
    if llm_degraded_blocks_act and _is_llm_degraded(record):
        fired.append("llm_degraded_blocks_act")
        new_reasons.append("LLM 降级模式触发护栏，强方向降级为 WAIT。")

    # Rule 2: Uncalibrated category blocks strong actions.
    if uncalibrated_category_blocks_act and _is_uncalibrated_category(record, qualified_categories):
        fired.append("uncalibrated_category_blocks_act")
        new_reasons.append("未校准类别触发护栏，强方向降级为 WAIT。")

    # Rule 3: High evidence conflict blocks strong actions.
    if high_conflict_blocks_act and _has_high_conflict(record, high_conflict_threshold):
        fired.append("high_conflict_blocks_act")
        new_reasons.append("证据冲突过高触发护栏，强方向降级为 WAIT。")

    if not fired:
        return final_direction, final_downgrade_reason, []

    # All fired rules escalate to WAIT. (AVOID escalation would require a
    # separate, more severe rule set — not in the P0 minimum.)
    new_direction = "WAIT"
    new_reason = _REASON_SEPARATOR.join(new_reasons) if new_reasons else None
    return new_direction, new_reason, fired


def _is_llm_degraded(record: dict[str, Any]) -> bool:
    """Rule 1 helper: True when ``llm_telemetry.degraded_mode`` is True."""
    lt = record.get("llm_telemetry")
    if not isinstance(lt, dict):
        return False
    return bool(lt.get("degraded_mode"))


def _is_uncalibrated_category(
    record: dict[str, Any],
    qualified_categories: set[str] | None,
) -> bool:
    """Rule 2 helper: True when the event's category is not qualified.

    When ``qualified_categories`` is ``None``, returns False (treat all
    categories as qualified — useful for cold-start deployments with no
    calibration data yet). When a non-None set is passed, fail-closed:
    any category NOT in the qualified set triggers the rule, including
    novel categories that have no segment data. This is the correct
    behavior for a guardrail — the cold-start bypass is
    ``qualified_categories=None``, not "category absent from the set".
    """
    if qualified_categories is None:
        return False
    category = _extract_category(record)
    if not category:
        # Defensive: _extract_category always returns non-empty (worst case
        # "general"), but if a future change makes it return "" we fail
        # closed here — an unknown category is treated as unqualified.
        return True
    return category not in qualified_categories


def _has_high_conflict(record: dict[str, Any], threshold: float) -> bool:
    """Rule 3 helper: True when ``decision_quality.conflict_score >= threshold``."""
    dq = record.get("decision_quality")
    if not isinstance(dq, dict):
        return False
    try:
        conflict = float(dq.get("conflict_score") or 0.0)
    except (TypeError, ValueError):
        return False
    return conflict >= threshold


def _extract_category(record: dict[str, Any]) -> str:
    """Extract the event's base-rate category. Mirrors event_store._category."""
    legacy = record.get("legacy_analysis") or {}
    source = record.get("source") or {}
    if source.get("type") == "sports_event":
        return "sports_event"
    return str(
        legacy.get("base_rate_category")
        or source.get("type")
        or source.get("platform")
        or "general"
    )


def extract_qualified_categories(segments: dict[str, Any] | None) -> set[str]:
    """Helper for callers: extract the set of qualified category names from
    ``prediction_store.calibration_summary().segments``.

    Each segment is shaped like ``{"n": ..., "qualified": bool, ...}``. Returns
    an empty set when ``segments`` is None or empty.
    """
    if not isinstance(segments, dict):
        return set()
    qualified: set[str] = set()
    for category, segment in segments.items():
        if not isinstance(segment, dict):
            continue
        if segment.get("qualified"):
            qualified.add(str(category))
    return qualified
