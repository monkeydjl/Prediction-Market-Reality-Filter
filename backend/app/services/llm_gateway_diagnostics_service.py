"""Read-only diagnostics for LLM gateway route configuration.

The report intentionally avoids live provider calls and never returns API keys.
It describes what the gateway would try, and whether each provider has enough
local configuration to be attempted.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.services import llm_gateway_service as gateway

logger = logging.getLogger(__name__)


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
        "cost_cap": _cost_cap_diagnostics(),
    }


def _cost_cap_diagnostics() -> dict[str, Any]:
    """How close today's LLM spend is to ``LLM_DAILY_COST_CAP_USD``.

    ``llm_daily_spend_store.get_spend_today()`` is the number the gateway
    enforces the cap against, and it had **no reader anywhere outside the
    gateway** — no route, no CLI, no dashboard. An operator running with a cap
    could not see how close they were to having every LLM call refused until it
    happened, at which point the only symptom is ``degraded_reason:
    daily_cost_cap_exceeded`` on individual events.

    Reports ``enabled: false`` and no spend figure when the cap is disabled (the
    default, 0). That is deliberate, not a shortcut: reading the store calls
    ``_ensure_schema``, which takes a *write* transaction to CREATE TABLE on
    first use, and the gateway's own contract is that the disabled default never
    touches storage at all (``test_disabled_cap_never_touches_storage``). With
    no cap there is also nothing to be close to, and per-call cost is on
    ``/metrics`` regardless of this setting.

    ``spend_today_usd`` is ``None`` rather than ``0.0`` whenever it was not
    measured, so a caller cannot read "no data" as "nothing spent" (cf. the
    sentinel-vs-measurement rule in the source-reliability path).
    """
    cap = float(getattr(settings, "LLM_DAILY_COST_CAP_USD", 0) or 0)
    if cap <= 0:
        return {
            "enabled": False,
            "cap_usd": 0.0,
            "spend_today_usd": None,
            "remaining_usd": None,
            "used_ratio": None,
            "status": "disabled",
            "error": None,
        }

    try:
        from app.memory import llm_daily_spend_store

        spend = float(llm_daily_spend_store.get_spend_today())
    except Exception as exc:
        # Same posture as the gateway's fail-OPEN cap check: a broken counter
        # must not turn a diagnostics read into a 500.
        logger.warning("LLM spend lookup failed for diagnostics: %s", exc)
        return {
            "enabled": True,
            "cap_usd": round(cap, 6),
            "spend_today_usd": None,
            "remaining_usd": None,
            "used_ratio": None,
            "status": "unknown",
            "error": "spend_lookup_failed",
        }

    remaining = cap - spend
    used_ratio = spend / cap
    if spend >= cap:
        status = "exceeded"
    elif used_ratio >= 0.8:
        status = "warning"
    else:
        status = "ok"
    return {
        "enabled": True,
        "cap_usd": round(cap, 6),
        "spend_today_usd": round(spend, 6),
        "remaining_usd": round(max(0.0, remaining), 6),
        "used_ratio": round(used_ratio, 4),
        "status": status,
        "error": None,
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
