"""
ai_analysis_service.py

Legacy compatibility adapter for the analysis stack.

`analyze_market` is the single public entry point used by the older
prediction-market surface (the scheduler, the /analysis and /scan routes) and,
through `event_intelligence_service.analyze_event`, by the Event Intelligence
flow. It orchestrates the two layers below it - the probability engine and the
report generator - and assembles the legacy market/signal-shaped result dict
those callers expect. Its signature and output are intentionally unchanged.

The actual logic now lives in:
  - app/services/probability_engine_service.py  (LLM I/O, probability math, parsing)
  - app/services/analysis_report_service.py      (signal / risk report generation)

The names re-exported below are imported here so existing callers that do
`from app.services.ai_analysis_service import <symbol>` (e.g. scanner.scan_debug)
keep working after the split. New code should import from the engine or report
module directly rather than relying on these re-exports.
"""

import logging
from typing import Any

from app.core.config import settings
from app.services.base_rate_service import anchor_probability, classify_market, get_base_rate_context

logger = logging.getLogger(__name__)
from app.services.probability_engine_service import (
    _ask_ai,
    _clamp,
    _normalize_ai_analysis,
    apply_confidence_caps,
    apply_longshot_guardrail,
    build_deterministic_fallback_analysis,
    calculate_confidence_score,
    calculate_evidence_quality,
    calculate_priced_in_risk_score,
    clamp_probability,
    constrain_probability,
    default_evidence_profile,
    extract_evidence_profile,
    extract_semantics_profile,
    score_news_quality,
    translate_title,
)
from app.services.analysis_report_service import (
    build_risk_flags,
    calculate_narrative_risk_score,
    calculate_position_size,
    calculate_risk_level,
    calculate_signal,
    calculate_signal_direction,
    calculate_signal_strength,
    passes_analysis_quality_gate,
)


