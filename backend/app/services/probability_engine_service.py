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
import logging
import re
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings
from app.utils.market_utils import safe_float

logger = logging.getLogger(__name__)


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
    "reasoning_steps": [],
    "title_zh": "",
    "narrative_type": "unknown",
    "narrative_summary": "AI 分析暂不可用。",
    "reasoning": "因 AI 输出不可用或无效，已使用回退分析。",
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


async def translate_title(question: str) -> str:
    """Translate a market question into a concise Chinese title.

    Uses a minimal, fail-safe prompt designed to work even with small / fast
    models. On any error, returns the original English question so events
    always have a visible title.
    """
    if not question or not question.strip():
        return ""
    try:
        client = get_client()
        response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Translate to Simplified Chinese. Output ONLY the "
                        f"translation, nothing else:\n\n{question[:500]}"
                    ),
                },
            ],
            temperature=0,
            max_tokens=80,
        )
        text = (response.choices[0].message.content or "").strip().strip("\"'""''")
        return text[:300] if text else question[:120]
    except Exception as exc:
        logger.debug("translate_title failed [question=%.60s]: %s", question, exc)
        return question[:120]


async def _ask_ai(
    market_question: str,
    market_probability: float,
    news_context: str,
    sentiment_summary: str = "",
    base_rate_context: dict[str, Any] | None = None,
    deadline_info: str | None = None,
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
                    sentiment_summary=sentiment_summary,
                    base_rate_context=base_rate_context,
                    deadline_info=deadline_info,
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
    # Phase 5: capture token usage for telemetry. Attached as a private key
    # so _normalize_ai_analysis (which copies only known keys) does not
    # propagate it — analyze_market explicitly extracts it before normalize.
    usage = getattr(response, "usage", None)
    if usage is not None:
        parsed["_llm_usage"] = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }
    return parsed


def _build_system_prompt() -> str:
    return f"""
{HARD_RULES}

Return only valid JSON. Do not include markdown.
Use calibrated, conservative probability estimates.
The market probability is the anchor. Deviate only for clear, high-quality evidence.

All natural-language string values in the JSON (title_zh, narrative_summary,
reasoning, resolution_criteria, time_horizon) MUST be written in Simplified
Chinese (简体中文). Keep the JSON keys and enum values (e.g. narrative_type)
in English. Keep proper nouns / entity names in their common form.
""".strip()


def _build_user_prompt(
    market_question: str,
    market_probability: float,
    news_context: str,
    sentiment_summary: str = "",
    base_rate_context: dict[str, Any] | None = None,
    deadline_info: str | None = None,
) -> str:
    safe_question = _sanitize_text(market_question)[:700]
    safe_news = _sanitize_text(news_context)[:9000]

    # ── Build prompt with optional base rate + deadline context ────────
    parts: list[str] = [
        "Task: structured Prediction Market Narrative Filter analysis.",
        f"Market question:\n{safe_question}",
        f"Current market probability:\n{market_probability}%",
    ]
    if base_rate_context:
        br_cat = base_rate_context.get("category", "unknown")
        br_prior = base_rate_context.get("prior", 50)
        br_range = base_rate_context.get("historical_range", "20% – 80%")
        br_note = base_rate_context.get("note", "")
        parts.append(
            f"HISTORICAL BASE RATE:\nCategory: {br_cat}\n"
            f"Prior probability: {br_prior}%\n"
            f"Historical range: {br_range}\n"
            f"Note: {br_note}\n"
            f"Use this as your prior anchor. Deviate only when evidence clearly warrants it."
        )
    if deadline_info:
        parts.append(
            f"TIME TO DEADLINE: {deadline_info}\n"
            "Events near deadline → estimate conservatively (less room for surprise).\n"
            "Events far from deadline → more uncertainty."
        )
    parts.append(f"Structured news evidence and filtered news context:\n{safe_news}")
    parts.append("""Return exactly this JSON shape:
{
  "reasoning_steps": [
    {"step": "Identify the key conditions and their individual probabilities"},
    {"step": "Assess evidence for each condition"},
    {"step": "Evaluate condition dependencies"},
    {"step": "Synthesize joint probability"}
  ],
  "ai_probability": 0.0,
  "title_zh": "...",
  "narrative_type": "factual|speculative|meme|satire|conspiracy|clickbait|unknown",
  "narrative_summary": "...",
  "reasoning": "...",
  "has_strong_evidence": false,
  "reasoning_consistency": 0.0,
  "resolution_criteria": "...",
  "time_horizon": "...",
  "entities": ["..."]
}

Use reasoning_steps to show your thinking process. Each step should be a brief sentence in Chinese. This helps calibrate the final probability.

Probability guidance:
- Use MARKET SEMANTICS to understand the exact YES/NO resolution conditions.
- If AMBIGUITY_SCORE is high, lower confidence and stay closer to market.
- Treat EVIDENCE PROFILE as the primary evidence layer.
- High CONFLICT means lower confidence and smaller probability deviation.
- Low STRENGTH means stay close to market probability.
- Low FRESHNESS means the market may already have priced the news.
- Consider HISTORICAL BASE RATE as your prior. Adjust by TIME TO DEADLINE.

Structured event fields (always fill these from the question + evidence):
- title_zh: Directly translate the market question into Simplified Chinese (简体中文). Preserve every date, number, percentage, and proper noun from the original question exactly as written. Do NOT add dates, timeframes, or details that are not in the original question.
- resolution_criteria: the specific, checkable condition for YES vs NO.
- time_horizon: the deadline or time window.
- entities: the key subjects (people, organizations, assets, laws).""")

    prompt = "\n\n".join(parts)
    # Sentiment is an additive LLM-judgment signal layered on top of the
    # structured evidence. Only included when non-empty so the neutral
    # fallback (no real LLM call) is a clean no-op.
    if sentiment_summary:
        safe_sentiment = _sanitize_text(sentiment_summary)[:500]
        if safe_sentiment:
            prompt += f"\n\nLLM 情感分析: {safe_sentiment}"
    return prompt


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
    for key in ("title_zh", "narrative_type", "narrative_summary", "reasoning"):
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

    # Chain-of-thought reasoning steps: extract as-is when valid, else [].
    raw_steps = data.get("reasoning_steps")
    if isinstance(raw_steps, list):
        cleaned_steps: list[dict[str, str]] = []
        for step_item in raw_steps:
            if isinstance(step_item, dict) and "step" in step_item:
                step_text = str(step_item["step"]).strip()
                if step_text:
                    cleaned_steps.append({"step": step_text[:500]})
        result["reasoning_steps"] = cleaned_steps
    else:
        result["reasoning_steps"] = []

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
        "narrative_summary": "基于结构化新闻证据的确定性回退分析。",
        "reasoning": (
            "LLM 不可用或返回无效；概率根据证据方向、强度、结算相关性、新鲜度、"
            "冲突度、新闻质量、已定价风险与结算歧义度综合估算得出。"
        ),
        "has_strong_evidence": evidence_multiplier >= 0.35,
        "reasoning_consistency": min(0.75, max(0.3, evidence_multiplier)),
    }


