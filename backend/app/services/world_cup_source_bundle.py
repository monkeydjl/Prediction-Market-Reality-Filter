"""Combine multiple World Cup data-source payloads into one fact batch."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.core.config import settings
from app.services.sports_fact_service import (
    WORLD_CUP_TOURNAMENT,
    import_sports_facts,
)
from app.services.world_cup_data_source_service import (
    validate_world_cup_data_source_metadata,
    world_cup_data_to_facts,
)
from app.services.world_cup_match_source import world_cup_match_source_to_data
from app.services.world_cup_match_events_source import (
    world_cup_match_events_source_to_data,
)
from app.services.world_cup_lineups_source import world_cup_lineups_source_to_data
from app.services.world_cup_official_csv_source import (
    world_cup_official_csv_source_to_data,
)
from app.services.world_cup_player_awards_source import (
    world_cup_player_awards_source_to_data,
)
from app.services.world_cup_player_status_source import (
    world_cup_player_status_source_to_data,
)
from app.services.world_cup_standings_source import world_cup_standings_source_to_data
from app.utils.file_store import read_json_strict

_SOURCE_FEED_URL_SETTINGS = (
    ("matches", "WORLD_CUP_MATCH_SOURCE_URL"),
    ("match_events", "WORLD_CUP_MATCH_EVENTS_SOURCE_URL"),
    ("lineups", "WORLD_CUP_LINEUPS_SOURCE_URL"),
    ("standings", "WORLD_CUP_STANDINGS_SOURCE_URL"),
    ("player_awards", "WORLD_CUP_PLAYER_AWARDS_SOURCE_URL"),
    ("player_status", "WORLD_CUP_PLAYER_STATUS_SOURCE_URL"),
)


def preview_world_cup_source_bundle(payload: Any) -> dict[str, Any]:
    """Preview facts produced by a bundle of World Cup data sources."""

    sources, facts = _convert_bundle(payload)
    return {
        "source_count": len(sources),
        "converted_fact_count": len(facts),
        "sources": sources,
        "facts": facts,
    }


def import_world_cup_source_bundle(
    payload: Any,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Import facts produced by a bundle of World Cup data sources."""

    sources, facts = _convert_bundle(payload)
    result = import_sports_facts(
        {"facts": facts},
        replace=replace,
        default_tournament=WORLD_CUP_TOURNAMENT,
    )
    result["source_count"] = len(sources)
    result["converted_fact_count"] = len(facts)
    result["sources"] = sources
    return result


def fetch_world_cup_source_bundle_url(url: str | None = None) -> dict[str, Any]:
    """Fetch the configured remote multi-source World Cup bundle JSON."""

    source_url = _configured_source_bundle_url(url)
    payload = _fetch_json_url(source_url, label="World Cup source bundle URL")
    if not isinstance(payload, dict):
        raise ValueError("World Cup source bundle URL must return a JSON object")
    return payload


def preview_world_cup_source_bundle_url(url: str | None = None) -> dict[str, Any]:
    """Preview facts from the configured remote multi-source bundle URL."""

    source_url = _configured_source_bundle_url(url)
    payload = fetch_world_cup_source_bundle_url(source_url)
    metadata = validate_world_cup_source_bundle_metadata(payload)
    result = preview_world_cup_source_bundle(payload)
    result["source_url"] = _display_url(source_url)
    result["source_metadata"] = metadata
    return result


def import_world_cup_source_bundle_url(
    *,
    replace: bool = False,
    url: str | None = None,
) -> dict[str, Any]:
    """Import facts from the configured remote multi-source bundle URL."""

    source_url = _configured_source_bundle_url(url)
    payload = fetch_world_cup_source_bundle_url(source_url)
    metadata = validate_world_cup_source_bundle_metadata(payload)
    result = import_world_cup_source_bundle(payload, replace=replace)
    result["source_url"] = _display_url(source_url)
    result["source_metadata"] = metadata
    return result


