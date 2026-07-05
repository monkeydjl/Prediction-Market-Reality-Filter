"""Unified LLM Gateway with provider/model route resolution.

The Gateway hides provider and model ordering behind a small interface.  The
execution functions are added in the next task; this first slice defines the
route/config model so callers can share one routing vocabulary.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings


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


class LLMGatewayError(RuntimeError):
    """Raised when the Gateway cannot produce a usable LLM response."""


_TASK_ROUTE_SETTINGS = {
    "default": "LLM_ROUTE_DEFAULT",
    "probability_analysis": "LLM_ROUTE_PROBABILITY_ANALYSIS",
    "translation": "LLM_ROUTE_TRANSLATION",
    "cross_validation": "LLM_ROUTE_CROSS_VALIDATION",
    "world_cup": "LLM_ROUTE_WORLD_CUP",
    "startup_check": "LLM_ROUTE_STARTUP_CHECK",
}

_client_cache: dict[str, Any] = {}


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


def _legacy_route() -> list[LLMModelRoute]:
    model = (settings.OPENAI_MODEL or "").strip()
    if not model:
        return []
    return [LLMModelRoute(provider="legacy_openai", models=[model])]


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

    return _legacy_route()



def _provider_configs() -> dict[str, LLMProviderConfig]:
    return {
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


def _default_client_factory(config: LLMProviderConfig) -> AsyncOpenAI:
    cached = _client_cache.get(config.provider)
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
    _client_cache[config.provider] = client
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
    api_key = getattr(settings, "OPENAI_API_KEY", "") or ""
    if api_key:
        message = message.replace(api_key, "<redacted>")
    if len(message) > 300:
        return message[:300] + "..."
    return message


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
                return LLMResult(
                    ok=True,
                    content=content,
                    json_data=json_data,
                    provider=model_route.provider,
                    model=model,
                    attempts=[*attempts, success_attempt],
                    usage=_extract_usage(response),
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

