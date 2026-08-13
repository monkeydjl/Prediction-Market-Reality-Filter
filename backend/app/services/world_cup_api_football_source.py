"""Fetch API-Football World Cup feeds as a source bundle."""

from __future__ import annotations

import json
import time
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
# (feed kind, API path, default query params). Annotated because the two empty
# param dicts leave the value type unresolvable on their own.
_FEEDS: tuple[tuple[str, str, dict[str, str]], ...] = (
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
    source_fetches: list[dict[str, Any]] = []
    fixture_payload: dict[str, Any] | None = None
    for kind, path, payload_defaults in _FEEDS:
        source_url = _api_football_url(path)
        payload = _fetch_api_football_json(
            source_url,
            source_kind=kind,
            source_fetches=source_fetches,
        )
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

    call_budget = _detail_call_budget_summary(fixture_payload)
    remaining_detail_calls = call_budget["max_detail_calls"]
    fixture_ids = _fixture_ids(fixture_payload or {})

    if settings.WORLD_CUP_API_FOOTBALL_FETCH_EVENTS and fixture_payload:
        required_calls = len(fixture_ids)
        if required_calls > remaining_detail_calls:
            _skip_for_call_budget(
                skipped_sources,
                "match_events",
                _api_football_url("fixtures/events", {"fixture": "0"}),
                required_calls,
                remaining_detail_calls,
            )
            call_budget["detail_calls_skipped"] += required_calls
        else:
            event_source = _fixture_events_source(
                fixture_payload,
                observed_at,
                source_fetches,
            )
            remaining_detail_calls -= required_calls
            call_budget["detail_calls_used"] += required_calls
            if event_source:
                sources.append(event_source)
            else:
                skipped_sources.append({
                    "kind": "match_events",
                    "source_url": _display_url(_api_football_url("fixtures/events", {"fixture": "0"})),
                    "reason": "empty response",
                })

    if settings.WORLD_CUP_API_FOOTBALL_FETCH_LINEUPS and fixture_payload:
        required_calls = len(fixture_ids)
        if required_calls > remaining_detail_calls:
            _skip_for_call_budget(
                skipped_sources,
                "lineups",
                _api_football_url("fixtures/lineups", {"fixture": "0"}),
                required_calls,
                remaining_detail_calls,
            )
            call_budget["detail_calls_skipped"] += required_calls
        else:
            lineups_source = _fixture_lineups_source(
                fixture_payload,
                observed_at,
                source_fetches,
            )
            remaining_detail_calls -= required_calls
            call_budget["detail_calls_used"] += required_calls
            if lineups_source:
                sources.append(lineups_source)
            else:
                skipped_sources.append({
                    "kind": "lineups",
                    "source_url": _display_url(_api_football_url("fixtures/lineups", {"fixture": "0"})),
                    "reason": "empty response",
                })

    if settings.WORLD_CUP_API_FOOTBALL_FETCH_STATISTICS and fixture_payload:
        required_calls = len(fixture_ids) * 2
        if required_calls > remaining_detail_calls:
            _skip_for_call_budget(
                skipped_sources,
                "statistics",
                _api_football_url("fixtures/statistics", {"fixture": "0"}),
                required_calls,
                remaining_detail_calls,
            )
            call_budget["detail_calls_skipped"] += required_calls
        else:
            statistics_source = _fixture_statistics_source(
                fixture_payload,
                observed_at,
                source_fetches,
            )
            remaining_detail_calls -= required_calls
            call_budget["detail_calls_used"] += required_calls
            if statistics_source:
                sources.append(statistics_source)
            else:
                skipped_sources.append({
                    "kind": "statistics",
                    "source_url": _display_url(_api_football_url("fixtures/statistics", {"fixture": "0"})),
                    "reason": "empty response",
                })

    call_budget["detail_calls_remaining"] = remaining_detail_calls

    if not sources:
        raise ValueError("API-Football returned no usable World Cup source feeds")
    return {
        "sources": sources,
        "skipped_sources": skipped_sources,
        "source_fetches": source_fetches,
        "call_budget": call_budget,
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


def test_world_cup_api_football_connection() -> dict[str, Any]:
    """Test connectivity to API-Football by hitting the /status endpoint."""

    api_key = _clean(settings.WORLD_CUP_API_FOOTBALL_API_KEY)
    if not api_key:
        return {"ok": False, "error": "API key not configured"}

    base_url = _clean(settings.WORLD_CUP_API_FOOTBALL_BASE_URL).rstrip("/")
    if not base_url:
        return {"ok": False, "error": "API-Football base URL not configured"}

    url = f"{base_url}/status"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "x-apisports-key": api_key,
        },
    )
    try:
        with urlopen(request, timeout=5) as response:
            body = response.read(64 * 1024)
    except HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.code}"}
    except (TimeoutError, URLError) as exc:
        return {"ok": False, "error": f"Connection failed: {exc}"}

    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "error": "Invalid JSON response"}

    subscription = data.get("response", {}).get("subscription", {})
    requests_info = data.get("response", {}).get("requests", {})

    return {
        "ok": True,
        "subscription": {
            "plan": subscription.get("plan", ""),
            "active": subscription.get("active", False),
            "end": subscription.get("end"),
        },
        "requests_today": requests_info.get("current", 0),
        "requests_limit": requests_info.get("limit_day", 0),
        "error": None,
    }


