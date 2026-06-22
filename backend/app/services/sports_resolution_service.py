"""Deterministic settlement for curated World Cup sports events."""

from __future__ import annotations

import re
from typing import Any

from app.memory.event_store import list_all_events
from app.services.event_resolve_service import resolve_with_calibration
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts


async def resolve_world_cup_events(
    *,
    dry_run: bool = True,
    limit: int = 200,
) -> dict[str, Any]:
    """Resolve World Cup sports events when structured facts are decisive."""

    facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)
    entries = _world_cup_entries()[:limit]
    matches: list[dict[str, Any]] = []
    resolved_count = 0
    pending_count = 0

    for entry in entries:
        record = entry.get("record") or {}
        if record.get("outcome") is not None:
            continue
        decision = evaluate_world_cup_resolution(record, facts)
        if decision is None:
            pending_count += 1
            continue

        event_id = entry.get("event_id", "")
        matches.append({
            "event_id": event_id,
            "event_title": str(record.get("event_title", ""))[:120],
            "actual_outcome": decision["actual_outcome"],
            "confidence": decision["confidence"],
            "reason": decision["reason"],
            "facts": decision["facts"],
            "result": "would_resolve" if dry_run else "resolved",
        })
        resolved_count += 1
        if dry_run:
            continue
        await resolve_with_calibration(
            event_id=event_id,
            actual_outcome=decision["actual_outcome"],
            confidence=decision["confidence"],
            source="auto_sports",
            notes=decision["reason"],
        )

    final_entries = entries if dry_run else _world_cup_entries()
    return {
        "status": "ok",
        "dry_run": dry_run,
        "resolved_count": resolved_count,
        "pending_count": pending_count,
        "checked_count": len(entries),
        "unresolved_events": sum(
            1 for entry in final_entries
            if ((entry.get("record") or {}).get("outcome") is None)
        ),
        "matches": matches,
    }