def _smooth_penalty(
    value: float,
    good_thresh: float,
    bad_thresh: float,
    good_penalty: float,
    bad_penalty: float,
    higher_is_better: bool = True,
) -> float:
    """Continuous penalty interpolation — replaces 3-tier step functions.

    Maps value smoothly between good_penalty and bad_penalty using Hermite
    smoothstep (3t² - 2t³) so there are no discontinuities at thresholds.

    For higher_is_better=True (confidence, strength, freshness, relevance):
      value >= good_thresh → good_penalty
      value <= bad_thresh  → bad_penalty
    For higher_is_better=False (conflict, priced_in_risk, ambiguity):
      value <= good_thresh → good_penalty
      value >= bad_thresh  → bad_penalty
    """
    if higher_is_better:
        if value >= good_thresh:
            return good_penalty
        if value <= bad_thresh:
            return bad_penalty
        t = (good_thresh - value) / (good_thresh - bad_thresh)
    else:
        if value <= good_thresh:
            return good_penalty
        if value >= bad_thresh:
            return bad_penalty
        t = (value - good_thresh) / (bad_thresh - good_thresh)
    # Hermite smoothstep: 3t² - 2t³ (C1 continuous at boundaries)
    smooth_t = t * t * (3.0 - 2.0 * t)
    return good_penalty + smooth_t * (bad_penalty - good_penalty)


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
    # Phase 4: continuous sigmoid interpolation replaces 3-tier step functions.
    # Smooth penalties eliminate boundary discontinuities (e.g. confidence 0.749
    # vs 0.751 no longer jumps 0.15 in penalty).
    penalties: list[float] = []

    # 1. 置信度：低置信度时强力回归 (higher is better)
    penalties.append(
        _smooth_penalty(confidence, 0.75, 0.30, 0.15, 0.55, higher_is_better=True)
    )

    # 2. 证据强度 (higher is better)
    strength = evidence["evidence_strength"]
    penalties.append(
        _smooth_penalty(strength, 0.5, 0.10, 0.10, 0.50, higher_is_better=True)
    )

    # 3. 结算相关性 (higher is better)
    relevance = evidence["resolution_relevance_score"]
    penalties.append(
        _smooth_penalty(relevance, 0.5, 0.15, 0.10, 0.45, higher_is_better=True)
    )

    # 4. 证据冲突 (lower is better)
    conflict = evidence["conflict_score"]
    penalties.append(
        _smooth_penalty(conflict, 0.25, 0.55, 0.05, 0.40, higher_is_better=False)
    )

    # 5. 新鲜度 (higher is better)
    freshness = evidence["freshness_score"]
    penalties.append(
        _smooth_penalty(freshness, 0.75, 0.30, 0.05, 0.35, higher_is_better=True)
    )

    # 6. 已定价风险 (lower is better — high priced_in = more regression)
    penalties.append(
        _smooth_penalty(priced_in_risk_score, 40, 80, 0.05, 0.45, higher_is_better=False)
    )

    # 7. 市场问题歧义性 (lower is better — high ambiguity = more regression)
    ambiguity = semantics["ambiguity_score"]
    penalties.append(
        _smooth_penalty(ambiguity, 30, 70, 0.05, 0.35, higher_is_better=False)
    )

    # 8. Narrative 类型 (categorical — stays discrete)
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


