"""
probability_engine_service.py

Probability engine: the foundation layer of the analysis stack. It turns a
market question, a baseline probability, and a filtered news context into a
calibrated probability estimate plus the structured profiles that estimate
relies on.

Responsibilities:
  - LLM I/O: build prompts, call the model, normalize / fall back when the model
    is unavailable or returns invalid output.
  - Probability math: confidence scoring, priced-in risk, and the regression
    that constrains an AI probability back toward the market baseline.
  - Input parsing: extract the evidence and market-semantics profiles and the
    news-quality score from the (already filtered) news context string.

This module owns the shared low-level helpers and the risk-keyword table because
both this module and analysis_report_service build on them. It is the leaf of
the analysis dependency chain - it imports nothing from the report or the legacy
adapter modules, only base settings and the OpenAI client.
"""

import json
import re
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings
from app.utils.market_utils import safe_float


HARD_RULES = """
You are NOT a creative writer.
You are NOT a political commentator.
You must behave like a quantitative risk analyst.
Never exaggerate probabilities.
Never output extreme probabilities unless evidence is overwhelming.
Meme narratives should reduce confidence.
Satirical news should reduce confidence.
Low-quality news should reduce confidence.
If evidence is weak, stay near market probability.
Default toward uncertainty.
""".strip()

_client: AsyncOpenAI | None = None

RISK_KEYWORDS = {
    "meme": ("meme", "viral", "shitpost", "reddit", "tiktok", "twitter rumor", "x rumor"),
    "satire": ("satire", "satirical", "parody", "babylon bee", "the onion"),
    "conspiracy": ("conspiracy", "deep state", "cover-up", "coverup", "hoax", "false flag"),
    "clickbait": ("shocking", "you won't believe", "bombshell", "explosive claim", "insane"),
    "low_credibility": ("rumor", "unconfirmed", "anonymous source", "allegedly", "speculation"),
}

DEFAULT_ANALYSIS: dict[str, Any] = {
    "ai_probability": None,
    "narrative_type": "unknown",
    "narrative_summary": "AI analysis unavailable.",
    "reasoning": "Fallback analysis used because AI output was unavailable or invalid.",
    "has_strong_evidence": False,
    "reasoning_consistency": 0.3,
    "resolution_criteria": "",
    "time_horizon": "",
    "entities": [],
}


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.DASHSCOPE_BASE_URL,
            # Bound LLM calls so a stuck/over-limit provider cannot hang a scan
            # indefinitely. max_retries lets the SDK ride transient 429/5xx
            # (DashScope rate-limits) without the caller treating every blip
            # as a hard failure that drops to the deterministic fallback.
            timeout=60.0,
            max_retries=2,
        )
    return _client


async def _ask_ai(
    market_question: str,
    market_probability: float,
    news_context: str,
) -> dict[str, Any]:
    client = get_client()
    response = await client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": _build_system_prompt()},
            {
                "role": "user",
                "content": _build_user_prompt(
                    market_question=market_question,
                    market_probability=market_probability,
                    news_context=news_context,
                ),
            },
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("AI returned non-object JSON")
    return parsed


def _build_system_prompt() -> str:
    return f"""
{HARD_RULES}

Return only valid JSON. Do not include markdown.
Use calibrated, conservative probability estimates.
The market probability is the anchor. Deviate only for clear, high-quality evidence.
""".strip()


def _build_user_prompt(
    market_question: str,
    market_probability: float,
    news_context: str,
) -> str:
    safe_question = _sanitize_text(market_question)[:700]
    safe_news = _sanitize_text(news_context)[:9000]
    return f"""
Task: structured Prediction Market Narrative Filter analysis.

Market question:
{safe_question}

Current market probability:
{market_probability}%

Structured news evidence and filtered news context:
{safe_news}

Return exactly this JSON shape:
{{
  "ai_probability": 0.0,
  "narrative_type": "factual|speculative|meme|satire|conspiracy|clickbait|unknown",
  "narrative_summary": "...",
  "reasoning": "...",
  "has_strong_evidence": false,
  "reasoning_consistency": 0.0,
  "resolution_criteria": "...",
  "time_horizon": "...",
  "entities": ["..."]
}}

Probability guidance:
- Use MARKET SEMANTICS to understand the exact YES/NO resolution conditions.
- If AMBIGUITY_SCORE is high, lower confidence and stay closer to market.
- Treat EVIDENCE PROFILE as the primary evidence layer.
- High CONFLICT means lower confidence and smaller probability deviation.
- Low STRENGTH means stay close to market probability.
- Low FRESHNESS means the market may already have priced the news.
- Your estimate will be anchored to historical base rates after parsing.

Structured event fields (always fill these from the question + evidence):
- resolution_criteria: the specific, checkable condition that determines YES
  vs NO (e.g. "Bitcoin closes at or above $100,000 on any day in 2026").
- time_horizon: the deadline or time window (e.g. "by end of 2026", "Q3 2026").
- entities: the key subjects the event is about - people, organizations,
  assets, laws, etc. Use the most common surface form of each name.
""".strip()


