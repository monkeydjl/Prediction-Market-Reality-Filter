"""Normalize raw World Cup match-event payloads into discipline snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT
from app.services.world_cup_data_source_service import (
    import_world_cup_data,
    world_cup_data_to_facts,
)


def world_cup_match_events_source_to_data(payload: Any) -> dict[str, Any]:
    """Convert raw match event/card rows into normalized discipline data."""

    envelope = payload if isinstance(payload, dict) else {}
    rows = _event_rows(payload)
    default_match_id = _match_id(envelope)
    discipline = [
        row
        for index, raw in enumerate(rows)
        for row in [_normalize_event(raw, index, default_match_id=default_match_id)]
        if row
    ]
    if not discipline:
        raise ValueError("match-events payload did not contain card events")
    return {
        "tournament": _clean(envelope.get("tournament")) or WORLD_CUP_TOURNAMENT,
        "source": (
            _clean(envelope.get("source"))
            or _clean(envelope.get("provider"))
            or "world_cup_match_events_source"
        ),
        "source_url": _clean(envelope.get("source_url") or envelope.get("url")),
        "observed_at": _clean(envelope.get("observed_at")) or _utc_now(),
        "discipline": discipline,
    }


def preview_world_cup_match_events_source(payload: Any) -> dict[str, Any]:
    """Preview facts produced from a raw match-events payload."""

    data = world_cup_match_events_source_to_data(payload)
    facts = world_cup_data_to_facts(data)
    return {
        "normalized_event_count": len(data["discipline"]),
        "normalized_data": data,
        "converted_fact_count": len(facts),
        "facts": facts,
    }


def import_world_cup_match_events_source(
    payload: Any,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Import facts produced from a raw match-events payload."""

    data = world_cup_match_events_source_to_data(payload)
    result = import_world_cup_data(data, replace=replace)
    result["normalized_event_count"] = len(data["discipline"])
    result["normalized_data"] = data
    return result


def _event_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return _rows_from_list(payload)
    if not isinstance(payload, dict):
        raise ValueError("match-events payload must be an object or list")
    if _looks_like_event(payload):
        return [payload]

    rows: list[dict[str, Any]] = []
    for key in ("match_events", "events", "cards", "response", "data"):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            rows.extend(_rows_from_list(value))
            continue
        if isinstance(value, dict):
            rows.extend(_event_rows(value))
            continue
        raise ValueError(f"match-events {key} must be an object or list")
    return rows


def _rows_from_list(rows: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict) and _looks_like_event(row):
            normalized.append(row)
            continue
        if isinstance(row, (dict, list)):
            normalized.extend(_event_rows(row))
            continue
        raise ValueError("match-events rows must be objects")
    return normalized


def _looks_like_event(raw: dict[str, Any]) -> bool:
    return bool(
        _first(raw, ("type",), ("event_type",), ("detail",), ("card",))
        or raw.get("red_cards") is not None
        or raw.get("yellow_cards") is not None
    )


def _normalize_event(
    raw: dict[str, Any],
    index: int,
    *,
    default_match_id: str,
) -> dict[str, Any] | None:
    counts = _card_counts(raw)
    if counts is None:
        return None
    match_id = _match_id(raw) or default_match_id
    if not match_id:
        raise ValueError(f"match_events[{index}] missing match id")
    red_cards, yellow_cards, status = counts
    return _compact({
        "event_id": _text(_first(raw, ("event_id",), ("id",))),
        "match_id": match_id,
        "team": _team_name(raw),
        "player": _player_name(raw),
        "stage": _text(_first(raw, ("stage",), ("round",), ("league", "round"))),
        "minute": _minute(raw),
        "status": status,
        "red_cards": red_cards,
        "yellow_cards": yellow_cards,
        "detail": _text(_first(raw, ("detail",), ("card",), ("status",))),
        "notes": _text(_first(raw, ("comments",), ("notes",), ("reason",))),
    })


def _card_counts(raw: dict[str, Any]) -> tuple[int, int, str] | None:
    explicit_red = _number(raw.get("red_cards"))
    explicit_yellow = _number(raw.get("yellow_cards"))
    if explicit_red is not None or explicit_yellow is not None:
        status = "red_card" if (explicit_red or 0) > 0 else "yellow_card"
        return int(explicit_red or 0), int(explicit_yellow or 0), status

    text = _clean(" ".join([
        _text(_first(raw, ("type",), ("event_type",))),
        _text(_first(raw, ("detail",), ("card",), ("status",))),
    ])).lower()
    if "card" not in text and "yellow" not in text and "red" not in text:
        return None
    if "second yellow" in text:
        return 1, 0, "second_yellow_red_card"
    if "red" in text:
        return 1, 0, "red_card"
    if "yellow" in text:
        return 0, 1, "yellow_card"
    return None


def _match_id(raw: dict[str, Any]) -> str:
    return _text(_first(
        raw,
        ("match_id",),
        ("fixture_id",),
        ("fixture", "id"),
        ("match", "id"),
    ))


def _team_name(raw: dict[str, Any]) -> str:
    return _text(_first(
        raw,
        ("team",),
        ("team", "name"),
        ("country",),
    ))


def _player_name(raw: dict[str, Any]) -> str:
    return _text(_first(
        raw,
        ("player",),
        ("player", "name"),
        ("athlete",),
        ("athlete", "name"),
    ))


def _minute(raw: dict[str, Any]) -> str:
    explicit = _text(_first(raw, ("minute",), ("elapsed",)))
    if explicit:
        return explicit
    elapsed = _number(_first(raw, ("time", "elapsed")))
    if elapsed is None:
        return ""
    extra = _number(_first(raw, ("time", "extra")))
    if extra is None or extra == 0:
        return str(int(elapsed))
    return f"{int(elapsed)}+{int(extra)}"


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


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value not in ("", [], None, {})}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())