def evaluate_world_cup_resolution(
    record: dict[str, Any],
    facts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    source = record.get("source") or {}
    if source.get("type") != "sports_event":
        return None
    if source.get("tournament") != WORLD_CUP_TOURNAMENT:
        return None

    category = str(source.get("category") or "")
    if category == "team_progression":
        return _team_progression_resolution(record, facts)
    if category == "discipline":
        return _red_card_resolution(record, facts)
    if category == "match_format":
        return _match_format_resolution(record, facts)
    return None


def _world_cup_entries() -> list[dict[str, Any]]:
    entries = []
    for entry in list_all_events():
        source = ((entry.get("record") or {}).get("source") or {})
        if source.get("type") == "sports_event" and source.get("tournament") == WORLD_CUP_TOURNAMENT:
            entries.append(entry)
    return entries


def _team_progression_resolution(
    record: dict[str, Any],
    facts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    source = record.get("source") or {}
    required_stage = _required_progression_stage(source, record.get("event_title", ""))
    if not required_stage:
        return None
    team = _primary_team(source)
    if not team:
        return None
    relevant = [
        fact for fact in facts
        if fact.get("kind") == "qualification"
        and _same_text(fact.get("team"), team)
    ]
    for fact in relevant:
        status = str(fact.get("status") or "").lower()
        stage = str(fact.get("stage") or "").lower()
        if _progression_yes(required_stage, status, stage, fact):
            return _decision(
                100.0,
                f"{team} reached {required_stage}.",
                [fact],
            )
        if _progression_no(status, fact):
            return _decision(
                0.0,
                f"{team} was eliminated before reaching {required_stage}.",
                [fact],
            )
    return None


def _red_card_resolution(
    record: dict[str, Any],
    facts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    threshold = _parse_threshold(record.get("event_title", ""), "red cards")
    if threshold is None:
        return None
    relevant = [
        fact for fact in facts
        if fact.get("kind") in {"discipline", "match_state", "match_result", "tournament_status"}
    ]
    red_total = sum(float(fact.get("red_cards", 0.0)) for fact in relevant)
    if red_total >= threshold:
        return _decision(
            100.0,
            f"Official facts record {red_total:g} red cards, meeting the {threshold:g} threshold.",
            relevant,
        )
    if _tournament_complete(relevant):
        return _decision(
            0.0,
            f"Tournament complete with {red_total:g} red cards, below the {threshold:g} threshold.",
            relevant,
        )
    return None


def _match_format_resolution(
    record: dict[str, Any],
    facts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    title = _norm(record.get("event_title", ""))
    source_id = str((record.get("source") or {}).get("source_id") or "")
    relevant = [
        fact for fact in facts
        if fact.get("kind") in {"match_state", "match_result", "tournament_status"}
    ]
    if "penalty-shootout" in source_id or "penalty shootout" in title:
        shootout = [fact for fact in relevant if fact.get("penalty_shootout") is True]
        if shootout:
            return _decision(100.0, "At least one knockout match had a penalty shootout.", shootout)
        if _tournament_complete(relevant):
            return _decision(0.0, "Tournament complete with no penalty shootout fact recorded.", relevant)
        return None

    if "final-extra-time" in source_id or "final go to extra time" in title:
        final_facts = [
            fact for fact in relevant
            if str(fact.get("stage") or "").lower() == "final"
        ]
        for fact in final_facts:
            if fact.get("extra_time") is True:
                return _decision(100.0, "The World Cup final went to extra time.", [fact])
            if _is_finished(fact):
                return _decision(0.0, "The World Cup final finished without extra time.", [fact])
    return None


def _required_progression_stage(source: dict[str, Any], title: str) -> str:
    source_id = str(source.get("source_id") or "")
    text = _norm(f"{source_id} {title}")
    if "semifinal" in text:
        return "semifinal"
    if "knockout" in text:
        return "knockout_stage"
    return ""


def _primary_team(source: dict[str, Any]) -> str:
    for entity in source.get("entities") or []:
        text = str(entity or "").strip()
        if text and text != WORLD_CUP_TOURNAMENT:
            return text
    return ""


def _progression_yes(
    required_stage: str,
    status: str,
    stage: str,
    fact: dict[str, Any],
) -> bool:
    if required_stage == "knockout_stage":
        return (
            fact.get("already_qualified") is True
            or status in {"qualified", "advanced", "knockout_stage", "reached_knockout"}
            or stage in {"knockout", "round_of_32", "round_of_16", "quarterfinal", "semifinal", "final"}
        )
    if required_stage == "semifinal":
        return status in {"semifinal", "reached_semifinal", "qualified_semifinal"} or stage in {"semifinal", "final"}
    return False


def _progression_no(status: str, fact: dict[str, Any]) -> bool:
    return fact.get("already_eliminated") is True or status in {"eliminated", "out"}


def _parse_threshold(text: str, phrase: str) -> float | None:
    match = re.search(rf"at least\s+(\d+(?:\.\d+)?)\s+{re.escape(phrase)}", _norm(text))
    if not match:
        return None
    return float(match.group(1))


def _tournament_complete(facts: list[dict[str, Any]]) -> bool:
    return any(
        fact.get("tournament_complete") is True
        or (
            fact.get("kind") == "tournament_status"
            and str(fact.get("status") or "").lower() in {"complete", "completed", "finished"}
        )
        for fact in facts
    )


def _is_finished(fact: dict[str, Any]) -> bool:
    return str(fact.get("status") or "").lower() in {"finished", "complete", "completed"}


def _decision(
    actual_outcome: float,
    reason: str,
    facts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "actual_outcome": actual_outcome,
        "confidence": _min_confidence(facts),
        "reason": reason,
        "facts": [fact.get("fact_id", "") for fact in facts if fact.get("fact_id")],
    }


def _min_confidence(facts: list[dict[str, Any]]) -> float:
    values = []
    for fact in facts:
        try:
            values.append(float(fact.get("confidence", 1.0)))
        except (TypeError, ValueError):
            values.append(0.0)
    if not values:
        return 0.0
    return round(max(0.0, min(1.0, min(values))), 3)


def _same_text(left: Any, right: Any) -> bool:
    return _norm(left) == _norm(right)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())