async def analyze_market(
    market_question: str,
    market_probability: float,
    news_context: str,
    volume: float | None = None,
    liquidity: float | None = None,
    sentiment_summary: str = "",
    market_microstructure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    market_probability = _clamp(market_probability, 0, 100)
    news_quality_score = score_news_quality(news_context)
    evidence_profile = extract_evidence_profile(news_context)
    semantics_profile = extract_semantics_profile(news_context)
    priced_in_risk_score = calculate_priced_in_risk_score(
        market_probability=market_probability,
        evidence_profile=evidence_profile,
        volume=volume,
        liquidity=liquidity,
        market_microstructure=market_microstructure,
    )

    # ── Compute prompt-enrichment context ─────────────────────────────────
    # Base rate: give the LLM the historical prior for this market category
    base_rate_context = get_base_rate_context(market_question)
    # Deadline: extract time-to-deadline info from the semantics in news_context
    deadline_info = _extract_deadline_from_context(semantics_profile, news_context)

    used_fallback = False
    try:
        raw_analysis = await _ask_ai(
            market_question=market_question,
            market_probability=market_probability,
            news_context=news_context,
            sentiment_summary=sentiment_summary,
            base_rate_context=base_rate_context,
            deadline_info=deadline_info,
        )
    except Exception as exc:
        # LLM unavailable / invalid output: fall back to the deterministic
        # evidence-only estimate. MUST log so a provider outage does not silently
        # degrade every analysis to fallback quality - that is the worst kind of
        # failure (output still looks plausible, quality collapses, no signal).
        logger.warning(
            "LLM analysis failed, using deterministic fallback "
            "[question=%.80s]: %s",
            market_question, exc,
        )
        raw_analysis = build_deterministic_fallback_analysis(
            market_probability=market_probability,
            evidence_profile=evidence_profile,
            news_quality_score=news_quality_score,
            priced_in_risk_score=priced_in_risk_score,
            semantics_profile=semantics_profile,
        )
        # The deterministic fallback never produces title_zh. Make a lightweight
        # LLM call just for the title so new events get Chinese names even when
        # the full analysis degrades. Fail-closed: empty string on any error.
        title_zh = await translate_title(market_question)
        if title_zh:
            raw_analysis["title_zh"] = title_zh
        used_fallback = True

    # Phase 5: extract _llm_usage before _normalize_ai_analysis (which copies
    # only known keys and would drop it). None when fallback was used.
    llm_usage = raw_analysis.pop("_llm_usage", None) if isinstance(raw_analysis, dict) else None
    normalized = _normalize_ai_analysis(raw_analysis, market_probability)
    # ── Auto-translate titles ────────────────────────────────────────────
    # AUTO_TRANSLATE_TITLES=true (default): every event gets a Chinese title.
    # The LLM is asked to produce title_zh but smaller/faster models sometimes
    # skip it.  When the field is empty or the env flag is on, run a dedicated
    # lightweight translation call so the dashboard always has readable titles.
    if settings.AUTO_TRANSLATE_TITLES and not normalized.get("title_zh"):
        zh = await translate_title(market_question)
        if zh:
            normalized["title_zh"] = zh
    narrative_type = normalized["narrative_type"]
    base_rate = classify_market(market_question)
    evidence_quality = calculate_evidence_quality(
        evidence_profile=evidence_profile,
        news_quality_score=news_quality_score,
        semantics_profile=semantics_profile,
        priced_in_risk_score=priced_in_risk_score,
    )
    narrative_risk_score = calculate_narrative_risk_score(
        news_context=news_context,
        narrative_type=narrative_type,
    )
    confidence_score = calculate_confidence_score(
        news_context=news_context,
        news_quality_score=news_quality_score,
        narrative_type=narrative_type,
        reasoning=normalized["reasoning"],
        reasoning_consistency=normalized["reasoning_consistency"],
        evidence_profile=evidence_profile,
        priced_in_risk_score=priced_in_risk_score,
        semantics_profile=semantics_profile,
    )
    confidence_cap = apply_confidence_caps(
        confidence=confidence_score,
        market_probability=market_probability,
        base_rate_category=base_rate.category,
        evidence_quality=evidence_quality,
    )
    confidence_score = confidence_cap["confidence"]

    probability_constraint = constrain_probability(
        market_probability=market_probability,
        ai_probability=normalized["ai_probability"],
        confidence=confidence_score,
        narrative_type=narrative_type,
        has_strong_evidence=normalized["has_strong_evidence"],
        evidence_profile=evidence_profile,
        priced_in_risk_score=priced_in_risk_score,
        semantics_profile=semantics_profile,
        news_quality_score=news_quality_score,
        base_rate_category=base_rate.category,
    )
    evidence_constrained_probability = probability_constraint["probability"]
    effective_anchor_prior = market_probability if base_rate.category == "unknown" else None
    # Final base-rate anchoring step; do not clamp a second time.
    ai_probability = anchor_probability(
        llm_probability=evidence_constrained_probability,
        base_rate=base_rate,
        confidence=confidence_score,
        effective_prior=effective_anchor_prior,
    )
    base_rate_probability = ai_probability  # 保留字段名兼容
    divergence = round(ai_probability - market_probability, 2)
    signal = calculate_signal(
        divergence=divergence,
        confidence=confidence_score,
        evidence_profile=evidence_profile,
        priced_in_risk_score=priced_in_risk_score,
        news_quality_score=news_quality_score,
    )
    position_size = calculate_position_size(
        divergence=divergence,
        confidence=confidence_score,
        narrative_risk=narrative_risk_score,
    )
    signal_strength = calculate_signal_strength(
        divergence=divergence,
        confidence=confidence_score,
        news_quality_score=news_quality_score,
        narrative_risk=narrative_risk_score,
        evidence_profile=evidence_profile,
        priced_in_risk_score=priced_in_risk_score,
    )
    signal_direction = calculate_signal_direction(signal)
    expected_edge = round(divergence / 100, 4)
    risk_level = calculate_risk_level(narrative_risk_score, news_quality_score)
    risk_flags = build_risk_flags(news_context, narrative_type, news_quality_score)

    if signal == "WATCHLIST":
        position_size = min(position_size, 0.02)

    return {
        "market_question": market_question,
        "market_probability": market_probability,
        "ai_probability": ai_probability,
        "true_probability": ai_probability,
        "final_probability": ai_probability,
        "divergence": divergence,
        "signal_strength": signal_strength,
        "signal_direction": signal_direction,
        "overreaction_score": abs(divergence),
        "confidence_score": confidence_score,
        "confidence_cap_reasons": confidence_cap["reasons"],
        "narrative_type": narrative_type,
        "title_zh": normalized["title_zh"],
        "narrative_summary": normalized["narrative_summary"],
        "reasoning": normalized["reasoning"],
        "risk_flags": risk_flags,
        "signal": signal,
        "position_size": position_size,
        "narrative_risk_score": narrative_risk_score,
        "news_quality_score": news_quality_score,
        "evidence_direction": evidence_profile["evidence_direction"],
        "evidence_strength": evidence_profile["evidence_strength"],
        "evidence_conflict_score": evidence_profile["conflict_score"],
        "freshness_score": evidence_profile["freshness_score"],
        "resolution_relevance_score": evidence_profile["resolution_relevance_score"],
        "priced_in_risk_score": priced_in_risk_score,
        "market_ambiguity_score": semantics_profile["ambiguity_score"],
        "condition_type": semantics_profile["condition_type"],
        "base_rate_category": base_rate.category,
        "base_rate_prior": base_rate.prior,
        "base_rate_effective_prior": (
            effective_anchor_prior if effective_anchor_prior is not None else base_rate.prior
        ),
        "base_rate_range": [base_rate.low, base_rate.high],
        "evidence_constrained_probability": evidence_constrained_probability,
        "evidence_quality_factor": probability_constraint["evidence_quality_factor"],
        "evidence_quality_bucket": probability_constraint["evidence_quality_bucket"],
        "evidence_quality_reasons": probability_constraint["evidence_quality_reasons"],
        "probability_guardrail_triggered": probability_constraint["guardrail_triggered"],
        "probability_guardrail_reason": probability_constraint["guardrail_reason"],
        "base_rate_probability": base_rate_probability,
        "expected_edge": expected_edge,
        "risk_level": risk_level,
        "volume": volume,
        "liquidity": liquidity,
        "resolution_criteria": normalized["resolution_criteria"],
        "time_horizon": normalized["time_horizon"],
        "entities": normalized["entities"],
        "reasoning_steps": normalized["reasoning_steps"],
        "analysis_quality": "deterministic_fallback" if used_fallback else "llm",
        # Phase 5: real token usage from _ask_ai's response.usage. None when
        # the LLM call failed (deterministic fallback path). Consumed by
        # llm_telemetry_service.build_llm_telemetry.
        "llm_usage": llm_usage,
    }


def _extract_deadline_from_context(
    semantics_profile: dict[str, Any],
    news_context: str,
) -> str | None:
    """Extract deadline information from the news context for the LLM prompt.

    Looks for a DEADLINE field in the structured news context (emitted by
    build_semantics_context) and returns a human-readable time-to-deadline
    string. Returns None when no deadline is found.
    """
    import re
    from datetime import datetime, timezone

    # Try to find DEADLINE in the news context
    match = re.search(r"DEADLINE:\s*(.+?)(?:\n|$)", news_context or "")
    if not match:
        return None
    deadline_str = match.group(1).strip()
    if not deadline_str or deadline_str.lower() in ("none", "unknown", ""):
        return None

    # Try to parse the deadline and compute remaining time
    now = datetime.now(timezone.utc)
    for fmt in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y"):
        try:
            deadline = datetime.strptime(deadline_str, fmt).replace(tzinfo=timezone.utc)
            delta = deadline - now
            days = delta.days
            if days < 0:
                return f"{deadline_str} (已过期 {abs(days)} 天)"
            elif days == 0:
                return f"{deadline_str} (今天到期)"
            elif days <= 7:
                return f"{deadline_str} (还剩 {days} 天)"
            elif days <= 30:
                return f"{deadline_str} (还剩约 {days // 7} 周)"
            elif days <= 365:
                return f"{deadline_str} (还剩约 {days // 30} 个月)"
            else:
                return f"{deadline_str} (还剩约 {days // 365} 年)"
        except ValueError:
            continue

    # Could not parse, return raw
    return deadline_str