def calculate_evidence_quality(
    evidence_profile: dict[str, Any] | None,
    news_quality_score: float,
    semantics_profile: dict[str, Any] | None = None,
    priced_in_risk_score: int = 0,
) -> dict[str, Any]:
    """Return a deterministic evidence-quality factor and bucket."""
    evidence = evidence_profile or default_evidence_profile()
    semantics = semantics_profile or default_semantics_profile()
    strength = _clamp(float(evidence.get("evidence_strength", 0.0)), 0.0, 1.0)
    relevance = _clamp(float(evidence.get("resolution_relevance_score", 0.0)), 0.0, 1.0)
    freshness = _clamp(float(evidence.get("freshness_score", 0.5)), 0.0, 1.0)
    conflict = _clamp(float(evidence.get("conflict_score", 0.0)), 0.0, 1.0)
    source_count = max(0, int(evidence.get("source_count", 0) or 0))
    news_quality = _clamp(news_quality_score, 0.0, 1.0)
    ambiguity = _clamp(float(semantics.get("ambiguity_score", 50)), 0.0, 100.0) / 100.0
    priced_in = _clamp(float(priced_in_risk_score), 0.0, 100.0) / 100.0
    source_score = _clamp(source_count / 5.0, 0.0, 1.0)
    factor = round(_clamp(
        strength * 0.24 + relevance * 0.20 + freshness * 0.14
        + (1.0 - conflict) * 0.14 + source_score * 0.12
        + news_quality * 0.10 + (1.0 - ambiguity) * 0.04
        + (1.0 - priced_in) * 0.02,
        0.0,
        1.0,
    ), 3)
    reasons: list[str] = []
    if strength < 0.25 or relevance < 0.25:
        reasons.append("thin_or_indirect_evidence")
    if conflict >= 0.55:
        reasons.append("high_conflict")
    if freshness < 0.35:
        reasons.append("stale_evidence")
    if source_count < 2:
        reasons.append("single_or_missing_source")
    if ambiguity >= 0.70:
        reasons.append("ambiguous_resolution")
    if priced_in >= 0.70:
        reasons.append("likely_priced_in")
    if strength >= 0.70 and relevance >= 0.70:
        reasons.append("direct_relevant_evidence")
    if source_count >= 5 and conflict <= 0.20:
        reasons.append("multi_source_support")
    if factor < 0.35:
        bucket = "weak"
    elif factor < 0.55:
        bucket = "mixed"
    elif factor < 0.75:
        bucket = "solid"
    else:
        bucket = "strong"
    return {"factor": factor, "bucket": bucket, "reasons": reasons}

def calculate_priced_in_risk_score(
    market_probability: float,
    evidence_profile: dict[str, Any],
    volume: float | None = None,
    liquidity: float | None = None,
    market_microstructure: dict[str, Any] | None = None,
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
    # ── Log-continuous market depth scoring ─────────────────────────────
    # Replaces binary thresholds (volume≥100k→10, else 0) with a smooth
    # log-scale so 99k and 101k don't differ by 10 points. Max 10 per signal.
    import math as _math
    if volume_value >= 100_000:
        vol_norm = (_math.log10(volume_value) - 5.0) / 2.0  # 5=log10(100k), 7=log10(10M)
        score += int(_clamp(vol_norm * 10, 0, 10))
    if liquidity_value >= 50_000:
        liq_norm = (_math.log10(liquidity_value) - _math.log10(50_000)) / (_math.log10(10_000_000) - _math.log10(50_000))
        score += int(_clamp(liq_norm * 10, 0, 10))

    # ── Market microstructure signals ─────────────────────────────────────
    # Optional point-in-time market microstructure refinements. Each signal
    # is independent and additive. Only applied when the caller provides the
    # market_microstructure dict (backward-compatible: None → no adjustment).
    if market_microstructure is not None and isinstance(market_microstructure, dict):
        price_change_24h = market_microstructure.get("price_change_24h")
        if isinstance(price_change_24h, (int, float)) and abs(price_change_24h) > 5:
            score += 10

        bid_ask_spread = market_microstructure.get("bid_ask_spread")
        if isinstance(bid_ask_spread, (int, float)) and bid_ask_spread < 2:
            score += 5

        volume_z_score = market_microstructure.get("volume_z_score")
        if isinstance(volume_z_score, (int, float)) and volume_z_score > 2:
            score += 8

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
