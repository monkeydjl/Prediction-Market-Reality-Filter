"""Normalize raw World Cup player-award payloads into award snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT
from app.services.world_cup_data_source_service import (
    import_world_cup_data,
    world_cup_data_to_facts,
)


def world_cup_player_awards_source_to_data(payload: Any) -> dict[str, Any]:
    """Convert raw top-scorer/player-award data into normalized awards."""

    envelope = payload if isinstance(payload, dict) else {}
    rows = _award_rows(payload)
    awards = [
        _normalize_award(
            row,
            index,
            default_award=_clean(envelope.get("award") or envelope.get("award_name")) or "golden_boot",
            default_status=_clean(envelope.get("status")) or _status_from_final(envelope.get("final")),
        )
        for index, row in enumerate(rows)
    ]
    if not awards:
        raise ValueError("player-awards payload did not contain awards")
    return {
        "tournament": _clean(envelope.get("tournament")) or WORLD_CUP_TOURNAMENT,
        "source": (
            _clean(envelope.get("source"))
            or _clean(envelope.get("provider"))
            or "world_cup_player_awards_source"
        ),
        "source_url": _clean(envelope.get("source_url") or envelope.get("url")),
        "observed_at": _clean(envelope.get("observed_at")) or _utc_now(),
        "player_awards": awards,
    }


def preview_world_cup_player_awards_source(payload: Any) -> dict[str, Any]:
    """Preview facts produced from raw player-award data."""

    data = world_cup_player_awards_source_to_data(payload)
    facts = world_cup_data_to_facts(data)
    return {
        "normalized_award_count": len(data["player_awards"]),
        "normalized_data": data,
        "converted_fact_count": len(facts),
        "facts": facts,
    }


def import_world_cup_player_awards_source(
    payload: Any,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Import player-award facts produced from raw award data."""

    data = world_cup_player_awards_source_to_data(payload)
    result = import_world_cup_data(data, replace=replace)
    result["normalized_award_count"] = len(data["player_awards"])
    result["normalized_data"] = data
    return result


def _award_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return _rows_from_list(payload)
    if not isinstance(payload, dict):
        raise ValueError("player-awards payload must be an object or list")
    if _looks_like_award(payload):
        return [payload]

    rows: list[dict[str, Any]] = []
    for key in ("player_awards", "awards", "top_scorers", "scorers", "players", "response", "data"):
        value = payload.get(key)
        if value is None:
            continue
        rows.extend(_award_rows(value))
    return rows


def _rows_from_list(rows: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict) and _looks_like_award(row):
            normalized.append(row)
            continue
        if isinstance(row, (dict, list)):
            normalized.extend(_award_rows(row))
            continue
        raise ValueError("player-awards rows must be objects")
    return normalized


def _looks_like_award(raw: dict[str, Any]) -> bool:
    return bool(_player_name(raw) and _goals(raw) is not None)


def _normalize_award(
    raw: dict[str, Any],
    index: int,
    *,
    default_award: str,
    default_status: str,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"player_awards[{index}] must be an object")
    player = _player_name(raw)
    if not player:
        raise ValueError(f"player_awards[{index}] missing player")
    goals = _goals(raw)
    if goals is None:
        raise ValueError(f"player_awards[{index}] missing goals")

    award = _clean(_first(raw, ("award",), ("award_name",)))
    status = _clean(_first(raw, ("status",))) or _status_from_final(raw.get("final")) or default_status
    return _compact({
        "award": award.lower() or default_award.lower() or "golden_boot",
        "player": player,
        "team": _team_name(raw),
        "goals": goals,
        "rank": _rank(raw),
        "status": status.lower() or "current",
    })


def _player_name(raw: dict[str, Any]) -> str:
    return _text(_first(
        raw,
        ("player",),
        ("player", "name"),
        ("athlete",),
        ("athlete", "name"),
        ("scorer",),
        ("scorer", "name"),
        ("name",),
    ))


def _team_name(raw: dict[str, Any]) -> str:
    stats = _first_statistics(raw)
    team = _text(_first(
        raw,
        ("team",),
        ("team", "name"),
        ("country",),
        ("club", "name"),
    ))
    if team:
        return team
    if stats:
        return _text(_first(stats, ("team",), ("team", "name")))
    return ""


def _goals(raw: dict[str, Any]) -> int | float | None:
    stats = _first_statistics(raw)
    value = _first(
        raw,
        ("goals",),
        ("goal_count",),
        ("total_goals",),
        ("stats", "goals"),
        ("statistics", "goals"),
    )
    if isinstance(value, dict):
        value = value.get("total") or value.get("goals")
    if value is None and stats:
        value = _first(
            stats,
            ("goals", "total"),
            ("goals",),
            ("stats", "goals"),
        )
    return _number(value)


def _rank(raw: dict[str, Any]) -> int | float | None:
    return _number(_first(raw, ("rank",), ("position",), ("place",)))


def _first_statistics(raw: dict[str, Any]) -> dict[str, Any]:
    stats = raw.get("statistics")
    if isinstance(stats, list) and stats and isinstance(stats[0], dict):
        return stats[0]
    if isinstance(stats, dict):
        return stats
    return {}


def _status_from_final(value: Any) -> str:
    flag = _boolish(value)
    if flag is True:
        return "official"
    if flag is False:
        return "current"
    return ""


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


def _number(value: Any) -> int | float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return int(number) if number.is_integer() else number


def _boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = _clean(value).lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return None


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value not in ("", [], None, {})}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())
