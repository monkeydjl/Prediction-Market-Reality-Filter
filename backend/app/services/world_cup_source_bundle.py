"""Combine multiple World Cup data-source payloads into one fact batch."""

from __future__ import annotations

from typing import Any

from app.services.sports_fact_service import (
    WORLD_CUP_TOURNAMENT,
    import_sports_facts,
)
from app.services.world_cup_data_source_service import world_cup_data_to_facts
from app.services.world_cup_match_source import world_cup_match_source_to_data
from app.services.world_cup_player_awards_source import (
    world_cup_player_awards_source_to_data,
)
from app.services.world_cup_player_status_source import (
    world_cup_player_status_source_to_data,
)
from app.services.world_cup_standings_source import world_cup_standings_source_to_data


def preview_world_cup_source_bundle(payload: Any) -> dict[str, Any]:
    """Preview facts produced by a bundle of World Cup data sources."""

    sources, facts = _convert_bundle(payload)
    return {
        "source_count": len(sources),
        "converted_fact_count": len(facts),
        "sources": sources,
        "facts": facts,
    }


def import_world_cup_source_bundle(
    payload: Any,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Import facts produced by a bundle of World Cup data sources."""

    sources, facts = _convert_bundle(payload)
    result = import_sports_facts(
        {"facts": facts},
        replace=replace,
        default_tournament=WORLD_CUP_TOURNAMENT,
    )
    result["source_count"] = len(sources)
    result["converted_fact_count"] = len(facts)
    result["sources"] = sources
    return result


def _convert_bundle(payload: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries = _source_entries(payload)
    sources: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        kind = _source_kind(entry, index)
        source_payload = _source_payload(entry, index)
        source_payload = _with_entry_metadata(source_payload, entry)
        try:
            normalized_data = _source_to_data(kind, source_payload)
            source_facts = world_cup_data_to_facts(normalized_data)
        except ValueError as exc:
            raise ValueError(f"sources[{index}] {kind}: {exc}") from exc
        sources.append({
            "index": index,
            "kind": kind,
            "converted_fact_count": len(source_facts),
            "normalized_data": normalized_data,
        })
        facts.extend(source_facts)
    return sources, facts


def _source_entries(payload: Any) -> list[Any]:
    if not isinstance(payload, dict):
        raise ValueError("source bundle payload must be an object")
    sources = payload.get("sources")
    if not isinstance(sources, list):
        raise ValueError("source bundle must include a sources list")
    if not sources:
        raise ValueError("source bundle must include at least one source")
    return sources


def _source_kind(entry: Any, index: int) -> str:
    if not isinstance(entry, dict):
        raise ValueError(f"sources[{index}] must be an object")
    raw = _clean(entry.get("kind") or entry.get("type") or entry.get("source_type"))
    kind = raw.lower().replace("-", "_")
    aliases = {
        "normalized": "data",
        "world_cup_data": "data",
        "match": "matches",
        "fixtures": "matches",
        "fixture": "matches",
        "standings": "standings",
        "qualification": "standings",
        "qualifications": "standings",
        "awards": "player_awards",
        "player_award": "player_awards",
        "top_scorers": "player_awards",
        "topscorers": "player_awards",
        "statuses": "player_status",
        "player_statuses": "player_status",
        "injuries": "player_status",
        "availability": "player_status",
        "suspensions": "player_status",
        "lineups": "player_status",
    }
    kind = aliases.get(kind, kind)
    if kind not in {"data", "matches", "standings", "player_awards", "player_status"}:
        raise ValueError(f"sources[{index}] unsupported source kind '{raw}'")
    return kind


def _source_payload(entry: dict[str, Any], index: int) -> Any:
    if "payload" not in entry:
        raise ValueError(f"sources[{index}] missing payload")
    return entry["payload"]


def _source_to_data(kind: str, payload: Any) -> dict[str, Any]:
    if kind == "data":
        if not isinstance(payload, dict):
            raise ValueError("normalized data payload must be an object")
        return payload
    if kind == "matches":
        return world_cup_match_source_to_data(payload)
    if kind == "standings":
        return world_cup_standings_source_to_data(payload)
    if kind == "player_awards":
        return world_cup_player_awards_source_to_data(payload)
    return world_cup_player_status_source_to_data(payload)


def _with_entry_metadata(payload: Any, entry: dict[str, Any]) -> Any:
    if not isinstance(payload, dict):
        return payload
    merged = dict(payload)
    for field in ("tournament", "source", "source_url", "observed_at"):
        value = _clean(entry.get(field))
        if not value:
            continue
        if _has_metadata(merged, field):
            continue
        merged[field] = value
    return merged


def _has_metadata(payload: dict[str, Any], field: str) -> bool:
    if field == "source":
        return bool(_clean(payload.get("source") or payload.get("provider")))
    if field == "source_url":
        return bool(_clean(payload.get("source_url") or payload.get("url")))
    return bool(_clean(payload.get(field)))


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())
