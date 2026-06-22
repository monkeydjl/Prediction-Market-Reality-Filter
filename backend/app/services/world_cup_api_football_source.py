"""Fetch API-Football World Cup feeds as a source bundle."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.core.config import settings
from app.services.world_cup_source_bundle import (
    import_world_cup_source_bundle,
    preview_world_cup_source_bundle,
    validate_world_cup_source_bundle_metadata,
)

_PROVIDER = "api_football"
_FEEDS = (
    ("matches", "fixtures", {}),
    ("standings", "standings", {}),
    ("player_awards", "players/topscorers", {"award": "golden_boot"}),
    ("player_status", "injuries", {"type": "injury"}),
)


def build_world_cup_api_football_bundle(
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch configured API-Football feeds and assemble a source bundle."""

    observed_at = _utc_timestamp(now)
    sources: list[dict[str, Any]] = []
    skipped_sources: list[dict[str, Any]] = []
    fixture_payload: dict[str, Any] | None = None
    for kind, path, payload_defaults in _FEEDS:
        source_url = _api_football_url(path)
        payload = _fetch_api_football_json(source_url)
        if _is_empty_response(payload):
            skipped_sources.append({
                "kind": kind,
                "source_url": _display_url(source_url),
                "reason": "empty response",
            })
            continue
        if kind == "matches":
            fixture_payload = payload
        sources.append(
            _bundle_entry(kind, source_url, payload, payload_defaults, observed_at)
        )

    if settings.WORLD_CUP_API_FOOTBALL_FETCH_EVENTS and fixture_payload:
        event_source = _fixture_events_source(fixture_payload, observed_at)
        if event_source:
            sources.append(event_source)
        else:
            skipped_sources.append({
                "kind": "match_events",
                "source_url": _display_url(_api_football_url("fixtures/events", {"fixture": "0"})),
                "reason": "empty response",
            })

    if not sources:
        raise ValueError("API-Football returned no usable World Cup source feeds")
    return {
        "sources": sources,
        "skipped_sources": skipped_sources,
    }


