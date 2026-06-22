"""Normalize raw World Cup statistics payloads into stat facts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT
from app.services.world_cup_data_source_service import (
    import_world_cup_data,
    world_cup_data_to_facts,
)

_SKIP_PLAYER_STAT_PATHS = {
    "games.appearences",
    "games.appearances",
    "games.captain",
    "games.lineups",
    "games.minutes",
    "games.number",
    "games.position",
    "substitutes.in",
    "substitutes.out",
    "substitutes.bench",
}


def world_cup_statistics_source_to_data(payload: Any) -> dict[str, Any]:
    """Convert raw team/player statistics payloads into normalized stat rows."""

    if not isinstance(payload, dict):
        raise ValueError("statistics payload must be an object")
    team_stats = _direct_rows(payload.get("team_stats"), "team_stats")
    player_stats = _direct_rows(payload.get("player_stats"), "player_stats")

    if not team_stats and not player_stats:
        team_stats.extend(_api_football_team_stats(payload))
        player_stats.extend(_api_football_player_stats(payload))

    if not team_stats and not player_stats:
        raise ValueError("statistics payload did not contain team or player statistics")
    return {
        "tournament": _clean(payload.get("tournament")) or WORLD_CUP_TOURNAMENT,
        "source": (
            _clean(payload.get("source"))
            or _clean(payload.get("provider"))
            or "world_cup_statistics_source"
        ),
        "source_url": _clean(payload.get("source_url") or payload.get("url")),
        "observed_at": _clean(payload.get("observed_at")) or _utc_now(),
        "team_stats": team_stats,
        "player_stats": player_stats,
    }


def preview_world_cup_statistics_source(payload: Any) -> dict[str, Any]:
    """Preview facts produced from raw World Cup statistics data."""

    data = world_cup_statistics_source_to_data(payload)
    facts = world_cup_data_to_facts(data)
    return {
        "normalized_team_stat_count": len(data["team_stats"]),
        "normalized_player_stat_count": len(data["player_stats"]),
        "normalized_data": data,
        "converted_fact_count": len(facts),
        "facts": facts,
    }


def import_world_cup_statistics_source(
    payload: Any,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Import facts produced from raw World Cup statistics data."""

    data = world_cup_statistics_source_to_data(payload)
    result = import_world_cup_data(data, replace=replace)
    result["normalized_team_stat_count"] = len(data["team_stats"])
    result["normalized_player_stat_count"] = len(data["player_stats"])
    result["normalized_data"] = data
    return result


