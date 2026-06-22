"""Convert trusted World Cup data-source payloads into structured facts."""

from __future__ import annotations

import csv
import os
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
    facts.extend(_qualification_facts(payload.get("qualifications", []), tournament, source, source_url, observed_at))
    facts.extend(_player_award_facts(payload.get("player_awards", []), tournament, source, source_url, observed_at))

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


def world_cup_data_file_to_facts(path: str | None = None) -> list[dict[str, Any]]:
    """Convert the configured data-source snapshot into facts without writing."""

    return world_cup_data_to_facts(load_world_cup_data_file(path))


def import_world_cup_data_file(
    *,
    replace: bool = False,
    path: str | None = None,
) -> dict[str, Any]:
    """Import facts from the configured trusted World Cup data-source file."""

    source_path = os.path.abspath(path or settings.WORLD_CUP_DATA_FILE)
    result = import_world_cup_data(load_world_cup_data_file(source_path), replace=replace)
    result["source_file"] = source_path
    return result


def _expand_csv_payload(payload: dict[str, Any]) -> dict[str, Any]:
    csv_payload = payload.get("csv")
    if csv_payload is None:
        return payload
    if not isinstance(csv_payload, dict):
        raise ValueError("csv must be an object")

    expanded = dict(payload)
    for section in ("matches", "qualifications", "player_awards"):
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
        for field in ("already_qualified", "already_eliminated"):
            if _has_value(raw.get(field)):
                fact[field] = _bool(raw.get(field), field)
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


def _compact(fact: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in fact.items() if value not in ("", [], None, {})}


def _slug(value: Any) -> str:
    return "-".join(_clean(value).lower().replace("_", " ").split())


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())