def preview_world_cup_api_football_bundle(
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Preview facts from configured API-Football World Cup feeds."""

    payload = build_world_cup_api_football_bundle(now=now)
    metadata = validate_world_cup_source_bundle_metadata(payload)
    result = preview_world_cup_source_bundle(payload)
    result.update(_provider_result_metadata(payload, metadata))
    return result


def import_world_cup_api_football_bundle(
    *,
    replace: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Import facts from configured API-Football World Cup feeds."""

    payload = build_world_cup_api_football_bundle(now=now)
    metadata = validate_world_cup_source_bundle_metadata(payload)
    result = import_world_cup_source_bundle(payload, replace=replace)
    result.update(_provider_result_metadata(payload, metadata))
    return result


def _api_football_url(path: str, params: dict[str, str] | None = None) -> str:
    base_url = _clean(settings.WORLD_CUP_API_FOOTBALL_BASE_URL).rstrip("/")
    if not base_url:
        raise ValueError("WORLD_CUP_API_FOOTBALL_BASE_URL is not configured")
    if params is not None:
        return f"{base_url}/{path}?{urlencode(params)}"

    league_id = _clean(settings.WORLD_CUP_API_FOOTBALL_LEAGUE_ID)
    season = _clean(settings.WORLD_CUP_API_FOOTBALL_SEASON)
    if not league_id:
        raise ValueError("WORLD_CUP_API_FOOTBALL_LEAGUE_ID is not configured")
    if not season:
        raise ValueError("WORLD_CUP_API_FOOTBALL_SEASON is not configured")
    return f"{base_url}/{path}?{urlencode({'league': league_id, 'season': season})}"


def _fetch_api_football_json(source_url: str) -> dict[str, Any]:
    api_key = _clean(settings.WORLD_CUP_API_FOOTBALL_API_KEY)
    if not api_key:
        raise ValueError("WORLD_CUP_API_FOOTBALL_API_KEY is not configured")

    request = Request(
        source_url,
        headers={
            "Accept": "application/json",
            "User-Agent": _clean(settings.WORLD_CUP_SOURCE_BUNDLE_USER_AGENT),
            "x-apisports-key": api_key,
        },
    )
    try:
        with urlopen(
            request,
            timeout=settings.WORLD_CUP_SOURCE_BUNDLE_TIMEOUT_SECONDS,
        ) as response:
            body = response.read(settings.WORLD_CUP_SOURCE_BUNDLE_MAX_BYTES + 1)
    except HTTPError as exc:
        raise ValueError(f"API-Football returned HTTP {exc.code}") from exc
    except (TimeoutError, URLError) as exc:
        raise ValueError("API-Football fetch failed") from exc

    if len(body) > settings.WORLD_CUP_SOURCE_BUNDLE_MAX_BYTES:
        raise ValueError("API-Football response too large")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("API-Football did not return valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("API-Football response must be a JSON object")
    errors = payload.get("errors")
    if errors:
        raise ValueError("API-Football returned errors")
    return payload


def _bundle_entry(
    kind: str,
    source_url: str,
    payload: dict[str, Any],
    payload_defaults: dict[str, Any],
    observed_at: str,
) -> dict[str, Any]:
    display_url = _display_url(source_url)
    enriched_payload = {
        **payload,
        **payload_defaults,
        "provider": _PROVIDER,
        "source": _PROVIDER,
        "source_url": display_url,
        "observed_at": _clean(payload.get("observed_at")) or observed_at,
    }
    return {
        "kind": kind,
        "source": _PROVIDER,
        "source_url": display_url,
        "observed_at": enriched_payload["observed_at"],
        "payload": enriched_payload,
    }


def _fixture_events_source(
    fixture_payload: dict[str, Any],
    observed_at: str,
) -> dict[str, Any] | None:
    events: list[dict[str, Any]] = []
    source_url = ""
    for fixture_id in _fixture_ids(fixture_payload):
        event_url = _api_football_url("fixtures/events", {"fixture": fixture_id})
        source_url = source_url or event_url
        payload = _fetch_api_football_json(event_url)
        response = payload.get("response")
        if not isinstance(response, list):
            continue
        for row in response:
            if not isinstance(row, dict):
                continue
            enriched = dict(row)
            if "fixture" not in enriched and "fixture_id" not in enriched:
                enriched["fixture"] = {"id": fixture_id}
            events.append(enriched)
    if not events:
        return None
    return _bundle_entry(
        "match_events",
        source_url or _api_football_url("fixtures/events", {"fixture": "0"}),
        {"response": events},
        {},
        observed_at,
    )


def _fixture_ids(payload: dict[str, Any]) -> list[str]:
    response = payload.get("response")
    if not isinstance(response, list):
        return []
    fixture_ids: list[str] = []
    seen: set[str] = set()
    for row in response:
        if not isinstance(row, dict):
            continue
        fixture_id = _clean(row.get("fixture_id"))
        fixture = row.get("fixture")
        if not fixture_id and isinstance(fixture, dict):
            fixture_id = _clean(fixture.get("id"))
        if not fixture_id:
            fixture_id = _clean(row.get("id"))
        if not fixture_id or fixture_id in seen:
            continue
        seen.add(fixture_id)
        fixture_ids.append(fixture_id)
    return fixture_ids


def _provider_result_metadata(
    payload: dict[str, Any],
    source_metadata: list[dict[str, Any]],
) -> dict[str, Any]:
    sources = payload.get("sources") if isinstance(payload, dict) else []
    skipped = payload.get("skipped_sources") if isinstance(payload, dict) else []
    return {
        "provider": _PROVIDER,
        "source_feeds": [
            {
                "kind": _clean(entry.get("kind")),
                "source": _clean(entry.get("source")),
                "source_url": _clean(entry.get("source_url")),
                "observed_at": _clean(entry.get("observed_at")),
            }
            for entry in sources
            if isinstance(entry, dict)
        ],
        "skipped_source_count": len(skipped) if isinstance(skipped, list) else 0,
        "skipped_sources": skipped if isinstance(skipped, list) else [],
        "source_metadata": source_metadata,
    }


def _is_empty_response(payload: dict[str, Any]) -> bool:
    response = payload.get("response")
    return isinstance(response, list) and len(response) == 0


def _display_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _utc_timestamp(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())
