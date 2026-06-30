"""Convert trusted World Cup data-source payloads into structured facts."""

from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from io import StringIO
from typing import Any

from app.core.config import settings
from app.services.sports_fact_service import (
    WORLD_CUP_TOURNAMENT,
    import_sports_facts,
)
from app.utils.file_store import read_json_strict


def world_cup_data_to_facts(payload: Any) -> list[dict[str, Any]]:
    """Normalize a match-data snapshot into PMRF sports facts.

    This is deliberately source-agnostic: official/API adapters should first
    map their response into this small shape, then use the existing fact import
    and deterministic resolution path.
    """

    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    payload = _expand_csv_payload(payload)
    tournament = _clean(payload.get("tournament")) or WORLD_CUP_TOURNAMENT
    source = _clean(payload.get("source")) or "structured_data"
    source_url = _clean(payload.get("source_url") or payload.get("url"))
    observed_at = _clean(payload.get("observed_at"))

    facts: list[dict[str, Any]] = []
    facts.extend(_match_facts(payload.get("matches", []), tournament, source, source_url, observed_at))
    facts.extend(_discipline_facts(payload.get("discipline", []), tournament, source, source_url, observed_at))
    facts.extend(_qualification_facts(payload.get("qualifications", []), tournament, source, source_url, observed_at))
    facts.extend(_player_award_facts(payload.get("player_awards", []), tournament, source, source_url, observed_at))
    facts.extend(_player_status_facts(payload.get("player_statuses", []), tournament, source, source_url, observed_at))
    facts.extend(_team_stat_facts(payload.get("team_stats", []), tournament, source, source_url, observed_at))
    facts.extend(_player_stat_facts(payload.get("player_stats", []), tournament, source, source_url, observed_at))

    status_fact = _tournament_status_fact(payload, tournament, source, source_url, observed_at)
    if status_fact:
        facts.append(status_fact)

    if not facts:
        raise ValueError("payload did not contain convertible World Cup data")
    return facts


def import_world_cup_data(payload: Any, *, replace: bool = False) -> dict[str, Any]:
    """Convert a trusted data-source payload and import the resulting facts."""

    facts = world_cup_data_to_facts(payload)
    result = import_sports_facts(
        {"facts": facts},
        replace=replace,
        default_tournament=WORLD_CUP_TOURNAMENT,
    )
    result["converted_fact_count"] = len(facts)
    return result


def load_world_cup_data_file(path: str | None = None) -> dict[str, Any]:
    """Load the configured trusted World Cup data-source snapshot."""

    source_path = os.path.abspath(path or settings.WORLD_CUP_DATA_FILE)
    if not os.path.exists(source_path):
        raise FileNotFoundError(source_path)
    payload = read_json_strict(source_path, {})
    if not isinstance(payload, dict):
        raise ValueError("World Cup data file must contain a JSON object")
    return payload


def validate_world_cup_data_source_metadata(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_hours: float | None = None,
) -> dict[str, Any]:
    """Validate configured data-source metadata before trusted-file import."""

    source = _clean(payload.get("source"))
    if not source:
        raise ValueError("World Cup data file missing source")
    observed_at_text = _clean(payload.get("observed_at"))
    if not observed_at_text:
        raise ValueError("World Cup data file missing observed_at")

    observed_at = _parse_observed_at(observed_at_text)
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)
    age_seconds = int(max(0.0, (current_time - observed_at).total_seconds()))
    age_limit = (
        settings.WORLD_CUP_DATA_MAX_AGE_HOURS
        if max_age_hours is None
        else max_age_hours
    )
    if age_limit > 0 and age_seconds > age_limit * 3600:
        raise ValueError(
            f"World Cup data file is stale: observed_at older than {age_limit:g} hours"
        )

    return {
        "source": source,
        "source_url": _clean(payload.get("source_url") or payload.get("url")),
        "observed_at": _format_utc(observed_at),
        "age_seconds": age_seconds,
        "max_age_hours": age_limit,
    }


