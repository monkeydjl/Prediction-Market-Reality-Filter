"""scoring_service.py
===================
Pure scoring and report-building functions extracted from event_intelligence_service.

These functions compute trust/impact/value scores and build human-readable output
(headlines, assessments, action recommendations). All are pure functions with no
store or network I/O — trivially testable.
"""

from typing import Any

from app.utils.helpers import clamp01
from app.utils.market_utils import safe_float


def calculate_trust_score(analysis: dict[str, Any]) -> int:
    confidence = clamp01(analysis.get("confidence_score"))
    news_quality = clamp01(analysis.get("news_quality_score"))
    evidence_strength = clamp01(analysis.get("evidence_strength"))
    relevance = clamp01(analysis.get("resolution_relevance_score"))
    conflict_penalty = 1.0 - clamp01(analysis.get("evidence_conflict_score"))
    score = (
        confidence * 30
        + news_quality * 25
        + evidence_strength * 20
        + relevance * 15
        + conflict_penalty * 10
    )
    return int(round(max(0, min(100, score))))


def calculate_impact_score(analysis: dict[str, Any]) -> int:
    probability_change = min(
        abs(safe_float(analysis.get("divergence"), 0.0)),
        40.0,
    ) / 40.0
    confidence = clamp01(analysis.get("confidence_score"))
    evidence_strength = clamp01(analysis.get("evidence_strength"))
    relevance = clamp01(analysis.get("resolution_relevance_score"))
    score = (
        probability_change * 45
        + confidence * 20
        + evidence_strength * 20
        + relevance * 15
    )
    return int(round(max(0, min(100, score))))


def calculate_value_score(impact_score: int, trust_score: int) -> int:
    """Linear value score: impact weighted by trust.

    trust=100 -> value = impact  (full trust, full value)
    trust=0   -> value = 0       (zero trust, zero value)
    """
    return int(round(impact_score * trust_score / 100))


def probability_direction(change: float) -> str:
    if change >= 2:
        return "rising"
    if change <= -2:
        return "falling"
    return "stable"


def score_level(score: int) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 45:
        return "MEDIUM"
    return "LOW"


def impact_drivers(analysis: dict[str, Any]) -> list[str]:
    drivers = []
    if abs(safe_float(analysis.get("divergence"), 0.0)) >= 10:
        drivers.append("material_probability_change")
    if clamp01(analysis.get("evidence_strength")) >= 0.35:
        drivers.append("strong_evidence")
    if clamp01(analysis.get("resolution_relevance_score")) >= 0.35:
        drivers.append("direct_resolution_relevance")
    base_rate_category = analysis.get("base_rate_category")
    if (
        isinstance(base_rate_category, str)
        and base_rate_category
        and base_rate_category != "unknown"
    ):
        drivers.append(f"base_rate:{base_rate_category}")
    return drivers or ["monitor_for_confirmation"]


_DIRECTION_ZH = {"rising": "上行", "falling": "下行", "stable": "持平"}
_LEVEL_ZH = {"HIGH": "高", "MEDIUM": "中", "LOW": "低"}


def build_headline(
    question: str,
    change: float,
    trust_score: int,
    impact_score: int,
) -> str:
    direction = _DIRECTION_ZH.get(probability_direction(change), "持平")
    return (
        f"{direction}概率信号：「{question[:90]}」"
        f"（可信度 {trust_score}/100，影响 {impact_score}/100）"
    )


def build_why_it_matters(analysis: dict[str, Any], change: float) -> str:
    narrative = str(analysis.get("narrative_summary") or "").strip()
    if narrative:
        return narrative[:500]
    return (
        "该事件值得关注：现有证据显示其发生概率相对当前基准"
        f"移动了约 {abs(change):.1f} 个百分点。"
    )


def build_probability_assessment(
    baseline: float,
    estimated: float,
    trust_score: int,
) -> str:
    return (
        f"基准概率 {baseline:.1f}% 变化至 {estimated:.1f}%，"
        f"可信度{_LEVEL_ZH.get(score_level(trust_score), '中')}。"
    )


def recommended_action(
    trust_score: int,
    impact_score: int,
    change: float,
    *,
    signal_direction: str | None = None,
    confidence: str | None = None,
) -> str:
    """Human-readable action recommendation.

    When signal_direction is provided (from legacy_analysis), returns a
    structured direction phrase ("押 YES（置信度：high）" etc.). When None,
    falls back to the legacy trust/impact-based logic for backward
    compatibility with callers that don't pass signal data.
    """
    if signal_direction in ("LONG", "STRONG_LONG"):
        return f"押 YES（置信度：{confidence or '未知'}）"
    if signal_direction in ("SHORT", "STRONG_SHORT"):
        return f"押 NO（置信度：{confidence or '未知'}）"
    if signal_direction == "WATCHLIST":
        return "等待更多证据"
    # Legacy fallback: no signal data -> trust/impact based phrase
    if trust_score >= 70 and impact_score >= 60:
        return "建议人工复核，并持续关注后续证据。"
    if trust_score >= 45 and abs(change) >= 5:
        return "作为活跃情报项跟踪，等待进一步确认。"
    return "保持观察；当前证据强度不足以升级处理。"
