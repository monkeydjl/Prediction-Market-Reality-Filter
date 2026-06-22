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
)


def world_cup_data_source_status() -> dict[str, Any]:
    """Return sanitized config and last-run status for World Cup data sources."""

    return {
        "facts": sports_fact_status(tournament=WORLD_CUP_TOURNAMENT),
        "configured_sources": {
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
            },
        },
        "scheduled_import": {
            "enabled": settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED,
            "mode": settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE,
            "replace": settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_REPLACE,
            "hour_utc": settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_HOUR_UTC,
            "minute_utc": settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_MINUTE_UTC,
        },
        "runs": {
            "world_cup_source_bundle_import": loop_run_store.last_run(
                "world_cup_source_bundle_import"
            ),
        },
    }


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
