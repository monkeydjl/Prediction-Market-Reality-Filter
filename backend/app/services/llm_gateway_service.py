"""Unified LLM Gateway with provider/model route resolution.

The Gateway hides provider and model ordering behind a small interface.  The
execution functions are added in the next task; this first slice defines the
route/config model so callers can share one routing vocabulary.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMProviderConfig:
    provider: str
    api_key: str
    base_url: str


@dataclass(frozen=True)
class LLMModelRoute:
    provider: str
    models: list[str]


@dataclass(frozen=True)
class LLMAttempt:
    provider: str
    model: str
    status: str
    error_type: str | None = None
    error_message: str | None = None
    latency_ms: int = 0


@dataclass(frozen=True)
class LLMResult:
    ok: bool
    content: str | None = None
    json_data: dict[str, Any] | None = None
    provider: str | None = None
    model: str | None = None
    attempts: list[LLMAttempt] | None = None
    usage: dict[str, int] | None = None
    degraded_reason: str | None = None


@dataclass(frozen=True)
class LLMEmbeddingResult:
    ok: bool
    vectors: list[list[float]] | None = None
    provider: str | None = None
    model: str | None = None
    attempts: list[LLMAttempt] | None = None
    usage: dict[str, int] | None = None
    degraded_reason: str | None = None


class LLMGatewayError(RuntimeError):
    """Raised when the Gateway cannot produce a usable LLM response."""


_TASK_ROUTE_SETTINGS = {
    "default": "LLM_ROUTE_DEFAULT",
    "probability_analysis": "LLM_ROUTE_PROBABILITY_ANALYSIS",
    "translation": "LLM_ROUTE_TRANSLATION",
    "open_web_extraction": "LLM_ROUTE_OPEN_WEB_EXTRACTION",
    "cross_validation": "LLM_ROUTE_CROSS_VALIDATION",
    "world_cup": "LLM_ROUTE_WORLD_CUP",
    "startup_check": "LLM_ROUTE_STARTUP_CHECK",
    "embedding": "LLM_ROUTE_EMBEDDING",
}

_client_cache: dict[tuple[str, str, str, float, int], Any] = {}


def reset_llm_gateway_clients_for_tests() -> None:
    """Clear cached provider clients between tests."""
    _client_cache.clear()


def parse_route_string(route: str) -> list[LLMModelRoute]:
    """Parse ``provider:model1,model2|provider2:model3`` preserving order."""
    parsed: list[LLMModelRoute] = []
    if not route or not route.strip():
        return parsed

    for raw_segment in route.split("|"):
        segment = raw_segment.strip()
        if not segment or ":" not in segment:
            continue
        provider_raw, models_raw = segment.split(":", 1)
        provider = provider_raw.strip().lower()
        models = [model.strip() for model in models_raw.split(",") if model.strip()]
        if not provider or not models:
            continue
        parsed.append(LLMModelRoute(provider=provider, models=models))
    return parsed


_INDEXED_OPENAI_KEY_RE = re.compile(r"^OPENAI_API_KEY_(\d+)$")
_INDEXED_OPENAI_MODEL_RE = re.compile(r"^OPENAI_MODEL_(\d+)_(\d+)$")


def _indexed_openai_provider_indices() -> list[int]:
    """Return configured numbered OpenAI-compatible provider indices."""
    indices: set[int] = set()
    for name, value in os.environ.items():
        if not value.strip():
            continue
        key_match = _INDEXED_OPENAI_KEY_RE.match(name)
        if key_match:
            indices.add(int(key_match.group(1)))
            continue
        model_match = _INDEXED_OPENAI_MODEL_RE.match(name)
        if model_match:
            indices.add(int(model_match.group(1)))
    return sorted(indices)


def _indexed_openai_models(provider_index: int) -> list[str]:
    """Return models for ``OPENAI_MODEL_<provider_index>_<model_index>`` in numeric order."""
    models_by_index: dict[int, str] = {}
    prefix = f"OPENAI_MODEL_{provider_index}_"
    for name, value in os.environ.items():
        if not name.startswith(prefix):
            continue
        model_match = _INDEXED_OPENAI_MODEL_RE.match(name)
        if not model_match:
            continue
        model = value.strip()
        if model:
            models_by_index[int(model_match.group(2))] = model
    return [models_by_index[index] for index in sorted(models_by_index)]


def _indexed_openai_routes() -> list[LLMModelRoute]:
    """Build routes from OPENAI_API_KEY_N / OPENAI_MODEL_N_M env variables."""
    routes: list[LLMModelRoute] = []
    for provider_index in _indexed_openai_provider_indices():
        api_key = os.getenv(f"OPENAI_API_KEY_{provider_index}", "").strip()
        models = _indexed_openai_models(provider_index)
        if not api_key or not models:
            continue
        routes.append(LLMModelRoute(provider=f"openai_{provider_index}", models=models))
    return routes


def _indexed_openai_provider_configs() -> dict[str, LLMProviderConfig]:
    """Build provider configs from numbered OpenAI-compatible env variables."""
    configs: dict[str, LLMProviderConfig] = {}
    for provider_index in _indexed_openai_provider_indices():
        provider = f"openai_{provider_index}"
        api_key = os.getenv(f"OPENAI_API_KEY_{provider_index}", "").strip()
        base_url = os.getenv(f"OPENAI_BASE_URL_{provider_index}", "").strip()
        if not api_key:
            continue
        configs[provider] = LLMProviderConfig(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
        )
    return configs


def _legacy_route() -> list[LLMModelRoute]:
    model = (settings.OPENAI_MODEL or "").strip()
    if not model:
        return []
    return [LLMModelRoute(provider="legacy_openai", models=[model])]


def _legacy_embedding_route() -> list[LLMModelRoute]:
    model = (settings.EMBEDDING_MODEL or "").strip()
    if not model:
        return []
    return [LLMModelRoute(provider="legacy_embedding", models=[model])]


def build_route(task: str = "default") -> list[LLMModelRoute]:
    """Build the route for a task, falling back to default then legacy config."""
    normalized_task = (task or "default").strip().lower()
    setting_name = _TASK_ROUTE_SETTINGS.get(normalized_task, "")

    route_value = getattr(settings, setting_name, "") if setting_name else ""
    routes = parse_route_string(route_value)
    if routes:
        return routes

    if normalized_task != "default":
        default_routes = parse_route_string(settings.LLM_ROUTE_DEFAULT)
        if default_routes:
            return default_routes

    if normalized_task == "embedding":
        legacy_embedding_routes = _legacy_embedding_route()
        if legacy_embedding_routes:
            return legacy_embedding_routes

    indexed_routes = _indexed_openai_routes()
    if indexed_routes:
        return indexed_routes

    return _legacy_route()



def has_configured_llm_route(
    task: str = "default",
    *,
    route: list[LLMModelRoute] | None = None,
    provider_configs: dict[str, LLMProviderConfig] | None = None,
) -> bool:
    """Return True when a task has at least one model backed by an API key.

    This mirrors the Gateway's route/config resolution, including numbered
    OpenAI-compatible providers (OPENAI_API_KEY_N / OPENAI_MODEL_N_M). Callers
    use it only for cheap feature availability checks; complete_chat/json remain
    the authoritative fallback executor.
    """
    routes = route if route is not None else build_route(task)
    configs = provider_configs if provider_configs is not None else _provider_configs()
    for model_route in routes:
        if not model_route.models:
            continue
        config = configs.get(model_route.provider)
        if config is not None and config.api_key.strip():
            return True
    return False


def _provider_configs() -> dict[str, LLMProviderConfig]:
    configs = {
        "legacy_embedding": LLMProviderConfig(
            provider="legacy_embedding",
            api_key=settings.EMBEDDING_API_KEY or settings.OPENAI_API_KEY,
            base_url=settings.EMBEDDING_BASE_URL,
        ),
        "legacy_openai": LLMProviderConfig(
            provider="legacy_openai",
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.DASHSCOPE_BASE_URL,
        ),
        "deepseek": LLMProviderConfig(
            provider="deepseek",
            api_key=settings.LLM_PROVIDER_DEEPSEEK_API_KEY or settings.OPENAI_API_KEY,
            base_url=settings.LLM_PROVIDER_DEEPSEEK_BASE_URL,
        ),
        "dashscope": LLMProviderConfig(
            provider="dashscope",
            api_key=settings.LLM_PROVIDER_DASHSCOPE_API_KEY or settings.OPENAI_API_KEY,
            base_url=settings.LLM_PROVIDER_DASHSCOPE_BASE_URL,
        ),
        "openai": LLMProviderConfig(
            provider="openai",
            api_key=settings.LLM_PROVIDER_OPENAI_API_KEY or settings.OPENAI_API_KEY,
            base_url=settings.LLM_PROVIDER_OPENAI_BASE_URL,
        ),
        "openrouter": LLMProviderConfig(
            provider="openrouter",
            api_key=settings.LLM_PROVIDER_OPENROUTER_API_KEY,
            base_url=settings.LLM_PROVIDER_OPENROUTER_BASE_URL,
        ),
    }
    configs.update(_indexed_openai_provider_configs())
    return configs


def _default_client_factory(config: LLMProviderConfig) -> AsyncOpenAI:
    cache_key = (
        config.provider,
        config.api_key,
        config.base_url,
        settings.LLM_TIMEOUT_SECONDS,
        settings.LLM_MAX_RETRIES_PER_MODEL,
    )
    cached = _client_cache.get(cache_key)
    if cached is not None:
        return cached

    kwargs: dict[str, Any] = {
        "api_key": config.api_key,
        "timeout": settings.LLM_TIMEOUT_SECONDS,
        "max_retries": settings.LLM_MAX_RETRIES_PER_MODEL,
    }
    if config.base_url:
        kwargs["base_url"] = config.base_url
    client = AsyncOpenAI(**kwargs)
    _client_cache[cache_key] = client
    return client


def _classify_exception(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timeout" in text or "timed out" in text:
        return "timeout"
    if "rate limit" in text or "too many requests" in text or "429" in text:
        return "rate_limit"
    if "401" in text or "403" in text or "unauthorized" in text or "api key" in text:
        return "auth_error"
    if "not found" in text or "model" in text and "does not exist" in text:
        return "model_not_found"
    if "500" in text or "502" in text or "503" in text or "504" in text:
        return "provider_5xx"
    if "network" in text or "connection" in text:
        return "network_error"
    return "provider_error"


def _content_error_type(content: str) -> str | None:
    lowered = content.lower()
    error_markers = (
        "\u8d1f\u8f7d\u8fc7\u9ad8",
        "rate limit",
        "too many requests",
        "temporarily unavailable",
        "overloaded",
        "model is busy",
    )
    if any(marker in lowered for marker in error_markers):
        return "provider_error_in_content"
    return None


def _extract_usage(response: Any) -> dict[str, int] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def _redact_error(exc: Exception) -> str:
    message = str(exc)
    known_keys = {
        getattr(settings, "OPENAI_API_KEY", "") or "",
        settings.LLM_PROVIDER_DEEPSEEK_API_KEY,
        settings.LLM_PROVIDER_DASHSCOPE_API_KEY,
        settings.LLM_PROVIDER_OPENAI_API_KEY,
        settings.LLM_PROVIDER_OPENROUTER_API_KEY,
    }
    for provider_index in _indexed_openai_provider_indices():
        known_keys.add(os.getenv(f"OPENAI_API_KEY_{provider_index}", ""))
    for api_key in sorted((key for key in known_keys if key), key=len, reverse=True):
        message = message.replace(api_key, "<redacted>")
    if len(message) > 300:
        return message[:300] + "..."
    return message


def _cost_cap_exceeded() -> tuple[bool, float, float]:
    """Whether today's LLM spend has reached ``LLM_DAILY_COST_CAP_USD``.

    Returns ``(exceeded, spend_today, cap)``. A cap of 0 (the default) means
    unlimited and short-circuits before touching SQLite, so deployments that
    never opt in pay no per-call cost for this check.

    Fail-OPEN on storage errors: if the counter cannot be read we let the call
    through rather than bricking every LLM path on a disk hiccup. The cap is a
    spend guard, not a correctness invariant, and a stuck-closed gateway is the
    more damaging failure.
    """
    cap = float(getattr(settings, "LLM_DAILY_COST_CAP_USD", 0) or 0)
    if cap <= 0:
        return False, 0.0, 0.0
    try:
        from app.memory import llm_daily_spend_store

        spend = llm_daily_spend_store.get_spend_today()
    except Exception:
        logger.warning("Daily LLM spend lookup failed; allowing call", exc_info=True)
        return False, 0.0, cap
    return spend >= cap, spend, cap


def _record_usage(model: str, usage: dict[str, int] | None) -> None:
    """Record one successful call's tokens and cost.

    Called from every gateway success path, which is the only place that sees
    all of them: 13 modules reach the gateway, and instrumenting a *caller*
    counts that caller only. ``pmrf_llm_token_cost_total`` was previously
    incremented from ``llm_telemetry_service`` — one call site inside the event
    enrichment path, itself behind ``LLM_TELEMETRY_ENABLED`` (default off), so
    on a default install the counter never moved at all and with telemetry on
    it saw ~half of one event's tokens (``translate_title`` is a second real
    gateway call and was invisible).

    Two sinks, deliberately different in scope:

    - Prometheus (``/metrics``) is **always** updated. It is a process-lifetime
      observability counter with no storage cost and no behavioural effect.
    - The daily-spend counter that enforces ``LLM_DAILY_COST_CAP_USD`` is only
      written when the cap is enabled, because it takes a SQLite write lock and
      the disabled default must stay a pure no-op.

    Both price with ``_lookup_price`` against the model that actually served
    the call, so the cap and the cost series cannot disagree.
    """
    if not usage:
        return
    total_tokens = int(usage.get("total_tokens", 0) or 0)
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)

    try:
        from app.services.llm_telemetry_service import _lookup_price

        price_per_1k = _lookup_price(model)
    except Exception:
        logger.warning("LLM price lookup failed", exc_info=True)
        return

    try:
        from app.utils.metrics import LLM_TOKEN_COST, LLM_TOKEN_USAGE

        if prompt_tokens > 0:
            LLM_TOKEN_USAGE.labels(model=model, kind="input").inc(prompt_tokens)
        if completion_tokens > 0:
            LLM_TOKEN_USAGE.labels(model=model, kind="output").inc(completion_tokens)
        if total_tokens > 0:
            LLM_TOKEN_COST.labels(model=model).inc(total_tokens * price_per_1k / 1000.0)
    except Exception:  # pragma: no cover - defensive, metrics must never break a call
        logger.warning("LLM token metrics record failed", exc_info=True)

    cap = float(getattr(settings, "LLM_DAILY_COST_CAP_USD", 0) or 0)
    if cap <= 0 or total_tokens <= 0:
        return
    try:
        from app.memory import llm_daily_spend_store

        llm_daily_spend_store.add_spend(total_tokens * price_per_1k / 1000.0)
    except Exception:
        logger.warning("Daily LLM spend record failed", exc_info=True)


def _cost_cap_attempts(routes: list[LLMModelRoute]) -> list[LLMAttempt]:
    """Build ``skipped`` attempts for every route the cap blocked.

    Mirrors the shape of the missing-api-key path so telemetry and the
    diagnostics endpoint render a capped run the same way as any other
    all-routes-unavailable run.
    """
    return [
        LLMAttempt(
            provider=model_route.provider,
            model=model,
            status="skipped",
            error_type="daily_cost_cap_exceeded",
            error_message="Daily LLM cost cap reached.",
        )
        for model_route in routes
        for model in model_route.models
    ]


async def _complete(
    *,
    task: str = "default",
    messages: list[dict[str, str]],
    temperature: float = 0,
    max_tokens: int | None = None,
    response_format: dict[str, Any] | None = None,
    route: list[LLMModelRoute] | None = None,
    provider_configs: dict[str, LLMProviderConfig] | None = None,
    client_factory: Callable[[LLMProviderConfig], Any] | None = None,
    expect_json: bool = False,
) -> LLMResult:
    attempts: list[LLMAttempt] = []
    routes = route if route is not None else build_route(task)
    configs = provider_configs if provider_configs is not None else _provider_configs()
    factory = client_factory or _default_client_factory

    # Blocking SQLite read on the cap counter: offload so the pre-call check
    # doesn't freeze the loop for every concurrent LLM caller.
    capped, spend, cap = await asyncio.to_thread(_cost_cap_exceeded)
    if capped:
        logger.warning(
            "Daily LLM cost cap reached (%.4f/%.4f USD); refusing task=%s",
            spend,
            cap,
            task,
        )
        return LLMResult(
            ok=False,
            attempts=_cost_cap_attempts(routes),
            degraded_reason="daily_cost_cap_exceeded",
        )

    for model_route in routes:
        config = configs.get(model_route.provider)
        if config is None:
            for model in model_route.models:
                attempts.append(
                    LLMAttempt(
                        provider=model_route.provider,
                        model=model,
                        status="skipped",
                        error_type="missing_provider_config",
                        error_message="Provider config is not available.",
                    )
                )
            continue

        if not config.api_key:
            for model in model_route.models:
                attempts.append(
                    LLMAttempt(
                        provider=model_route.provider,
                        model=model,
                        status="skipped",
                        error_type="missing_api_key",
                        error_message="Provider API key is empty.",
                    )
                )
            continue

        client = factory(config)
        for model in model_route.models:
            started = time.perf_counter()
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                }
                if max_tokens is not None:
                    kwargs["max_tokens"] = max_tokens
                effective_response_format = response_format
                if expect_json and effective_response_format is None:
                    effective_response_format = {"type": "json_object"}
                if effective_response_format is not None:
                    kwargs["response_format"] = effective_response_format

                response = await client.chat.completions.create(**kwargs)
                latency_ms = int((time.perf_counter() - started) * 1000)
                content = (response.choices[0].message.content or "").strip()
                if not content:
                    attempts.append(
                        LLMAttempt(
                            provider=model_route.provider,
                            model=model,
                            status="failed",
                            error_type="empty_response",
                            error_message="Provider returned empty content.",
                            latency_ms=latency_ms,
                        )
                    )
                    continue

                content_error = _content_error_type(content)
                if content_error is not None:
                    attempts.append(
                        LLMAttempt(
                            provider=model_route.provider,
                            model=model,
                            status="failed",
                            error_type=content_error,
                            error_message=content[:300],
                            latency_ms=latency_ms,
                        )
                    )
                    continue

                json_data: dict[str, Any] | None = None
                if expect_json:
                    try:
                        parsed = json.loads(content)
                    except Exception as exc:
                        attempts.append(
                            LLMAttempt(
                                provider=model_route.provider,
                                model=model,
                                status="failed",
                                error_type="invalid_json",
                                error_message=_redact_error(exc),
                                latency_ms=latency_ms,
                            )
                        )
                        continue
                    if not isinstance(parsed, dict):
                        attempts.append(
                            LLMAttempt(
                                provider=model_route.provider,
                                model=model,
                                status="failed",
                                error_type="invalid_json",
                                error_message="JSON response is not an object.",
                                latency_ms=latency_ms,
                            )
                        )
                        continue
                    json_data = parsed

                success_attempt = LLMAttempt(
                    provider=model_route.provider,
                    model=model,
                    status="success",
                    latency_ms=latency_ms,
                )
                usage = _extract_usage(response)
                # Blocking SQLite write: offload so recording spend doesn't
                # stall the loop on the write lock after every success.
                await asyncio.to_thread(_record_usage, model, usage)
                return LLMResult(
                    ok=True,
                    content=content,
                    json_data=json_data,
                    provider=model_route.provider,
                    model=model,
                    attempts=[*attempts, success_attempt],
                    usage=usage,
                )
            except Exception as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                attempts.append(
                    LLMAttempt(
                        provider=model_route.provider,
                        model=model,
                        status="failed",
                        error_type=_classify_exception(exc),
                        error_message=_redact_error(exc),
                        latency_ms=latency_ms,
                    )
                )

    return LLMResult(
        ok=False,
        attempts=attempts,
        degraded_reason="all_routes_failed",
    )


async def complete_chat(
    *,
    task: str = "default",
    messages: list[dict[str, str]],
    temperature: float = 0,
    max_tokens: int | None = None,
    response_format: dict[str, Any] | None = None,
    route: list[LLMModelRoute] | None = None,
    provider_configs: dict[str, LLMProviderConfig] | None = None,
    client_factory: Callable[[LLMProviderConfig], Any] | None = None,
) -> LLMResult:
    return await _complete(
        task=task,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
        route=route,
        provider_configs=provider_configs,
        client_factory=client_factory,
        expect_json=False,
    )


async def complete_json(
    *,
    task: str = "default",
    messages: list[dict[str, str]],
    temperature: float = 0,
    max_tokens: int | None = None,
    response_format: dict[str, Any] | None = None,
    route: list[LLMModelRoute] | None = None,
    provider_configs: dict[str, LLMProviderConfig] | None = None,
    client_factory: Callable[[LLMProviderConfig], Any] | None = None,
) -> LLMResult:
    return await _complete(
        task=task,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
        route=route,
        provider_configs=provider_configs,
        client_factory=client_factory,
        expect_json=True,
    )


async def complete_embeddings(
    *,
    input: list[str],
    task: str = "embedding",
    route: list[LLMModelRoute] | None = None,
    provider_configs: dict[str, LLMProviderConfig] | None = None,
    client_factory: Callable[[LLMProviderConfig], Any] | None = None,
) -> LLMEmbeddingResult:
    attempts: list[LLMAttempt] = []
    if not input:
        return LLMEmbeddingResult(ok=True, vectors=[], attempts=[])

    routes = route if route is not None else build_route(task)
    configs = provider_configs if provider_configs is not None else _provider_configs()
    factory = client_factory or _default_client_factory

    # Blocking SQLite read on the cap counter: offload so the pre-call check
    # doesn't freeze the loop for every concurrent LLM caller.
    capped, spend, cap = await asyncio.to_thread(_cost_cap_exceeded)
    if capped:
        logger.warning(
            "Daily LLM cost cap reached (%.4f/%.4f USD); refusing embeddings",
            spend,
            cap,
        )
        return LLMEmbeddingResult(
            ok=False,
            attempts=_cost_cap_attempts(routes),
            degraded_reason="daily_cost_cap_exceeded",
        )

    for model_route in routes:
        config = configs.get(model_route.provider)
        if config is None:
            for model in model_route.models:
                attempts.append(
                    LLMAttempt(
                        provider=model_route.provider,
                        model=model,
                        status="skipped",
                        error_type="missing_provider_config",
                        error_message="Provider config is not available.",
                    )
                )
            continue
        if not config.api_key:
            for model in model_route.models:
                attempts.append(
                    LLMAttempt(
                        provider=model_route.provider,
                        model=model,
                        status="skipped",
                        error_type="missing_api_key",
                        error_message="Provider API key is empty.",
                    )
                )
            continue

        client = factory(config)
        for model in model_route.models:
            started = time.perf_counter()
            try:
                response = await client.embeddings.create(model=model, input=input)
                latency_ms = int((time.perf_counter() - started) * 1000)
                vectors = [list(item.embedding) for item in response.data]
                if len(vectors) != len(input):
                    attempts.append(
                        LLMAttempt(
                            provider=model_route.provider,
                            model=model,
                            status="failed",
                            error_type="invalid_embedding_shape",
                            error_message="Embedding count did not match input count.",
                            latency_ms=latency_ms,
                        )
                    )
                    continue
                success_attempt = LLMAttempt(
                    provider=model_route.provider,
                    model=model,
                    status="success",
                    latency_ms=latency_ms,
                )
                usage = _extract_usage(response)
                # Blocking SQLite write: offload so recording spend doesn't
                # stall the loop on the write lock after every success.
                await asyncio.to_thread(_record_usage, model, usage)
                return LLMEmbeddingResult(
                    ok=True,
                    vectors=vectors,
                    provider=model_route.provider,
                    model=model,
                    attempts=[*attempts, success_attempt],
                    usage=usage,
                )
            except Exception as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                attempts.append(
                    LLMAttempt(
                        provider=model_route.provider,
                        model=model,
                        status="failed",
                        error_type=_classify_exception(exc),
                        error_message=_redact_error(exc),
                        latency_ms=latency_ms,
                    )
                )

    return LLMEmbeddingResult(
        ok=False,
        attempts=attempts,
        degraded_reason="all_routes_failed",
    )