def preview_world_cup_data_file(path: str | None = None) -> dict[str, Any]:
    """Preview facts from the configured data-source file with metadata checks."""

    source_path = os.path.abspath(path or settings.WORLD_CUP_DATA_FILE)
    payload = load_world_cup_data_file(source_path)
    metadata = validate_world_cup_data_source_metadata(payload)
    facts = world_cup_data_to_facts(payload)
    return {
        "source_file": source_path,
        "source_metadata": metadata,
        "converted_fact_count": len(facts),
        "facts": facts,
    }


def world_cup_data_file_to_facts(path: str | None = None) -> list[dict[str, Any]]:
    """Convert the configured data-source snapshot into facts without writing."""

    return preview_world_cup_data_file(path)["facts"]


def _parse_observed_at(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("observed_at must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observed_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def import_world_cup_data_file(
    *,
    replace: bool = False,
    path: str | None = None,
) -> dict[str, Any]:
    """Import facts from the configured trusted World Cup data-source file."""

    source_path = os.path.abspath(path or settings.WORLD_CUP_DATA_FILE)
    payload = load_world_cup_data_file(source_path)
    metadata = validate_world_cup_data_source_metadata(payload)
    result = import_world_cup_data(payload, replace=replace)
    result["source_file"] = source_path
    result["source_metadata"] = metadata
    return result


def _expand_csv_payload(payload: dict[str, Any]) -> dict[str, Any]:
    csv_payload = payload.get("csv")
    if csv_payload is None:
        return payload
    if not isinstance(csv_payload, dict):
        raise ValueError("csv must be an object")

    expanded = dict(payload)
    for section in (
        "matches",
        "discipline",
        "qualifications",
        "player_awards",
        "player_statuses",
        "team_stats",
        "player_stats",
    ):
        csv_text = csv_payload.get(section)
        if csv_text in (None, ""):
            continue
        if expanded.get(section):
            raise ValueError(f"provide either {section} or csv.{section}, not both")
        expanded[section] = _csv_rows(csv_text, f"csv.{section}")
    expanded.pop("csv", None)
    return expanded


def _csv_rows(csv_text: Any, name: str) -> list[dict[str, str]]:
    if not isinstance(csv_text, str):
        raise ValueError(f"{name} must be a string")
    reader = csv.DictReader(StringIO(csv_text.strip()))
    if not reader.fieldnames:
        raise ValueError(f"{name} must include a header row")
    rows = [
        {str(key).strip(): str(value or "").strip() for key, value in row.items() if key}
        for row in reader
    ]
    if not rows:
        raise ValueError(f"{name} must include at least one data row")
    return rows


def _match_facts(
    matches: Any,
    tournament: str,
    source: str,
    source_url: str,
    observed_at: str,
) -> list[dict[str, Any]]:
    rows = _require_list(matches, "matches")
    facts: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"matches[{index}] must be an object")
        match_id = _clean(raw.get("match_id") or raw.get("id"))
        if not match_id:
            raise ValueError(f"matches[{index}] missing match_id")
        fact = _base_fact(
            fact_id=f"wc2026:match:{match_id}",
            kind="match_result",
            tournament=tournament,
            source=source,
            source_url=source_url,
            observed_at=observed_at,
        )
        fact.update({
            "match_id": match_id,
            "stage": _clean(raw.get("stage")),
            "kickoff_at": _clean(raw.get("kickoff_at")),
            "venue": _clean(raw.get("venue")),
            "referee": _clean(raw.get("referee")),
            "home_team": _clean(raw.get("home_team") or raw.get("home")),
            "away_team": _clean(raw.get("away_team") or raw.get("away")),
            "winner": _clean(raw.get("winner")),
            "status": _clean(raw.get("status")).lower() or "scheduled",
        })
        score = _score(raw)
        if score:
            fact["score"] = score
        red_cards = _card_total(raw, "red_cards")
        if red_cards is not None:
            fact["red_cards"] = red_cards
        yellow_cards = _card_total(raw, "yellow_cards")
        if yellow_cards is not None:
            fact["yellow_cards"] = yellow_cards
        for field in ("extra_time", "penalty_shootout"):
            if _has_value(raw.get(field)):
                fact[field] = _bool(raw.get(field), field)
        facts.append(_compact(fact))
    return facts