def validate_world_cup_api_football_pipeline() -> dict[str, Any]:
    """Run a full pipeline diagnostic: connection + sample fetch + fact coverage."""
    from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts

    result: dict[str, Any] = {"steps": []}

    # Step 1: connection test
    conn = test_world_cup_api_football_connection()
    result["steps"].append({"name": "connection", "ok": conn["ok"], "detail": conn})
    if not conn["ok"]:
        result["ok"] = False
        result["error"] = conn.get("error", "Connection failed")
        return result

    # Step 2: sample fixture fetch (counts against quota — 1 call)
    api_key = _clean(settings.WORLD_CUP_API_FOOTBALL_API_KEY)
    base_url = _clean(settings.WORLD_CUP_API_FOOTBALL_BASE_URL).rstrip("/")
    league_id = _clean(settings.WORLD_CUP_API_FOOTBALL_LEAGUE_ID)
    season = _clean(settings.WORLD_CUP_API_FOOTBALL_SEASON)
    fixture_url = f"{base_url}/fixtures?league={league_id}&season={season}"
    request = Request(
        fixture_url,
        headers={"Accept": "application/json", "x-apisports-key": api_key},
    )
    try:
        with urlopen(request, timeout=10) as response:
            body = response.read(2 * 1024 * 1024)
        fixture_data = json.loads(body.decode("utf-8"))
        api_fixtures = fixture_data.get("response", [])
        api_fixture_ids = set()
        for row in api_fixtures:
            if isinstance(row, dict):
                fixture = row.get("fixture", {})
                fid = str(fixture.get("id", "")) if isinstance(fixture, dict) else ""
                if fid:
                    api_fixture_ids.add(fid)
        fixture_count = len(api_fixtures)
        fixture_ok = fixture_count > 0
        fixture_error = (
            ""
            if fixture_ok
            else (
                f"API-Football returned 0 fixtures for league={league_id} "
                f"season={season}; check provider coverage/config before import."
            )
        )
        result["steps"].append({
            "name": "fixture_fetch",
            "ok": fixture_ok,
            "fixture_count": fixture_count,
            "fixture_ids_sample": sorted(api_fixture_ids)[:10],
            **({"error": fixture_error} if fixture_error else {}),
        })
        if not fixture_ok:
            result["ok"] = False
            result["error"] = fixture_error
            return result
    except Exception as exc:
        result["steps"].append({
            "name": "fixture_fetch",
            "ok": False,
            "error": str(exc),
        })
        result["ok"] = False
        result["error"] = f"Fixture fetch failed: {exc}"
        return result

    # Step 3: compare with stored facts
    stored_facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT, kind="match_result")
    stored_match_ids = {f.get("match_id") for f in stored_facts if f.get("match_id")}
    covered = api_fixture_ids & stored_match_ids
    missing = api_fixture_ids - stored_match_ids
    extra = stored_match_ids - api_fixture_ids

    coverage = {
        "api_fixture_count": len(api_fixture_ids),
        "stored_fact_count": len(stored_facts),
        "covered": len(covered),
        "missing_from_store": len(missing),
        "missing_ids_sample": sorted(missing)[:10],
        "extra_in_store": len(extra),
    }
    coverage_ok = len(missing) == 0 or len(stored_facts) > 0
    result["steps"].append({"name": "fact_coverage", "ok": coverage_ok, "detail": coverage})

    result["ok"] = True
    result["coverage"] = coverage
    result["summary"] = (
        f"Connected. {len(api_fixture_ids)} fixtures from API, "
        f"{len(stored_facts)} match facts stored, "
        f"{len(missing)} fixtures not yet imported."
    )
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


