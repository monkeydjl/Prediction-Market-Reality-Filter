"""Normalize raw World Cup standings payloads into qualification snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT
from app.services.world_cup_data_source_service import (
    import_world_cup_data,
    world_cup_data_to_facts,
)


def world_cup_standings_source_to_data(payload: Any) -> dict[str, Any]:
    """Convert raw standings/group-table data into normalized qualifications."""

    envelope = payload if isinstance(payload, dict) else {}
    rows = _standing_rows(payload)
    qualifications = [
        _normalize_standing(row, index)
        for index, row in enumerate(rows)
    ]
    if not qualifications:
        raise ValueError("standings-source payload did not contain standings")
    return {
        "tournament": _clean(envelope.get("tournament")) or WORLD_CUP_TOURNAMENT,
        "source": (
            _clean(envelope.get("source"))
            or _clean(envelope.get("provider"))
            or "world_cup_standings_source"
        ),
        "source_url": _clean(envelope.get("source_url") or envelope.get("url")),
        "observed_at": _clean(envelope.get("observed_at")) or _utc_now(),
        "qualifications": qualifications,
    }


def preview_world_cup_standings_source(payload: Any) -> dict[str, Any]:
    """Preview facts produced from raw standings data."""

    data = world_cup_standings_source_to_data(payload)
    facts = world_cup_data_to_facts(data)
    return {
        "normalized_qualification_count": len(data["qualifications"]),
        "normalized_data": data,
        "converted_fact_count": len(facts),
        "facts": facts,
    }


def import_world_cup_standings_source(
    payload: Any,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Import qualification facts produced from raw standings data."""

    data = world_cup_standings_source_to_data(payload)
    result = import_world_cup_data(data, replace=replace)
    result["normalized_qualification_count"] = len(data["qualifications"])
    result["normalized_data"] = data
    return result


def _standing_rows(payload: Any, stage: str = "") -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows: list[dict[str, Any]] = []
        for item in payload:
            rows.extend(_standing_rows(item, stage))
        return rows
    if not isinstance(payload, dict):
        raise ValueError("standings-source payload must be an object or list")

    if _looks_like_standing(payload):
        row = dict(payload)
        if stage and not _first(row, ("stage",), ("group",)):
            row["stage"] = stage
        return [row]

    rows: list[dict[str, Any]] = []
    group_map = payload.get("groups")
    if isinstance(group_map, dict):
        for group_name, group_rows in group_map.items():
            rows.extend(_standing_rows(group_rows, _clean(group_name)))
    elif isinstance(group_map, list):
        rows.extend(_standing_rows(group_map, stage))

    league_standings = _dig(payload, ("league", "standings"))
    if isinstance(league_standings, list):
        rows.extend(_standing_rows(league_standings, stage))

    for key in ("standings", "tables", "response", "data"):
        value = payload.get(key)
        if value is None:
            continue
        rows.extend(_standing_rows(value, stage))
    return rows


def _looks_like_standing(raw: dict[str, Any]) -> bool:
    return bool(_team_name(raw))


def _normalize_standing(raw: dict[str, Any], index: int) -> dict[str, Any]:
    team = _team_name(raw)
    if not team:
        raise ValueError(f"standings[{index}] missing team")

    status_text = _text(_first(
        raw,
        ("qualification_status",),
        ("status",),
        ("description",),
        ("note",),
    ))
    explicit_qualified = _boolish(_first(
        raw,
        ("already_qualified",),
        ("qualified",),
        ("advanced",),
    ))
    explicit_eliminated = _boolish(_first(
        raw,
        ("already_eliminated",),
        ("eliminated",),
    ))
    status = _normalize_status(status_text, explicit_qualified, explicit_eliminated)
    qualification = {
        "team": team,
        "stage": _text(_first(raw, ("stage",), ("group",), ("round",))),
        "status": status,
    }
    if explicit_qualified is not None:
        qualification["already_qualified"] = explicit_qualified
    elif status in {"qualified", "advanced", "knockout_stage"}:
        qualification["already_qualified"] = True
    if explicit_eliminated is not None:
        qualification["already_eliminated"] = explicit_eliminated
    elif status in {"eliminated", "out"}:
        qualification["already_eliminated"] = True
    return _compact(qualification)


def _normalize_status(
    status_text: str,
    already_qualified: bool | None,
    already_eliminated: bool | None,
) -> str:
    text = _clean(status_text).lower()
    if already_qualified is True:
        return "qualified"
    if already_eliminated is True:
        return "eliminated"
    if "not qualified" in text or "not yet qualified" in text:
        return text
    if "not eliminated" in text or "not yet eliminated" in text:
        return text
    if any(token in text for token in ("qualified", "advance", "advanced", "promotion")):
        return "qualified"
    if "knockout" in text or "round of" in text:
        return "knockout_stage"
    if any(token in text for token in ("eliminated", "out")):
        return "eliminated"
    return text


def _team_name(raw: dict[str, Any]) -> str:
    return _text(_first(
        raw,
        ("team",),
        ("team", "name"),
        ("country",),
        ("name",),
    ))


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
