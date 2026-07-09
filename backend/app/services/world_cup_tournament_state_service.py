"""World Cup tournament state helpers derived from normalized sports facts."""

from __future__ import annotations

from typing import Any

_ELIMINATED_STATUSES = {"eliminated", "out", "knocked_out", "knocked-out"}
_QUALIFIED_STATUSES = {"qualified", "advanced", "through", "clinched"}
_GROUP_STAGE_KEYS = {"group_stage", "group", "groups"}
_KNOCKOUT_STAGE_ORDER = {
    "round_of_32": 1,
    "round-of-32": 1,
    "r32": 1,
    "round_of_16": 2,
    "round-of-16": 2,
    "r16": 2,
    "quarter_final": 3,
    "quarter-final": 3,
    "quarterfinal": 3,
    "quarterfinals": 3,
    "semi_final": 4,
    "semi-final": 4,
    "semifinal": 4,
    "semifinals": 4,
    "final": 5,
}


def build_qualification_state(facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract current tournament team state from qualification and result facts."""

    eliminated: set[str] = set()
    qualified: set[str] = set()
    group_teams: set[str] = set()
    knockout_participants_by_stage: dict[str, set[str]] = {}
    match_result_fact_count = 0
    knockout_result_fact_count = 0
    inferred_group_eliminations: set[str] = set()
    latest_observed_at = ""

    for fact in facts:
        observed_at = str(fact.get("observed_at") or fact.get("created_at") or "").strip()
        if observed_at and observed_at > latest_observed_at:
            latest_observed_at = observed_at

        if fact.get("kind") == "match_result":
            match_result_fact_count += 1
            stage = _normalize_stage(fact.get("stage"))
            home = str(fact.get("home_team") or "").strip()
            away = str(fact.get("away_team") or "").strip()
            teams = {team for team in (home, away) if team}

            if stage in _GROUP_STAGE_KEYS:
                group_teams.update(teams)
                continue

            if stage in _KNOCKOUT_STAGE_ORDER:
                knockout_result_fact_count += 1
                knockout_participants_by_stage.setdefault(stage, set()).update(teams)
                winner, loser = _match_winner_loser(fact)
                if winner:
                    qualified.add(winner)
                if loser:
                    eliminated.add(loser)
                    qualified.discard(loser)
            continue

        team = str(fact.get("team") or "").strip()
        if not team:
            continue

        status = str(fact.get("status") or "").strip().lower()
        is_eliminated = bool(fact.get("already_eliminated")) or status in _ELIMINATED_STATUSES
        is_qualified = bool(fact.get("already_qualified")) or status in _QUALIFIED_STATUSES

        if is_eliminated:
            eliminated.add(team)
            qualified.discard(team)
        elif is_qualified and team not in eliminated:
            qualified.add(team)

    if group_teams and knockout_participants_by_stage:
        earliest_stage = min(
            knockout_participants_by_stage,
            key=lambda stage: _KNOCKOUT_STAGE_ORDER[stage],
        )
        earliest_participants = knockout_participants_by_stage[earliest_stage]
        inferred_group_eliminations = group_teams - earliest_participants
        eliminated.update(inferred_group_eliminations)
        qualified.difference_update(inferred_group_eliminations)

    qualified.difference_update(eliminated)

    return {
        "eliminated_teams": sorted(eliminated),
        "qualified_teams": sorted(qualified),
        "eliminated_count": len(eliminated),
        "qualified_count": len(qualified),
        "qualification_fact_count": sum(1 for fact in facts if fact.get("kind") == "qualification"),
        "match_result_fact_count": match_result_fact_count,
        "knockout_result_fact_count": knockout_result_fact_count,
        "inferred_group_eliminated_count": len(inferred_group_eliminations),
        "latest_observed_at": latest_observed_at,
    }


def qualification_cache_signature(state: dict[str, Any]) -> str:
    """Build a compact cache key segment for qualification-sensitive simulation results."""

    eliminated = ",".join(state.get("eliminated_teams") or [])
    qualified = ",".join(state.get("qualified_teams") or [])
    latest = str(state.get("latest_observed_at") or "")
    count = str(state.get("qualification_fact_count") or 0)
    match_count = str(state.get("match_result_fact_count") or 0)
    knockout_count = str(state.get("knockout_result_fact_count") or 0)
    return f"facts={count}|matches={match_count}|ko={knockout_count}|latest={latest}|elim={eliminated}|qual={qualified}"


def _normalize_stage(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _match_winner_loser(fact: dict[str, Any]) -> tuple[str | None, str | None]:
    home = str(fact.get("home_team") or "").strip()
    away = str(fact.get("away_team") or "").strip()
    if not home or not away:
        return None, None

    winner = str(fact.get("winner") or "").strip()
    if winner:
        if winner.casefold() == home.casefold():
            return home, away
        if winner.casefold() == away.casefold():
            return away, home

    score = fact.get("score")
    if not isinstance(score, dict):
        return None, None

    try:
        home_score = float(score.get("home"))
        away_score = float(score.get("away"))
    except (TypeError, ValueError):
        return None, None

    if home_score > away_score:
        return home, away
    if away_score > home_score:
        return away, home
    return None, None
