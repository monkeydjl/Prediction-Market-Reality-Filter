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
    if category in {"team_progression", "tournament_winner"}:
        return _team_progression_resolution(record, facts)
    if category == "group_stage":
        return _group_stage_resolution(record, facts)
    if category == "discipline":
        return _red_card_resolution(record, facts)
    if category == "match_format":
        result = _match_format_resolution(record, facts)
        if result:
            return result
    if category == "player_awards":
        return _player_award_resolution(record, facts)
    if category == "tournament_totals":
        return _total_goals_resolution(record, facts)
    result = _total_goals_resolution(record, facts)
    if result:
        return result
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
    teams = _candidate_teams(source)
    if not teams:
        return None
    multi = len(teams) > 1
    eliminated_count = 0
    for team in teams:
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
                if not multi:
                    return _decision(
                        0.0,
                        f"{team} was eliminated before reaching {required_stage}.",
                        [fact],
                    )
                eliminated_count += 1
    if multi and eliminated_count == len(teams):
        return _decision(
            0.0,
            f"All candidate teams eliminated before reaching {required_stage}.",
            [fact for fact in facts if fact.get("kind") == "qualification"
             and any(_same_text(fact.get("team"), t) for t in teams)],
        )
    return None


_GROUP_STAGE_FINAL_STAGES = frozenset({
    "complete", "completed", "finished", "final", "official",
})


