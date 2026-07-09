"""Read-only diagnostics for LLM gateway route configuration.

The report intentionally avoids live provider calls and never returns API keys.
It describes what the gateway would try, and whether each provider has enough
local configuration to be attempted.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.services import llm_gateway_service as gateway


def build_llm_diagnostics() -> dict[str, Any]:
    provider_configs = gateway._provider_configs()
    tasks = [
        _task_diagnostics(task, setting_name, provider_configs)
        for task, setting_name in gateway._TASK_ROUTE_SETTINGS.items()
    ]
    return {
        "tasks": tasks,
        "configured_task_count": sum(1 for task in tasks if task["configured"]),
        "unconfigured_task_count": sum(1 for task in tasks if not task["configured"]),
    }


def _task_diagnostics(
    task: str,
    setting_name: str,
    provider_configs: dict[str, gateway.LLMProviderConfig],
) -> dict[str, Any]:
    routes, source = _resolve_route_with_source(task, setting_name)
    route_rows = [
        _route_diagnostics(route, provider_configs)
        for route in routes
    ]
    return {
        "task": task,
        "setting": setting_name,
        "route_source": source,
        "configured": any(row["api_key_configured"] and row["models"] for row in route_rows),
        "routes": route_rows,
    }


def _resolve_route_with_source(
    task: str,
    setting_name: str,
) -> tuple[list[gateway.LLMModelRoute], str]:
    route_value = getattr(settings, setting_name, "") if setting_name else ""
    task_routes = gateway.parse_route_string(route_value)
    if task_routes:
        return task_routes, "task"

    if task != "default":
        default_routes = gateway.parse_route_string(settings.LLM_ROUTE_DEFAULT)
        if default_routes:
            return default_routes, "default"

    if task == "embedding":
        legacy_embedding_routes = gateway._legacy_embedding_route()
        if legacy_embedding_routes:
            return legacy_embedding_routes, "legacy_embedding"

    indexed_routes = gateway._indexed_openai_routes()
    if indexed_routes:
        return indexed_routes, "indexed_openai"

    legacy_routes = gateway._legacy_route()
    if legacy_routes:
        return legacy_routes, "legacy_openai"

    return [], "none"


def _route_diagnostics(
    route: gateway.LLMModelRoute,
    provider_configs: dict[str, gateway.LLMProviderConfig],
) -> dict[str, Any]:
    config = provider_configs.get(route.provider)
    return {
        "provider": route.provider,
        "models": list(route.models),
        "provider_configured": config is not None,
        "api_key_configured": bool(config and config.api_key.strip()),
        "base_url_configured": bool(config and config.base_url.strip()),
    }
