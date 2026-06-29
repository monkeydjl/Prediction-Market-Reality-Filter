"""LLM telemetry service (Phase 5: LLM Cost and Stability Telemetry).

Pure-function layer that aggregates LLM stability and cost signals into an
``llm_telemetry`` overlay block. Applies to ALL events — every event makes
at least one LLM call (or falls back to deterministic).

This is a **hybrid** implementation:
- Minimal instrumentation in ``_ask_ai`` captures real ``response.usage``
  token counts (attached as ``llm_usage`` on the analysis dict).
- This pure function reads that + ``analysis_quality`` + ``sentiment_profile``
  fallback flag to produce a structured telemetry block.

Unlike Phases 1-4 (decision overlays), this is an observability layer — it
does NOT participate in the ``merge_quality_overlays`` direction pipeline
and does NOT produce ``downgrade_reason`` / ``suggested_direction``. It only
records what happened during the LLM call for audit/monitoring.

The function is synchronous and deterministic — no LLM calls, no I/O.
``settings`` is intentionally not passed; the orchestrator extracts concrete
scalar config values and passes them explicitly.

Invariants:
- MUST NOT mutate ``analysis``, ``sentiment_profile``, or any overlay block.
- MUST NOT raise — malformed inputs produce a best-effort block with error.
- Returns ``None`` when ``enabled=False`` (no key attached, byte-identical
  to pre-Phase-5 records).
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Per-1K-token pricing (USD) for common models. This is a cost *estimate*
# for observability, NOT a billing system. Prices approximate blended
# input+output rates as of 2025; updated manually when models change.
_MODEL_PRICING_PER_1K: dict[str, float] = {
    "gpt-4o-mini": 0.00015,
    "gpt-4o": 0.005,
    "gpt-4-turbo": 0.01,
    "gpt-4": 0.03,
    "gpt-3.5-turbo": 0.0005,
    "deepseek-chat": 0.00014,
    "deepseek-reasoner": 0.00055,
}
# Conservative fallback for unknown models (assumes mid-tier pricing).
_DEFAULT_PRICE_PER_1K: float = 0.005

# Chars-per-token heuristic for estimating prompt size when real usage is
# unavailable (degraded mode). English text averages ~4 chars/token.
_CHARS_PER_TOKEN: int = 4


def build_llm_telemetry(
    *,
    analysis: dict[str, Any] | None,
    sentiment_profile: dict[str, Any] | None,
    news_context: str,
    model: str,
    enabled: bool,
) -> dict[str, Any] | None:
    """Build the ``llm_telemetry`` overlay block.

    Pure function: reads inputs, returns overlay block, no writeback.
    Never raises — malformed inputs produce a best-effort block with error.
    Returns ``None`` when ``enabled=False`` (byte-identical to pre-Phase-5).
    """
    if not enabled:
        return None

    try:
        return _build_block(analysis, sentiment_profile, news_context, model)
    except Exception as exc:
        logger.warning("llm_telemetry build failed: %s", exc)
        # Best-effort fallback: still surface degraded_mode + analysis_quality
        analysis_quality = _extract_analysis_quality(analysis)
        return {
            "error": "build_failed",
            "degraded_mode": analysis_quality == "deterministic_fallback",
            "degraded_reason": None,
            "analysis_quality": analysis_quality,
            "sentiment_degraded": False,
            "llm_call_count": 0,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "estimated_token_cost": 0.0,
            "model": model,
        }


def _build_block(
    analysis: dict[str, Any] | None,
    sentiment_profile: dict[str, Any] | None,
    news_context: str,
    model: str,
) -> dict[str, Any]:
    analysis_quality = _extract_analysis_quality(analysis)
    degraded_mode = analysis_quality == "deterministic_fallback"
    degraded_reason = "llm_call_failed" if degraded_mode else None

    sentiment_degraded = _extract_sentiment_degraded(sentiment_profile)

    llm_usage = _extract_llm_usage(analysis)
    prompt_tokens = llm_usage.get("prompt_tokens") if llm_usage else None
    completion_tokens = llm_usage.get("completion_tokens") if llm_usage else None
    total_tokens = llm_usage.get("total_tokens") if llm_usage else None

    # llm_call_count: lower bound. 1 when LLM succeeded (the main _ask_ai
    # call). 0 when degraded without title translation. We cannot detect
    # translate_title calls without instrumenting them, so this is a
    # conservative minimum.
    if degraded_mode:
        # Fallback path: _ask_ai did not run. translate_title may have
        # been called (1 call) but we can't detect it here.
        llm_call_count = 0
    else:
        llm_call_count = 1

    price_per_1k = _lookup_price(model)
    estimated_cost = _estimate_cost(
        total_tokens, news_context, price_per_1k
    )

    return {
        "degraded_mode": degraded_mode,
        "degraded_reason": degraded_reason,
        "analysis_quality": analysis_quality,
        "sentiment_degraded": sentiment_degraded,
        "llm_call_count": llm_call_count,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "estimated_token_cost": round(estimated_cost, 6),
        "model": model,
    }


def _extract_analysis_quality(analysis: dict[str, Any] | None) -> str:
    if not isinstance(analysis, dict):
        return "unknown"
    quality = analysis.get("analysis_quality")
    if isinstance(quality, str) and quality in ("llm", "deterministic_fallback"):
        return quality
    return "unknown"


def _extract_sentiment_degraded(
    sentiment_profile: dict[str, Any] | None,
) -> bool:
    """Extract the sentiment degradation flag.

    Returns True when sentiment_profile.fallback is True (the sentiment LLM
    call failed or returned malformed data). Returns False when the flag is
    False or missing (sentiment was real or not requested). Note: this does
    NOT distinguish "sentiment was real" from "sentiment was not requested"
    — both surface as False. The caller can infer from NEWS_SENTIMENT_ENABLED
    if that distinction matters.
    """
    if not isinstance(sentiment_profile, dict):
        return False
    return bool(sentiment_profile.get("fallback", False))


def _extract_llm_usage(
    analysis: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(analysis, dict):
        return None
    usage = analysis.get("llm_usage")
    if not isinstance(usage, dict):
        return None
    # Validate that at least one token count is a non-negative int
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        val = usage.get(key)
        if val is not None:
            if not isinstance(val, int) or val < 0:
                return None
    return usage


def _lookup_price(model: str) -> float:
    """Look up the per-1K-token price for a model. Falls back to a
    conservative default for unknown models."""
    if not isinstance(model, str) or not model:
        return _DEFAULT_PRICE_PER_1K
    # Exact match first, then case-insensitive, then prefix match (for
    # versioned model names like "gpt-4o-mini-2024-07-18").
    if model in _MODEL_PRICING_PER_1K:
        return _MODEL_PRICING_PER_1K[model]
    model_lower = model.lower()
    for key, price in _MODEL_PRICING_PER_1K.items():
        if model_lower == key.lower():
            return price
    # Prefix match (e.g. "gpt-4o-mini-2024-07-18" -> "gpt-4o-mini")
    for key, price in _MODEL_PRICING_PER_1K.items():
        if model_lower.startswith(key.lower()):
            return price
    return _DEFAULT_PRICE_PER_1K


def _estimate_cost(
    total_tokens: int | None,
    news_context: str,
    price_per_1k: float,
) -> float:
    """Estimate the token cost in USD.

    When ``total_tokens`` is available (real usage from the API), compute
    the exact cost: ``total_tokens * price_per_1k / 1000``.

    When ``total_tokens`` is None (degraded mode — LLM call failed), estimate
    the prompt size from ``news_context`` length using the chars/4 heuristic.
    This gives a rough "what the call WOULD have cost" estimate, useful for
    monitoring cost trends even when the LLM is down.
    """
    if total_tokens is not None and total_tokens > 0:
        return total_tokens * price_per_1k / 1000.0
    # Degraded: estimate from news_context length
    context_len = len(news_context or "")
    if context_len == 0:
        return 0.0
    estimated_tokens = context_len / _CHARS_PER_TOKEN
    return estimated_tokens * price_per_1k / 1000.0