def _group_stage_resolution(
    record: dict[str, Any],
    facts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve group-stage questions from qualification/group-table facts.

    Handles five question shapes detectable from the event title:
      1. "advance from the group stage" / "advance from its group" -> top 2
      2. "win its group" / "win the group" / "finish first" -> rank 1
      3. "runner-up" / "finish second" / "second in its group" -> rank 2
      4. "at least N points" -> points threshold
      5. "at least +N goal difference" -> goal difference threshold
    """

    source = record.get("source") or {}
    teams = _candidate_teams(source)
    if not teams:
        return None
    title = _norm(record.get("event_title", ""))

    threshold = _parse_threshold(record.get("event_title", ""), "points")
    goal_diff_threshold = _parse_goal_diff_threshold(record.get("event_title", ""))
    win_group = (
        "win its group" in title
        or "win the group" in title
        or "win his group" in title
        or "win their group" in title
        or "finish first" in title
        or "finish top" in title
        or "top of the group" in title
        or "first in its group" in title
        or "first in the group" in title
    )
    runner_up = (
        "runner-up" in title
        or "runner up" in title
        or "finish second" in title
        or "second in its group" in title
        or "second in the group" in title
    )
    advance = (
        "advance from the group" in title
        or "advance from its group" in title
        or "escape the group" in title
        or "get out of the group" in title
        or "qualify from the group" in title
    )
    if (
        threshold is None
        and goal_diff_threshold is None
        and not win_group
        and not runner_up
        and not advance
    ):
        return None

    team_facts: dict[str, list[dict[str, Any]]] = {}
    for team in teams:
        team_norm = _norm(team)
        team_facts[team] = [
            fact for fact in facts
            if fact.get("kind") == "qualification"
            and _norm(fact.get("team")) == team_norm
        ]

    group_complete = _group_stage_complete(facts)
    multi = len(teams) > 1
    resolved_no: list[dict[str, Any]] = []

    for team in teams:
        relevant = team_facts[team]
        if not relevant:
            continue

        latest = _latest_group_fact(relevant)

        if threshold is not None:
            points = _number(latest.get("points"))
            if points is not None and points >= threshold:
                return _decision(
                    100.0,
                    f"{team} reached {points:g} points in the group stage, "
                    f"meeting the {threshold:g} threshold.",
                    [latest],
                )
            if points is not None and group_complete and points < threshold:
                if not multi:
                    return _decision(
                        0.0,
                        f"{team} finished with {points:g} points, below the "
                        f"{threshold:g} threshold.",
                        [latest],
                    )
                resolved_no.append(latest)
                continue

        if goal_diff_threshold is not None:
            gd = _number(latest.get("goal_diff"))
            if gd is not None and gd >= goal_diff_threshold:
                return _decision(
                    100.0,
                    f"{team} reached a +{gd:g} goal difference in the group "
                    f"stage, meeting the +{goal_diff_threshold:g} threshold.",
                    [latest],
                )
            if gd is not None and group_complete and gd < goal_diff_threshold:
                if not multi:
                    return _decision(
                        0.0,
                        f"{team} finished with a +{gd:g} goal difference, "
                        f"below the +{goal_diff_threshold:g} threshold.",
                        [latest],
                    )
                resolved_no.append(latest)
                continue

        if win_group:
            rank = _number(latest.get("rank"))
            status = str(latest.get("status") or "").lower()
            if rank == 1 or status in {"group_winner", "won_group"}:
                return _decision(
                    100.0,
                    f"{team} won its group (rank 1).",
                    [latest],
                )
            if group_complete and (
                (rank is not None and rank > 1)
                or _group_stage_eliminated(status, latest)
            ):
                if not multi:
                    return _decision(
                        0.0,
                        f"{team} did not win its group "
                        f"(rank {rank if rank is not None else '?'}).",
                        [latest],
                    )
                resolved_no.append(latest)
                continue

        if runner_up:
            rank = _number(latest.get("rank"))
            status = str(latest.get("status") or "").lower()
            if rank == 2 or status in {"group_runner_up", "runner_up"}:
                return _decision(
                    100.0,
                    f"{team} finished as runner-up in its group (rank 2).",
                    [latest],
                )
            if group_complete and (
                (rank is not None and rank != 2)
                or _group_stage_eliminated(status, latest)
            ):
                if not multi:
                    return _decision(
                        0.0,
                        f"{team} did not finish as runner-up "
                        f"(rank {rank if rank is not None else '?'}).",
                        [latest],
                    )
                resolved_no.append(latest)
                continue

        if advance:
            rank = _number(latest.get("rank"))
            status = str(latest.get("status") or "").lower()
            qualified = latest.get("already_qualified") is True
            if (
                qualified
                or (rank is not None and rank <= 2)
                or status in {"qualified", "advanced", "knockout_stage"}
            ):
                return _decision(
                    100.0,
                    f"{team} advanced from the group stage "
                    f"(rank {rank if rank is not None else '?'}).",
                    [latest],
                )
            if group_complete and _group_stage_eliminated(status, latest):
                if not multi:
                    return _decision(
                        0.0,
                        f"{team} was eliminated in the group stage.",
                        [latest],
                    )
                resolved_no.append(latest)
                continue

    if multi and resolved_no and len(resolved_no) == len(teams):
        return _decision(
            0.0,
            "All candidate teams failed to meet the group-stage condition.",
            resolved_no,
        )
    return None


def _latest_group_fact(facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the most recently observed qualification fact for a team."""

    def _key(fact: dict[str, Any]) -> str:
        return str(fact.get("observed_at") or "")
    return max(facts, key=_key)


def _group_stage_complete(facts: list[dict[str, Any]]) -> bool:
    """Check whether the group stage is officially over."""

    for fact in facts:
        if fact.get("kind") == "tournament_status":
            text = _norm(f"{fact.get('status', '')} {fact.get('stage', '')}")
            if (
                str(fact.get("status") or "").lower() in _GROUP_STAGE_FINAL_STAGES
                or "group stage complete" in text
                or "groups completed" in text
            ):
                return True
        if (
            fact.get("kind") == "qualification"
            and str(fact.get("stage") or "").lower() == "group_stage"
            and str(fact.get("status") or "").lower() in _GROUP_STAGE_FINAL_STAGES
        ):
            return True
    return False


def _group_stage_eliminated(status: str, fact: dict[str, Any]) -> bool:
    if fact.get("already_eliminated") is True:
        return True
    return status in {"eliminated", "out", "failed_to_advance"}


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


def _player_award_resolution(
    record: dict[str, Any],
    facts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    threshold = _parse_threshold(record.get("event_title", ""), "goals")
    if threshold is None:
        return None
    relevant = [
        fact for fact in facts
        if fact.get("kind") in {"player_award", "tournament_status"}
    ]
    scorer_facts = [
        fact for fact in relevant
        if fact.get("kind") == "player_award"
        and _is_top_scorer_fact(fact)
        and _number(fact.get("goals")) is not None
    ]
    if not scorer_facts:
        return None

    top_goals = max(_number(fact.get("goals")) or 0.0 for fact in scorer_facts)
    if top_goals >= threshold:
        return _decision(
            100.0,
            f"Top-scorer facts record {top_goals:g} goals, meeting the {threshold:g} threshold.",
            scorer_facts,
        )
    if any(_is_final_award_fact(fact) for fact in scorer_facts) or _tournament_complete(relevant):
        return _decision(
            0.0,
            f"Final top-scorer facts record {top_goals:g} goals, below the {threshold:g} threshold.",
            relevant,
        )
    return None


def _total_goals_resolution(
    record: dict[str, Any],
    facts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    threshold = _parse_threshold(record.get("event_title", ""), "total goals")
    if threshold is None:
        return None
    relevant = [
        fact for fact in facts
        if fact.get("kind") in {"match_result", "tournament_status"}
    ]
    match_facts = [fact for fact in relevant if fact.get("kind") == "match_result"]
    total = 0.0
    for fact in match_facts:
        score = fact.get("score") or {}
        total += float(fact.get("home_goals") or fact.get("home_score") or score.get("home") or 0)
        total += float(fact.get("away_goals") or fact.get("away_score") or score.get("away") or 0)
    if total >= threshold:
        return _decision(
            100.0,
            f"Match results record {total:g} total goals, meeting the {threshold:g} threshold.",
            match_facts,
        )
    if _tournament_complete(relevant):
        return _decision(
            0.0,
            f"Tournament complete with {total:g} total goals, below the {threshold:g} threshold.",
            relevant,
        )
    return None


def _required_progression_stage(source: dict[str, Any], title: str) -> str:
    source_id = str(source.get("source_id") or "")
    text = _norm(f"{source_id} {title}")
    if ("win" in text or "champion" in text) and "knockout" not in text:
        return "final_winner"
    if "final" in text and "semifinal" not in text and "quarterfinal" not in text and "quarter-final" not in text:
        return "final"
    if "semifinal" in text or "semi-final" in text:
        return "semifinal"
    if "quarterfinal" in text or "quarter-final" in text or "quarter final" in text:
        return "quarterfinal"
    if "round of 16" in text or "round_of_16" in text or "last 16" in text:
        return "round_of_16"
    if "knockout" in text:
        return "knockout_stage"
    return ""


def _primary_team(source: dict[str, Any]) -> str:
    for entity in source.get("entities") or []:
        text = str(entity or "").strip()
        if text and text != WORLD_CUP_TOURNAMENT:
            return text
    return ""


_NON_TEAM_ENTITIES = frozenset({
    "knockout stage", "quarterfinals", "semifinals", "final",
    "round of 16", "underdog", "europe", "conmebol", "uefa",
    "south america", "penalty shootout", "extra time", "red cards",
    "total goals", "top scorer", "golden boot", "golden glove",
})


def _candidate_teams(source: dict[str, Any]) -> list[str]:
    teams = []
    for entity in source.get("entities") or []:
        text = str(entity or "").strip()
        if not text or text == WORLD_CUP_TOURNAMENT:
            continue
        if text.lower() in _NON_TEAM_ENTITIES:
            continue
        teams.append(text)
    return teams


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
    if required_stage == "round_of_16":
        return (
            status in {"round_of_16", "reached_round_of_16", "advanced"}
            or stage in {"round_of_16", "quarterfinal", "semifinal", "final"}
        )
    if required_stage == "quarterfinal":
        return (
            status in {"quarterfinal", "reached_quarterfinal", "qualified_quarterfinal", "advanced"}
            or stage in {"quarterfinal", "semifinal", "final"}
        )
    if required_stage == "semifinal":
        return status in {"semifinal", "reached_semifinal", "qualified_semifinal"} or stage in {"semifinal", "final"}
    if required_stage == "final":
        return status in {"final", "reached_final", "qualified_final"} or stage == "final"
    if required_stage == "final_winner":
        return status in {"champion", "winner", "won_final"}
    return False


def _progression_no(status: str, fact: dict[str, Any]) -> bool:
    return fact.get("already_eliminated") is True or status in {"eliminated", "out"}


def _parse_threshold(text: str, phrase: str) -> float | None:
    match = re.search(rf"at least\s+(\d+(?:\.\d+)?)\s+{re.escape(phrase)}", _norm(text))
    if not match:
        return None
    return float(match.group(1))


def _parse_goal_diff_threshold(text: str) -> float | None:
    norm = _norm(text)
    if "goal difference" not in norm:
        return None
    match = re.search(r"at least\s+\+?(\d+(?:\.\d+)?)", norm)
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


def _is_top_scorer_fact(fact: dict[str, Any]) -> bool:
    text = _norm(f"{fact.get('award', '')} {fact.get('status', '')}")
    return (
        "top scorer" in text
        or "top_scorer" in text
        or "golden boot" in text
        or "golden_boot" in text
        or "scoring leader" in text
        or "scoring_leader" in text
    )


def _is_final_award_fact(fact: dict[str, Any]) -> bool:
    return str(fact.get("status") or "").lower() in {
        "final",
        "official",
        "confirmed",
        "complete",
        "completed",
        "finished",
    }


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


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
