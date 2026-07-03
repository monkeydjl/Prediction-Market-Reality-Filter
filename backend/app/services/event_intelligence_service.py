import asyncio
import hashlib
import itertools
import logging
import math
import time
from typing import Any

from app.core.config import settings
from app.services.scoring_service import (
    build_headline,
    build_probability_assessment,
    build_why_it_matters,
    calculate_impact_score,
    calculate_trust_score,
    calculate_value_score,
    impact_drivers,
    probability_direction,
    recommended_action,
    score_level,
)
from app.services.translation_service import translate_articles
from app.utils.full_text_fetcher import fetch_full_text
from app.utils.market_utils import safe_float
from app.utils.helpers import clamp01


logger = logging.getLogger(__name__)

# discover analyzes up to limit * this many candidates across all event sources;
# the cap bounds analyze_event / LLM calls per scan as sources are added.
_CANDIDATE_POOL_FACTOR = 3


_STRENGTH_TO_CONFIDENCE = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}


# Short labels for Prometheus metrics — reason strings can be long Chinese
# sentences; we map them to stable cardinality-bounded labels so the
# ``reason`` label dimension stays small and meaningful.
_REASON_LABEL_MAP = {
    "证据冲突": "high_conflict",
    "市场过薄": "thin_market",
    "来源不足": "source_insufficient",
    "wide_spread": "wide_spread",
    "spread": "wide_spread",
    "LLM 降级模式": "llm_degraded",
    "未校准类别": "uncalibrated_category",
    "证据冲突过高": "high_conflict",
}


def _short_reason(reason: str) -> str:
    """Map a long Chinese downgrade_reason to a short stable label for
    Prometheus. Falls back to 'other' when no known keyword matches —
    keeps label cardinality bounded."""
    for keyword, label in _REASON_LABEL_MAP.items():
        if keyword in reason:
            return label
    return "other"


def _build_actionable_recommendation(
    analysis: dict[str, Any],
    *,
    change: float,
) -> dict[str, Any] | None:
    """Build a structured actionable recommendation from the legacy signal.

    Returns None when:
    - ACTIONABLE_RECOMMENDATION_ENABLED is false
    - signal is WATCHLIST and edge is small (direction=WAIT but still returns
      a recommendation; only returns None when feature disabled)

    Maps legacy_analysis.signal -> direction (YES/NO/AVOID/WAIT) and
    signal_strength -> confidence (high/medium/low).
    """
    if not settings.ACTIONABLE_RECOMMENDATION_ENABLED:
        return None

    signal = str(analysis.get("signal") or "WATCHLIST")
    signal_direction = str(analysis.get("signal_direction") or "NEUTRAL")
    signal_strength = str(analysis.get("signal_strength") or "LOW")
    confidence = _STRENGTH_TO_CONFIDENCE.get(signal_strength, "low")

    # Direction from signal
    if signal_direction in ("LONG", "STRONG_LONG"):
        direction = "YES"
    elif signal_direction in ("SHORT", "STRONG_SHORT"):
        direction = "NO"
    else:
        direction = "WAIT"

    # AVOID override: high risk + low confidence
    risk_flags = analysis.get("risk_flags", [])
    if not isinstance(risk_flags, list):
        risk_flags = []
    if len(risk_flags) >= 2 and confidence == "low":
        direction = "AVOID"

    position_size = safe_float(analysis.get("position_size"), 0.02)
    suggested_allocation_pct = round(position_size * 100, 2)
    expected_edge = safe_float(analysis.get("expected_edge"), 0.0)
    edge_pct = round(expected_edge * 100, 2)
    risk_level = str(analysis.get("risk_level") or "UNKNOWN").lower()
    if risk_level not in ("low", "medium", "high"):
        risk_level = "medium"

    baseline = safe_float(analysis.get("market_probability"), 50.0)
    estimated = safe_float(analysis.get("ai_probability"), baseline)
    rationale = (
        f"市场定价 {baseline:.1f}%，估计 {estimated:.1f}%，"
        f"方向 {direction}，证据强度 {safe_float(analysis.get('evidence_strength'), 0.0):.2f}。"
    )
    # calibration_status is set by the caller (analyze_event) which has access
    # to segment stats; default to uncalibrated_provisional for the build_event_record
    # path (calibration_feedback may override later).
    calibration_status = "uncalibrated_provisional"

    return {
        "direction": direction,
        "confidence": confidence,
        "suggested_allocation_pct": suggested_allocation_pct,
        "edge": edge_pct,
        "risk_level": risk_level,
        "rationale": rationale,
        "calibration_status": calibration_status,
    }


