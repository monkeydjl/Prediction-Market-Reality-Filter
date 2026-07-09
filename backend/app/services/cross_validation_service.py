"""cross_validation_service.py
==========================
Multi-model cross-validation for probability estimates.

An independent second model re-estimates the probability for the same event
question and evidence the primary engine saw, and the result is summarized as
agreement / divergence against the system's published estimate. This is a
human-review signal: when an independent model sharply disagrees with the
system, that is worth a closer look.

Opt-in: disabled unless the legacy cross-validation model or the explicit
Gateway cross-validation route is configured. A missing config or any call
failure yields None (cross-validation is skipped), so it never breaks analysis.

The second model is asked with the exact same prompts as the primary (reused
from probability_engine_service) for an apples-to-apples comparison. The live
call lives behind `_ask_second_model` so tests stay network-free.

Event vocabulary only - no trading terms.
"""

import logging
from typing import Any

from app.core.config import settings
from app.services.llm_gateway_service import LLMModelRoute, LLMProviderConfig, complete_json
from app.services.probability_engine_service import (
    _build_system_prompt,
    _build_user_prompt,
)
from app.utils.failure_policy import fail_closed_none
from app.utils.market_utils import safe_float

logger = logging.getLogger(__name__)


async def cross_validate(
    question: str,
    news_context: str,
    primary_probability: float,
    market_baseline: float | None = None,
) -> dict[str, Any] | None:
    """Re-estimate the probability with an independent second model.

    Returns None when cross-validation is disabled (no model configured) or the
    second model is unavailable. Otherwise returns:
        {model, probability, primary_probability, divergence, agreement}
    where agreement is high / medium / low by absolute divergence (points).

    ``market_baseline`` is the real market price (e.g. 90% from Polymarket).
    The second model receives this as its anchor, NOT ``primary_probability``
    (which is the primary AI's output). Passing the primary's output would make
    the second model anchor to our own estimate, defeating independent
    cross-validation.
    """
    if not settings.CROSS_VALIDATION_MODEL and not settings.LLM_ROUTE_CROSS_VALIDATION:
        return None
    # Use the real market baseline as the anchor for the second model. Fall back
    # to primary_probability only when no baseline is available (e.g. news-only
    # events without a linked market).
    anchor = market_baseline if market_baseline is not None else primary_probability
    try:
        raw = await _ask_second_model(
            market_question=question,
            market_probability=anchor,
            news_context=news_context,
        )
    except Exception as exc:
        return fail_closed_none(
            logger,
            "cross_validation",
            exc,
            context={"model": settings.CROSS_VALIDATION_MODEL},
        )

    second = _clamp_pct(raw.get("ai_probability"), primary_probability)
    divergence = round(abs(second - primary_probability), 2)
    return {
        "model": raw.get("_llm_model") or settings.CROSS_VALIDATION_MODEL,
        "probability": round(second, 2),
        "primary_probability": round(primary_probability, 2),
        "divergence": divergence,
        "agreement": _agreement(divergence),
    }


async def _ask_second_model(
    market_question: str,
    market_probability: float,
    news_context: str,
) -> dict[str, Any]:
    route = None
    provider_configs = None
    if settings.CROSS_VALIDATION_MODEL and not settings.LLM_ROUTE_CROSS_VALIDATION:
        route = [
            LLMModelRoute(
                provider="legacy_cross_validation",
                models=[settings.CROSS_VALIDATION_MODEL],
            )
        ]
        provider_configs = {
            "legacy_cross_validation": LLMProviderConfig(
                provider="legacy_cross_validation",
                api_key=settings.CROSS_VALIDATION_API_KEY or settings.OPENAI_API_KEY,
                base_url=settings.CROSS_VALIDATION_BASE_URL or settings.DASHSCOPE_BASE_URL,
            )
        }
    result = await complete_json(
        task="cross_validation",
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
        route=route,
        provider_configs=provider_configs,
    )
    if not result.ok or result.json_data is None:
        raise RuntimeError(result.degraded_reason or "cross-validation LLM unavailable")
    parsed = dict(result.json_data)
    if not isinstance(parsed, dict):
        raise ValueError("second model returned non-object JSON")
    if result.model:
        parsed["_llm_model"] = result.model
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
