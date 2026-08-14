"""Normalize raw World Cup player-status payloads into status snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT
from app.services.world_cup_data_source_service import (
    import_world_cup_data,
    world_cup_data_to_facts,
)


def world_cup_player_status_source_to_data(payload: Any) -> dict[str, Any]:
    """Convert raw injury/availability/suspension data into player_statuses."""

    envelope = payload if isinstance(payload, dict) else {}
    rows = _status_rows(payload)
    statuses = [
        _normalize_status_row(
            row,
            index,
            default_team=_team_name(envelope),
            default_kind=_clean(envelope.get("kind") or envelope.get("type") or envelope.get("category")),
        )
        for index, row in enumerate(rows)
    ]
    if not statuses:
        raise ValueError("player-status payload did not contain player statuses")
    return {
        "tournament": _clean(envelope.get("tournament")) or WORLD_CUP_TOURNAMENT,
        "source": (
            _clean(envelope.get("source"))
            or _clean(envelope.get("provider"))
            or "world_cup_player_status_source"
        ),
        "source_url": _clean(envelope.get("source_url") or envelope.get("url")),
        "observed_at": _clean(envelope.get("observed_at")) or _utc_now(),
        "player_statuses": statuses,
    }


def preview_world_cup_player_status_source(payload: Any) -> dict[str, Any]:
    """Preview facts produced from raw player-status data."""

    data = world_cup_player_status_source_to_data(payload)
    facts = world_cup_data_to_facts(data)
    return {
        "normalized_status_count": len(data["player_statuses"]),
        "normalized_data": data,
        "converted_fact_count": len(facts),
        "facts": facts,
    }


def import_world_cup_player_status_source(
    payload: Any,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Import facts produced from raw player-status data."""

    data = world_cup_player_status_source_to_data(payload)
    result = import_world_cup_data(data, replace=replace)
    result["normalized_status_count"] = len(data["player_statuses"])
    result["normalized_data"] = data
    return result


def get_team_injury_impact(team_name: str) -> float | None:
    """Role-weighted Out impact in [0, 1] for a team, from imported facts.

    The P1-F3 fallback in app/sports/football/adapters/_shared.py calls this
    when the static table in football_injury has no row for the team. Role tier
    comes from the team's own lineup facts (a player listed as starting counts
    as "starter", anyone else falls to the bench weight) because player-status
    facts carry no role of their own. None when the team has no facts or no Out
    rows — never 0.0, which would claim known-healthy.
    """

    name = (team_name or "").strip()
    if not name:
        return None
    from app.services.sports_fact_service import load_sports_facts
    from app.sports.football.football_injury import summarize_injury_impact

    facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)
    team_norm = _norm(name)
    team_facts = [fact for fact in facts if _norm(fact.get("team")) == team_norm]
    if not team_facts:
        return None

    starters = {
        _norm(fact.get("player"))
        for fact in team_facts
        if fact.get("kind") == "lineup"
        and _clean(fact.get("status")).lower() in {"starting", "starter"}
        and fact.get("player")
    }
    rows = [
        {
            "player": fact.get("player"),
            "role": "starter" if _norm(fact.get("player")) in starters else "bench",
            # summarize_injury_impact counts status == "out" only; the fact
            # store spells the same absence as injured/suspended/banned.
            "status": "out",
        }
        for fact in team_facts
        if fact.get("kind") in {"injury", "suspension"}
        and _clean(fact.get("status")).lower() in {"out", "injured", "suspended", "banned"}
    ]
    return summarize_injury_impact(rows)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _status_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return _rows_from_list(payload)
    if not isinstance(payload, dict):
        raise ValueError("player-status payload must be an object or list")
    if _looks_like_status(payload):
        return [payload]

    rows: list[dict[str, Any]] = []
    for key in (
        "player_statuses",
        "injuries",
        "availability",
        "suspensions",
        "lineups",
        "players",
        "response",
        "data",
    ):
        value = payload.get(key)
        if value is None:
            continue
        rows.extend(_status_rows(value))
    return rows