def _discipline_facts(
    discipline: Any,
    tournament: str,
    source: str,
    source_url: str,
    observed_at: str,
) -> list[dict[str, Any]]:
    rows = _require_list(discipline, "discipline")
    facts: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"discipline[{index}] must be an object")
        red_cards = _number(raw.get("red_cards"))
        yellow_cards = _number(raw.get("yellow_cards"))
        if red_cards is None and yellow_cards is None:
            raise ValueError(f"discipline[{index}] missing card counts")
        match_id = _clean(raw.get("match_id") or raw.get("fixture_id"))
        team = _clean(raw.get("team"))
        player = _clean(raw.get("player"))
        status = _clean(raw.get("status") or raw.get("card") or raw.get("detail")).lower()
        suffix = _slug(
            raw.get("event_id")
            or raw.get("id")
            or ":".join([
                match_id,
                team,
                player,
                _clean(raw.get("minute")),
                status,
                str(index),
            ])
        )
        fact = _base_fact(
            fact_id=_clean(raw.get("fact_id")) or f"wc2026:discipline:{suffix}",
            kind="discipline",
            tournament=tournament,
            source=source,
            source_url=source_url,
            observed_at=observed_at,
        )
        fact.update({
            "match_id": match_id,
            "team": team,
            "player": player,
            "stage": _clean(raw.get("stage")),
            "status": status or "reported",
            "minute": _clean(raw.get("minute")),
            "notes": _clean(raw.get("notes") or raw.get("reason") or raw.get("detail")),
            "applies_to": _clean_list(raw.get("applies_to")),
        })
        if red_cards is not None:
            fact["red_cards"] = red_cards
        if yellow_cards is not None:
            fact["yellow_cards"] = yellow_cards
        facts.append(_compact(fact))
    return facts


def _qualification_facts(
    qualifications: Any,
    tournament: str,
    source: str,
    source_url: str,
    observed_at: str,
) -> list[dict[str, Any]]:
    rows = _require_list(qualifications, "qualifications")
    facts: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"qualifications[{index}] must be an object")
        team = _clean(raw.get("team"))
        if not team:
            raise ValueError(f"qualifications[{index}] missing team")
        status = _clean(raw.get("status")).lower()
        fact = _base_fact(
            fact_id=f"wc2026:qualification:{_slug(team)}:{status or 'status'}",
            kind="qualification",
            tournament=tournament,
            source=source,
            source_url=source_url,
            observed_at=observed_at,
        )
        fact.update({
            "team": team,
            "stage": _clean(raw.get("stage")),
            "status": status,
        })
        for opt in ("group",):
            value = _clean(raw.get(opt))
            if value:
                fact[opt] = value
        for field in ("already_qualified", "already_eliminated"):
            if _has_value(raw.get(field)):
                fact[field] = _bool(raw.get(field), field)
        for field in ("rank", "played", "won", "drawn", "lost", "points",
                      "goals_for", "goals_against", "goal_diff"):
            number = _number(raw.get(field))
            if number is not None:
                fact[field] = number
        facts.append(_compact(fact))
    return facts


def _player_award_facts(
    awards: Any,
    tournament: str,
    source: str,
    source_url: str,
    observed_at: str,
) -> list[dict[str, Any]]:
    rows = _require_list(awards, "player_awards")
    facts: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"player_awards[{index}] must be an object")
        award = _clean(raw.get("award") or raw.get("name")).lower()
        player = _clean(raw.get("player"))
        if not award:
            raise ValueError(f"player_awards[{index}] missing award")
        fact = _base_fact(
            fact_id=f"wc2026:award:{_slug(award)}:{_slug(player) or index}",
            kind="player_award",
            tournament=tournament,
            source=source,
            source_url=source_url,
            observed_at=observed_at,
        )
        fact.update({
            "award": award,
            "player": player,
            "team": _clean(raw.get("team")),
            "status": _clean(raw.get("status")).lower() or "current",
        })
        for field in ("goals", "rank"):
            if raw.get(field) is not None:
                fact[field] = raw.get(field)
        facts.append(_compact(fact))
    return facts


