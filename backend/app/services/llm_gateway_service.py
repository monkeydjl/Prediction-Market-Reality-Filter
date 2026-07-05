"""Unified LLM Gateway with provider/model route resolution.

The Gateway hides provider and model ordering behind a small interface.  The
execution functions are added in the next task; this first slice defines the
route/config model so callers can share one routing vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