def build_event_record(
    analysis: dict[str, Any],
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert legacy market analysis into an event intelligence record."""
    source_info = _source_info(source)
    risk_flags = analysis.get("risk_flags", [])
    if not isinstance(risk_flags, list):
        risk_flags = []
    risk_level = analysis.get("risk_level")
    if not isinstance(risk_level, str) or not risk_level.strip():
        risk_level = "UNKNOWN"
    evidence_direction = analysis.get("evidence_direction")
    if not isinstance(evidence_direction, str) or not evidence_direction.strip():
        evidence_direction = "neutral"
    question = str(
        analysis.get("event_question")
        or analysis.get("market_question")
        or ""
    ).strip()
    baseline = safe_float(analysis.get("market_probability"), 50.0)
    estimated_value = analysis.get("ai_probability")
    if estimated_value is None:
        estimated_value = analysis.get("final_probability")
    estimated = safe_float(estimated_value, baseline)
    change = round(estimated - baseline, 2)
    # Post-hoc risk: if AI probability deviates from market by >30pp, flag it.
    # Large deviations without strong evidence are likely hallucinations.
    if abs(change) > 30:
        tag = f"large_deviation_{abs(change):.0f}pp"
        if tag not in risk_flags:
            risk_flags.append(tag)
        if not evidence_direction or evidence_direction == "neutral":
            risk_flags.append("low_evidence_large_deviation")
    confidence = clamp01(analysis.get("confidence_score"))
    news_quality = clamp01(analysis.get("news_quality_score"))
    evidence_strength = clamp01(analysis.get("evidence_strength"))
    source_count = max(0, int(safe_float(analysis.get("source_count"), 0)))
    trust_score = calculate_trust_score(analysis)
    impact_score = calculate_impact_score(analysis)

    return {
        "event_id": _event_id(question),
        "event_title": question,
        "event_title_zh": str(analysis.get("title_zh") or "").strip()[:300],
        "event_summary": _summary(analysis),
        "probability": {
            "baseline": round(baseline, 2),
            "estimated": round(estimated, 2),
            "change": change,
            "direction": probability_direction(change),
        },
        "credibility": {
            "score": trust_score,
            "level": score_level(trust_score),
            "confidence": round(confidence, 3),
            "news_quality": round(news_quality, 3),
            "evidence_strength": round(evidence_strength, 3),
            "source_count": source_count,
        },
        "impact": {
            "score": impact_score,
            "level": score_level(impact_score),
            "drivers": impact_drivers(analysis),
        },
        "risk": {
            "level": risk_level,
            "flags": risk_flags,
        },
        "evidence": {
            "direction": evidence_direction,
            "strength": round(evidence_strength, 3),
            "conflict": round(clamp01(analysis.get("evidence_conflict_score")), 3),
            "freshness": round(clamp01(analysis.get("freshness_score")), 3),
            "resolution_relevance": round(
                clamp01(analysis.get("resolution_relevance_score")), 3
            ),
        },
        "source": source_info,
        "value_score": calculate_value_score(impact_score, trust_score),
        "tracking": _default_tracking(impact_score),
        "intelligence_report": {
            "headline": build_headline(
                str(analysis.get("title_zh") or "").strip() or question,
                change,
                trust_score,
                impact_score,
            ),
            "why_it_matters": build_why_it_matters(analysis, change),
            "probability_assessment": build_probability_assessment(
                baseline,
                estimated,
                trust_score,
            ),
            "recommended_action": recommended_action(
                trust_score,
                impact_score,
                change,
                signal_direction=analysis.get("signal_direction"),
                confidence=_STRENGTH_TO_CONFIDENCE.get(
                    str(analysis.get("signal_strength") or "LOW"), "low"
                ),
            ),
        },
        # legacy_analysis carries the full legacy market-analysis dict
        # (signal, position_size, expected_edge, ...) retained verbatim for
        # backward compatibility with /scan and /trades. The event layer does
        # NOT read it for its own logic; the event-facing fields above
        # (probability, credibility, impact, evidence, intelligence_report) are
        # the canonical surface.
        "legacy_analysis": analysis,
        "semantics": _build_semantics(analysis),
        "actionable_recommendation": _build_actionable_recommendation(
            analysis, change=change
        ),
        # Audit layer populated by analyze_event when EVIDENCE_BREAKDOWN_ENABLED
        # is on. Default empty so build_event_record callers always get a
        # complete EventRecord-shaped dict.
        "evidence_breakdown": [],
    }


def _build_all_overlays(
    record: dict[str, Any],
    *,
    analysis: dict[str, Any],
    sentiment_profile: dict[str, Any] | None,
    news_context: str,
    market_quote: dict[str, Any] | None,
    filtered_articles: list[dict[str, Any]] | None = None,
    volume: float | None = None,
    liquidity: float | None = None,
) -> None:
    """Build all 5 overlays + merge + guardrail in-place on ``record``.

    Shared between ``analyze_event`` (live) and ``replay_record`` (replay)
    so the overlay build sequence has a single source of truth. Pure
    pull-out from analyze_event; no behavior change. Best-effort: each
    overlay is wrapped in try/except and emits an error block on failure
    (matches live production behavior).
    """
    # Phase 1: Decision Quality overlay. Best-effort audit layer — wrapped in
    # try/except so a build failure never blocks event production. When the
    # feature flag is off, the record has no `decision_quality` key
    # (byte-identical to pre-Phase-1 records).
    try:
        if settings.DECISION_QUALITY_ENABLED:
            from app.services.decision_quality_service import build_decision_quality
            _overlay_t0 = time.perf_counter()
            dq = build_decision_quality(
                recommendation=record.get("actionable_recommendation"),
                evidence_breakdown=record.get("evidence_breakdown", []),
                enabled=True,
                max_items=settings.DECISION_QUALITY_MAX_EVIDENCE_ITEMS,
                high_threshold=settings.DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD,
                medium_threshold=settings.DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD,
            )
            record["decision_quality"] = dq
            # P0-6 metrics: overlay latency + downgrade counts.
            from app.utils.metrics import record_overlay_latency
            record_overlay_latency("decision_quality", time.perf_counter() - _overlay_t0)
            if isinstance(dq, dict) and dq.get("downgraded"):
                from app.utils.metrics import DECISION_QUALITY_DOWNGRADE, RULE_FIRE
                reason = dq.get("downgrade_reason") or "unknown"
                DECISION_QUALITY_DOWNGRADE.labels(reason=_short_reason(reason)).inc()
                RULE_FIRE.labels(rule="decision_quality_downgrade").inc()
    except Exception as exc:
        logger.warning("decision_quality build failed: %s", exc)
        from app.utils.metrics import record_overlay_build_failure
        record_overlay_build_failure("decision_quality")
        fallback_direction = (record.get("actionable_recommendation") or {}).get("direction", "WAIT")
        record["decision_quality"] = {
            "error": "build_failed",
            "raw_direction": fallback_direction,
            "displayed_direction": fallback_direction,
            "downgraded": False,
            "downgrade_reason": None,
            "decision_rationale_zh": "决策质量构建失败，使用原始方向。本分析仅供参考，不构成投资建议。",
            "supporting_evidence": [],
            "opposing_evidence": [],
            "conflict_score": 0.0,
            "consensus_level": "none",
            "reversal_triggers": [],
        }

    # Phase 2: Market Quality overlay. Best-effort audit layer — only
    # computed for ``source.type == "prediction_market"`` (Polymarket/Kalshi);
    # ``build_market_quality`` returns None for other source types
    # (Metaculus prediction_question, open_web, sports_event, manual), so the
    # record stays byte-identical to pre-Phase-2 for those sources. When the
    # feature flag is off, no ``market_quality`` key is attached.
    try:
        if settings.MARKET_QUALITY_ENABLED:
            from app.services.market_quality_service import build_market_quality
            _overlay_t0 = time.perf_counter()
            mq = build_market_quality(
                recommendation=record.get("actionable_recommendation"),
                source=record.get("source"),
                market_quote=record.get("market_quote"),
                volume=volume,
                liquidity=liquidity,
                max_spread_pct=settings.MARKET_MAX_SPREAD_PCT,
                min_liquidity=settings.MARKET_MIN_LIQUIDITY,
                min_volume=settings.MARKET_MIN_VOLUME,
                score_threshold=settings.MARKET_QUALITY_SCORE_THRESHOLD,
            )
            if mq is not None:
                record["market_quality"] = mq
                # P0-6 metrics: latency + downgrade counts.
                from app.utils.metrics import record_overlay_latency
                record_overlay_latency("market_quality", time.perf_counter() - _overlay_t0)
                if isinstance(mq, dict) and mq.get("downgraded"):
                    from app.utils.metrics import RULE_FIRE
                    RULE_FIRE.labels(rule="market_quality_downgrade").inc()
    except Exception as exc:
        logger.warning("market_quality build failed: %s", exc)
        from app.utils.metrics import record_overlay_build_failure
        record_overlay_build_failure("market_quality")
        # Fallback only for prediction_market sources (matches the gating
        # in ``build_market_quality``). Non-prediction-market sources stay
        # without the block, so the audit layer remains absent on failure
        # rather than emitting a misleading error block for an unrelated
        # source type.
        src = record.get("source")
        if isinstance(src, dict) and src.get("type") == "prediction_market":
            fallback_direction = (record.get("actionable_recommendation") or {}).get("direction", "WAIT")
            record["market_quality"] = {
                "error": "build_failed",
                "raw_direction": fallback_direction,
                "suggested_direction": fallback_direction,
                "downgraded": False,
                "applied_to_displayed_direction": False,
                "downgrade_reason": None,
                "score": 0.0,
                "liquidity_score": None,
                "volume_score": None,
                "spread_penalty": None,
                "thin_market_flag": False,
                "stale_price_flag": None,
            }

    # Phase 2b: Execution Quality overlay (Plan 3 §3.5). Best-effort audit
    # layer — only computed for ``source.type == "prediction_market"``.
    # ``build_execution_quality`` returns None for other source types, so
    # the record stays byte-identical to pre-Plan-3 for those sources.
    # When the feature flag is off, no ``execution_quality`` key is attached.
    try:
        if settings.EXECUTION_QUALITY_ENABLED:
            from app.services.execution_quality_service import build_execution_quality
            _overlay_t0 = time.perf_counter()
            eq = build_execution_quality(
                recommendation=record.get("actionable_recommendation"),
                source=record.get("source"),
                market_quote=record.get("market_quote"),
                volume=volume,
                liquidity=liquidity,
                max_spread_pct=settings.EXECUTION_MAX_SPREAD_PCT,
                stale_price_seconds=settings.EXECUTION_STALE_PRICE_SECONDS,
                min_liquidity=settings.EXECUTION_MIN_LIQUIDITY,
                target_order_size=settings.EXECUTION_TARGET_ORDER_SIZE,
                fee_rate_pct=settings.EXECUTION_FEE_RATE_PCT,
            )
            if eq is not None:
                record["execution_quality"] = eq
                from app.utils.metrics import record_overlay_latency
                record_overlay_latency("execution_quality", time.perf_counter() - _overlay_t0)
                if isinstance(eq, dict) and eq.get("downgraded"):
                    from app.utils.metrics import RULE_FIRE
                    RULE_FIRE.labels(rule="execution_quality_downgrade").inc()
    except Exception as exc:
        logger.warning("execution_quality build failed: %s", exc)
        from app.utils.metrics import record_overlay_build_failure
        record_overlay_build_failure("execution_quality")
        src = record.get("source")
        if isinstance(src, dict) and src.get("type") == "prediction_market":
            fallback_direction = (record.get("actionable_recommendation") or {}).get("direction", "WAIT")
            record["execution_quality"] = {
                "error": "build_failed",
                "executable": False,
                "effective_entry_price": None,
                "estimated_slippage_pct": None,
                "max_safe_position_size": None,
                "stale_price_flag": None,
                "platform_constraint_reasons": ["构建失败"],
                "raw_direction": fallback_direction,
                "suggested_direction": fallback_direction,
                "downgraded": False,
                "applied_to_displayed_direction": False,
            }

    # Phase 4: Source Reliability overlay. Best-effort audit layer — only
    # computed when ``evidence_breakdown`` is non-empty (prediction_market,
    # prediction_question, open_web). ``build_source_reliability`` returns
    # None for empty evidence_breakdown (e.g., sports_event with match stats),
    # so the record stays byte-identical to pre-Phase-4 for those sources.
    # When the feature flag is off, no ``source_reliability`` key is attached.
    try:
        if settings.SOURCE_RELIABILITY_ENABLED:
            from app.services.source_reliability_service import build_source_reliability
            raw_direction = (record.get("actionable_recommendation") or {}).get("direction", "WAIT")
            # Plan 4 §6.1: load source-trust-registry overrides (optional
            # prior). Best-effort — on failure, log and continue without
            # overrides (byte-identical to SOURCE_TRUST_REGISTRY_ENABLED=false).
            registry_overrides: list[dict[str, Any]] | None = None
            if settings.SOURCE_TRUST_REGISTRY_ENABLED:
                try:
                    from app.memory import source_trust_registry_store
                    registry_overrides = source_trust_registry_store.list_entries()
                except Exception as exc:
                    logger.warning(
                        "source_trust_registry load failed, continuing "
                        "without overrides: %s", exc
                    )
                    registry_overrides = None
            _overlay_t0 = time.perf_counter()
            sr = build_source_reliability(
                evidence_breakdown=record.get("evidence_breakdown", []),
                evidence_items=filtered_articles or [],
                raw_direction=raw_direction,
                enabled=True,
                score_threshold=settings.SOURCE_RELIABILITY_SCORE_THRESHOLD,
                min_trusted_ratio=settings.SOURCE_RELIABILITY_MIN_TRUSTED_RATIO,
                min_domain_diversity=settings.SOURCE_RELIABILITY_MIN_DOMAIN_DIVERSITY,
                min_sources=settings.SOURCE_RELIABILITY_MIN_SOURCES,
                registry_overrides=registry_overrides,
            )
            if sr is not None:
                record["source_reliability"] = sr
                # P0-6 metrics: latency + downgrade counts.
                from app.utils.metrics import record_overlay_latency
                record_overlay_latency("source_reliability", time.perf_counter() - _overlay_t0)
                if isinstance(sr, dict) and sr.get("downgraded"):
                    from app.utils.metrics import RULE_FIRE
                    RULE_FIRE.labels(rule="source_reliability_downgrade").inc()
    except Exception as exc:
        logger.warning("source_reliability build failed: %s", exc)
        from app.utils.metrics import record_overlay_build_failure
        record_overlay_build_failure("source_reliability")
        # Fallback only when evidence_breakdown is non-empty (matches the
        # gating in ``build_source_reliability``). Events without evidence
        # stay without the block, so the audit layer remains absent rather
        # than emitting a misleading error block.
        if record.get("evidence_breakdown"):
            fallback_direction = (record.get("actionable_recommendation") or {}).get("direction", "WAIT")
            record["source_reliability"] = {
                "error": "build_failed",
                "raw_direction": fallback_direction,
                "suggested_direction": fallback_direction,
                "downgraded": False,
                "applied_to_displayed_direction": False,
                "downgrade_reason": None,
                "overall_score": 0.0,
                "source_count": 0,
                "domain_diversity": 0,
                "trusted_source_ratio": 0.0,
                "official_source_count": 0,
                "unknown_source_ratio": 0.0,
                "source_breakdown": [],
            }

    # Merge overlays: most-strict direction wins (3-way: decision_quality +
    # market_quality + source_reliability). Sets
    # ``final_displayed_direction`` / ``final_downgrade_reason`` only when at
    # least one overlay produced a direction; otherwise both stay absent
    # (byte-identical to pre-overlay records when all features are off).
    try:
        from app.services.market_quality_service import merge_quality_overlays
        _overlay_t0 = time.perf_counter()
        final_direction, final_reason, market_applied, source_applied = merge_quality_overlays(
            record.get("decision_quality"),
            record.get("market_quality"),
            record.get("source_reliability"),
        )
        if final_direction is not None:
            record["final_displayed_direction"] = final_direction
            record["final_downgrade_reason"] = final_reason
            if market_applied and isinstance(record.get("market_quality"), dict):
                record["market_quality"]["applied_to_displayed_direction"] = True
            if source_applied and isinstance(record.get("source_reliability"), dict):
                record["source_reliability"]["applied_to_displayed_direction"] = True
        from app.utils.metrics import record_overlay_latency
        record_overlay_latency("merge", time.perf_counter() - _overlay_t0)
    except Exception as exc:
        logger.warning("merge_quality_overlays failed: %s", exc)
        from app.utils.metrics import record_overlay_build_failure
        record_overlay_build_failure("merge")

    # Phase 5: LLM Telemetry overlay. Best-effort observability layer —
    # applies to ALL events (every event makes at least one LLM call or
    # falls back to deterministic). Records degraded_mode, analysis_quality,
    # real token counts (from _ask_ai instrumentation), estimated cost, and
    # sentiment degradation flag. Pure observability — does NOT participate
    # in merge_quality_overlays and does NOT mutate any overlay block.
    try:
        if settings.LLM_TELEMETRY_ENABLED:
            from app.services.llm_telemetry_service import build_llm_telemetry
            _overlay_t0 = time.perf_counter()
            record["llm_telemetry"] = build_llm_telemetry(
                analysis=analysis,
                sentiment_profile=sentiment_profile,
                news_context=news_context,
                model=settings.OPENAI_MODEL,
                enabled=True,
            )
            from app.utils.metrics import record_overlay_latency
            record_overlay_latency("llm_telemetry", time.perf_counter() - _overlay_t0)
    except Exception as exc:
        logger.warning("llm_telemetry build failed: %s", exc)
        from app.utils.metrics import record_overlay_build_failure
        record_overlay_build_failure("llm_telemetry")
        record["llm_telemetry"] = {
            "error": "build_failed",
            "degraded_mode": (analysis or {}).get("analysis_quality") == "deterministic_fallback",
            "degraded_reason": None,
            "analysis_quality": (analysis or {}).get("analysis_quality", "unknown"),
            "sentiment_degraded": False,
            "llm_call_count": 0,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "estimated_token_cost": 0.0,
            "model": settings.OPENAI_MODEL,
        }

    # P0-8: Strategy-layer Guardrails. Apply global risk controls AFTER the
    # per-event overlay merge has produced final_displayed_direction AND after
    # llm_telemetry is populated (rule 1 reads llm_telemetry.degraded_mode).
    # The guardrail service is pure / synchronous / deterministic; the I/O
    # (calibration_summary read) happens here so the service stays pure. When
    # GUARDRAILS_ENABLED=false, evaluate_guardrails is a no-op and no keys
    # are attached (byte-identical to pre-guardrail records). Best-effort:
    # any failure here is logged and the pre-guardrail direction is preserved.
    try:
        if settings.GUARDRAILS_ENABLED:
            from app.services.guardrail_service import (
                evaluate_guardrails,
                extract_qualified_categories,
            )
            # Best-effort fetch of qualified categories from the calibration
            # store. A read failure (or no calibration data yet) passes None
            # to evaluate_guardrails, which treats None as "skip the
            # qualification check" — the cold-start path stays unblocked.
            qualified_cats: set[str] | None = None
            try:
                from app.memory.prediction_store import calibration_summary
                summary = calibration_summary()
                qualified_cats = extract_qualified_categories(
                    summary.get("segments")
                )
            except Exception as exc:
                logger.debug(
                    "calibration_summary unavailable for guardrails: %s", exc
                )
            fired_dir, fired_reason, fired_rules = evaluate_guardrails(
                final_direction=record.get("final_displayed_direction"),
                final_downgrade_reason=record.get("final_downgrade_reason"),
                record=record,
                enabled=True,
                llm_degraded_blocks_act=settings.GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT,
                uncalibrated_category_blocks_act=settings.GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT,
                high_conflict_blocks_act=settings.GUARDRAIL_HIGH_CONFLICT_BLOCKS_ACT,
                high_conflict_threshold=settings.GUARDRAIL_HIGH_CONFLICT_THRESHOLD,
                market_not_executable_blocks_act=settings.GUARDRAIL_MARKET_NOT_EXECUTABLE_BLOCKS_ACT,
                qualified_categories=qualified_cats,
            )
            if fired_rules:
                # Capture pre-guardrail direction BEFORE overwriting so we
                # can detect a strong->WAIT downgrade below.
                pre_guardrail_dir = record.get("final_displayed_direction")
                record["final_displayed_direction"] = fired_dir
                record["final_downgrade_reason"] = fired_reason
                # Record which guardrails fired (audit trail for operators /
                # metrics). Only attached when at least one rule fired —
                # absent key = no guardrail fired (matches the existing
                # convention of "no key when feature off / no-op").
                record["guardrail_fired"] = fired_rules
                # P0-6 metrics: count each guardrail rule fire via RULE_FIRE
                # (with rule label for finer-grained attribution). We do NOT
                # also increment FINAL_DIRECTION_CHANGE here — that counter is
                # the save_events() single source of truth for "an event's
                # final_displayed_direction changed across an update", and
                # save_events will catch this guardrail-induced change when
                # the record is persisted. Double-counting would inflate the
                # metric by 1 per guardrail-triggered save.
                from app.utils.metrics import RULE_FIRE
                for rule_name in fired_rules:
                    RULE_FIRE.labels(rule=rule_name).inc()
    except Exception as exc:
        logger.warning("guardrail evaluation failed: %s", exc)

    # Plan 4 §6.2: Review Queue detectors. Best-effort — runs pure-function
    # detectors after the guardrail + final_displayed_direction is set and
    # enqueues candidates into review_queue_store. Wrapped in try/except so
    # a detector or enqueue failure NEVER blocks event production. When
    # REVIEW_QUEUE_ENABLED=false (default), this block is a no-op —
    # byte-identical to pre-Plan-4.
    try:
        if settings.REVIEW_QUEUE_ENABLED:
            from app.services.review_queue_detectors import detect_review_candidates
            from app.memory import review_queue_store
            event_id = record.get("event_id")
            if event_id:
                candidates = detect_review_candidates(
                    record,
                    mismatch_confidence_threshold=settings.REVIEW_QUEUE_MISMATCH_CONFIDENCE,
                    auto_resolve_confidence_threshold=settings.REVIEW_QUEUE_AUTO_RESOLVE_CONFIDENCE,
                )
                for candidate in candidates:
                    try:
                        review_queue_store.enqueue_item(
                            event_id=event_id,
                            trigger=candidate["trigger"],
                            severity=candidate["severity"],
                            reason=candidate["reason"],
                            context=candidate.get("context", {}),
                        )
                    except Exception as exc:
                        logger.warning(
                            "review_queue enqueue failed for event %s "
                            "trigger %s: %s",
                            event_id, candidate.get("trigger"), exc,
                        )
    except Exception as exc:
        logger.warning("review_queue detector run failed: %s", exc)


async def analyze_event(
    event_question: str,
    baseline_probability: float = 50.0,
    news_context: str = "",
    source: dict[str, Any] | None = None,
    volume: float | None = None,
    liquidity: float | None = None,
    sentiment_profile: dict[str, Any] | None = None,
    market_quote: dict[str, Any] | None = None,
    filtered_articles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from app.services.ai_analysis_service import analyze_market
    from app.services.cross_validation_service import credibility_delta, cross_validate

    sports_context = _build_sports_analysis_context(event_question, source)
    combined_context = _append_context(
        news_context,
        sports_context.get("context", ""),
    )
    # Fold the LLM sentiment summary into the prompt context as a dedicated
    # signal alongside the structured evidence. Guard with `if sentiment_summary:`
    # so a missing/empty summary (e.g. the neutral fallback) is a no-op and the
    # integration stays additive, never blocking.
    sentiment_summary = ""
    if isinstance(sentiment_profile, dict):
        sentiment_summary = str(sentiment_profile.get("summary") or "").strip()
    analysis = await analyze_market(
        market_question=event_question,
        market_probability=baseline_probability,
        news_context=combined_context,
        volume=volume,
        liquidity=liquidity,
        sentiment_summary=sentiment_summary,
    )
    record = build_event_record(analysis, source=source)
    cross = await cross_validate(
        question=event_question,
        news_context=combined_context,
        primary_probability=record["probability"]["estimated"],
        market_baseline=baseline_probability,
    )
    if cross is not None:
        record["cross_validation"] = cross
        credibility = record["credibility"]
        adjusted = max(0, min(100, credibility["score"] + credibility_delta(cross["agreement"])))
        credibility["score"] = adjusted
        credibility["level"] = score_level(adjusted)
    if sports_context.get("context"):
        record["sports_context"] = {
            "fact_count": sports_context.get("fact_count", 0),
            "signals": sports_context.get("signals", {}),
            "facts": sports_context.get("facts", []),
        }
    if sentiment_profile is not None:
        record["sentiment_profile"] = sentiment_profile
    if market_quote is not None:
        record["market_quote"] = market_quote
    _apply_calibration_feedback(record, analysis, cross)
    from app.services.evidence_aggregation_service import aggregate_evidence_breakdown

    if settings.EVIDENCE_BREAKDOWN_ENABLED and sentiment_profile and filtered_articles:
        record["evidence_breakdown"] = aggregate_evidence_breakdown(
            sentiment_profile.get("articles", []),
            filtered_articles,
        )
    else:
        record["evidence_breakdown"] = []

    _build_all_overlays(
        record,
        analysis=analysis,
        sentiment_profile=sentiment_profile,
        news_context=combined_context,
        market_quote=market_quote,
        filtered_articles=filtered_articles,
        volume=volume,
        liquidity=liquidity,
    )
    return record


def _apply_calibration_feedback(
    record: dict[str, Any],
    analysis: dict[str, Any],
    cross: dict[str, Any] | None,
) -> None:
    """Record the probability signals and, when enabled, fold calibration
    history back into the published estimate.

    The component probabilities (market baseline, anchored LLM estimate, and the
    cross-validation model when present) are ALWAYS recorded under
    `calibration_components`, so a per-component Brier history can accumulate as
    events resolve - this is the data the feedback loop later weights by, and it
    must be captured even while the loop is off.

    When settings.CALIBRATION_FEEDBACK_ENABLED is on, the recorded signals are
    fused (weighted by each component's Brier history) and shrunk toward the
    base-rate prior (by the category's Brier history), and the result overwrites
    the published probability. Until enough outcomes have accumulated this is a
    no-op (the adjusted value equals the LLM estimate), so the default-off and
    early-on behavior is identical to today's single-LLM estimate.
    """
    probability = record["probability"]
    components = {
        "market": probability["baseline"],
        "llm": probability["estimated"],
    }
    if cross is not None and _looks_numeric(cross.get("probability")):
        components["cross_validation"] = float(cross["probability"])
    record["calibration_components"] = components

    if not settings.CALIBRATION_FEEDBACK_ENABLED:
        return

    from app.services.calibration_feedback_service import adjust_probability

    category = str(analysis.get("base_rate_category") or "unknown")
    prior = safe_float(analysis.get("base_rate_prior"), probability["baseline"])
    adjusted, info = adjust_probability(components, category, prior)

    baseline = probability["baseline"]
    probability["estimated"] = adjusted
    probability["change"] = round(adjusted - baseline, 2)
    probability["direction"] = probability_direction(probability["change"])
    record["calibration_feedback"] = info
    record["intelligence_report"]["probability_assessment"] = (
        build_probability_assessment(
            baseline, adjusted, record["credibility"]["score"]
        )
    )


async def analyze_event_question(
    event_question: str,
    baseline_probability: float = 50.0,
    news_context: str | None = None,
    volume: float | None = None,
    liquidity: float | None = None,
) -> dict[str, Any]:
    if news_context is not None:
        record = await analyze_event(
            event_question=event_question,
            baseline_probability=baseline_probability,
            news_context=news_context,
            source={"type": "manual"},
            volume=volume,
            liquidity=liquidity,
        )
    else:
        filtered_news = await _build_filtered_news(event_question)
        record = await analyze_event(
            event_question=event_question,
            baseline_probability=baseline_probability,
            news_context=filtered_news["context"],
            source={"type": "manual"},
            volume=volume,
            liquidity=liquidity,
            sentiment_profile=filtered_news.get("sentiment_profile"),
            filtered_articles=filtered_news.get("articles", []),
        )
        record["news_filter"] = filtered_news["summary"]
        articles = await translate_articles(filtered_news.get("articles") or [])
        record["evidence_items"] = build_evidence_items(articles)

    _persist_events([record])
    return record


async def _collect_candidate_events(
    limit: int,
    shared_articles: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Fetch candidate events from every event source concurrently, isolating a
    failing source so one down source does not break discovery.

    Market sources produce candidates from prices; the open-web source extracts
    them from `shared_articles` (the same feed articles used for evidence), so an
    article can become an event subject, not just evidence. Each source is asked
    for `limit` candidates. The per-source lists are round-robin interleaved and
    the merged pool is capped at ``limit * _CANDIDATE_POOL_FACTOR``. Interleaving
    keeps every source represented under the cap (a plain concatenation would let
    the first source fill the whole budget); the cap bounds how many analyze_event
    / LLM calls a scan makes as sources are added. discover_events still ranks the
    pool by value_score and returns the top ``limit``.
    """
    from app.services.event_extraction_service import extract_candidate_events
    from app.services.kalshi_event_source import (
        fetch_candidate_events as fetch_kalshi_events,
    )
    from app.services.manifold_event_source import (
        fetch_candidate_events as fetch_manifold_events,
    )
    from app.services.metacus_event_source import (
        fetch_candidate_events as fetch_metaculus_events,
    )
    from app.services.polymarket_event_source import (
        fetch_candidate_events as fetch_polymarket_events,
        fetch_crypto_candidate_events as fetch_polymarket_crypto_events,
    )
    from app.services.world_cup_event_source import (
        fetch_candidate_events as fetch_world_cup_events,
    )

    candidate_sources: list[tuple[str, Any]] = [
        ("Polymarket", fetch_polymarket_events),
        ("Manifold", fetch_manifold_events),
        ("Kalshi", fetch_kalshi_events),
    ]
    # Opt-in crypto-only Polymarket fetch. The default Polymarket fetch ranks by
    # volume, so geopolitics crowds crypto out of the top-N; this adds a
    # crypto-only fetch as an extra candidate source so crypto markets reach the
    # pool. Dedupe keeps cross-source duplicates out (a crypto market surfacing
    # in both the default and the crypto-only fetch is analyzed once).
    if settings.POLYMARKET_CRYPTO_FETCH_ENABLED:
        candidate_sources.append(("Polymarket Crypto", fetch_polymarket_crypto_events))
    if settings.WORLD_CUP_SOURCE_ENABLED:
        candidate_sources.append(("World Cup", fetch_world_cup_events))
    # Metaculus requires an API token; auto-disabled when unset so an empty
    # source never makes authenticated network calls. The adapter itself also
    # short-circuits on the empty token, but checking here keeps it out of the
    # interleaved labels and the gather() call entirely.
    if settings.METACULUS_API_TOKEN:
        candidate_sources.append(("Metaculus", fetch_metaculus_events))
    labels = [name for name, _ in candidate_sources] + ["Open Web"]
    # Apply per-source weight multipliers: the primary market source (Polymarket)
    # gets more of the candidate budget, supplementary sources get less.  Keeps
    # the round-robin interleave balanced under the cap while shifting the event
    # mix toward real market prices.
    _weights = settings.SOURCE_WEIGHTS

    def _src_limit(name: str) -> int:
        return max(1, int(limit * _weights.get(name, 1.0)))

    results = await asyncio.gather(
        *(fetch(_src_limit(name)) for name, fetch in candidate_sources),
        extract_candidate_events(
            shared_articles or [], _src_limit("Open Web"),
        ) if settings.OPEN_WEB_ENABLED else asyncio.sleep(0, result=[]),
        return_exceptions=True,
    )
    per_source: list[list[dict[str, Any]]] = []
    for label, result in zip(labels, results):
        if isinstance(result, Exception):
            logger.warning("Event source failed [%s]: %s", label, result)
            # Report source failure to status tracker
            try:
                from app.services.discovery_status import source_done
                asyncio.ensure_future(source_done(label, 0, str(result)[:200]))
            except Exception:
                pass
            continue
        per_source.append(result)
        try:
            from app.services.discovery_status import source_done
            asyncio.ensure_future(source_done(label, len(result)))
        except Exception:
            pass

    # Round-robin across sources so the cap keeps every source represented.
    merged = [
        candidate
        for tier in itertools.zip_longest(*per_source)
        for candidate in tier
        if candidate is not None
    ]
    # Drop cross-source duplicates before the cap, so the same real-world event
    # surfacing from multiple sources is analyzed once (higher-priority source
    # kept). Runs before analysis, saving LLM calls.
    from app.services.candidate_dedup_service import dedupe_candidates

    deduped = dedupe_candidates(merged)
    # ── Cross-source matching: World Cup → prediction market ───────────
    # World Cup events are only kept when a prediction-market event covers
    # the same topic.  Matched World Cup analysis is injected as extra
    # evidence (sports_context) into the market event; unmatched World Cup
    # events are dropped.
    if settings.WORLD_CUP_SOURCE_ENABLED:
        deduped = _cross_match_world_cup(deduped)
    return deduped[: limit * _CANDIDATE_POOL_FACTOR]


_WORLD_CUP_KEYWORDS: set[str] = {
    "world cup", "fifa", "knockout", "group stage", "final",
    "semifinal", "quarterfinal", "goal", "champion", "qualify",
    "world cup 2026", "2026 fifa",
}


def _cross_match_world_cup(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge World Cup candidate evidence into matching prediction-market events.

    For each World Cup candidate, search the prediction-market candidates for
    a matching event (keyword overlap on the question text).  When a match is
    found, the World Cup baseline / analysis data is attached as
    ``sports_context`` on the market candidate and the World Cup entry is
    dropped.  Unmatched World Cup entries are also dropped.
    """
    market: list[dict[str, Any]] = []
    world_cup: list[dict[str, Any]] = []
    for c in candidates:
        src = c.get("source") or {}
        if src.get("type") == "sports_event":
            world_cup.append(c)
        else:
            market.append(c)

    if not world_cup or not market:
        return market  # nothing to match

    # Build keyword sets for market candidates
    market_tokens: list[set[str]] = []
    for m in market:
        q = str(m.get("question", "") or "").lower()
        market_tokens.append(set(q.split()))

    matched_wc: set[int] = set()
    for wi, wc in enumerate(world_cup):
        wc_q = str(wc.get("question", "") or "").lower()
        wc_tokens = set(wc_q.split())

        best_idx = -1
        best_overlap = 0
        for mi, mt in enumerate(market_tokens):
            overlap = len(wc_tokens & mt)
            if overlap > best_overlap:
                best_overlap = overlap
                best_idx = mi

        # Require a meaningful overlap: at least 2 keyword tokens in common,
        # or explicit World Cup keyword presence on both sides.
        wc_has_kw = any(kw in wc_q for kw in _WORLD_CUP_KEYWORDS)
        mkt_has_kw = any(kw in str(market[best_idx].get("question", "") or "").lower()
                         for kw in _WORLD_CUP_KEYWORDS) if best_idx >= 0 else False

        if best_idx >= 0 and (best_overlap >= 2 or (wc_has_kw and mkt_has_kw)):
            # Attach World Cup analysis as supplementary evidence
            target = market[best_idx]
            sports = target.setdefault("sports_context", {})
            sports["world_cup_signal"] = {
                "question": wc.get("question"),
                "baseline_probability": wc.get("baseline_probability"),
                "volume": wc.get("volume"),
                "liquidity": wc.get("liquidity"),
            }
            matched_wc.add(wi)

    dropped = len(world_cup) - len(matched_wc)
    if dropped:
        logger.info(
            "Cross-match: %d World Cup candidates dropped (no market match), %d merged",
            dropped, len(matched_wc),
        )
    return market


async def discover_events(
    limit: int = 10,
    use_cache: bool = True,
) -> dict[str, Any]:
    from app.memory.event_cache import get_cached_event, set_cached_event
    from app.services.event_collection_service import collect_shared_articles
    from app.services.discovery_status import (  # status tracking
        reset as status_reset,
        set_phase as status_phase,
        event_analyzed as status_event,
        event_saved as status_saved,
        done as status_done,
        fail as status_fail,
    )

    await status_reset(limit)

    # Query-independent feeds are fetched once per scan and reused twice: as
    # open-web event candidates (extraction) and as shared evidence for every
    # candidate below.
    await status_phase("collecting", "获取 RSS 和候选事件…")
    try:
        shared_articles = await collect_shared_articles()
        candidate_events = await _collect_candidate_events(
            limit, shared_articles=shared_articles
        )
    except Exception as exc:
        await status_fail(f"数据源收集失败: {exc}")
        raise

    if not candidate_events:
        await status_fail("未获取到任何候选事件 — 检查数据源（Polymarket/Kalshi/Manifold）是否可达")
        return {"platform": "Event Intelligence Platform",
                "source": "Multi-source event discovery",
                "count": 0, "events": [],
                "status": {"message": "No candidates from any source"}}

    await status_phase("analyzing", f"开始分析 {len(candidate_events)} 个候选事件…")
    semaphore = asyncio.Semaphore(getattr(settings, "LLM_CONCURRENCY", 4))

    total_errors = 0

    async def process_event(
        candidate: dict[str, Any],
    ) -> tuple[dict[str, Any], bool] | None:
        """Analyze one candidate. Returns (record, is_new).

        is_new is False when the record came from the per-question cache; such
        records are still returned for the response but must NOT be re-audited,
        because their probability snapshot is unchanged and re-auditing would
        append duplicate snapshots that pollute trend analysis and grow the
        audit log without bound.
        """
        async with semaphore:
            try:
                question = str(candidate.get("question") or "").strip()
                if not question:
                    return None

                if use_cache:
                    cached = get_cached_event(question)
                    if cached is not None:
                        return cached, False

                filtered_news = await _build_filtered_news(
                    question, shared_articles=shared_articles
                )
                source = candidate.get("source")
                market_quote = candidate.get("bid_ask")
                sports_context = _build_sports_analysis_context(question, source)
                # Merge World Cup cross-matched evidence if present
                if candidate.get("sports_context", {}).get("world_cup_signal"):
                    wc = candidate["sports_context"]["world_cup_signal"]
                    sports_context.setdefault("wc_baseline", wc.get("baseline_probability"))
                    sports_context.setdefault("wc_question", wc.get("question"))
                    if not sports_context.get("context"):
                        sports_context["context"] = f"World Cup analysis: {wc.get('question', '')}"
                # Prediction-market events carry a real baseline probability from
                # the market itself.  Require matching news only for open-web
                # extracted events (where there is no market price anchor).
                is_market_event = (source or {}).get("type") == "prediction_market"
                if (
                    not is_market_event
                    and filtered_news["summary"]["selected_count"] == 0
                    and not sports_context.get("context")
                ):
                    return None

                record = await analyze_event(
                    event_question=question,
                    baseline_probability=safe_float(
                        candidate.get("baseline_probability"), 50.0
                    ),
                    news_context=filtered_news["context"],
                    source=source,
                    volume=candidate.get("volume"),
                    liquidity=candidate.get("liquidity"),
                    sentiment_profile=filtered_news.get("sentiment_profile"),
                    market_quote=market_quote,
                    filtered_articles=filtered_news.get("articles", []),
                )
                record["news_filter"] = filtered_news["summary"]
                articles = await translate_articles(filtered_news.get("articles") or [])
                record["evidence_items"] = build_evidence_items(articles)
                if use_cache:
                    set_cached_event(question, record)
                await status_event(question, success=True)
                return record, True
            except Exception as exc:
                logger.warning(
                    "Event discovery failed [%s]: %s",
                    str(candidate.get("question", ""))[:80],
                    exc,
                )
                q = str(candidate.get("question", ""))
                await status_event(q, success=False, error=str(exc)[:200])
                return None

    # Use asyncio.wait (not wait_for+gather) so on timeout we keep already-
    # completed results instead of losing the whole batch. Still-running tasks
    # are cancelled; done tasks are extracted and persisted as partial results.
    tasks = [asyncio.ensure_future(process_event(c)) for c in candidate_events]
    done, pending = await asyncio.wait(
        tasks, timeout=settings.EVENT_DISCOVER_TIMEOUT_SECONDS
    )
    timed_out = bool(pending)
    if timed_out:
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        logger.warning(
            "[discover_events] Hard timeout after %ds — %d/%d candidates "
            "completed, %d discarded",
            settings.EVENT_DISCOVER_TIMEOUT_SECONDS,
            len(done), len(tasks), len(pending),
        )
    # Extract results from done tasks in original candidate order (asyncio.wait
    # returns a set, so we iterate the task list to stay deterministic).
    # process_event swallows its own exceptions and returns None; .result()
    # only raises on an unexpected bug, treated as a failure so the scan stays
    # resilient. Pending (timed-out) tasks contribute None.
    raw = []
    for t in tasks:
        if t in done:
            try:
                raw.append(t.result())
            except Exception as exc:
                logger.warning("process_event task crashed: %s", exc)
                raw.append(None)
        else:
            raw.append(None)
    results = [item for item in raw if item is not None]
    events = [record for record, _ in results]
    events.sort(key=lambda item: item.get("value_score", 0), reverse=True)
    # Persist / audit only freshly-analyzed records. Cached records already
    # have their snapshot; re-auditing them would append duplicate snapshots.
    fresh = [record for record, is_new in results if is_new]
    _persist_events(fresh)
    await status_saved(len(events[:limit]))

    error_count = len(tasks) - len(results)
    if timed_out and not events:
        await status_fail(
            f"分析超时 ({settings.EVENT_DISCOVER_TIMEOUT_SECONDS}s)，0 个结果保存。"
            f"请降低 EVENT_DISCOVER_LIMIT 或增加 EVENT_DISCOVER_TIMEOUT_SECONDS"
        )
    else:
        await status_done(len(events[:limit]), error_count)
    return {
        "platform": "Event Intelligence Platform",
        "source": "Multi-source event discovery",
        "count": len(events[:limit]),
        "events": events[:limit],
        "status": {
            "candidates": len(candidate_events),
            "analyzed": len(done),
            "errors": error_count,
            "results": len(events[:limit]),
            **({"timeout": True} if timed_out else {}),
        },
    }


def _persist_events(records: list[dict[str, Any]]) -> None:
    """Durable persistence + audit + prediction freeze for event records.

    Each stage has its own error boundary so a failure in one does not silently
    swallow the others (a single shared try/except previously meant a freeze
    error could leave events saved but predictions missing, with only a generic
    warning):

    - save_events is the gate: if the durable store write fails, abort (audit and
      freeze would reference unsaved events), logging the failure explicitly.
    - record_event (audit) failures are isolated per event and never block the
      freeze - the audit log is observability, not the loop's source of truth.
    - freeze_prediction failures are isolated per event and logged with the
      event_id + reason, so a missing prediction is visible, not hidden.
    """
    if not records:
        return
    from app.memory.event_store import save_events
    from app.memory.prediction_store import freeze_prediction
    from app.services.event_audit_service import record_event
    from app.memory.event_market_link_store import get_verified_link, upsert_link

    try:
        saved_entries = save_events(records)
    except Exception as exc:
        # The store write is the foundation; without it audit/freeze would
        # reference unsaved events. Abort the batch and surface it.
        logger.error("Event store write failed, skipping audit/freeze: %s", exc)
        return

    for record in [entry["record"] for entry in saved_entries]:
        event_id = record.get("event_id")
        # Discovery-time contract linking: for market-derived events, create a
        # verified link using the source_id (market contract id) immediately.
        # This enables the contract-id settlement path in auto_resolve from day
        # one, instead of requiring a text match first. Idempotent — upsert_link
        # is a no-op when a verified link already exists for this event_id.
        try:
            source = record.get("source") or {}
            source_id = source.get("source_id")
            platform = source.get("platform", "")
            if (
                source_id
                and source.get("type") == "prediction_market"
                and not get_verified_link(event_id)
            ):
                upsert_link(
                    event_id,
                    market_name=platform,
                    contract_id=str(source_id),
                    market_question=source.get("question", record.get("event_title", "")),
                    resolution_criteria=(record.get("semantics") or {}).get(
                        "resolution_criteria", ""
                    ),
                    link_method="discovery",
                    link_confidence=1.0,
                    verified=True,
                )
        except Exception as exc:
            logger.warning("Discovery-time link failed for %s: %s", event_id, exc)
        try:
            record_event(record)
        except Exception as exc:
            logger.warning("Event audit failed for %s: %s", event_id, exc)
        try:
            # Freeze a committed prediction for market-derived events. Idempotent
            # and market-gated inside the store, so re-scans and news events are
            # safe no-ops (no market price -> no edge -> no prediction).
            if (record.get("legacy_analysis") or {}).get("analysis_quality") == "deterministic_fallback":
                logger.warning(
                    "Skipping prediction freeze for fallback analysis [%s]",
                    event_id,
                )
                continue
            pred = freeze_prediction(record)
            # Auto-create a simulated trade for paper-trading evaluation.
            # Gate: PAPER_TRADE_ENABLED must be true; for watch-grade events
            # PAPER_TRADE_WATCH_ENABLED must also be true.
            trade_dec = pred.get("decision") if pred else None
            create_trade = False
            if trade_dec in ("act", "provisional_act"):
                create_trade = getattr(settings, "PAPER_TRADE_ENABLED", False)
            elif trade_dec == "watch" and getattr(settings, "PAPER_TRADE_WATCH_ENABLED", False):
                create_trade = getattr(settings, "PAPER_TRADE_ENABLED", False)
            if pred and create_trade:
                try:
                    from app.memory.simulated_trade_store import open_trade
                    rec = record.get("actionable_recommendation") or {}
                    direction = rec.get("direction") if rec.get("direction") in ("YES", "NO") else "YES"
                    ai_prob = pred.get("ai_probability", 50.0)
                    mkt_prob = pred.get("market_probability", 50.0)
                    entry_edge = ai_prob - mkt_prob
                    # Edge-direction consistency: if the system thinks the
                    # probability is LOWER than the market, the correct trade
                    # is NO (short the overpriced YES). Conversely, if higher,
                    # the correct trade is YES. Override the recommendation
                    # direction when it contradicts the edge sign.
                    if entry_edge > 0 and direction == "NO":
                        direction = "YES"
                    elif entry_edge < 0 and direction == "YES":
                        direction = "NO"
                    position = rec.get("suggested_allocation_pct", None)
                    if position is None:
                        position = {"act": 4.0, "provisional_act": 2.0, "watch": 1.0}.get(trade_dec, 2.0)
                    open_trade(
                        event_id,
                        event_title=record.get("event_title_zh") or record.get("event_title", ""),
                        direction=direction,
                        entry_prob=ai_prob,
                        market_prob=mkt_prob,
                        confidence=round((record.get("credibility") or {}).get("score", 50), 1),
                        trust_weight=pred.get("trust"),
                        decision=trade_dec,
                        position_pct=float(position),
                    )
                except Exception as exc:
                    logger.warning("Simulated trade creation failed for %s: %s", event_id, exc)
        except Exception as exc:
            logger.warning("Prediction freeze failed for %s: %s", event_id, exc)


async def _build_filtered_news(
    event_question: str,
    shared_articles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from app.services.event_collection_service import collect_articles
    from app.services.market_semantics_service import parse_market_semantics
    from app.services.news_filter_service import filter_news_for_market
    from app.services.news_sentiment_service import analyze_sentiment
    from app.services.semantic_relevance_service import annotate_semantic_relevance

    articles = await collect_articles(event_question, shared_articles=shared_articles)
    # Opt-in semantic relevance (no-op unless EMBEDDING_MODEL is configured);
    # filter_news_for_market blends it with keyword relevance. Pass the parsed
    # semantics so the embedding query is enriched with the event's entities and
    # resolution conditions - critical for price-threshold questions (e.g. crypto
    # "reach $2,000") whose relevant news shares little surface vocabulary with
    # the question and would otherwise be dropped by keyword relevance alone.
    semantics = parse_market_semantics(event_question)
    await annotate_semantic_relevance(event_question, articles, semantics)
    filtered = filter_news_for_market(
        market_question=event_question,
        articles=articles,
    )
    # Full-text enrichment moved here from collect_articles so the per-event
    # HTTP budget (NEWS_FULL_TEXT_MAX_ARTICLES fetches) is spent on the articles
    # that survived relevance filtering (the most-relevant ones reach the LLM),
    # not the source-order top-N (which filter_news_for_market may drop). Reads
    # the cap at call time so monkeypatches on settings take effect.
    #
    # Fail-closed pattern preserved: gather(return_exceptions=True) so one
    # slow/failing URL never breaks the batch; fetch_full_text also returns None
    # on internal failure, but the isinstance(str) guard safely absorbs both
    # None and exception objects.
    enriched_articles = filtered.get("articles") or []
    full_text_cap = settings.NEWS_FULL_TEXT_MAX_ARTICLES
    if settings.NEWS_FULL_TEXT_FETCH_ENABLED:
        top_articles = enriched_articles[:full_text_cap]
        full_text_tasks = [fetch_full_text(a.get("url", "")) for a in top_articles]
        full_texts = await asyncio.gather(*full_text_tasks, return_exceptions=True)
        for article, full_text in zip(top_articles, full_texts):
            if isinstance(full_text, str) and full_text:
                article["full_text"] = full_text
            else:
                article["full_text"] = None
        for article in enriched_articles[full_text_cap:]:
            article["full_text"] = None
    else:
        for article in enriched_articles:
            article["full_text"] = None
    # LLM sentiment analysis on the filtered articles. analyze_sentiment returns
    # a neutral fallback on any failure (never raises), so this is purely
    # additive - a fallback flows through transparently without breaking the
    # pipeline.
    filtered["sentiment_profile"] = await analyze_sentiment(
        event_question, enriched_articles
    )
    # ── Phase 4: Fuse LLM sentiment into the evidence profile ─────────────
    # The keyword-based evidence profile is computed before sentiment (inside
    # filter_news_for_market). Now that sentiment is available, blend it in so
    # the LLM sentiment direction/strength formally participates in the
    # evidence signal that flows into clamp_probability and confidence scoring.
    from app.services.evidence_scoring_service import apply_sentiment_fusion
    from app.services.news_filter_service import build_news_context

    sentiment = filtered.get("sentiment_profile")
    evidence = filtered.get("evidence_profile")
    if sentiment and evidence:
        apply_sentiment_fusion(evidence, sentiment)
        semantics = filtered.get("market_semantics") or {}
        filtered["context"] = build_news_context(enriched_articles, evidence, semantics)
    return filtered


def _build_sports_analysis_context(
    event_question: str,
    source: dict[str, Any] | None,
) -> dict[str, Any]:
    source = source or {}
    if source.get("type") != "sports_event":
        return {}
    from app.services.sports_fact_service import load_sports_facts
    from app.services.sports_signal_service import (
        build_sports_signals,
        render_sports_context,
    )

    tournament = str(source.get("tournament") or "2026 FIFA World Cup")
    facts = load_sports_facts(tournament=tournament)
    bundle = build_sports_signals(event_question, source, facts)
    context = render_sports_context(bundle)
    return {**bundle, "context": context}


def _append_context(news_context: str, extra_context: str) -> str:
    parts = [part.strip() for part in (news_context, extra_context) if part and part.strip()]
    return "\n\n".join(parts)


def _priority_from_score(score: int) -> str:
    return {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}.get(
        score_level(score), "medium"
    )


def _default_tracking(impact_score: int) -> dict[str, str]:
    """Default human-tracking decision for a freshly analyzed event.

    status starts at "watching"; priority seeds from impact level. A user's
    explicit choice is preserved across re-scans by event_store.save_events.
    """
    return {"status": "watching", "priority": _priority_from_score(impact_score)}


def build_evidence_items(articles: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Per-item evidence rows for the detail UI, from the filtered news articles.

    Each article already carries kind (official/news), source, url, recency and
    quality/relevance scores from the news filter. The backend computes evidence
    direction only in aggregate (there is no per-item stance), so each item
    exposes quality / relevance rather than a fabricated supports/contradicts.
    """
    items: list[dict[str, Any]] = []
    for article in articles or []:
        title = str(article.get("title") or "").strip()
        if not title:
            continue
        item = {
            "kind": article.get("kind") or "news",
            "source": str(article.get("source") or "").strip(),
            "title": title[:300],
            "summary": str(article.get("description") or "").strip()[:500],
            "url": str(article.get("url") or "").strip(),
            "published": str(article.get("published") or "").strip(),
            "quality": round(clamp01(article.get("quality_score")), 3),
            "relevance": round(clamp01(article.get("relevance_score")), 3),
        }
        # Chinese translations (added by translation_service.translate_articles
        # during discovery) carry through when present; the UI shows zh with the
        # English original as fallback. Absent for untranslated/manual flows.
        title_zh = str(article.get("title_zh") or "").strip()
        if title_zh:
            item["title_zh"] = title_zh[:300]
        summary_zh = str(article.get("summary_zh") or "").strip()
        if summary_zh:
            item["summary_zh"] = summary_zh[:500]
        items.append(item)
    return items


def _summary(analysis: dict[str, Any]) -> str:
    return str(
        analysis.get("narrative_summary")
        or analysis.get("reasoning")
        or "暂无摘要。"
    )[:500]


def _source_info(source: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {"type": "manual"}
    clean = {
        key: value
        for key, value in source.items()
        if (
            isinstance(value, (int, float, bool))
            or (isinstance(value, str) and value.strip())
        )
    }
    return clean or {"type": "manual"}


def _build_semantics(analysis: dict[str, Any]) -> dict[str, Any] | None:
    """Build the EventSemantics dict from analysis, or None when empty.

    Populated from the LLM analysis output (resolution_criteria, time_horizon,
    entities). Returns None when all three are empty/blank so records without
    structured semantics stay small and the field's absence is meaningful.
    """
    resolution_criteria = str(analysis.get("resolution_criteria") or "").strip()
    time_horizon = str(analysis.get("time_horizon") or "").strip()
    entities_raw = analysis.get("entities")
    entities = (
        [str(item).strip() for item in entities_raw if str(item or "").strip()]
        if isinstance(entities_raw, list)
        else []
    )
    if not resolution_criteria and not time_horizon and not entities:
        return None
    return {
        "resolution_criteria": resolution_criteria,
        "time_horizon": time_horizon,
        "entities": entities,
    }


def _event_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _looks_numeric(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
