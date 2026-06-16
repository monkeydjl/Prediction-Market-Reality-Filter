"""
analysis_report_service.py

Report generation: turns a probability estimate and its supporting evidence into
the classification fields a human reviewer reads - the signal, signal strength
and direction, position sizing, narrative-risk and overall risk levels, and the
risk flags.

This layer builds on the probability engine. It imports the shared primitives
(the clamp helper, the risk-keyword table, and the evidence / semantics profile
parsers) from probability_engine_service rather than redefining them, keeping the
dependency one-directional (report -> engine, never the reverse).

Note on vocabulary: the signal / position / long / short language here is legacy
prediction-market vocabulary retained for the existing /scan and /trades surface.
New event-facing output must not adopt it (see the event-conventions guidance).

Compatibility-layer status: the functions in this module (calculate_signal,
calculate_signal_strength, calculate_signal_direction, calculate_position_size)
exist solely to serve the legacy /scan, /trades, and scheduler surfaces. The
event flow (event_intelligence_service.build_event_record) does NOT call them -
it builds its own event-vocabulary fields (probability.change, credibility,
impact, intelligence_report) directly from the neutral analysis fields. Renaming
the legacy vocabulary here would break /scan, scheduler, and the
characterization tests in tests/test_ai_analysis_service.py that lock this
contract; the boundary is therefore enforced by documentation, not renaming.
"""

from typing import Any

from app.services.probability_engine_service import (
    RISK_KEYWORDS,
    _clamp,
    default_evidence_profile,
    extract_evidence_profile,
    extract_semantics_profile,
)


def calculate_narrative_risk_score(
    news_context: str,
    narrative_type: str,
) -> int:
    text = f"{news_context or ''} {narrative_type or ''}".lower()
    score = 20

    weights = {
        "meme": 25,
        "satire": 30,
        "conspiracy": 30,
        "clickbait": 20,
        "low_credibility": 15,
    }
    for category, words in RISK_KEYWORDS.items():
        if any(word in text for word in words):
            score += weights.get(category, 10)

    if "speculative" in text:
        score += 15
    if "unknown" in (narrative_type or "").lower():
        score += 10

    return int(_clamp(score, 0, 100))


def calculate_position_size(
    divergence: float,
    confidence: float,
    narrative_risk: int,
) -> float:
    abs_divergence = abs(divergence)
    risk_multiplier = 1 - (_clamp(narrative_risk, 0, 100) / 100)
    score = abs_divergence * confidence * risk_multiplier

    if score >= 18 and confidence >= 0.75 and narrative_risk < 45:
        return 0.25
    if score >= 10 and confidence >= 0.65 and narrative_risk < 65:
        return 0.10
    if score >= 5 and confidence >= 0.5:
        return 0.05
    return 0.02


def calculate_signal(
    divergence: float,
    confidence: float,
    evidence_profile: dict[str, Any] | None = None,
    priced_in_risk_score: int = 0,
    news_quality_score: float = 0.0,
) -> str:
    evidence = evidence_profile or default_evidence_profile()
    if not passes_analysis_quality_gate(
        confidence=confidence,
        evidence_profile=evidence,
        priced_in_risk_score=priced_in_risk_score,
        news_quality_score=news_quality_score,
    ):
        return "WATCHLIST"

    # ── 强信号：大偏差 + 高置信 ────────────────────────────────────────
    if divergence > 20 and confidence > 0.68:
        return "STRONG_LONG"
    if divergence < -20 and confidence > 0.68:
        return "STRONG_SHORT"

    # ── 中等信号：中偏差 + 中置信 ──────────────────────────────────────
    if divergence > 10 and confidence > 0.50:
        return "LONG"
    if divergence < -10 and confidence > 0.50:
        return "SHORT"

    return "WATCHLIST"


def calculate_signal_strength(
    divergence: float,
    confidence: float,
    news_quality_score: float,
    narrative_risk: int,
    evidence_profile: dict[str, Any] | None = None,
    priced_in_risk_score: int = 0,
) -> str:
    evidence = evidence_profile or default_evidence_profile()
    if not passes_analysis_quality_gate(
        confidence=confidence,
        evidence_profile=evidence,
        priced_in_risk_score=priced_in_risk_score,
        news_quality_score=news_quality_score,
    ):
        return "LOW"

    adjusted = abs(divergence) * confidence * news_quality_score
    if narrative_risk >= 70:
        adjusted *= 0.6
    if adjusted >= 16:
        return "HIGH"
    if adjusted >= 8:
        return "MEDIUM"
    return "LOW"


def passes_analysis_quality_gate(
    confidence: float,
    evidence_profile: dict[str, Any],
    priced_in_risk_score: int,
    news_quality_score: float,
) -> bool:
    """
    信号质量门。所有条件必须同时满足。

    阈值设计原则：
      - confidence 0.50：基于真实 GNews+RSS 数据的可达上限约 0.55-0.65
      - evidence_strength 0.20：3条文章中2条同方向即可达到
      - resolution_relevance 0.22：实体命中+条件类型匹配即可达到
      - conflict 0.65：允许中等分歧（市场本来就有分歧）
      - priced_in 80：放宽，让系统判断而不是规则拦截

    STRONG 信号（divergence>20, confidence>0.68）仍然是高门槛。
    """
    if confidence < 0.50:
        return False
    if news_quality_score < 0.40:
        return False
    if evidence_profile["evidence_strength"] < 0.20:
        return False
    if evidence_profile["resolution_relevance_score"] < 0.22:
        return False
    if evidence_profile["conflict_score"] > 0.65:
        return False
    if priced_in_risk_score > 80:
        return False
    return True


def calculate_signal_direction(signal: str) -> str:
    if signal in ("STRONG_LONG", "LONG"):
        return "LONG"
    if signal in ("STRONG_SHORT", "SHORT"):
        return "SHORT"
    return "NEUTRAL"


def calculate_risk_level(
    narrative_risk_score: int,
    news_quality_score: float,
) -> str:
    if narrative_risk_score >= 70 or news_quality_score < 0.35:
        return "HIGH"
    if narrative_risk_score >= 45 or news_quality_score < 0.6:
        return "MEDIUM"
    return "LOW"


def build_risk_flags(
    news_context: str,
    narrative_type: str,
    news_quality_score: float,
) -> list[str]:
    text = f"{news_context or ''} {narrative_type or ''}".lower()
    flags = []
    for category, words in RISK_KEYWORDS.items():
        if any(word in text for word in words):
            flags.append(category)
    if news_quality_score < 0.4:
        flags.append("low_news_quality")
    evidence = extract_evidence_profile(news_context)
    if evidence["conflict_score"] > 0.45:
        flags.append("conflicting_evidence")
    if evidence["evidence_strength"] < 0.25:
        flags.append("weak_evidence")
    if evidence["resolution_relevance_score"] < 0.35:
        flags.append("low_resolution_relevance")
    if evidence["freshness_score"] < 0.5:
        flags.append("stale_news")
    semantics = extract_semantics_profile(news_context)
    if semantics["ambiguity_score"] >= 40:
        flags.append("resolution_ambiguity")
    return sorted(set(flags))