def _fetch_api_football_json(
    source_url: str,
    *,
    source_kind: str,
    source_fetches: list[dict[str, Any]],
) -> dict[str, Any]:
    started = time.perf_counter()
    api_key = _clean(settings.WORLD_CUP_API_FOOTBALL_API_KEY)
    if not api_key:
        _record_fetch(
            source_fetches,
            source_kind,
            source_url,
            started,
            "failed",
            "WORLD_CUP_API_FOOTBALL_API_KEY is not configured",
        )
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
        _record_fetch(
            source_fetches,
            source_kind,
            source_url,
            started,
            "failed",
            f"HTTP {exc.code}",
        )
        raise ValueError(f"API-Football returned HTTP {exc.code}") from exc
    except (TimeoutError, URLError) as exc:
        _record_fetch(
            source_fetches,
            source_kind,
            source_url,
            started,
            "failed",
            "fetch failed",
        )
        raise ValueError("API-Football fetch failed") from exc

    if len(body) > settings.WORLD_CUP_SOURCE_BUNDLE_MAX_BYTES:
        _record_fetch(
            source_fetches,
            source_kind,
            source_url,
            started,
            "failed",
            "response too large",
        )
        raise ValueError("API-Football response too large")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _record_fetch(
            source_fetches,
            source_kind,
            source_url,
            started,
            "failed",
            "invalid JSON",
        )
        raise ValueError("API-Football did not return valid JSON") from exc
    if not isinstance(payload, dict):
        _record_fetch(
            source_fetches,
            source_kind,
            source_url,
            started,
            "failed",
            "response must be a JSON object",
        )
        raise ValueError("API-Football response must be a JSON object")
    errors = payload.get("errors")
    if errors:
        error_summary = _provider_error_summary(errors)
        _record_fetch(
            source_fetches,
            source_kind,
            source_url,
            started,
            "failed",
            error_summary,
        )
        raise ValueError(f"API-Football {error_summary}")
    _record_fetch(source_fetches, source_kind, source_url, started, "success")
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
    source_fetches: list[dict[str, Any]],
) -> dict[str, Any] | None:
    events: list[dict[str, Any]] = []
    source_url = ""
    for fixture_id in _fixture_ids(fixture_payload):
        event_url = _api_football_url("fixtures/events", {"fixture": fixture_id})
        source_url = source_url or event_url
        payload = _fetch_api_football_json(
            event_url,
            source_kind="match_events",
            source_fetches=source_fetches,
        )
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


def _fixture_lineups_source(
    fixture_payload: dict[str, Any],
    observed_at: str,
    source_fetches: list[dict[str, Any]],
) -> dict[str, Any] | None:
    lineups: list[dict[str, Any]] = []
    source_url = ""
    for fixture_id in _fixture_ids(fixture_payload):
        lineup_url = _api_football_url("fixtures/lineups", {"fixture": fixture_id})
        source_url = source_url or lineup_url
        payload = _fetch_api_football_json(
            lineup_url,
            source_kind="lineups",
            source_fetches=source_fetches,
        )
        response = payload.get("response")
        if not isinstance(response, list):
            continue
        for row in response:
            if not isinstance(row, dict):
                continue
            enriched = dict(row)
            if "fixture" not in enriched and "fixture_id" not in enriched:
                enriched["fixture"] = {"id": fixture_id}
            lineups.append(enriched)
    if not lineups:
        return None
    return _bundle_entry(
        "lineups",
        source_url or _api_football_url("fixtures/lineups", {"fixture": "0"}),
        {"response": lineups},
        {},
        observed_at,
    )