def _direct_rows(value: Any, name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    rows = []
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            raise ValueError(f"{name}[{index}] must be an object")
        rows.append(row)
    return rows


def _api_football_team_stats(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    default_match_id = _match_id(payload)
    default_stage = _stage(payload)
    for group in _response_rows(payload):
        if not _looks_like_team_stat_group(group):
            continue
        match_id = _match_id(group) or default_match_id
        stage = _stage(group) or default_stage
        team = _team_name(group)
        if not team:
            raise ValueError("statistics team rows missing team")
        for stat in _stat_rows(group.get("statistics")):
            stat_name = _text(_first(stat, ("stat_name",), ("name",), ("type",)))
            value, unit = _stat_value_and_unit(
                _first(stat, ("stat_value",), ("value",), ("total",))
            )
            if value is None:
                continue
            rows.append(_compact({
                "team": team,
                "match_id": match_id,
                "stage": stage,
                "stat_name": stat_name,
                "stat_value": value,
                "stat_unit": unit,
            }))
    return rows


def _api_football_player_stats(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    default_match_id = _match_id(payload)
    default_stage = _stage(payload)
    for group in _response_rows(payload):
        players = group.get("players")
        if not isinstance(players, list):
            continue
        team = _team_name(group)
        match_id = _match_id(group) or default_match_id
        stage = _stage(group) or default_stage
        for player_row in players:
            if not isinstance(player_row, dict):
                raise ValueError("statistics player rows must be objects")
            player = _player_name(player_row)
            if not player:
                raise ValueError("statistics player rows missing player")
            for stat_group in _stat_rows(player_row.get("statistics")):
                position = _text(_first(stat_group, ("games", "position"), ("position",)))
                number = _text(_first(stat_group, ("games", "number"), ("number",)))
                rows.extend(
                    _flatten_player_stat_group(
                        stat_group,
                        player=player,
                        team=_team_name(player_row) or team,
                        match_id=_match_id(player_row) or match_id,
                        stage=_stage(player_row) or stage,
                        position=position,
                        jersey_number=number,
                    )
                )
    return rows


def _flatten_player_stat_group(
    stat_group: dict[str, Any],
    *,
    player: str,
    team: str,
    match_id: str,
    stage: str,
    position: str,
    jersey_number: str,
) -> list[dict[str, Any]]:
    if _first(stat_group, ("type",), ("stat_name",), ("name",)):
        value, unit = _stat_value_and_unit(
            _first(stat_group, ("stat_value",), ("value",), ("total",))
        )
        if value is None:
            return []
        return [_compact({
            "team": team,
            "player": player,
            "match_id": match_id,
            "stage": stage,
            "position": position,
            "jersey_number": jersey_number,
            "stat_name": _text(_first(stat_group, ("type",), ("stat_name",), ("name",))),
            "stat_value": value,
            "stat_unit": unit,
        })]

    rows: list[dict[str, Any]] = []
    for path, value in _numeric_leaves(stat_group):
        if path in _SKIP_PLAYER_STAT_PATHS:
            continue
        stat_value, unit = _stat_value_and_unit(value)
        if stat_value is None:
            continue
        rows.append(_compact({
            "team": team,
            "player": player,
            "match_id": match_id,
            "stage": stage,
            "position": position,
            "jersey_number": jersey_number,
            "stat_name": path,
            "stat_value": stat_value,
            "stat_unit": unit,
        }))
    return rows


def _response_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("response")
    if value is None:
        value = payload.get("data")
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("statistics response must be a list")
    rows = []
    for row in value:
        if not isinstance(row, dict):
            raise ValueError("statistics response rows must be objects")
        rows.append(row)
    return rows


def _looks_like_team_stat_group(raw: dict[str, Any]) -> bool:
    return raw.get("statistics") is not None and raw.get("players") is None


def _stat_rows(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if not isinstance(value, list):
        raise ValueError("statistics groups must be objects or lists")
    rows = []
    for row in value:
        if not isinstance(row, dict):
            raise ValueError("statistics rows must be objects")
        rows.append(row)
    return rows


def _numeric_leaves(raw: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    leaves: list[tuple[str, Any]] = []
    for key, value in raw.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            leaves.extend(_numeric_leaves(value, path))
            continue
        if isinstance(value, list):
            continue
        if _stat_value_and_unit(value)[0] is not None:
            leaves.append((path, value))
    return leaves


def _stat_value_and_unit(value: Any) -> tuple[float | None, str]:
    if value is None or value == "":
        return None, ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, float(value)), ""
    text = _clean(value)
    unit = ""
    if text.endswith("%"):
        unit = "%"
        text = text[:-1]
    try:
        return max(0.0, float(text)), unit
    except (TypeError, ValueError):
        return None, unit


def _match_id(raw: dict[str, Any]) -> str:
    return _text(_first(raw, ("match_id",), ("fixture_id",), ("fixture", "id"), ("match", "id")))


def _team_name(raw: dict[str, Any]) -> str:
    return _text(_first(raw, ("team",), ("team", "name"), ("participant", "name")))


def _player_name(raw: dict[str, Any]) -> str:
    return _text(_first(raw, ("player",), ("player", "name"), ("name",)))


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


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value not in ("", [], None, {})}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())
