"""LLM-powered news sentiment analysis for prediction market events.

Replaces the keyword-based evidence_direction heuristic with LLM judgment.
Batch-analyzes up to 6 articles per event in a single LLM call to control cost.
"""
import json
import logging
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

_MAX_ARTICLES_PER_CALL = 6
_MAX_FULL_TEXT_CHARS = 2000  # per article in prompt

_SYSTEM_PROMPT = """You are a news sentiment analyst for prediction markets.
Given a market question and a list of news articles, analyze:
1. Each article's sentiment polarity toward the YES outcome (positive/negative/neutral)
2. Each article's impact on the event probability (high/medium/low)
3. The overall evidence direction (support_yes / oppose_yes / neutral)
4. Key quotes or facts that drive the assessment

Return ONLY valid JSON (no markdown) with this structure:
{
  "articles": [
    {
      "index": 0,
      "sentiment": "positive|negative|neutral",
      "impact": "high|medium|low",
      "key_facts": ["fact 1", "fact 2"],
      "relevance_to_question": 0.0-1.0
    }
  ],
  "overall_direction": "support_yes|oppose_yes|neutral",
  "overall_strength": 0.0-1.0,
  "conflict_level": 0.0-1.0,
  "summary": "中文一句话总结整体证据方向与强度"
}

All natural-language string values MUST be written in Simplified Chinese (简体中文).
Be conservative: only mark high impact for clear, direct evidence.
"""


def _build_user_prompt(market_question: str, articles: list[dict[str, Any]]) -> str:
    # Read the cap at call time so tests/monkeypatches on settings take effect.
    max_articles = settings.NEWS_SENTIMENT_MAX_ARTICLES or _MAX_ARTICLES_PER_CALL
    article_blocks = []
    for i, article in enumerate(articles[:max_articles]):
        title = article.get("title", "")[:200]
        desc = article.get("description", "")[:500]
        full_text = article.get("full_text") or ""
        full_text = full_text[:_MAX_FULL_TEXT_CHARS] if full_text else ""
        source = article.get("source", "unknown")
        block = f"""---
Article {i}:
Source: {source}
Title: {title}
Description: {desc}
"""
        if full_text:
            block += f"Full text: {full_text}\n"
        article_blocks.append(block)
    return f"""Market question: {market_question[:500]}

News articles:
{"".join(article_blocks)}
"""


async def analyze_sentiment(
    market_question: str,
    articles: list[dict[str, Any]],
) -> dict[str, Any]:
    """Analyze news sentiment for a market question using LLM.

    Returns sentiment profile dict. On any failure, returns a deterministic
    neutral fallback (never raises).
    """
    if not settings.NEWS_SENTIMENT_ENABLED:
        return _neutral_fallback("NEWS_SENTIMENT_ENABLED is false")
    if not articles:
        return _neutral_fallback("no articles")
    if not settings.OPENAI_API_KEY:
        return _neutral_fallback("no OPENAI_API_KEY configured")

    try:
        client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.DASHSCOPE_BASE_URL,
            timeout=30.0,
            max_retries=1,
        )
        response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(market_question, articles)},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        parsed = json.loads(content)
        # Validate minimum structure
        if "articles" not in parsed or "overall_direction" not in parsed:
            return _neutral_fallback("malformed LLM response")
        return parsed
    except Exception as exc:
        logger.warning("news_sentiment LLM call failed: %s", exc)
        # Generic reason in the summary: this string flows into the persisted
        # record and the probability-engine LLM prompt, so it must not leak
        # exception details. The detailed exception is already logged above.
        return _neutral_fallback("LLM 调用失败")


def _neutral_fallback(reason: str) -> dict[str, Any]:
    """Deterministic neutral fallback when LLM is unavailable."""
    return {
        "articles": [],
        "overall_direction": "neutral",
        "overall_strength": 0.0,
        "conflict_level": 0.0,
        "summary": f"情感分析不可用（{reason}），回退为中性",
        "fallback": True,
    }