def _fixture_statistics_source(
    fixture_payload: dict[str, Any],
    observed_at: str,
    source_fetches: list[dict[str, Any]],
) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    source_url = ""
    for fixture_id in _fixture_ids(fixture_payload):
        statistics_url = _api_football_url("fixtures/statistics", {"fixture": fixture_id})
        source_url = source_url or statistics_url
        statistics_payload = _fetch_api_football_json(
            statistics_url,
            source_kind="team_statistics",
            source_fetches=source_fetches,
        )
        _append_fixture_rows(rows, statistics_payload, fixture_id)

        players_url = _api_football_url("fixtures/players", {"fixture": fixture_id})
        source_url = source_url or players_url
        players_payload = _fetch_api_football_json(
            players_url,
            source_kind="player_statistics",
            source_fetches=source_fetches,
        )
        _append_fixture_rows(rows, players_payload, fixture_id)
    if not rows:
        return None
    return _bundle_entry(
        "statistics",
        source_url or _api_football_url("fixtures/statistics", {"fixture": "0"}),
        {"response": rows},
        {},
        observed_at,
    )


def _append_fixture_rows(
    rows: list[dict[str, Any]],
    payload: dict[str, Any],
    fixture_id: str,
) -> None:
    response = payload.get("response")
    if not isinstance(response, list):
        return
    for row in response:
        if not isinstance(row, dict):
            continue
        enriched = dict(row)
        if "fixture" not in enriched and "fixture_id" not in enriched:
            enriched["fixture"] = {"id": fixture_id}
        rows.append(enriched)


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
    source_fetches = payload.get("source_fetches") if isinstance(payload, dict) else []
    call_budget = payload.get("call_budget") if isinstance(payload, dict) else {}
    return {
        "provider": _PROVIDER,
        "source_feeds": [
            {
                "kind": _clean(entry.get("kind")),
                "source": _clean(entry.get("source")),
                "source_url": _clean(entry.get("source_url")),
                "observed_at": _clean(entry.get("observed_at")),
            }
            # Same list guard the fields below already carry: a payload without
            # a "sources" key would otherwise iterate None.
            for entry in (sources if isinstance(sources, list) else [])
            if isinstance(entry, dict)
        ],
        "skipped_source_count": len(skipped) if isinstance(skipped, list) else 0,
        "skipped_sources": skipped if isinstance(skipped, list) else [],
        "source_fetch_count": len(source_fetches) if isinstance(source_fetches, list) else 0,
        "source_fetches": source_fetches if isinstance(source_fetches, list) else [],
        "call_budget": call_budget if isinstance(call_budget, dict) else {},
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


def _provider_error_summary(errors: Any) -> str:
    if isinstance(errors, dict):
        keys = [_clean(key) for key in errors.keys()]
        visible = [key for key in keys if key][:5]
        return "provider returned errors" + (f": {', '.join(visible)}" if visible else "")
    if isinstance(errors, list):
        return f"provider returned {len(errors)} error(s)"
    if isinstance(errors, str):
        return "provider returned errors"
    return "provider returned errors"


def _detail_call_budget_summary(fixture_payload: dict[str, Any] | None) -> dict[str, Any]:
    fixture_count = len(_fixture_ids(fixture_payload or {}))
    max_detail_calls = max(0, int(settings.WORLD_CUP_API_FOOTBALL_MAX_DETAIL_CALLS))
    enabled_detail_feeds = []
    if settings.WORLD_CUP_API_FOOTBALL_FETCH_EVENTS:
        enabled_detail_feeds.append("match_events")
    if settings.WORLD_CUP_API_FOOTBALL_FETCH_LINEUPS:
        enabled_detail_feeds.append("lineups")
    if settings.WORLD_CUP_API_FOOTBALL_FETCH_STATISTICS:
        enabled_detail_feeds.append("statistics")
    return {
        "fixture_count": fixture_count,
        "max_detail_calls": max_detail_calls,
        "enabled_detail_feeds": enabled_detail_feeds,
        "detail_calls_used": 0,
        "detail_calls_skipped": 0,
        "detail_calls_remaining": max_detail_calls,
    }


def _skip_for_call_budget(
    skipped_sources: list[dict[str, Any]],
    kind: str,
    source_url: str,
    required_calls: int,
    remaining_calls: int,
) -> None:
    skipped_sources.append({
        "kind": kind,
        "source_url": _display_url(source_url),
        "reason": "call budget exceeded",
        "required_calls": required_calls,
        "remaining_calls": remaining_calls,
    })


def _record_fetch(
    source_fetches: list[dict[str, Any]],
    kind: str,
    source_url: str,
    started: float,
    status: str,
    error: str = "",
) -> None:
    item = {
        "kind": kind,
        "source_url": _display_url(source_url),
        "status": status,
        "duration_ms": _duration_ms(started),
    }
    if error:
        item["error"] = error
    source_fetches.append(item)


def _duration_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