def _normalize_ai_analysis(
    data: dict[str, Any],
    market_probability: float,
) -> dict[str, Any]:
    result = DEFAULT_ANALYSIS.copy()
    if not isinstance(data, dict):
        result["ai_probability"] = market_probability
        return result

    raw_probability = data.get("ai_probability")
    if raw_probability is None:
        raw_probability = market_probability

    result["ai_probability"] = _clamp(raw_probability, 0, 100)
    for key in ("narrative_type", "narrative_summary", "reasoning"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()[:1200]

    # Structured event fields. resolution_criteria / time_horizon are cleaned
    # the same way as the narrative text fields; entities is a cleaned list.
    for key in ("resolution_criteria", "time_horizon"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()[:500]
    result["entities"] = _clean_entities(data.get("entities"))

    result["has_strong_evidence"] = bool(data.get("has_strong_evidence", False))
    result["reasoning_consistency"] = _clamp(
        data.get("reasoning_consistency", 0.3),
        0,
        1,
    )
    return result


def _clean_entities(raw: Any) -> list[str]:
    """Normalize an LLM entities value into a deduped, length-capped list[str].

    Tolerates the value being missing, a non-list, or a list of non-strings:
    each item is stringified, stripped, empties dropped, order preserved on
    first occurrence, capped at 10. Raw surface form only (no alias merging).
    """
    if not isinstance(raw, list):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        cleaned.append(text[:120])
        if len(cleaned) >= 10:
            break
    return cleaned


def build_deterministic_fallback_analysis(
    market_probability: float,
    evidence_profile: dict[str, Any],
    news_quality_score: float,
    priced_in_risk_score: int,
    semantics_profile: dict[str, Any],
) -> dict[str, Any]:
    direction = evidence_profile["evidence_direction"]
    direction_sign = 0
    if direction == "support":
        direction_sign = 1
    elif direction == "oppose":
        direction_sign = -1

    evidence_multiplier = (
        evidence_profile["evidence_strength"]
        * evidence_profile["resolution_relevance_score"]
        * evidence_profile["freshness_score"]
        * (1 - evidence_profile["conflict_score"])
        * news_quality_score
        * (1 - priced_in_risk_score / 100)
        * (1 - semantics_profile["ambiguity_score"] / 100)
    )
    max_move = 22.0
    probability = market_probability + (direction_sign * max_move * evidence_multiplier)

    return {
        **DEFAULT_ANALYSIS,
        "ai_probability": round(_clamp(probability, 0, 100), 2),
        "narrative_type": "evidence_fallback",
        "narrative_summary": "Deterministic fallback based on structured news evidence.",
        "reasoning": (
            "LLM unavailable or invalid; probability estimated from evidence direction, "
            "strength, resolution relevance, freshness, conflict, news quality, priced-in "
            "risk, and resolution ambiguity."
        ),
        "has_strong_evidence": evidence_multiplier >= 0.35,
        "reasoning_consistency": min(0.75, max(0.3, evidence_multiplier)),
    }


def clamp_probability(
    market_probability: float,
    ai_probability: float,
    confidence: float = 0.0,
    narrative_type: str = "",
    has_strong_evidence: bool = False,
    evidence_profile: dict[str, Any] | None = None,
    priced_in_risk_score: int = 0,
    semantics_profile: dict[str, Any] | None = None,
) -> float:
    """
    将 AI 概率约束在合理范围内并向市场概率回归。

    设计原则：
    - 用一个统一的 regression_strength（0–1）替代多个叠加因子
    - regression_strength = 1 → 完全等于市场概率（无信号）
    - regression_strength = 0 → 完全信任 AI 概率
    - 各个维度的惩罚权重加权后取均值，避免叠加放大
    """
    market_probability = _clamp(market_probability, 0, 100)
    ai_probability = _clamp(ai_probability, 0, 100)
    narrative = (narrative_type or "").lower()
    evidence = evidence_profile or default_evidence_profile()
    semantics = semantics_profile or default_semantics_profile()

    # ── 计算各维度回归强度（越高 = 越不信任 AI，越靠近市场概率）────────
    penalties: list[float] = []

    # 1. 置信度：低置信度时强力回归
    if confidence >= 0.75:
        penalties.append(0.15)
    elif confidence >= 0.60:
        penalties.append(0.30)
    else:
        penalties.append(0.55)

    # 2. 证据强度
    strength = evidence["evidence_strength"]
    if strength >= 0.5:
        penalties.append(0.10)
    elif strength >= 0.25:
        penalties.append(0.30)
    else:
        penalties.append(0.50)

    # 3. 分辨率相关性
    relevance = evidence["resolution_relevance_score"]
    if relevance >= 0.5:
        penalties.append(0.10)
    elif relevance >= 0.35:
        penalties.append(0.25)
    else:
        penalties.append(0.45)

    # 4. 证据冲突
    conflict = evidence["conflict_score"]
    if conflict <= 0.25:
        penalties.append(0.05)
    elif conflict <= 0.45:
        penalties.append(0.20)
    else:
        penalties.append(0.40)

    # 5. 新鲜度
    freshness = evidence["freshness_score"]
    if freshness >= 0.75:
        penalties.append(0.05)
    elif freshness >= 0.5:
        penalties.append(0.20)
    else:
        penalties.append(0.35)

    # 6. 已定价风险
    if priced_in_risk_score >= 70:
        penalties.append(0.45)
    elif priced_in_risk_score >= 50:
        penalties.append(0.25)
    else:
        penalties.append(0.05)

    # 7. 市场问题歧义性
    ambiguity = semantics["ambiguity_score"]
    if ambiguity >= 60:
        penalties.append(0.35)
    elif ambiguity >= 40:
        penalties.append(0.20)
    else:
        penalties.append(0.05)

    # 8. Narrative 类型
    if "meme" in narrative or "satire" in narrative or "conspiracy" in narrative:
        penalties.append(0.40)
    elif "speculative" in narrative or "clickbait" in narrative:
        penalties.append(0.20)
    else:
        penalties.append(0.05)

    # 9. 强证据奖励（降低回归）
    if has_strong_evidence:
        penalties.append(-0.15)  # 负惩罚 = 允许更大偏差

    # ── 加权平均得出最终回归强度 ──────────────────────────────────────
    regression_strength = _clamp(sum(penalties) / len(penalties), 0, 0.90)

    # ── 计算最大允许偏差（绝对限制）──────────────────────────────────
    max_deviation = 35.0
    if confidence < 0.50:
        max_deviation = min(max_deviation, 12.0)
    elif confidence < 0.65:
        max_deviation = min(max_deviation, 20.0)

    # ── 应用回归 ──────────────────────────────────────────────────────
    raw_deviation = ai_probability - market_probability
    clamped_deviation = max(-max_deviation, min(max_deviation, raw_deviation))
    final_deviation = clamped_deviation * (1.0 - regression_strength)
    result = market_probability + final_deviation

    return round(_clamp(result, 0, 100), 2)


def score_news_quality(news_context: str) -> float:
    text = (news_context or "").lower()
    if not text.strip():
        return 0.25

    embedded_scores = [
        float(value)
        for value in re.findall(r"quality:\s*([0-9.]+)", text)
        if _looks_like_float(value)
    ]
    embedded_relevance = [
        float(value)
        for value in re.findall(r"relevance:\s*([0-9.]+)", text)
        if _looks_like_float(value)
    ]

    quality = 0.55
    item_count = len(re.findall(r"(news item|rss title:|google news:|title:)", text))
    if item_count >= 5:
        quality += 0.15
    elif item_count >= 2:
        quality += 0.08
    else:
        quality -= 0.08

    if embedded_scores:
        quality = (quality * 0.55) + (sum(embedded_scores) / len(embedded_scores) * 0.45)
    if embedded_relevance:
        relevance_avg = sum(embedded_relevance) / len(embedded_relevance)
        if relevance_avg < 0.35:
            quality -= 0.2
        elif relevance_avg > 0.7:
            quality += 0.08

    penalty_hits = 0
    for words in RISK_KEYWORDS.values():
        penalty_hits += sum(1 for word in words if word in text)
    quality -= min(0.45, penalty_hits * 0.07)

    if len(text) < 300:
        quality -= 0.12
    if "reuters" in text or "associated press" in text or "ap news" in text:
        quality += 0.12
    if "official" in text or "filing" in text or "court" in text:
        quality += 0.08

    return round(_clamp(quality, 0, 1), 3)


def calculate_confidence_score(
    news_context: str,
    news_quality_score: float,
    narrative_type: str,
    reasoning: str,
    reasoning_consistency: float,
    evidence_profile: dict[str, Any] | None = None,
    priced_in_risk_score: int = 0,
    semantics_profile: dict[str, Any] | None = None,
) -> float:
    # news_quantity: source_count から来る（正則に依存しない）
    evidence = evidence_profile or default_evidence_profile()
    source_count = evidence.get("source_count", 0)
    news_quantity_score = _clamp(source_count / 5.0, 0, 1)

    narrative = (narrative_type or "").lower()
    if narrative in {"factual", "fundamental", "official"}:
        clarity_score = 0.8
    elif narrative in {"speculative", "meme", "satire", "conspiracy", "clickbait"}:
        clarity_score = 0.35
    elif narrative and narrative != "unknown":
        clarity_score = 0.55
    else:
        clarity_score = 0.3

    reasoning_score = _clamp(reasoning_consistency, 0, 1)
    if len((reasoning or "").split()) < 12:
        reasoning_score = min(reasoning_score, 0.45)

    evidence_score = (
        evidence["evidence_strength"] * 0.45
        + evidence["freshness_score"] * 0.25
        + (1 - evidence["conflict_score"]) * 0.2
        + evidence["resolution_relevance_score"] * 0.1
    )

    confidence = (
        news_quality_score * 0.25
        + news_quantity_score * 0.15
        + clarity_score * 0.15
        + reasoning_score * 0.2
        + evidence_score * 0.25
    )
    confidence -= (priced_in_risk_score / 100) * 0.12
    semantics = semantics_profile or default_semantics_profile()
    confidence -= (semantics["ambiguity_score"] / 100) * 0.1
    # 下限 0.0（不再硬设 0.30 的假地板，让低质量如实呈现）
    return round(_clamp(confidence, 0.0, 0.90), 3)


def calculate_priced_in_risk_score(
    market_probability: float,
    evidence_profile: dict[str, Any],
    volume: float | None = None,
    liquidity: float | None = None,
) -> int:
    score = 10
    direction = evidence_profile["evidence_direction"]
    strength = evidence_profile["evidence_strength"]
    freshness = evidence_profile["freshness_score"]
    source_count = evidence_profile["source_count"]

    if direction == "support" and market_probability >= 65:
        score += 25
    elif direction == "oppose" and market_probability <= 35:
        score += 25

    if strength >= 0.6:
        score += 10
    if freshness < 0.5:
        score += 25
    elif freshness < 0.75:
        score += 10
    if source_count >= 5:
        score += 15
    elif source_count >= 3:
        score += 8

    volume_value = _clamp(volume or 0, 0, 10_000_000)
    liquidity_value = _clamp(liquidity or 0, 0, 10_000_000)
    if volume_value >= 100_000:
        score += 10
    if liquidity_value >= 50_000:
        score += 10

    return int(_clamp(score, 0, 100))


def extract_evidence_profile(news_context: str) -> dict[str, Any]:
    text = news_context or ""
    profile = default_evidence_profile()

    direction = _extract_text_value(r"direction:\s*([a-z_]+)", text)
    if direction in {"support", "oppose", "neutral"}:
        profile["evidence_direction"] = direction

    profile["evidence_strength"] = _extract_float_value(
        r"strength:\s*([0-9.]+)",
        text,
        profile["evidence_strength"],
    )
    profile["conflict_score"] = _extract_float_value(
        r"conflict:\s*([0-9.]+)",
        text,
        profile["conflict_score"],
    )
    profile["freshness_score"] = _extract_float_value(
        r"freshness:\s*([0-9.]+)",
        text,
        profile["freshness_score"],
    )
    profile["resolution_relevance_score"] = _extract_float_value(
        r"resolution_relevance:\s*([0-9.]+)",
        text,
        profile["resolution_relevance_score"],
    )
    profile["source_count"] = _extract_int_value(
        r"source_count:\s*([0-9]+)",
        text,
        profile["source_count"],
    )
    return profile


def default_evidence_profile() -> dict[str, Any]:
    return {
        "evidence_direction": "neutral",
        "evidence_strength": 0.0,
        "conflict_score": 0.0,
        "freshness_score": 0.5,
        "resolution_relevance_score": 0.0,
        "source_count": 0,
    }


def extract_semantics_profile(news_context: str) -> dict[str, Any]:
    text = news_context or ""
    profile = default_semantics_profile()

    condition_type = _extract_text_value(r"condition_type:\s*([a-z_]+)", text)
    if condition_type:
        profile["condition_type"] = condition_type
    profile["ambiguity_score"] = _extract_int_value(
        r"ambiguity_score:\s*([0-9]+)",
        text,
        profile["ambiguity_score"],
    )
    return profile


def default_semantics_profile() -> dict[str, Any]:
    return {
        "condition_type": "unknown",
        "ambiguity_score": 50,
    }


def _extract_text_value(pattern: str, text: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).lower()


def _extract_float_value(pattern: str, text: str, fallback: float) -> float:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return fallback
    try:
        return _clamp(float(match.group(1)), 0, 1)
    except (TypeError, ValueError):
        return fallback


def _extract_int_value(pattern: str, text: str, fallback: int) -> int:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return fallback
    try:
        return max(0, int(match.group(1)))
    except (TypeError, ValueError):
        return fallback


def _sanitize_text(text: str) -> str:
    return (text or "").replace("\x00", " ").strip()


def _looks_like_float(value: str) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _clamp(value: Any, lo: float, hi: float) -> float:
    number = safe_float(value, lo)
    return round(max(lo, min(hi, number)), 3)
