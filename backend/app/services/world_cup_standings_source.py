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
    source = (
        _clean(envelope.get("source"))
        or _clean(envelope.get("provider"))
        or "world_cup_standings_source"
    )
    rows = _standing_rows(payload, source=source)
    qualifications = [
        _normalize_standing(row, index)
        for index, row in enumerate(rows)
    ]
    if not qualifications:
        raise ValueError("standings-source payload did not contain standings")
    return {
        "tournament": _clean(envelope.get("tournament")) or WORLD_CUP_TOURNAMENT,
        "source": source,
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

    _require_trusted_import_metadata(payload)
    data = world_cup_standings_source_to_data(payload)
    result = import_world_cup_data(data, replace=replace)
    result["normalized_qualification_count"] = len(data["qualifications"])
    result["normalized_data"] = data
    return result


def _require_trusted_import_metadata(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ValueError("standings-source import requires source_url and observed_at metadata")
    source_url = _clean(payload.get("source_url") or payload.get("url"))
    observed_at = _clean(payload.get("observed_at"))
    if not source_url:
        raise ValueError("standings-source import requires source_url")
    if not source_url.lower().startswith(("https://", "http://")):
        raise ValueError("standings-source import source_url must be an http(s) URL")
    if not observed_at:
        raise ValueError("standings-source import requires observed_at")


def _standing_rows(payload: Any, stage: str = "", source: str = "") -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows: list[dict[str, Any]] = []
        for item in payload:
            rows.extend(_standing_rows(item, stage, source))
        return rows
    if not isinstance(payload, dict):
        raise ValueError("standings-source payload must be an object or list")

    if _looks_like_standing(payload):
        row = dict(payload)
        if stage and not _first(row, ("stage",), ("group",)):
            row["stage"] = stage
        return [row]

    table = payload.get("table")
    if isinstance(table, list):
        return _table_rows(table, payload, stage, source)

    rows = []  # same list type as the early-return branch above; declared there
    group_map = payload.get("groups")
    if isinstance(group_map, dict):
        for group_name, group_rows in group_map.items():
            rows.extend(_standing_rows(group_rows, _clean(group_name), source))
    elif isinstance(group_map, list):
        rows.extend(_standing_rows(group_map, stage, source))

    league_standings = _dig(payload, ("league", "standings"))
    if isinstance(league_standings, list):
        rows.extend(_standing_rows(league_standings, stage, source))

    for key in ("standings", "tables", "response", "data"):
        value = payload.get(key)
        if value is None:
            continue
        rows.extend(_standing_rows(value, stage, source))
    return rows


def _table_rows(
    table: list[Any],
    parent: dict[str, Any],
    stage: str,
    source: str,
) -> list[dict[str, Any]]:
    group = _text(_first(parent, ("group",), ("group_name",))) or stage
    completed_football_data_group = (
        source == "football_data"
        and len(table) >= 4
        and all(_first_number(row, "played") == 3 for row in table if isinstance(row, dict))
        and all(isinstance(row, dict) for row in table)
    )

    rows: list[dict[str, Any]] = []
    for item in table:
        rows.extend(_standing_rows(
            _table_row(item, group, completed_football_data_group),
            group,
            source,
        ))
    return rows


def _table_row(
    item: Any,
    group: str,
    completed_football_data_group: bool,
) -> Any:
    if not isinstance(item, dict):
        return item

    row = dict(item)
    if group and not _first(row, ("group",), ("stage",)):
        row["group"] = group
        row["stage"] = group
    if completed_football_data_group:
        rank = _first_number(row, "rank")
        if rank is not None and rank <= 2:
            row["already_qualified"] = True
        elif rank is not None:
            row["already_eliminated"] = True
    return row


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
    # Heterogeneous by design: the numeric table columns and the two boolean
    # flags below join the string fields, so this is not a dict[str, str].
    qualification: dict[str, Any] = {
        "team": team,
        "stage": _text(_first(raw, ("stage",), ("group",), ("round",))),
        "status": status,
    }
    group_value = _text(_first(raw, ("group",), ("group_name",)))
    if group_value:
        qualification["group"] = group_value
    elif stage := qualification.get("stage", ""):
        # If a group wrapper was used as the stage label, mirror it into group.
        normalized_stage = stage.strip().upper()
        if normalized_stage.startswith("GROUP") and normalized_stage != stage.strip():
            qualification["group"] = stage.strip()
    for field in ("rank", "played", "won", "drawn", "lost", "points",
                  "goals_for", "goals_against", "goal_diff"):
        value = _first_number(raw, field)
        if value is not None:
            qualification[field] = value
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


def _first_number(raw: dict[str, Any], field: str) -> float | None:
    """Return the first numeric value found under any alias of ``field``."""

    aliases: tuple[str, ...] = (field,)
    short = {
        "goals_for": ("gf",),
        "goals_against": ("ga",),
        "goal_diff": ("gd", "difference", "goaldifference"),
        "played": ("p", "mp", "matches", "games", "playedgames"),
        "won": ("w", "wins"),
        "drawn": ("d", "draw", "draws"),
        "lost": ("l", "losses"),
        "points": ("pts",),
        "rank": ("pos", "position", "ranking"),
    }.get(field, ())
    camel = {
        "goals_for": ("goalsfor",),
        "goals_against": ("goalsagainst",),
    }.get(field, ())
    lowered = {str(k).lower(): v for k, v in raw.items()}
    for alias in aliases + short + camel:
        if alias in lowered:
            return _number(lowered[alias])
    return None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value not in ("", [], None, {})}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())