def _player_status_facts(
    statuses: Any,
    tournament: str,
    source: str,
    source_url: str,
    observed_at: str,
) -> list[dict[str, Any]]:
    rows = _require_list(statuses, "player_statuses")
    facts: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"player_statuses[{index}] must be an object")
        player = _clean(raw.get("player") or raw.get("name"))
        team = _clean(raw.get("team"))
        if not player:
            raise ValueError(f"player_statuses[{index}] missing player")
        if not team:
            raise ValueError(f"player_statuses[{index}] missing team")
        kind = _player_status_kind(raw)
        status = _player_status(raw, kind)
        match_id = _clean(raw.get("match_id") or raw.get("fixture_id"))
        fact = _base_fact(
            fact_id=(
                f"wc2026:player-status:{kind}:{_slug(team)}:{_slug(player)}:"
                f"{_slug(match_id or status or index)}"
            ),
            kind=kind,
            tournament=tournament,
            source=source,
            source_url=source_url,
            observed_at=observed_at,
        )
        fact.update({
            "team": team,
            "player": player,
            "status": status,
            "severity": _clean(raw.get("severity")).lower(),
            "match_id": match_id,
            "stage": _clean(raw.get("stage")),
            "position": _clean(raw.get("position")),
            "formation": _clean(raw.get("formation")),
            "jersey_number": _clean(raw.get("jersey_number") or raw.get("number")),
            "notes": _clean(raw.get("notes") or raw.get("reason") or raw.get("description")),
            "applies_to": _clean_list(raw.get("applies_to")),
        })
        facts.append(_compact(fact))
    return facts


def _team_stat_facts(
    stats: Any,
    tournament: str,
    source: str,
    source_url: str,
    observed_at: str,
) -> list[dict[str, Any]]:
    rows = _require_list(stats, "team_stats")
    facts: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"team_stats[{index}] must be an object")
        team = _clean(raw.get("team"))
        stat_name = _clean(raw.get("stat_name") or raw.get("name") or raw.get("type")).lower()
        raw_value = raw.get("stat_value") if raw.get("stat_value") is not None else raw.get("value")
        stat_value = _number(raw_value)
        if not team:
            raise ValueError(f"team_stats[{index}] missing team")
        if not stat_name:
            raise ValueError(f"team_stats[{index}] missing stat_name")
        if stat_value is None:
            raise ValueError(f"team_stats[{index}] missing stat_value")
        match_id = _clean(raw.get("match_id") or raw.get("fixture_id"))
        fact = _base_fact(
            fact_id=(
                f"wc2026:team-stat:{_slug(team)}:{_slug(match_id or 'tournament')}:"
                f"{_slug(stat_name)}"
            ),
            kind="team_stat",
            tournament=tournament,
            source=source,
            source_url=source_url,
            observed_at=observed_at,
        )
        fact.update({
            "team": team,
            "match_id": match_id,
            "stage": _clean(raw.get("stage")),
            "stat_name": stat_name,
            "stat_value": stat_value,
            "stat_unit": _clean(raw.get("stat_unit") or raw.get("unit")),
            "notes": _clean(raw.get("notes") or raw.get("reason") or raw.get("description")),
        })
        facts.append(_compact(fact))
    return facts


def _player_stat_facts(
    stats: Any,
    tournament: str,
    source: str,
    source_url: str,
    observed_at: str,
) -> list[dict[str, Any]]:
    rows = _require_list(stats, "player_stats")
    facts: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"player_stats[{index}] must be an object")
        player = _clean(raw.get("player") or raw.get("name"))
        stat_name = _clean(raw.get("stat_name") or raw.get("name") or raw.get("type")).lower()
        raw_value = raw.get("stat_value") if raw.get("stat_value") is not None else raw.get("value")
        stat_value = _number(raw_value)
        if not player:
            raise ValueError(f"player_stats[{index}] missing player")
        if not stat_name:
            raise ValueError(f"player_stats[{index}] missing stat_name")
        if stat_value is None:
            raise ValueError(f"player_stats[{index}] missing stat_value")
        team = _clean(raw.get("team"))
        match_id = _clean(raw.get("match_id") or raw.get("fixture_id"))
        fact = _base_fact(
            fact_id=(
                f"wc2026:player-stat:{_slug(team)}:{_slug(player)}:"
                f"{_slug(match_id or 'tournament')}:{_slug(stat_name)}"
            ),
            kind="player_stat",
            tournament=tournament,
            source=source,
            source_url=source_url,
            observed_at=observed_at,
        )
        fact.update({
            "team": team,
            "player": player,
            "match_id": match_id,
            "stage": _clean(raw.get("stage")),
            "position": _clean(raw.get("position")),
            "jersey_number": _clean(raw.get("jersey_number") or raw.get("number")),
            "stat_name": stat_name,
            "stat_value": stat_value,
            "stat_unit": _clean(raw.get("stat_unit") or raw.get("unit")),
            "notes": _clean(raw.get("notes") or raw.get("reason") or raw.get("description")),
        })
        facts.append(_compact(fact))
    return facts


