"""Normalize raw World Cup match-source payloads into data-source snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT
from app.services.world_cup_data_source_service import (
    import_world_cup_data,
    world_cup_data_to_facts,
)


def world_cup_match_source_to_data(payload: Any) -> dict[str, Any]:
    """Convert a raw fixture/result payload into the normalized data shape.

    This is a thin adapter boundary for future official/API match feeds. It only
    extracts stable match fields; fact conversion and resolution stay in the
    existing services.
    """

    rows = _match_rows(payload)
    envelope = payload if isinstance(payload, dict) else {}
    matches = [_normalize_match(row, index) for index, row in enumerate(rows)]
    if not matches:
        raise ValueError("match-source payload did not contain matches")
    return {
        "tournament": _clean(envelope.get("tournament")) or WORLD_CUP_TOURNAMENT,
        "source": (
            _clean(envelope.get("source"))
            or _clean(envelope.get("provider"))
            or "world_cup_match_source"
        ),
        "source_url": _clean(envelope.get("source_url") or envelope.get("url")),
        "observed_at": _clean(envelope.get("observed_at")) or _utc_now(),
        "matches": matches,
    }


def preview_world_cup_match_source(payload: Any) -> dict[str, Any]:
    """Preview facts produced from a raw match-source payload."""

    data = world_cup_match_source_to_data(payload)
    facts = world_cup_data_to_facts(data)
    return {
        "normalized_match_count": len(data["matches"]),
        "normalized_data": data,
        "converted_fact_count": len(facts),
        "facts": facts,
    }


def import_world_cup_match_source(
    payload: Any,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Import facts produced from a raw match-source payload."""

    data = world_cup_match_source_to_data(payload)
    result = import_world_cup_data(data, replace=replace)
    result["normalized_match_count"] = len(data["matches"])
    result["normalized_data"] = data
    return result


def _match_rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("match-source payload must be an object or list")

    for key in ("matches", "fixtures", "response", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _nested_rows(value)
            if nested:
                return nested
    if _looks_like_match(payload):
        return [payload]
    raise ValueError("match-source payload did not contain matches")


def _nested_rows(payload: dict[str, Any]) -> list[Any]:
    for key in ("matches", "fixtures", "response", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return [payload] if _looks_like_match(payload) else []


def _looks_like_match(raw: dict[str, Any]) -> bool:
    return bool(_match_id(raw) and _team_name(raw, "home") and _team_name(raw, "away"))


def _normalize_match(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"matches[{index}] must be an object")

    match_id = _match_id(raw)
    if not match_id:
        raise ValueError(f"matches[{index}] missing match id")
    home_team = _team_name(raw, "home")
    away_team = _team_name(raw, "away")
    if not home_team or not away_team:
        raise ValueError(f"matches[{index}] missing home or away team")

    status_text = _text(_first(
        raw,
        ("fixture", "status", "short"),
        ("fixture", "status", "long"),
        ("status", "short"),
        ("status", "long"),
        ("status",),
    ))
    match = {
        "match_id": match_id,
        "stage": _text(_first(raw, ("stage",), ("round",), ("group",), ("league", "round"))),
        "home_team": home_team,
        "away_team": away_team,
        "status": _normalize_status(status_text),
    }

    winner = _winner(raw, home_team, away_team)
    if winner:
        match["winner"] = winner

    home_score = _number(_first(
        raw,
        ("home_score",),
        ("score", "home"),
        ("score", "fullTime", "home"),
        ("score", "fulltime", "home"),
        ("goals", "home"),
        ("result", "home"),
    ))
    away_score = _number(_first(
        raw,
        ("away_score",),
        ("score", "away"),
        ("score", "fullTime", "away"),
        ("score", "fulltime", "away"),
        ("goals", "away"),
        ("result", "away"),
    ))
    if home_score is not None and away_score is not None:
        match["home_score"] = home_score
        match["away_score"] = away_score

    for card in ("red_cards", "yellow_cards"):
        home = _number(_first(
            raw,
            (f"home_{card}",),
            ("cards", "home", card.replace("_cards", "")),
            ("discipline", "home", card),
        ))
        away = _number(_first(
            raw,
            (f"away_{card}",),
            ("cards", "away", card.replace("_cards", "")),
            ("discipline", "away", card),
        ))
        if home is not None:
            match[f"home_{card}"] = home
        if away is not None:
            match[f"away_{card}"] = away

    extra_time = _boolish(_first(raw, ("extra_time",), ("extraTime",)))
    if extra_time is None and status_text.lower() in {"aet", "pen", "penalties"}:
        extra_time = True
    if extra_time is not None:
        match["extra_time"] = extra_time

    penalty_shootout = _boolish(_first(raw, ("penalty_shootout",), ("penalties",)))
    if penalty_shootout is None and (
        status_text.lower() in {"pen", "penalties"}
        or isinstance(_dig(raw, ("score", "penalty")), dict)
        or isinstance(_dig(raw, ("score", "penalties")), dict)
    ):
        penalty_shootout = True
    if penalty_shootout is not None:
        match["penalty_shootout"] = penalty_shootout

    return _compact(match)


def _match_id(raw: dict[str, Any]) -> str:
    return _text(_first(
        raw,
        ("match_id",),
        ("id",),
        ("fixture_id",),
        ("fixture", "id"),
        ("match", "id"),
    ))


def _team_name(raw: dict[str, Any], side: str) -> str:
    camel = f"{side}Team"
    return _text(_first(
        raw,
        (f"{side}_team",),
        (side,),
        (side, "name"),
        ("teams", side, "name"),
        (camel, "name"),
    ))


def _winner(raw: dict[str, Any], home_team: str, away_team: str) -> str:
    explicit_value = _first(
        raw,
        ("winner",),
        ("winner", "name"),
        ("winning_team",),
        ("winningTeam", "name"),
    )
    explicit = "" if isinstance(explicit_value, bool) else _text(explicit_value)
    if explicit:
        return explicit
    home_winner = _boolish(_dig(raw, ("teams", "home", "winner")))
    away_winner = _boolish(_dig(raw, ("teams", "away", "winner")))
    if home_winner is True:
        return home_team
    if away_winner is True:
        return away_team
    return ""


def _normalize_status(value: str) -> str:
    text = _clean(value).lower()
    if not text:
        return "scheduled"
    if text in {"ft", "aet", "pen", "finished", "full time", "after extra time", "penalties"}:
        return "finished"
    if text in {"ns", "scheduled", "not started", "tbd"}:
        return "scheduled"
    if text in {"1h", "2h", "ht", "live", "in progress"}:
        return "live"
    return text


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
