"""Fetch Sportmonks-style World Cup feeds as a source bundle."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.core.config import settings
from app.services.world_cup_source_bundle import (
    import_world_cup_source_bundle,
    preview_world_cup_source_bundle,
    validate_world_cup_source_bundle_metadata,
)

_PROVIDER = "sportmonks"
_FEEDS: tuple[tuple[str, str, Callable[[dict[str, Any], str, str], dict[str, Any]]], ...] = (
    ("matches", "WORLD_CUP_SPORTMONKS_FIXTURES_URL", lambda payload, url, observed: _fixtures_data(payload, url, observed)),
    ("standings", "WORLD_CUP_SPORTMONKS_STANDINGS_URL", lambda payload, url, observed: _standings_data(payload, url, observed)),
    ("player_awards", "WORLD_CUP_SPORTMONKS_TOP_SCORERS_URL", lambda payload, url, observed: _top_scorers_data(payload, url, observed)),
    ("lineups", "WORLD_CUP_SPORTMONKS_LINEUPS_URL", lambda payload, url, observed: _lineups_data(payload, url, observed)),
    ("cards", "WORLD_CUP_SPORTMONKS_CARDS_URL", lambda payload, url, observed: _cards_data(payload, url, observed)),
)


def build_world_cup_sportmonks_bundle(
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch configured Sportmonks-style feeds and assemble a source bundle."""

    configured = _configured_feeds()
    if not configured:
        raise ValueError("No Sportmonks World Cup feed URLs are configured")

    observed_at = _utc_timestamp(now)
    sources: list[dict[str, Any]] = []
    skipped_sources: list[dict[str, Any]] = []
    source_fetches: list[dict[str, Any]] = []
    for kind, source_url, converter in configured:
        payload = _fetch_sportmonks_json(
            source_url,
            source_kind=kind,
            source_fetches=source_fetches,
        )
        data = converter(payload, _display_url(source_url), observed_at)
        if not _has_rows(data):
            skipped_sources.append({
                "kind": kind,
                "source_url": _display_url(source_url),
                "reason": "empty response",
            })
            continue
        sources.append(_bundle_entry(kind, source_url, data))

    if not sources:
        raise ValueError("Sportmonks returned no usable World Cup source feeds")
    return {
        "sources": sources,
        "skipped_sources": skipped_sources,
        "source_fetches": source_fetches,
    }