def _rows_from_list(rows: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict) and _looks_like_status(row):
            normalized.append(row)
            continue
        if isinstance(row, (dict, list)):
            normalized.extend(_status_rows(row))
            continue
        raise ValueError("player-status rows must be objects")
    return normalized


def _looks_like_status(raw: dict[str, Any]) -> bool:
    return bool(_player_name(raw))


def _normalize_status_row(
    raw: dict[str, Any],
    index: int,
    *,
    default_team: str,
    default_kind: str,
) -> dict[str, Any]:
    player = _player_name(raw)
    team = _team_name(raw) or default_team
    if not player:
        raise ValueError(f"player_statuses[{index}] missing player")
    if not team:
        raise ValueError(f"player_statuses[{index}] missing team")
    kind = _kind(raw, default_kind)
    status = _status(raw, kind)
    return _compact({
        "kind": kind,
        "team": team,
        "player": player,
        "status": status,
        "severity": _clean(raw.get("severity") or raw.get("risk")).lower(),
        "match_id": _match_id(raw),
        "stage": _clean(raw.get("stage") or raw.get("round")),
        "reason": _reason(raw),
        "applies_to": _clean_list(raw.get("applies_to")),
    })


def _kind(raw: dict[str, Any], default_kind: str) -> str:
    kind = _clean(raw.get("kind") or raw.get("type") or raw.get("category") or default_kind).lower()
    aliases = {
        "injuries": "injury",
        "injury_status": "injury",
        "available": "availability",
        "availability_status": "availability",
        "suspensions": "suspension",
        "suspended": "suspension",
        "lineups": "lineup",
    }
    kind = aliases.get(kind, kind)
    if kind in {"injury", "availability", "suspension", "lineup"}:
        return kind

    status = _status_text(raw)
    if status in {"suspended", "banned"}:
        return "suspension"
    if status in {"starting", "starter", "bench", "substitute"}:
        return "lineup"
    if (
        status in {"out", "injured", "doubtful", "questionable"}
        or raw.get("injury")
        or _provider_injury_reason(raw)
    ):
        return "injury"
    if status:
        return "availability"
    raise ValueError("player_statuses kind must be injury, availability, suspension, or lineup")


def _status(raw: dict[str, Any], kind: str) -> str:
    status = _status_text(raw)
    if status:
        return status
    if kind == "injury":
        return "injured"
    if kind == "suspension":
        return "suspended"
    if kind == "lineup":
        return "listed"
    return "unknown"


def _status_text(raw: dict[str, Any]) -> str:
    return _clean(_first(
        raw,
        ("status",),
        ("availability",),
        ("state",),
        ("player", "status"),
    )).lower()


def _player_name(raw: dict[str, Any]) -> str:
    return _text(_first(
        raw,
        ("player",),
        ("player", "name"),
        ("athlete",),
        ("athlete", "name"),
        ("name",),
    ))


def _team_name(raw: dict[str, Any]) -> str:
    return _text(_first(
        raw,
        ("team",),
        ("team", "name"),
        ("country",),
        ("club", "name"),
    ))


def _match_id(raw: dict[str, Any]) -> str:
    return _text(_first(
        raw,
        ("match_id",),
        ("fixture_id",),
        ("fixture", "id"),
        ("match", "id"),
    ))


def _reason(raw: dict[str, Any]) -> str:
    injury = raw.get("injury")
    if isinstance(injury, dict):
        injury = injury.get("type") or injury.get("reason") or injury.get("description")
    return _clean(_first(
        raw,
        ("reason",),
        ("description",),
        ("note",),
        ("notes",),
        ("player", "reason"),
        ("player", "type"),
    ) or injury)


def _provider_injury_reason(raw: dict[str, Any]) -> str:
    reason = _clean(_first(raw, ("player", "reason")))
    if reason:
        return reason
    status_type = _clean(_first(raw, ("player", "type"))).lower()
    if status_type in {"missing fixture", "injury", "injured", "doubtful", "questionable"}:
        return status_type
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