def _tournament_status_fact(
    payload: dict[str, Any],
    tournament: str,
    source: str,
    source_url: str,
    observed_at: str,
) -> dict[str, Any] | None:
    raw = payload.get("tournament_status")
    if raw is None and payload.get("tournament_complete") is None:
        return None
    if raw is not None and not isinstance(raw, dict):
        raise ValueError("tournament_status must be an object")
    raw = raw or {}
    complete = raw.get("tournament_complete", payload.get("tournament_complete"))
    status = _clean(raw.get("status")).lower()
    if not status:
        status = "complete" if complete is True else "in_progress"
    fact = _base_fact(
        fact_id="wc2026:tournament:status",
        kind="tournament_status",
        tournament=tournament,
        source=source,
        source_url=source_url,
        observed_at=observed_at,
    )
    fact["status"] = status
    if _has_value(complete):
        fact["tournament_complete"] = _bool(complete, "tournament_complete")
    return _compact(fact)


def _player_status_kind(raw: dict[str, Any]) -> str:
    kind = _clean(raw.get("kind") or raw.get("type") or raw.get("category")).lower()
    aliases = {
        "injuries": "injury",
        "injury_status": "injury",
        "availability_status": "availability",
        "available": "availability",
        "suspensions": "suspension",
        "suspended": "suspension",
        "lineups": "lineup",
        "starter": "lineup",
    }
    kind = aliases.get(kind, kind)
    if kind in {"injury", "availability", "suspension", "lineup"}:
        return kind

    status = _clean(raw.get("status") or raw.get("availability") or raw.get("state")).lower()
    if status in {"suspended", "banned"}:
        return "suspension"
    if status in {"starting", "starter", "bench", "substitute"}:
        return "lineup"
    if status in {"out", "injured", "doubtful", "questionable"} or raw.get("injury"):
        return "injury"
    if status:
        return "availability"
    raise ValueError("player_statuses kind must be injury, availability, suspension, or lineup")


def _player_status(raw: dict[str, Any], kind: str) -> str:
    status = _clean(raw.get("status") or raw.get("availability") or raw.get("state")).lower()
    if status:
        return status
    if kind == "injury":
        return "injured"
    if kind == "suspension":
        return "suspended"
    if kind == "lineup":
        return "listed"
    return "unknown"


def _base_fact(
    *,
    fact_id: str,
    kind: str,
    tournament: str,
    source: str,
    source_url: str,
    observed_at: str,
) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "kind": kind,
        "tournament": tournament,
        "source": source,
        "source_url": source_url,
        "observed_at": observed_at,
        "confidence": 1.0,
    }


def _score(raw: dict[str, Any]) -> dict[str, Any]:
    score = raw.get("score")
    if isinstance(score, dict):
        return score
    home = raw.get("home_score")
    away = raw.get("away_score")
    if home is None or away is None:
        return {}
    return {"home": home, "away": away}


def _card_total(raw: dict[str, Any], field: str) -> float | None:
    total = _number(raw.get(field))
    if total is not None:
        return total
    home = _number(raw.get(f"home_{field}"))
    away = _number(raw.get(f"away_{field}"))
    if home is None and away is None:
        return None
    return float(home or 0.0) + float(away or 0.0)


def _require_list(value: Any, name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return value


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    text = _clean(value).lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"{field} must be a boolean")


def _has_value(value: Any) -> bool:
    return value is not None and _clean(value) != ""


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clean(item) for item in value if _clean(item)]


def _compact(fact: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in fact.items() if value not in ("", [], None, {})}


def _slug(value: Any) -> str:
    return "-".join(_clean(value).lower().replace("_", " ").split())


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())