def build_world_cup_source_bundle_from_feeds(
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch configured raw source feeds and assemble a bundle payload."""

    entries: list[dict[str, Any]] = []
    observed_at = _utc_timestamp(now)
    for kind, setting_name, source_url in _configured_source_feed_urls():
        payload = _fetch_json_url(
            source_url,
            label=f"{setting_name} World Cup source feed",
        )
        if not isinstance(payload, (dict, list)):
            raise ValueError(f"{setting_name} must return a JSON object or array")
        entries.append(_source_feed_entry(kind, source_url, payload, observed_at))
    if not entries:
        raise ValueError("No World Cup source feed URLs are configured")
    return {"sources": entries}


def preview_world_cup_source_bundle_feeds(
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Preview facts from configured raw World Cup source feed URLs."""

    payload = build_world_cup_source_bundle_from_feeds(now=now)
    metadata = validate_world_cup_source_bundle_metadata(payload)
    result = preview_world_cup_source_bundle(payload)
    result["source_feeds"] = _source_feed_summary(payload)
    result["source_metadata"] = metadata
    return result


def import_world_cup_source_bundle_feeds(
    *,
    replace: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Import facts from configured raw World Cup source feed URLs."""

    payload = build_world_cup_source_bundle_from_feeds(now=now)
    metadata = validate_world_cup_source_bundle_metadata(payload)
    result = import_world_cup_source_bundle(payload, replace=replace)
    result["source_feeds"] = _source_feed_summary(payload)
    result["source_metadata"] = metadata
    return result


def load_world_cup_source_bundle_file(path: str | None = None) -> dict[str, Any]:
    """Load the configured multi-source World Cup bundle file."""

    source_path = os.path.abspath(path or settings.WORLD_CUP_SOURCE_BUNDLE_FILE)
    if not os.path.exists(source_path):
        raise FileNotFoundError(source_path)
    payload = read_json_strict(source_path, {})
    if not isinstance(payload, dict):
        raise ValueError("World Cup source bundle file must contain a JSON object")
    return payload


def validate_world_cup_source_bundle_metadata(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_hours: float | None = None,
) -> list[dict[str, Any]]:
    """Validate freshness metadata for each source in a configured bundle."""

    metadata: list[dict[str, Any]] = []
    entries = _source_entries(payload)
    for index, entry in enumerate(entries):
        kind = _source_kind(entry, index)
        source_payload = _source_payload(entry, index)
        source_metadata = _source_metadata(entry, source_payload)
        try:
            validated = validate_world_cup_data_source_metadata(
                source_metadata,
                now=now,
                max_age_hours=max_age_hours,
            )
        except ValueError as exc:
            raise ValueError(f"sources[{index}] {kind}: {exc}") from exc
        metadata.append({
            "index": index,
            "kind": kind,
            **validated,
        })
    return metadata


def preview_world_cup_source_bundle_file(path: str | None = None) -> dict[str, Any]:
    """Preview facts from the configured multi-source bundle file."""

    source_path = os.path.abspath(path or settings.WORLD_CUP_SOURCE_BUNDLE_FILE)
    payload = load_world_cup_source_bundle_file(source_path)
    metadata = validate_world_cup_source_bundle_metadata(payload)
    result = preview_world_cup_source_bundle(payload)
    result["source_file"] = source_path
    result["source_metadata"] = metadata
    return result


def import_world_cup_source_bundle_file(
    *,
    replace: bool = False,
    path: str | None = None,
) -> dict[str, Any]:
    """Import facts from the configured multi-source bundle file."""

    source_path = os.path.abspath(path or settings.WORLD_CUP_SOURCE_BUNDLE_FILE)
    payload = load_world_cup_source_bundle_file(source_path)
    metadata = validate_world_cup_source_bundle_metadata(payload)
    result = import_world_cup_source_bundle(payload, replace=replace)
    result["source_file"] = source_path
    result["source_metadata"] = metadata
    return result


def _convert_bundle(payload: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries = _source_entries(payload)
    sources: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        kind = _source_kind(entry, index)
        source_payload = _source_payload(entry, index)
        source_payload = _with_entry_metadata(source_payload, entry, kind)
        try:
            normalized_data = _source_to_data(kind, source_payload)
            source_facts = world_cup_data_to_facts(normalized_data)
        except ValueError as exc:
            raise ValueError(f"sources[{index}] {kind}: {exc}") from exc
        sources.append({
            "index": index,
            "kind": kind,
            "converted_fact_count": len(source_facts),
            "normalized_data": normalized_data,
        })
        facts.extend(source_facts)
    return sources, facts


def _source_entries(payload: Any) -> list[Any]:
    if not isinstance(payload, dict):
        raise ValueError("source bundle payload must be an object")
    sources = payload.get("sources")
    if not isinstance(sources, list):
        raise ValueError("source bundle must include a sources list")
    if not sources:
        raise ValueError("source bundle must include at least one source")
    return sources


def _source_kind(entry: Any, index: int) -> str:
    if not isinstance(entry, dict):
        raise ValueError(f"sources[{index}] must be an object")
    raw = _clean(entry.get("kind") or entry.get("type") or entry.get("source_type"))
    kind = raw.lower().replace("-", "_")
    aliases = {
        "normalized": "data",
        "world_cup_data": "data",
        "match": "matches",
        "fixtures": "matches",
        "fixture": "matches",
        "events": "match_events",
        "match_event": "match_events",
        "fixture_events": "match_events",
        "cards": "match_events",
        "card_events": "match_events",
        "discipline": "match_events",
        "lineup": "lineups",
        "lineups": "lineups",
        "starting_xi": "lineups",
        "startxi": "lineups",
        "csv": "official_csv",
        "official_csv": "official_csv",
        "official_csv_v1": "official_csv",
        "strict_csv": "official_csv",
        "standings": "standings",
        "qualification": "standings",
        "qualifications": "standings",
        "awards": "player_awards",
        "player_award": "player_awards",
        "top_scorers": "player_awards",
        "topscorers": "player_awards",
        "statuses": "player_status",
        "player_statuses": "player_status",
        "injuries": "player_status",
        "availability": "player_status",
        "suspensions": "player_status",
    }
    kind = aliases.get(kind, kind)
    if kind not in {
        "data",
        "matches",
        "match_events",
        "lineups",
        "official_csv",
        "standings",
        "player_awards",
        "player_status",
    }:
        raise ValueError(f"sources[{index}] unsupported source kind '{raw}'")
    return kind


def _source_payload(entry: dict[str, Any], index: int) -> Any:
    if "payload" not in entry:
        raise ValueError(f"sources[{index}] missing payload")
    return entry["payload"]


def _source_to_data(kind: str, payload: Any) -> dict[str, Any]:
    if kind == "data":
        if not isinstance(payload, dict):
            raise ValueError("normalized data payload must be an object")
        return payload
    if kind == "matches":
        return world_cup_match_source_to_data(payload)
    if kind == "match_events":
        return world_cup_match_events_source_to_data(payload)
    if kind == "lineups":
        return world_cup_lineups_source_to_data(payload)
    if kind == "official_csv":
        return world_cup_official_csv_source_to_data(payload)
    if kind == "standings":
        return world_cup_standings_source_to_data(payload)
    if kind == "player_awards":
        return world_cup_player_awards_source_to_data(payload)
    return world_cup_player_status_source_to_data(payload)


def _configured_source_bundle_url(url: str | None = None) -> str:
    source_url = _clean(url or settings.WORLD_CUP_SOURCE_BUNDLE_URL)
    if not source_url:
        raise ValueError("WORLD_CUP_SOURCE_BUNDLE_URL is not configured")
    return source_url


def _configured_source_feed_urls() -> list[tuple[str, str, str]]:
    urls: list[tuple[str, str, str]] = []
    for kind, setting_name in _SOURCE_FEED_URL_SETTINGS:
        source_url = _clean(getattr(settings, setting_name, ""))
        if source_url:
            urls.append((kind, setting_name, source_url))
    return urls


def _fetch_json_url(source_url: str, *, label: str) -> Any:
    request = Request(source_url, headers=_bundle_url_headers())
    try:
        with urlopen(
            request,
            timeout=settings.WORLD_CUP_SOURCE_BUNDLE_TIMEOUT_SECONDS,
        ) as response:
            body = response.read(settings.WORLD_CUP_SOURCE_BUNDLE_MAX_BYTES + 1)
    except HTTPError as exc:
        raise ValueError(f"{label} returned HTTP {exc.code}") from exc
    except (TimeoutError, URLError) as exc:
        raise ValueError(f"{label} fetch failed") from exc

    if len(body) > settings.WORLD_CUP_SOURCE_BUNDLE_MAX_BYTES:
        raise ValueError(f"{label} response too large")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} did not return valid JSON") from exc


def _bundle_url_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    user_agent = _clean(settings.WORLD_CUP_SOURCE_BUNDLE_USER_AGENT)
    if user_agent:
        headers["User-Agent"] = user_agent
    auth_header = _clean(settings.WORLD_CUP_SOURCE_BUNDLE_AUTH_HEADER)
    auth_value = _clean(settings.WORLD_CUP_SOURCE_BUNDLE_AUTH_VALUE)
    if auth_header and auth_value:
        headers[auth_header] = auth_value
    return headers


def _display_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _source_feed_entry(
    kind: str,
    source_url: str,
    payload: dict[str, Any] | list[Any],
    observed_at: str,
) -> dict[str, Any]:
    source = ""
    payload_observed_at = ""
    feed_payload: dict[str, Any] | list[Any] = payload
    display_url = _display_url(source_url)
    if isinstance(payload, dict):
        source = _clean(payload.get("source") or payload.get("provider"))
        payload_observed_at = _clean(payload.get("observed_at"))
        feed_payload = dict(payload)
        feed_payload["source_url"] = display_url

    return {
        "kind": kind,
        "source": source or _source_name_from_url(source_url),
        "source_url": display_url,
        "observed_at": payload_observed_at or observed_at,
        "payload": feed_payload,
    }


def _source_feed_summary(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "kind": _clean(entry.get("kind")),
            "source": _clean(entry.get("source")),
            "source_url": _clean(entry.get("source_url")),
            "observed_at": _clean(entry.get("observed_at")),
        }
        for entry in _source_entries(payload)
    ]


def _source_name_from_url(source_url: str) -> str:
    host = _clean(urlsplit(source_url).netloc)
    return host or "world_cup_configured_source"


def _utc_timestamp(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _with_entry_metadata(payload: Any, entry: dict[str, Any], kind: str) -> Any:
    if isinstance(payload, list) and kind != "data":
        payload = {"response": payload}
    if not isinstance(payload, dict):
        return payload
    merged = dict(payload)
    for field in ("tournament", "source", "source_url", "observed_at"):
        value = _clean(entry.get(field))
        if not value:
            continue
        if _has_metadata(merged, field):
            continue
        merged[field] = value
    return merged


def _source_metadata(entry: dict[str, Any], payload: Any) -> dict[str, Any]:
    payload_data = payload if isinstance(payload, dict) else {}
    return {
        "source": (
            _clean(entry.get("source"))
            or _clean(payload_data.get("source"))
            or _clean(payload_data.get("provider"))
        ),
        "source_url": (
            _clean(entry.get("source_url") or entry.get("url"))
            or _clean(payload_data.get("source_url") or payload_data.get("url"))
        ),
        "observed_at": _clean(entry.get("observed_at") or payload_data.get("observed_at")),
    }


def _has_metadata(payload: dict[str, Any], field: str) -> bool:
    if field == "source":
        return bool(_clean(payload.get("source") or payload.get("provider")))
    if field == "source_url":
        return bool(_clean(payload.get("source_url") or payload.get("url")))
    return bool(_clean(payload.get(field)))


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())