def preview_world_cup_sportmonks_bundle(
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Preview facts from configured Sportmonks-style World Cup feeds."""

    payload = build_world_cup_sportmonks_bundle(now=now)
    metadata = validate_world_cup_source_bundle_metadata(payload)
    result = preview_world_cup_source_bundle(payload)
    result.update(_provider_result_metadata(payload, metadata))
    return result


def import_world_cup_sportmonks_bundle(
    *,
    replace: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Import facts from configured Sportmonks-style World Cup feeds."""

    payload = build_world_cup_sportmonks_bundle(now=now)
    metadata = validate_world_cup_source_bundle_metadata(payload)
    result = import_world_cup_source_bundle(payload, replace=replace)
    result.update(_provider_result_metadata(payload, metadata))
    return result


def test_world_cup_sportmonks_connection() -> dict[str, Any]:
    """Test connectivity to Sportmonks by hitting the first configured feed URL."""

    api_token = _clean(settings.WORLD_CUP_SPORTMONKS_API_TOKEN)
    if not api_token:
        return {"ok": False, "error": "API token not configured"}

    configured = _configured_feeds()
    if not configured:
        return {"ok": False, "error": "No Sportmonks feed URLs configured"}

    kind, source_url, _converter = configured[0]
    request_url = _with_api_token(source_url, api_token)
    request = Request(
        request_url,
        headers={"Accept": "application/json"},
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

    if not isinstance(data, dict):
        return {"ok": False, "error": "Unexpected response format"}
    if data.get("errors"):
        return {"ok": False, "error": f"Provider errors: {data['errors']}"}

    items = data.get("data") or data.get("response") or []
    rate_limit = data.get("rate_limit", {})

    return {
        "ok": True,
        "feed_tested": kind,
        "feed_url": _display_url(source_url),
        "item_count": len(items) if isinstance(items, list) else 0,
        "rate_limit": {
            "remaining": rate_limit.get("remaining"),
            "limit": rate_limit.get("limit"),
            "resets_at": rate_limit.get("resets_at_timestamp"),
        } if isinstance(rate_limit, dict) and rate_limit else None,
        "error": None,
    }


def validate_world_cup_sportmonks_pipeline() -> dict[str, Any]:
    """Run a full pipeline diagnostic: connection + fixture fetch + fact coverage."""
    from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts

    result: dict[str, Any] = {"steps": []}

    # Step 1: connection test
    conn = test_world_cup_sportmonks_connection()
    result["steps"].append({"name": "connection", "ok": conn["ok"], "detail": conn})
    if not conn["ok"]:
        result["ok"] = False
        result["error"] = conn.get("error", "Connection failed")
        return result

    # Step 2: fetch fixtures feed
    api_token = _clean(settings.WORLD_CUP_SPORTMONKS_API_TOKEN)
    fixtures_url = _clean(settings.WORLD_CUP_SPORTMONKS_FIXTURES_URL)
    if not fixtures_url:
        result["steps"].append({"name": "fixture_fetch", "ok": False, "error": "No fixtures feed URL configured"})
        result["ok"] = False
        result["error"] = "No fixtures feed URL configured"
        return result

    request_url = _with_api_token(fixtures_url, api_token)
    request = Request(request_url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=10) as response:
            body = response.read(2 * 1024 * 1024)
        data = json.loads(body.decode("utf-8"))
        items = data.get("data") or data.get("response") or []
        fixture_ids: set[str] = set()
        for row in items:
            if isinstance(row, dict):
                fid = str(row.get("id") or row.get("fixture_id") or row.get("match_id") or "")
                if fid:
                    fixture_ids.add(fid)
        result["steps"].append({
            "name": "fixture_fetch",
            "ok": True,
            "fixture_count": len(items) if isinstance(items, list) else 0,
            "fixture_ids_sample": sorted(fixture_ids)[:10],
        })
    except Exception as exc:
        result["steps"].append({"name": "fixture_fetch", "ok": False, "error": str(exc)})
        result["ok"] = False
        result["error"] = f"Fixture fetch failed: {exc}"
        return result

    # Step 3: compare with stored facts
    stored_facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT, kind="match_result")
    stored_match_ids = {f.get("match_id") for f in stored_facts if f.get("match_id")}
    covered = fixture_ids & stored_match_ids
    missing = fixture_ids - stored_match_ids
    extra = stored_match_ids - fixture_ids

    coverage = {
        "api_fixture_count": len(fixture_ids),
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
        f"Connected. {len(fixture_ids)} fixtures from Sportmonks, "
        f"{len(stored_facts)} match facts stored, "
        f"{len(missing)} fixtures not yet imported."
    )
    return result


def _configured_feeds() -> list[tuple[str, str, Callable[[dict[str, Any], str, str], dict[str, Any]]]]:
    feeds = []
    for kind, setting_name, converter in _FEEDS:
        source_url = _clean(getattr(settings, setting_name, ""))
        if source_url:
            feeds.append((kind, source_url, converter))
    return feeds


def _fetch_sportmonks_json(
    source_url: str,
    *,
    source_kind: str,
    source_fetches: list[dict[str, Any]],
) -> dict[str, Any]:
    started = time.perf_counter()
    api_token = _clean(settings.WORLD_CUP_SPORTMONKS_API_TOKEN)
    if not api_token:
        _record_fetch(
            source_fetches,
            source_kind,
            source_url,
            started,
            "failed",
            "WORLD_CUP_SPORTMONKS_API_TOKEN is not configured",
        )
        raise ValueError("WORLD_CUP_SPORTMONKS_API_TOKEN is not configured")

    request_url = _with_api_token(source_url, api_token)
    request = Request(
        request_url,
        headers={
            "Accept": "application/json",
            "User-Agent": _clean(settings.WORLD_CUP_SOURCE_BUNDLE_USER_AGENT),
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
        raise ValueError(f"Sportmonks returned HTTP {exc.code}") from exc
    except (TimeoutError, URLError) as exc:
        _record_fetch(
            source_fetches,
            source_kind,
            source_url,
            started,
            "failed",
            "fetch failed",
        )
        raise ValueError("Sportmonks fetch failed") from exc

    if len(body) > settings.WORLD_CUP_SOURCE_BUNDLE_MAX_BYTES:
        _record_fetch(
            source_fetches,
            source_kind,
            source_url,
            started,
            "failed",
            "response too large",
        )
        raise ValueError("Sportmonks response too large")
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
        raise ValueError("Sportmonks did not return valid JSON") from exc
    if not isinstance(payload, dict):
        _record_fetch(
            source_fetches,
            source_kind,
            source_url,
            started,
            "failed",
            "response must be a JSON object",
        )
        raise ValueError("Sportmonks response must be a JSON object")
    if payload.get("errors"):
        _record_fetch(
            source_fetches,
            source_kind,
            source_url,
            started,
            "failed",
            "provider returned errors",
        )
        raise ValueError("Sportmonks returned errors")
    _record_fetch(source_fetches, source_kind, source_url, started, "success")
    return payload


def _fixtures_data(payload: dict[str, Any], source_url: str, observed_at: str) -> dict[str, Any]:
    matches = [_fixture_row(row) for row in _items(payload)]
    return _base_data(source_url, observed_at, {"matches": matches})


def _standings_data(payload: dict[str, Any], source_url: str, observed_at: str) -> dict[str, Any]:
    qualifications = []
    for row in _items(payload):
        normalized = _qualification_row(row)
        if normalized:
            qualifications.append(normalized)
    return _base_data(source_url, observed_at, {"qualifications": qualifications})


def _top_scorers_data(payload: dict[str, Any], source_url: str, observed_at: str) -> dict[str, Any]:
    awards = [_top_scorer_row(row) for row in _items(payload)]
    return _base_data(source_url, observed_at, {"player_awards": awards})


def _lineups_data(payload: dict[str, Any], source_url: str, observed_at: str) -> dict[str, Any]:
    lineups = [_lineup_row(row) for row in _items(payload)]
    return _base_data(source_url, observed_at, {"lineups": lineups})


def _cards_data(payload: dict[str, Any], source_url: str, observed_at: str) -> dict[str, Any]:
    cards = [_card_row(row) for row in _items(payload)]
    return _base_data(source_url, observed_at, {"cards": cards})


def _fixture_row(raw: dict[str, Any]) -> dict[str, Any]:
    match_id = _text(_first(raw, ("id",), ("fixture_id",), ("match_id",)))
    home, away = _participants(raw)
    if not match_id:
        raise ValueError("Sportmonks fixture missing id")
    if not home.get("name") or not away.get("name"):
        raise ValueError(f"Sportmonks fixture {match_id} missing home/away participants")

    row: dict[str, Any] = {
        "match_id": match_id,
        "stage": _text(_first(raw, ("round", "name"), ("round",), ("stage",), ("name",))),
        "kickoff_at": _text(_first(raw, ("starting_at",), ("kickoff_at",))),
        "venue": _text(_first(raw, ("venue", "name"), ("venue_name",))),
        "referee": _referee(raw),
        "home_team": home["name"],
        "away_team": away["name"],
        "status": _fixture_status(raw),
        "winner": _winner_name(raw, home, away),
    }
    home_score, away_score = _score(raw, home.get("id"), away.get("id"))
    if home_score != "":
        row["home_score"] = home_score
    if away_score != "":
        row["away_score"] = away_score
    return _compact(row)


def _qualification_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    team = _team_name(raw)
    if not team:
        raise ValueError("Sportmonks standing missing team")
    status = _qualification_status(raw)
    if not status:
        return None
    row: dict[str, Any] = {
        "team": team,
        "stage": _text(_first(raw, ("group", "name"), ("round", "name"), ("stage",))),
        "status": status,
    }
    if status == "qualified":
        row["already_qualified"] = True
    if status == "eliminated":
        row["already_eliminated"] = True
    return _compact(row)


def _top_scorer_row(raw: dict[str, Any]) -> dict[str, Any]:
    player = _text(_first(raw, ("player", "name"), ("player_name",), ("name",)))
    if not player:
        raise ValueError("Sportmonks top scorer missing player")
    return _compact({
        "award": "golden_boot",
        "player": player,
        "team": _team_name(raw),
        "goals": _text(_first(raw, ("total",), ("goals",), ("goals", "total"))),
        "rank": _text(_first(raw, ("position",), ("rank",))),
        "status": "current",
    })


def _lineup_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Sportmonks lineup row into a thin lineup record.

    Sportmonks lineup payloads typically nest players under ``lineup`` /
    ``players`` with per-card fields. The adapter keeps only the fields the
    signal pipeline needs: match_id, team, player, position, and starting flag.
    """
    match_id = _text(_first(raw, ("fixture_id",), ("match_id",), ("id",)))
    team = _team_name(raw)
    players_raw = raw.get("lineup") or raw.get("players") or []
    if not isinstance(players_raw, list):
        players_raw = []
    players = []
    for entry in players_raw:
        if not isinstance(entry, dict):
            continue
        player = _text(_first(entry, ("player", "name"), ("player_name",), ("name",)))
        if not player:
            continue
        players.append(_compact({
            "player": player,
            "team": _text(_first(entry, ("team", "name"), ("team_name",))) or team,
            "position": _text(_first(entry, ("position", "name"), ("position",), ("role",))),
            "starting": _boolish(entry.get("starting")) or _boolish(entry.get("starter")),
        }))
    if not players:
        raise ValueError("Sportmonks lineup row has no players")
    return _compact({
        "match_id": match_id,
        "team": team,
        "players": players,
    })


def _card_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Sportmonks card event row into a thin card record."""
    match_id = _text(_first(raw, ("fixture_id",), ("match_id",), ("id",)))
    player = _text(_first(raw, ("player", "name"), ("player_name",), ("name",)))
    if not player:
        raise ValueError("Sportmonks card row missing player")
    card_type = _text(_first(raw, ("type",), ("card_type",), ("card",))).lower()
    if "red" in card_type:
        card_type = "red"
    elif "yellow" in card_type or card_type in {"y", "yc"}:
        card_type = "yellow"
    return _compact({
        "match_id": match_id,
        "player": player,
        "team": _team_name(raw),
        "card_type": card_type,
        "minute": _text(_first(raw, ("minute",), ("time",))),
    })


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("data")
    if value is None:
        value = payload.get("response")
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Sportmonks feed data must be a list")
    rows = []
    for row in value:
        if not isinstance(row, dict):
            raise ValueError("Sportmonks feed rows must be objects")
        rows.append(row)
    return rows


def _participants(raw: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    participants = raw.get("participants") or raw.get("teams")
    if isinstance(participants, dict):
        home = _participant_from_dict(participants.get("home"))
        away = _participant_from_dict(participants.get("away"))
        return home, away
    if not isinstance(participants, list):
        return {}, {}

    home: dict[str, str] = {}
    away: dict[str, str] = {}
    fallback: list[dict[str, str]] = []
    for participant in participants:
        if not isinstance(participant, dict):
            continue
        normalized = _participant_from_dict(participant)
        if not normalized.get("name"):
            continue
        location = _participant_location(participant)
        if location == "home":
            home = normalized
        elif location == "away":
            away = normalized
        else:
            fallback.append(normalized)
    if not home and fallback:
        home = fallback.pop(0)
    if not away and fallback:
        away = fallback.pop(0)
    return home, away


def _participant_from_dict(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {
        "id": _text(raw.get("id")),
        "name": _text(_first(raw, ("name",), ("participant", "name"))),
        "winner": _text(_first(raw, ("winner",), ("meta", "winner"))).lower(),
    }


def _participant_location(raw: dict[str, Any]) -> str:
    location = _text(_first(raw, ("meta", "location"), ("location",), ("side",))).lower()
    if location in {"home", "away"}:
        return location
    if raw.get("home") is True:
        return "home"
    if raw.get("away") is True:
        return "away"
    return ""


def _fixture_status(raw: dict[str, Any]) -> str:
    status = _text(_first(
        raw,
        ("state", "short_name"),
        ("state", "name"),
        ("state", "developer_name"),
        ("status",),
    )).lower()
    aliases = {
        "ft": "finished",
        "finished": "finished",
        "fulltime": "finished",
        "full time": "finished",
        "not_started": "scheduled",
        "not started": "scheduled",
        "ns": "scheduled",
    }
    return aliases.get(status, status or "scheduled")


def _winner_name(raw: dict[str, Any], home: dict[str, str], away: dict[str, str]) -> str:
    winner_id = _text(_first(raw, ("winner_participant_id",), ("winner", "id")))
    if winner_id and winner_id == home.get("id"):
        return home.get("name", "")
    if winner_id and winner_id == away.get("id"):
        return away.get("name", "")
    if home.get("winner") in {"1", "true", "yes"}:
        return home.get("name", "")
    if away.get("winner") in {"1", "true", "yes"}:
        return away.get("name", "")
    return _text(raw.get("winner"))


def _score(raw: dict[str, Any], home_id: str, away_id: str) -> tuple[Any, Any]:
    scores = raw.get("scores")
    if isinstance(scores, dict):
        return _score_value(scores.get("home")), _score_value(scores.get("away"))
    home_score = raw.get("home_score")
    away_score = raw.get("away_score")
    if home_score is not None or away_score is not None:
        return _score_value(home_score), _score_value(away_score)
    if not isinstance(scores, list):
        return "", ""

    by_participant: dict[str, Any] = {}
    for score in scores:
        if not isinstance(score, dict):
            continue
        participant_id = _text(_first(score, ("participant_id",), ("score", "participant")))
        goals = _score_value(_first(score, ("score", "goals"), ("goals",), ("score",)))
        if participant_id and goals != "":
            by_participant[participant_id] = goals
    return by_participant.get(home_id, ""), by_participant.get(away_id, "")


def _score_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _score_value(value.get("goals") or value.get("total"))
    if value is None:
        return ""
    return value


def _qualification_status(raw: dict[str, Any]) -> str:
    text = _text(_first(raw, ("status",), ("result",), ("description",))).lower()
    if text in {"qualified", "advanced", "eliminated"}:
        return "qualified" if text == "advanced" else text
    if "qualified" in text or "advanced" in text or "knockout" in text:
        return "qualified"
    if "eliminated" in text:
        return "eliminated"
    return ""


def _team_name(raw: dict[str, Any]) -> str:
    return _text(_first(
        raw,
        ("participant", "name"),
        ("team", "name"),
        ("participant_name",),
        ("team_name",),
    ))


def _referee(raw: dict[str, Any]) -> str:
    value = _first(raw, ("referee", "name"), ("referee",))
    if value:
        return _text(value)
    referees = raw.get("referees")
    if isinstance(referees, list) and referees:
        first = referees[0]
        if isinstance(first, dict):
            return _text(_first(first, ("name",), ("referee", "name")))
    return ""


def _base_data(source_url: str, observed_at: str, sections: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": _PROVIDER,
        "source_url": source_url,
        "observed_at": observed_at,
        **sections,
    }


def _bundle_entry(kind: str, source_url: str, data: dict[str, Any]) -> dict[str, Any]:
    display_url = _display_url(source_url)
    return {
        "kind": "data",
        "source": _PROVIDER,
        "source_url": display_url,
        "observed_at": _clean(data.get("observed_at")),
        "feed_kind": kind,
        "payload": data,
    }


def _provider_result_metadata(
    payload: dict[str, Any],
    source_metadata: list[dict[str, Any]],
) -> dict[str, Any]:
    sources = payload.get("sources") if isinstance(payload, dict) else []
    skipped = payload.get("skipped_sources") if isinstance(payload, dict) else []
    source_fetches = payload.get("source_fetches") if isinstance(payload, dict) else []
    return {
        "provider": _PROVIDER,
        "source_feeds": [
            {
                "kind": _clean(entry.get("feed_kind") or entry.get("kind")),
                "source": _clean(entry.get("source")),
                "source_url": _clean(entry.get("source_url")),
                "observed_at": _clean(entry.get("observed_at")),
            }
            for entry in sources
            if isinstance(entry, dict)
        ],
        "skipped_source_count": len(skipped) if isinstance(skipped, list) else 0,
        "skipped_sources": skipped if isinstance(skipped, list) else [],
        "source_fetch_count": len(source_fetches) if isinstance(source_fetches, list) else 0,
        "source_fetches": source_fetches if isinstance(source_fetches, list) else [],
        "source_metadata": source_metadata,
    }


def _has_rows(data: dict[str, Any]) -> bool:
    for key in ("matches", "qualifications", "player_awards", "lineups", "cards"):
        value = data.get(key)
        if isinstance(value, list) and value:
            return True
    return False


def _with_api_token(source_url: str, api_token: str) -> str:
    parts = urlsplit(source_url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if not any(key == "api_token" for key, _value in query):
        query.append(("api_token", api_token))
    return urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        urlencode(query),
        parts.fragment,
    ))


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


def _first(raw: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value = _dig(raw, path)
        if value not in (None, ""):
            return value
    return None


def _dig(raw: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = raw
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("shortName") or value.get("displayName")
    return _clean(value)


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value not in ("", [], None, {})}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _boolish(value: Any) -> bool:
    """Normalize truthy/falsy values from API responses to a Python bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "starting", "starter"}


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
