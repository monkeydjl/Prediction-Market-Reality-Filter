"""cross_validation_service.py
==========================
Multi-model cross-validation for probability estimates.

An independent second model re-estimates the probability for the same event
question and evidence the primary engine saw, and the result is summarized as
agreement / divergence against the system's published estimate. This is a
human-review signal: when an independent model sharply disagrees with the
system, that is worth a closer look.

Opt-in: disabled unless `settings.CROSS_VALIDATION_MODEL` is set. The base URL
and API key fall back to the primary model's when left empty, so the common case
is cross-validating with a different model on the same provider. A missing
config or any call failure yields None (cross-validation is skipped), so it never
breaks analysis.

The second model is asked with the exact same prompts as the primary (reused
from probability_engine_service) for an apples-to-apples comparison. The live
call lives behind `_ask_second_model` so tests stay network-free.

Event vocabulary only - no trading terms.
"""

import json
import logging

from openai import AsyncOpenAI

from app.core.config import settings
from app.services.probability_engine_service import (
    _build_system_prompt,
    _build_user_prompt,
)
from app.utils.market_utils import safe_float

logger = logging.getLogger(__name__)

_second_client: AsyncOpenAI | None = None


async def cross_validate(
    question: str,
    news_context: str,
    primary_probability: float,
) -> dict | None:
    """Re-estimate the probability with an independent second model.

    Returns None when cross-validation is disabled (no model configured) or the
    second model is unavailable. Otherwise returns:
        {model, probability, primary_probability, divergence, agreement}
    where agreement is high / medium / low by absolute divergence (points).
    """
    if not settings.CROSS_VALIDATION_MODEL:
        return None
    try:
        raw = await _ask_second_model(
            market_question=question,
            market_probability=primary_probability,
            news_context=news_context,
        )
    except Exception as exc:
        logger.warning("Cross-validation failed: %s", exc)
        return None

    second = _clamp_pct(raw.get("ai_probability"), primary_probability)
    divergence = round(abs(second - primary_probability), 2)
    return {
        "model": settings.CROSS_VALIDATION_MODEL,
        "probability": round(second, 2),
        "primary_probability": round(primary_probability, 2),
        "divergence": divergence,
        "agreement": _agreement(divergence),
    }


def _get_second_client() -> AsyncOpenAI:
    global _second_client
    if _second_client is None:
        _second_client = AsyncOpenAI(
            api_key=settings.CROSS_VALIDATION_API_KEY or settings.OPENAI_API_KEY,
            base_url=settings.CROSS_VALIDATION_BASE_URL or settings.DASHSCOPE_BASE_URL,
            timeout=60.0,
            max_retries=2,
        )
    return _second_client


async def _ask_second_model(
    market_question: str,
    market_probability: float,
    news_context: str,
) -> dict:
    client = _get_second_client()
    response = await client.chat.completions.create(
        model=settings.CROSS_VALIDATION_MODEL,
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
        raise ValueError("second model returned non-object JSON")
    return parsed


def _agreement(divergence: float) -> str:
    if divergence <= 10:
        return "high"
    if divergence <= 25:
        return "medium"
    return "low"


def _clamp_pct(value, fallback: float) -> float:
    number = safe_float(value, fallback)
    return max(0.0, min(100.0, number))


_CREDIBILITY_DELTA = {"high": 5, "medium": 0, "low": -15}


def credibility_delta(agreement: str) -> int:
    """How much an independent second model's agreement nudges credibility.

    High agreement modestly corroborates the estimate; low agreement (the models
    disagree) penalizes it; medium is neutral. Only applied when cross-validation
    is enabled.
    """
    return _CREDIBILITY_DELTA.get(agreement, 0)
