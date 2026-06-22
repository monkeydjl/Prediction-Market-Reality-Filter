"""Normalize raw World Cup lineup payloads into lineup facts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT
from app.services.world_cup_data_source_service import (
    import_world_cup_data,
    world_cup_data_to_facts,
)


def world_cup_lineups_source_to_data(payload: Any) -> dict[str, Any]:
    """Convert raw lineup/starting-XI payloads into player_statuses."""

    envelope = payload if isinstance(payload, dict) else {}
    rows = _lineup_rows(payload)
    default_match_id = _match_id(envelope)
    default_team = _team_name(envelope)
    statuses: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        statuses.extend(
            _normalize_lineup_row(
                row,
                index,
                default_match_id=default_match_id,
                default_team=default_team,
                default_stage=_stage(envelope),
            )
        )
    if not statuses:
        raise ValueError("lineups payload did not contain player lineups")
    return {
        "tournament": _clean(envelope.get("tournament")) or WORLD_CUP_TOURNAMENT,
        "source": (
            _clean(envelope.get("source"))
            or _clean(envelope.get("provider"))
            or "world_cup_lineups_source"
        ),
        "source_url": _clean(envelope.get("source_url") or envelope.get("url")),
        "observed_at": _clean(envelope.get("observed_at")) or _utc_now(),
        "player_statuses": statuses,
    }


def preview_world_cup_lineups_source(payload: Any) -> dict[str, Any]:
    """Preview facts produced from raw lineup data."""

    data = world_cup_lineups_source_to_data(payload)
    facts = world_cup_data_to_facts(data)
    return {
        "normalized_lineup_count": len(data["player_statuses"]),
        "normalized_data": data,
        "converted_fact_count": len(facts),
        "facts": facts,
    }


def import_world_cup_lineups_source(
    payload: Any,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Import facts produced from raw lineup data."""

    data = world_cup_lineups_source_to_data(payload)
    result = import_world_cup_data(data, replace=replace)
    result["normalized_lineup_count"] = len(data["player_statuses"])
    result["normalized_data"] = data
    return result


def _lineup_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return _rows_from_list(payload)
    if not isinstance(payload, dict):
        raise ValueError("lineups payload must be an object or list")
    if _looks_like_lineup_row(payload):
        return [payload]

    rows: list[dict[str, Any]] = []
    for key in ("lineups", "response", "data"):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            rows.extend(_rows_from_list(value))
            continue
        if isinstance(value, dict):
            rows.extend(_lineup_rows(value))
            continue
        raise ValueError(f"lineups {key} must be an object or list")
    return rows


def _rows_from_list(rows: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict) and _looks_like_lineup_row(row):
            normalized.append(row)
            continue
        if isinstance(row, (dict, list)):
            normalized.extend(_lineup_rows(row))
            continue
        raise ValueError("lineup rows must be objects")
    return normalized


def _looks_like_lineup_row(raw: dict[str, Any]) -> bool:
    return bool(
        raw.get("startXI") is not None
        or raw.get("starting") is not None
        or raw.get("substitutes") is not None
        or _player_name(raw)
    )


def _normalize_lineup_row(
    raw: dict[str, Any],
    index: int,
    *,
    default_match_id: str,
    default_team: str,
    default_stage: str,
) -> list[dict[str, Any]]:
    match_id = _match_id(raw) or default_match_id
    team = _team_name(raw) or default_team
    stage = _stage(raw) or default_stage
    formation = _text(raw.get("formation"))
    if _player_name(raw):
        status = _status(raw)
        normalized = _lineup_status(
            raw,
            match_id=match_id,
            team=team,
            stage=stage,
            formation=formation,
            status=status,
        )
        if not normalized.get("team"):
            raise ValueError(f"lineups[{index}] missing team")
        return [normalized]

    if not team:
        raise ValueError(f"lineups[{index}] missing team")
    statuses: list[dict[str, Any]] = []
    for entry in _player_entries(raw.get("startXI") or raw.get("starting")):
        statuses.append(
            _lineup_status(
                entry,
                match_id=match_id,
                team=team,
                stage=stage,
                formation=formation,
                status="starting",
            )
        )
    for entry in _player_entries(raw.get("substitutes")):
        statuses.append(
            _lineup_status(
                entry,
                match_id=match_id,
                team=team,
                stage=stage,
                formation=formation,
                status="bench",
            )
        )
    return statuses


def _lineup_status(
    raw: dict[str, Any],
    *,
    match_id: str,
    team: str,
    stage: str,
    formation: str,
    status: str,
) -> dict[str, Any]:
    player = _player_name(raw)
    if not player:
        raise ValueError("lineup player rows missing player")
    return _compact({
        "kind": "lineup",
        "team": _team_name(raw) or team,
        "player": player,
        "status": status,
        "match_id": _match_id(raw) or match_id,
        "stage": _stage(raw) or stage,
        "position": _position(raw),
        "formation": _text(raw.get("formation")) or formation,
        "jersey_number": _text(_first(raw, ("jersey_number",), ("number",), ("player", "number"))),
        "reason": _text(_first(raw, ("grid",), ("player", "grid"))),
        "applies_to": _clean_list(raw.get("applies_to")),
    })


def _player_entries(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("lineup player groups must be lists")
    rows = []
    for row in value:
        if not isinstance(row, dict):
            raise ValueError("lineup player rows must be objects")
        rows.append(row)
    return rows


def _status(raw: dict[str, Any]) -> str:
    status = _clean(_first(raw, ("status",), ("role",), ("lineup_status",))).lower()
    if status in {"start", "starter", "starting", "starting xi", "xi"}:
        return "starting"
    if status in {"bench", "substitute", "sub"}:
        return "bench"
    return status or "listed"


def _match_id(raw: dict[str, Any]) -> str:
    return _text(_first(
        raw,
        ("match_id",),
        ("fixture_id",),
        ("fixture", "id"),
        ("match", "id"),
    ))


def _team_name(raw: dict[str, Any]) -> str:
    return _text(_first(raw, ("team",), ("team", "name"), ("country",)))


def _player_name(raw: dict[str, Any]) -> str:
    return _text(_first(
        raw,
        ("player",),
        ("player", "name"),
        ("athlete",),
        ("athlete", "name"),
        ("name",),
    ))


def _position(raw: dict[str, Any]) -> str:
    return _text(_first(
        raw,
        ("position",),
        ("pos",),
        ("player", "pos"),
        ("player", "position"),
    ))


def _stage(raw: dict[str, Any]) -> str:
    return _text(_first(raw, ("stage",), ("round",), ("league", "round")))


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


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clean(item) for item in value if _clean(item)]


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value not in ("", [], None, {})}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())
