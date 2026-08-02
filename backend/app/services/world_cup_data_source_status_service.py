"""Operational status for configured World Cup data sources."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.core.config import settings
from app.memory import loop_run_store
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, sports_fact_status


_FEED_URL_SETTINGS = (
    ("matches", "WORLD_CUP_MATCH_SOURCE_URL"),
    ("match_events", "WORLD_CUP_MATCH_EVENTS_SOURCE_URL"),
    ("lineups", "WORLD_CUP_LINEUPS_SOURCE_URL"),
    ("standings", "WORLD_CUP_STANDINGS_SOURCE_URL"),
    ("player_awards", "WORLD_CUP_PLAYER_AWARDS_SOURCE_URL"),
    ("player_status", "WORLD_CUP_PLAYER_STATUS_SOURCE_URL"),
    ("statistics", "WORLD_CUP_STATISTICS_SOURCE_URL"),
)

_READINESS_ISSUE_DETAILS = {
    "qualification_source_not_configured": {
        "severity": "error",
        "message": "尚未配置真实积分榜/出线数据源",
        "action": (
            "配置 WORLD_CUP_STANDINGS_SOURCE_URL，或配置 Football-Data.org、"
            "API-Football、SportMonks standings provider。"
        ),
    },
    "qualification_facts_missing": {
        "severity": "error",
        "message": "尚未导入真实出线/淘汰事实",
        "action": "从真实 standings 源导入 qualification facts 后再信任出线状态。",
    },
    "qualification_import_required": {
        "severity": "error",
        "message": "已配置真实出线源，但尚未成功导入出线事实",
        "action": "在数据源面板运行推荐来源的 Import，确认导入结果包含 qualification facts。",
    },
    "scheduled_import_disabled": {
        "severity": "warn",
        "message": "世界杯真实数据定时导入未开启",
        "action": "开启 WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED 并选择真实导入模式。",
    },
    "last_import_failed": {
        "severity": "warn",
        "message": "最近一次世界杯数据导入失败",
        "action": "检查最近导入错误和数据源配置，修复后重新导入。",
    },
}

_READINESS_ISSUE_DETAILS["recommended_provider_validation_failed"] = {
    "severity": "error",
    "message": "Recommended real-data provider validation failed",
    "action": (
        "API-Football pipeline validation failed; fix league/season/plan/provider "
        "coverage or choose another real standings source before import."
    ),
}
_READINESS_ISSUE_DETAILS["qualification_facts_untrusted"] = {
    "severity": "error",
    "message": "Qualification facts exist but lack trusted source metadata",
    "action": "Re-import qualification facts from a real standings source with source_url and observed_at.",
}


def world_cup_data_source_status() -> dict[str, Any]:
    """Return sanitized config and last-run status for World Cup data sources."""

    facts = sports_fact_status(tournament=WORLD_CUP_TOURNAMENT)
    configured_sources = {
        "data_file": _file_config(settings.WORLD_CUP_DATA_FILE),
        "bundle_file": _file_config(settings.WORLD_CUP_SOURCE_BUNDLE_FILE),
        "bundle_url": _url_config(settings.WORLD_CUP_SOURCE_BUNDLE_URL),
        "feeds": [
            {
                "kind": kind,
                **_url_config(getattr(settings, setting_name, "")),
            }
            for kind, setting_name in _FEED_URL_SETTINGS
        ],
        "api_football": {
            "configured": bool(_clean(settings.WORLD_CUP_API_FOOTBALL_API_KEY)),
            "base_url": _display_url(settings.WORLD_CUP_API_FOOTBALL_BASE_URL),
            "league_id": _clean(settings.WORLD_CUP_API_FOOTBALL_LEAGUE_ID),
            "season": _clean(settings.WORLD_CUP_API_FOOTBALL_SEASON),
            "fetch_events": settings.WORLD_CUP_API_FOOTBALL_FETCH_EVENTS,
            "fetch_lineups": settings.WORLD_CUP_API_FOOTBALL_FETCH_LINEUPS,
            "fetch_statistics": settings.WORLD_CUP_API_FOOTBALL_FETCH_STATISTICS,
            "max_detail_calls": settings.WORLD_CUP_API_FOOTBALL_MAX_DETAIL_CALLS,
        },
        "football_data": {
            "configured": bool(_clean(settings.FOOTBALL_DATA_API_KEY)),
            "base_url": _display_url(settings.FOOTBALL_DATA_BASE_URL),
            "competition": "WC",
        },
        "sportmonks": {
            "configured": bool(
                _clean(settings.WORLD_CUP_SPORTMONKS_API_TOKEN)
                and (
                    _clean(settings.WORLD_CUP_SPORTMONKS_FIXTURES_URL)
                    or _clean(settings.WORLD_CUP_SPORTMONKS_STANDINGS_URL)
                    or _clean(settings.WORLD_CUP_SPORTMONKS_TOP_SCORERS_URL)
                )
            ),
            "feeds": [
                {
                    "kind": "matches",
                    **_url_config(settings.WORLD_CUP_SPORTMONKS_FIXTURES_URL),
                },
                {
                    "kind": "standings",
                    **_url_config(settings.WORLD_CUP_SPORTMONKS_STANDINGS_URL),
                },
                {
                    "kind": "player_awards",
                    **_url_config(settings.WORLD_CUP_SPORTMONKS_TOP_SCORERS_URL),
                },
            ],
        },
    }
    scheduled_import = {
        "enabled": settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED,
        "mode": settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE,
        "replace": settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_REPLACE,
        "hour_utc": settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_HOUR_UTC,
        "minute_utc": settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_MINUTE_UTC,
    }
    runs = {
        "world_cup_source_bundle_import": loop_run_store.last_run(
            "world_cup_source_bundle_import"
        ),
        "world_cup_matchday_refresh": loop_run_store.last_run(
            "world_cup_matchday_refresh"
        ),
        "world_cup_api_football_validate": loop_run_store.last_run(
            "world_cup_api_football_validate"
        ),
    }

    return {
        "facts": facts,
        "configured_sources": configured_sources,
        "real_data_readiness": _real_data_readiness(
            facts=facts,
            configured_sources=configured_sources,
            scheduled_import=scheduled_import,
            runs=runs,
        ),
        "scheduled_import": scheduled_import,
        "matchday_refresh": {
            "enabled": settings.WORLD_CUP_MATCHDAY_REFRESH_ENABLED,
            "interval_minutes": settings.WORLD_CUP_MATCHDAY_REFRESH_INTERVAL_MINUTES,
            "window_hours": settings.WORLD_CUP_MATCHDAY_REFRESH_WINDOW_HOURS,
        },
        "runs": runs,
    }


def _real_data_readiness(
    *,
    facts: dict[str, Any],
    configured_sources: dict[str, Any],
    scheduled_import: dict[str, Any],
    runs: dict[str, Any],
) -> dict[str, Any]:
    by_kind = facts.get("by_kind") if isinstance(facts.get("by_kind"), dict) else {}
    qualification_fact_counts = _qualification_fact_counts()
    qualification_fact_count = qualification_fact_counts["trusted"]
    untrusted_qualification_fact_count = qualification_fact_counts["untrusted"]
    match_result_count = int(by_kind.get("match_result") or 0)
    qualification_source_configured = _qualification_source_configured(configured_sources)
    recommended_import = _recommended_qualification_import(configured_sources)
    scheduled_import_enabled = bool(scheduled_import.get("enabled"))
    scheduled_import_mode = _clean(scheduled_import.get("mode")).lower()
    last_import = runs.get("world_cup_source_bundle_import")
    last_import_failed = _current_import_failed(
        last_import, scheduled_import_mode, scheduled_import_enabled
    )
    last_provider_validation = _recommended_provider_validation(
        recommended_import["mode"],
        runs,
    )
    last_provider_validation_failed = (
        isinstance(last_provider_validation, dict)
        and last_provider_validation.get("status") == "failed"
    )
    qualification_source_state = (
        "ready"
        if qualification_source_configured and qualification_fact_count > 0
        else "validation_failed"
        if qualification_source_configured and last_provider_validation_failed
        else "configured_not_imported"
        if qualification_source_configured
        else "not_configured"
    )

    issues: list[str] = []
    if not qualification_source_configured:
        issues.append("qualification_source_not_configured")
    if qualification_fact_count <= 0:
        issues.append("qualification_facts_missing")
        if qualification_source_configured and not last_provider_validation_failed:
            issues.append("qualification_import_required")
    if untrusted_qualification_fact_count > 0:
        issues.append("qualification_facts_untrusted")
    if not scheduled_import_enabled:
        issues.append("scheduled_import_disabled")
    if last_import_failed:
        issues.append("last_import_failed")
    if last_provider_validation_failed and qualification_fact_count <= 0:
        issues.append("recommended_provider_validation_failed")

    return {
        "ok": not _blocking_readiness_issues(issues),
        "qualification_source_configured": qualification_source_configured,
        "qualification_source_state": qualification_source_state,
        "qualification_fact_count": qualification_fact_count,
        "untrusted_qualification_fact_count": untrusted_qualification_fact_count,
        "match_result_count": match_result_count,
        "recommended_qualification_import_mode": recommended_import["mode"],
        "recommended_qualification_import_label": recommended_import["label"],
        "scheduled_import_enabled": scheduled_import_enabled,
        "last_import_failed": last_import_failed,
        "recommended_provider_last_validation_status": (
            str(last_provider_validation.get("status"))
            if isinstance(last_provider_validation, dict) and last_provider_validation.get("status")
            else ""
        ),
        "recommended_provider_last_validation_error": (
            str(last_provider_validation.get("error") or "")
            if isinstance(last_provider_validation, dict)
            else ""
        ),
        "issues": issues,
        "issue_details": [_readiness_issue_detail(issue) for issue in issues],
    }


def _blocking_readiness_issues(issues: list[str]) -> list[str]:
    return [
        issue
        for issue in issues
        if _READINESS_ISSUE_DETAILS.get(issue, {}).get("severity") != "warn"
    ]


def _current_import_failed(
    last_import: Any,
    scheduled_import_mode: str,
    scheduled_import_enabled: bool,
) -> bool:
    if not isinstance(last_import, dict) or last_import.get("status") != "failed":
        return False

    result = last_import.get("result")
    result_mode = ""
    if isinstance(result, dict):
        result_mode = _clean(result.get("mode")).lower()
    if result_mode and result_mode != scheduled_import_mode:
        return False

    error = _clean(last_import.get("error"))
    # A "WORLD_CUP_SOURCE_BUNDLE_URL is not configured" failure is only current
    # when the import loop is actually running in url mode. Otherwise it is stale
    # noise (the loop is disabled, or the mode moved to a real provider).
    url_bundle_active = scheduled_import_enabled and scheduled_import_mode == "url"
    if not result_mode and "WORLD_CUP_SOURCE_BUNDLE_URL" in error and not url_bundle_active:
        return False

    return True


def _readiness_issue_detail(issue: str) -> dict[str, str]:
    detail = _READINESS_ISSUE_DETAILS.get(issue, {})
    return {
        "code": issue,
        "severity": str(detail.get("severity") or "warn"),
        "message": str(detail.get("message") or issue),
        "action": str(detail.get("action") or ""),
    }


def _qualification_fact_counts() -> dict[str, int]:
    from app.services.sports_fact_service import load_sports_facts

    trusted = 0
    untrusted = 0
    for fact in load_sports_facts(
        tournament=WORLD_CUP_TOURNAMENT,
        kind="qualification",
    ):
        if _trusted_qualification_fact(fact):
            trusted += 1
        else:
            untrusted += 1
    return {"trusted": trusted, "untrusted": untrusted}


def _trusted_qualification_fact(fact: dict[str, Any]) -> bool:
    source_url = _clean(fact.get("source_url") or fact.get("url"))
    observed_at = _clean(fact.get("observed_at"))
    return bool(
        observed_at
        and source_url.lower().startswith(("https://", "http://"))
    )


def _qualification_source_configured(configured_sources: dict[str, Any]) -> bool:
    feeds = configured_sources.get("feeds")
    if _has_configured_feed_kind(feeds, "standings"):
        return True
    football_data = configured_sources.get("football_data")
    if isinstance(football_data, dict) and football_data.get("configured"):
        return True
    api_football = configured_sources.get("api_football")
    if isinstance(api_football, dict) and api_football.get("configured"):
        return True
    sportmonks = configured_sources.get("sportmonks")
    if isinstance(sportmonks, dict):
        if _has_configured_feed_kind(sportmonks.get("feeds"), "standings"):
            return True
    return False


def _recommended_qualification_import(configured_sources: dict[str, Any]) -> dict[str, str]:
    feeds = configured_sources.get("feeds")
    if _has_configured_feed_kind(feeds, "standings"):
        return {"mode": "feeds", "label": "Configured feeds"}
    football_data = configured_sources.get("football_data")
    if isinstance(football_data, dict) and football_data.get("configured"):
        return {"mode": "football_data", "label": "Football-Data.org"}
    api_football = configured_sources.get("api_football")
    if isinstance(api_football, dict) and api_football.get("configured"):
        return {"mode": "api_football", "label": "API-Football"}
    sportmonks = configured_sources.get("sportmonks")
    if isinstance(sportmonks, dict):
        if _has_configured_feed_kind(sportmonks.get("feeds"), "standings"):
            return {"mode": "sportmonks", "label": "SportMonks"}
    return {"mode": "", "label": ""}


def _recommended_provider_validation(
    recommended_mode: str,
    runs: dict[str, Any],
) -> dict[str, Any] | None:
    if recommended_mode != "api_football":
        return None
    run = runs.get("world_cup_api_football_validate")
    if not isinstance(run, dict):
        return None
    result = run.get("result")
    if run.get("job_name") == "world_cup_api_football_validate":
        return run
    if isinstance(result, dict) and result.get("provider") == "api_football":
        return run
    return None


def _has_configured_feed_kind(feeds: Any, kind: str) -> bool:
    if not isinstance(feeds, list):
        return False
    return any(
        isinstance(feed, dict)
        and feed.get("kind") == kind
        and bool(feed.get("configured"))
        for feed in feeds
    )


def _file_config(path: str) -> dict[str, Any]:
    value = _clean(path)
    absolute_path = os.path.abspath(value) if value else ""
    return {
        "configured": bool(value),
        "path": absolute_path,
        "exists": os.path.exists(absolute_path) if absolute_path else False,
    }


def _url_config(url: str) -> dict[str, Any]:
    value = _clean(url)
    return {
        "configured": bool(value),
        "source_url": _display_url(value) if value else "",
    }


def _display_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())
