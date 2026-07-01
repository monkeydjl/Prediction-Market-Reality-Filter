"""ReplayRunner: re-run Phase 1-5 overlays + merge + guardrail on a
frozen event record under a ReplayConfig.

Frozen input contract: the caller guarantees the record contains the
LLM-era artifacts (legacy_analysis, market_quote, sentiment_profile,
evidence_breakdown, source). We never call analyze_market / cross_validate
/ translate_articles / fetch_full_text — those would require live LLM +
network. If a required input is missing, the overlay's existing try/except
produces an error block (same as live production behavior).
"""
from __future__ import annotations

import copy
import logging
from contextlib import nullcontext
from typing import Any

from app.replay.config import ReplayConfig, apply_replay_config

logger = logging.getLogger(__name__)


def replay_record(record: dict[str, Any], cfg: ReplayConfig) -> dict[str, Any]:
    """Re-run all 5 overlays + merge + guardrail on a frozen record.

    Returns a deep copy of ``record`` with overlay fields recomputed under
    ``cfg``. Does not mutate the input. Idempotent: calling twice with the
    same cfg produces the same output.
    """
    replayed = copy.deepcopy(record)

    # Strip existing overlay fields so re-running produces fresh values.
    # Without this, build_decision_quality would short-circuit on a cached
    # block and the replay would just echo the original.
    for key in (
        "decision_quality",
        "market_quality",
        "source_reliability",
        "llm_telemetry",
        "execution_quality",
        "final_displayed_direction",
        "final_downgrade_reason",
        "guardrail_fired",
    ):
        replayed.pop(key, None)

    with apply_replay_config(cfg):
        _rebuild_overlays(replayed, original_record=record)

    return replayed


def _rebuild_overlays(
    replayed: dict[str, Any],
    *,
    original_record: dict[str, Any],
) -> None:
    """Run _build_all_overlays on ``replayed`` using inputs recovered from
    the original record. Mutates ``replayed`` in place.

    LLM-era inputs (news_context / filtered_articles) are not persisted by
    analyze_event, so we use empty-string / empty-list defaults. The 5
    overlay build functions do not read news_context itself (Phase 1 reads
    evidence_breakdown, not the raw context). filtered_articles is only used
    to re-aggregate evidence_breakdown; when empty, we fall back to the
    evidence_breakdown already on the record (preserved by replay_record's
    non-strip list above).
    """
    from app.services.event_intelligence_service import _build_all_overlays

    analysis = original_record.get("legacy_analysis", {}) or {}
    sentiment_profile = original_record.get("sentiment_profile")
    market_quote = original_record.get("market_quote")

    # volume / liquidity were analyze_event function args, not persisted on
    # the record. Recover from market_quote if present, else None.
    volume = None
    liquidity = None
    if isinstance(market_quote, dict):
        volume = market_quote.get("volume")
        liquidity = market_quote.get("liquidity")

    _build_all_overlays(
        replayed,
        analysis=analysis,
        sentiment_profile=sentiment_profile,
        news_context="",  # not persisted; overlays don't read it directly
        market_quote=market_quote,
        filtered_articles=None,  # not persisted; evidence_breakdown preserved
        volume=volume,
        liquidity=liquidity,
    )


def simulate_llm_degraded(
    replayed: dict[str, Any],
    cfg: ReplayConfig | None = None,
) -> None:
    """Force llm_telemetry.degraded_mode=True and re-run only the guardrail
    layer. Used by preset_llm_degraded to verify llm_degraded_blocks_act
    fires without requiring a real LLM failure.

    Mutates ``replayed`` in place. Assumes replay_record has already been
    called (so llm_telemetry / final_displayed_direction / etc. are populated
    or absent per the cfg that was used).

    ``cfg``: when provided, the guardrail re-run is wrapped in
    ``apply_replay_config(cfg)`` so the config's guardrail flags (e.g.
    ``guardrail_llm_degraded_blocks_act=True`` from preset_llm_degraded)
    are active. Without this, the CLI's ``run_replay`` would call this
    function after ``replay_record`` returns — at which point
    ``apply_replay_config`` has already exited and restored settings to
    their defaults (where ``GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT=False``), so
    the rule would never fire. When ``cfg`` is None (existing unit tests
    that patch ``settings`` directly), the current settings are used as-is.
    """
    if not isinstance(replayed.get("llm_telemetry"), dict):
        # Nothing to degrade — llm_telemetry wasn't built (flag was off).
        return

    replayed["llm_telemetry"]["degraded_mode"] = True
    replayed["llm_telemetry"]["analysis_quality"] = "deterministic_fallback"

    # Re-run guardrail only: strip the guardrail outputs so evaluate_guardrails
    # runs fresh with the degraded llm_telemetry. Other overlays are unaffected
    # because guardrail only reads llm_telemetry.degraded_mode + record fields.
    pre_guardrail_dir = replayed.get("final_displayed_direction")
    pre_guardrail_reason = replayed.get("final_downgrade_reason")
    replayed.pop("guardrail_fired", None)

    try:
        from app.core.config import settings
        # Wrap the guardrail re-run in apply_replay_config when a cfg is
        # provided, so the config's guardrail flags are active. Without
        # this, the CLI cannot trigger llm_degraded_blocks_act because
        # replay_record's apply_replay_config has already exited.
        ctx = apply_replay_config(cfg) if cfg is not None else nullcontext()
        with ctx:
            if not settings.GUARDRAILS_ENABLED:
                return
            from app.services.guardrail_service import (
                evaluate_guardrails,
                extract_qualified_categories,
            )
            qualified_cats: set[str] | None = None
            try:
                from app.memory.prediction_store import calibration_summary
                summary = calibration_summary()
                qualified_cats = extract_qualified_categories(summary.get("segments"))
            except Exception as exc:
                logger.debug("calibration_summary unavailable for degraded replay: %s", exc)
            fired_dir, fired_reason, fired_rules = evaluate_guardrails(
                final_direction=pre_guardrail_dir,
                final_downgrade_reason=pre_guardrail_reason,
                record=replayed,
                enabled=True,
                llm_degraded_blocks_act=settings.GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT,
                uncalibrated_category_blocks_act=settings.GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT,
                high_conflict_blocks_act=settings.GUARDRAIL_HIGH_CONFLICT_BLOCKS_ACT,
                high_conflict_threshold=settings.GUARDRAIL_HIGH_CONFLICT_THRESHOLD,
                qualified_categories=qualified_cats,
            )
            if fired_rules:
                replayed["final_displayed_direction"] = fired_dir
                replayed["final_downgrade_reason"] = fired_reason
                replayed["guardrail_fired"] = fired_rules
    except Exception as exc:
        logger.warning("simulate_llm_degraded guardrail re-run failed: %s", exc)
