"""Turn structured sports facts into World Cup analysis signals."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.services.sports_fact_aggregation import red_card_total
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT


def build_sports_signals(
    event_question: str,
    source: dict[str, Any] | None,
    facts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build deterministic signals for a World Cup event.

    The output is explanatory context, not a probability model. Impact size
    remains the LLM/model's job.
    """

    source = source or {}
    tournament = str(source.get("tournament") or WORLD_CUP_TOURNAMENT)
    relevant = [
        fact for fact in facts
        if _is_relevant_fact(event_question, source, fact, tournament)
    ]
    signals: dict[str, Any] = {}

    injury = _injury_signal(event_question, source, relevant)
    if injury:
        signals["injury_signal"] = injury

    discipline = _discipline_signal(event_question, relevant)
    if discipline:
        signals["discipline_signal"] = discipline

    qualification = _qualification_signal(event_question, source, relevant)
    if qualification:
        signals["qualification_signal"] = qualification

    match_format = _match_format_signal(event_question, relevant)
    if match_format:
        signals["match_format_signal"] = match_format

    player_award = _player_award_signal(event_question, relevant)
    if player_award:
        signals["player_award_signal"] = player_award

    schedule_fatigue = _schedule_fatigue_signal(event_question, source, relevant)
    if schedule_fatigue:
        signals["schedule_fatigue_signal"] = schedule_fatigue

    lineup = _lineup_signal(event_question, source, relevant)
    if lineup:
        signals["lineup_signal"] = lineup

    suspension = _suspension_signal(event_question, source, relevant)
    if suspension:
        signals["suspension_signal"] = suspension

    group_strength = _group_strength_signal(event_question, source, facts)
    if group_strength:
        signals["group_strength_signal"] = group_strength

    return {
        "tournament": tournament,
        "fact_count": len(relevant),
        "facts": [_fact_summary(fact) for fact in relevant[:12]],
        "signals": signals,
    }


def render_sports_context(bundle: dict[str, Any]) -> str:
    """Render signals into compact text for the probability prompt."""

    if not bundle or bundle.get("fact_count", 0) <= 0:
        return ""
    lines = [
        "SPORTS FACT SIGNALS",
        f"TOURNAMENT: {bundle.get('tournament', '')}",
        f"FACT_COUNT: {bundle.get('fact_count', 0)}",
        "FACT_SOURCE_RULE: Treat these as structured facts; do not invent missing facts.",
    ]
    for name, signal in (bundle.get("signals") or {}).items():
        lines.append(
            "SIGNAL "
            f"{name}: level={signal.get('level', 'unknown')} "
            f"direction={signal.get('direction', 'neutral')} "
            f"summary={signal.get('summary', '')}"
        )
    for fact in bundle.get("facts") or []:
        lines.append(
            "SPORTS FACT "
            f"id={fact.get('fact_id', '')} "
            f"kind={fact.get('kind', '')} "
            f"team={fact.get('team', '')} "
            f"player={fact.get('player', '')} "
            f"award={fact.get('award', '')} "
            f"goals={fact.get('goals', '')} "
            f"status={fact.get('status', '')} "
            f"source={fact.get('source', '')} "
            f"confidence={fact.get('confidence', '')}"
        )
    return "\n".join(lines)


def _is_relevant_fact(
    event_question: str,
    source: dict[str, Any],
    fact: dict[str, Any],
    tournament: str,
) -> bool:
    if fact.get("tournament") != tournament:
        return False
    source_id = str(source.get("source_id") or "")
    applies_to = {str(item) for item in fact.get("applies_to", [])}
    if source_id and source_id in applies_to:
        return True

    category = str(source.get("category") or "").lower()
    kind = str(fact.get("kind") or "")
    if category == "discipline" and kind in {"discipline", "match_state", "match_result"}:
        return True
    if category == "match_format" and kind in {"match_state", "match_result"}:
        return True
    if category == "team_progression":
        return _fact_matches_source_entities(fact, source)
    if category == "player_awards" and kind in {"injury", "availability", "lineup", "player_award"}:
        return True

    text = _norm(" ".join([
        event_question,
        str(source.get("question") or ""),
    ]))
    return any(token and token in text for token in _fact_tokens(fact))


def _injury_signal(
    event_question: str,
    source: dict[str, Any],
    facts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    injury_facts = [
        fact for fact in facts
        if fact.get("kind") in {"injury", "availability"}
        and fact.get("status") in {"out", "injured", "doubtful", "questionable"}
    ]
    if not injury_facts:
        return None
    high = [
        fact for fact in injury_facts
        if fact.get("severity") == "high" or fact.get("status") in {"out", "injured"}
    ]
    level = "high" if high else "medium" if len(injury_facts) >= 2 else "low"
    direction = "supports_no" if _is_yes_team_progression(event_question, source) else "neutral"
    names = [
        fact.get("player") or fact.get("team") or fact.get("fact_id", "")
        for fact in injury_facts[:3]
    ]
    return {
        "level": level,
        "direction": direction,
        "summary": f"{len(injury_facts)} relevant injury/availability facts: {', '.join(names)}.",
        "facts": [fact["fact_id"] for fact in injury_facts],
    }


def _discipline_signal(
    event_question: str,
    facts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    discipline_facts = [
        fact for fact in facts
        if fact.get("kind") in {"discipline", "suspension", "match_state", "match_result"}
    ]
    if not discipline_facts:
        return None
    # The same card arrives as a per-card `discipline` fact and inside the
    # per-match total on `match_result`; adding both reported it twice, and this
    # number is shown to the operator as `threshold_progress`.
    red_total, _counted = red_card_total(discipline_facts)
    suspensions = sum(1 for fact in discipline_facts if fact.get("kind") == "suspension")
    threshold = _parse_red_card_threshold(event_question)
    progress = round(red_total / threshold, 3) if threshold else None
    direction = "neutral"
    if threshold and red_total >= threshold:
        direction = "supports_yes"
    level = "high" if (progress or 0) >= 1 else "medium" if (progress or 0) >= 0.5 else "low"
    return {
        "level": level,
        "direction": direction,
        "summary": f"red_card_total={red_total:g}; suspensions={suspensions}.",
        "red_card_total": red_total,
        "red_card_threshold": threshold,
        "threshold_progress": progress,
        "suspensions": suspensions,
        "facts": [fact["fact_id"] for fact in discipline_facts],
    }


def _qualification_signal(
    event_question: str,
    source: dict[str, Any],
    facts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    q_facts = [fact for fact in facts if fact.get("kind") == "qualification"]
    if not q_facts:
        return None
    qualified = any(
        fact.get("already_qualified") is True
        or fact.get("status") in {"qualified", "advanced", "clinched"}
        for fact in q_facts
    )
    eliminated = any(
        fact.get("already_eliminated") is True
        or fact.get("status") in {"eliminated", "out"}
        for fact in q_facts
    )
    direction = "neutral"
    if _is_yes_team_progression(event_question, source):
        if qualified:
            direction = "supports_yes"
        elif eliminated:
            direction = "supports_no"
    level = "high" if qualified or eliminated else "medium"
    return {
        "level": level,
        "direction": direction,
        "summary": f"qualified={qualified}; eliminated={eliminated}.",
        "already_qualified": qualified,
        "already_eliminated": eliminated,
        "facts": [fact["fact_id"] for fact in q_facts],
    }


def _match_format_signal(
    event_question: str,
    facts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    question = _norm(event_question)
    wants_penalties = "penalty" in question or "shootout" in question
    wants_extra_time = "extra time" in question
    if not wants_penalties and not wants_extra_time:
        return None
    format_facts = [
        fact for fact in facts
        if fact.get("kind") in {"match_state", "match_result"}
    ]
    if not format_facts:
        return None
    penalty_seen = any(fact.get("penalty_shootout") for fact in format_facts)
    extra_seen = any(fact.get("extra_time") for fact in format_facts)
    supports = (wants_penalties and penalty_seen) or (wants_extra_time and extra_seen)
    return {
        "level": "high" if supports else "low",
        "direction": "supports_yes" if supports else "neutral",
        "summary": f"penalty_shootout_seen={penalty_seen}; extra_time_seen={extra_seen}.",
        "penalty_shootout_seen": penalty_seen,
        "extra_time_seen": extra_seen,
        "facts": [fact["fact_id"] for fact in format_facts],
    }


def _player_award_signal(
    event_question: str,
    facts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    award_facts = [
        fact for fact in facts
        if fact.get("kind") == "player_award" and _number(fact.get("goals")) is not None
    ]
    if not award_facts:
        return None
    threshold = _parse_goal_threshold(event_question)
    top_goals = max(_number(fact.get("goals")) or 0.0 for fact in award_facts)
    progress = round(top_goals / threshold, 3) if threshold else None
    final = any(
        str(fact.get("status") or "").lower()
        in {"final", "official", "confirmed", "complete", "completed", "finished"}
        for fact in award_facts
    )
    direction = "neutral"
    if threshold and top_goals >= threshold:
        direction = "supports_yes"
    elif threshold and final:
        direction = "supports_no"
    return {
        "level": "high" if direction != "neutral" else "medium",
        "direction": direction,
        "summary": f"top_scorer_goals={top_goals:g}; final={final}.",
        "top_scorer_goals": top_goals,
        "goal_threshold": threshold,
        "threshold_progress": progress,
        "final": final,
        "facts": [fact["fact_id"] for fact in award_facts],
    }


def _schedule_fatigue_signal(
    event_question: str,
    source: dict[str, Any],
    facts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    team = _primary_team(source)
    if not team:
        return None
    team_norm = _norm(team)
    match_facts = [
        fact for fact in facts
        if fact.get("kind") == "match_result"
        and (
            _norm(fact.get("home_team", "")) == team_norm
            or _norm(fact.get("away_team", "")) == team_norm
            or _norm(fact.get("team", "")) == team_norm
        )
        and fact.get("kickoff_at")
    ]
    if not match_facts:
        return None
    now_str = max(fact.get("kickoff_at", "") for fact in match_facts)
    try:
        latest_dt = datetime.fromisoformat(str(now_str).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    recent = []
    for fact in match_facts:
        try:
            ko = datetime.fromisoformat(str(fact["kickoff_at"]).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        days_diff = (latest_dt - ko).total_seconds() / 86400
        if days_diff <= 5:
            recent.append(fact)
    if len(recent) < 2:
        return None
    level = "high" if len(recent) >= 3 else "medium"
    return {
        "level": level,
        "direction": "neutral",
        "summary": f"{team} played {len(recent)} match(es) within 5 days of their latest fixture.",
        "matches_in_window": len(recent),
        "team": team,
        "facts": [fact["fact_id"] for fact in recent if fact.get("fact_id")],
    }


def _lineup_signal(
    event_question: str,
    source: dict[str, Any],
    facts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    team = _primary_team(source)
    if not team:
        return None
    team_norm = _norm(team)
    starters = [
        fact for fact in facts
        if fact.get("kind") == "lineup"
        and _norm(fact.get("team", "")) == team_norm
        and str(fact.get("status", "")).lower() in {"starting", "starter"}
    ]
    if not starters:
        return None
    starter_names = {_norm(fact.get("player", "")) for fact in starters if fact.get("player")}
    unavailable = [
        fact for fact in facts
        if fact.get("kind") in {"injury", "suspension"}
        and _norm(fact.get("team", "")) == team_norm
        and _norm(fact.get("player", "")) in starter_names
        and str(fact.get("status", "")).lower() in {"out", "injured", "suspended", "banned"}
    ]
    if not unavailable:
        return None

    # --- Player importance weighting ----------------------------------------
    # A star player missing from a top-ranked team has a bigger impact than
    # a squad player missing from a lower-ranked team.  Use the team's FIFA
    # ranking as a proxy for squad quality / depth.
    importance = _team_importance(team_norm, facts)

    # Weighted unavailable count: each missing starter × importance
    weighted_count = len(unavailable) * importance
    level = "high" if weighted_count >= 2.0 else "medium" if weighted_count >= 1.0 else "low"
    direction = "supports_no" if _is_yes_team_progression(event_question, source) else "neutral"
    names = [fact.get("player", "") for fact in unavailable[:4]]
    return {
        "level": level,
        "direction": direction,
        "summary": (
            f"{len(unavailable)} starter(s) unavailable for {team}: "
            f"{', '.join(names)} (importance={importance:.2f})."
        ),
        "unavailable_starters": len(unavailable),
        "importance": round(importance, 2),
        "team": team,
        "facts": [fact["fact_id"] for fact in unavailable if fact.get("fact_id")],
    }


def _suspension_signal(
    event_question: str,
    source: dict[str, Any],
    facts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    team = _primary_team(source)
    if not team:
        return None
    team_norm = _norm(team)
    suspension_facts = [
        fact for fact in facts
        if fact.get("kind") == "suspension"
        and _norm(fact.get("team", "")) == team_norm
    ]
    if not suspension_facts:
        return None
    level = "high" if len(suspension_facts) >= 2 else "medium"
    direction = "supports_no" if _is_yes_team_progression(event_question, source) else "neutral"
    names = [fact.get("player", "") for fact in suspension_facts[:4] if fact.get("player")]
    return {
        "level": level,
        "direction": direction,
        "summary": f"{len(suspension_facts)} player(s) suspended for {team}: {', '.join(names)}.",
        "suspended_count": len(suspension_facts),
        "team": team,
        "facts": [fact["fact_id"] for fact in suspension_facts if fact.get("fact_id")],
    }


def _group_strength_signal(
    event_question: str,
    source: dict[str, Any],
    facts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Compare a team's FIFA rank to its group's average rank.

    Uses ``team_stat`` facts that carry a ``group`` plus a ``stat_name``
    containing "rank"/"fifa" and a numeric ``stat_value``. Lower FIFA rank
    means a stronger team, so a team ranked well above (numerically below)
    its group average is favored in yes-framed progression/group questions.
    """
    team = _primary_team(source)
    if not team:
        return None
    team_norm = _norm(team)
    target_group = None
    for fact in facts:
        if _norm(fact.get("team", "")) == team_norm and fact.get("group"):
            target_group = str(fact.get("group"))
            break
    if not target_group:
        return None

    ranks: list[tuple[str, float]] = []
    seen: set[str] = set()
    for fact in facts:
        if fact.get("kind") != "team_stat":
            continue
        if str(fact.get("group", "")) != target_group:
            continue
        team_name = _norm(fact.get("team", ""))
        if not team_name or team_name in seen:
            continue
        stat_name = _norm(fact.get("stat_name", ""))
        if "rank" not in stat_name and "fifa" not in stat_name:
            continue
        rank = _number(fact.get("stat_value"))
        if rank is None:
            continue
        ranks.append((team_name, rank))
        seen.add(team_name)

    if len(ranks) < 2:
        return None

    avg_rank = sum(rank for _, rank in ranks) / len(ranks)
    target_rank = next((rank for name, rank in ranks if name == team_norm), None)
    if target_rank is None:
        return None

    yes_progression = _is_yes_team_progression(event_question, source)
    direction = "neutral"
    if target_rank < avg_rank:
        direction = "supports_yes" if yes_progression else "neutral"
    elif target_rank > avg_rank:
        direction = "supports_no" if yes_progression else "neutral"
    spread = abs(target_rank - avg_rank)
    level = "high" if spread >= 10 else "medium"
    return {
        "level": level,
        "direction": direction,
        "summary": (
            f"{team} (FIFA rank {target_rank:g}) in {target_group}; "
            f"group avg rank {avg_rank:.1f}."
        ),
        "team_rank": target_rank,
        "group_avg_rank": round(avg_rank, 1),
        "group": target_group,
        "facts": [],
    }


def _fact_summary(fact: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "fact_id",
        "kind",
        "team",
        "player",
        "award",
        "match_id",
        "status",
        "severity",
        "source",
        "confidence",
        "observed_at",
        "goals",
        "rank",
        "red_cards",
        "extra_time",
        "penalty_shootout",
        "already_qualified",
        "already_eliminated",
        "tournament_complete",
    )
    return {key: fact[key] for key in keep if key in fact}


def _fact_matches_source_entities(fact: dict[str, Any], source: dict[str, Any]) -> bool:
    entity_text = _norm(" ".join(str(item) for item in source.get("entities", [])))
    return any(token and token in entity_text for token in _fact_tokens(fact))


def _fact_tokens(fact: dict[str, Any]) -> list[str]:
    return [
        _norm(fact.get("team", "")),
        _norm(fact.get("player", "")),
        _norm(fact.get("home_team", "")),
        _norm(fact.get("away_team", "")),
        _norm(fact.get("winner", "")),
    ]


def _primary_team(source: dict[str, Any]) -> str:
    for entity in source.get("entities") or []:
        text = str(entity or "").strip()
        if text and text != WORLD_CUP_TOURNAMENT:
            return text
    return ""


def _is_yes_team_progression(event_question: str, source: dict[str, Any]) -> bool:
    category = str(source.get("category") or "").lower()
    if category == "group_stage":
        return True
    if category != "team_progression":
        return False
    question = _norm(event_question)
    return any(term in question for term in ("reach", "advance", "qualify"))


def _parse_red_card_threshold(event_question: str) -> float | None:
    question = _norm(event_question)
    match = re.search(r"at least\s+(\d+(?:\.\d+)?)\s+red cards?", question)
    if not match:
        return None
    return float(match.group(1))


def _parse_goal_threshold(event_question: str) -> float | None:
    question = _norm(event_question)
    match = re.search(r"at least\s+(\d+(?:\.\d+)?)\s+goals?", question)
    if not match:
        return None
    return float(match.group(1))


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _team_importance(team_norm: str, facts: list[dict[str, Any]]) -> float:
    """Compute player importance weight from team FIFA ranking.

    Losing a starter on a top-ranked team has more impact than on a
    lower-ranked team because top teams have less squad depth relative
    to their star players.

    Returns:
        Importance multiplier: 1.5 (top-10), 1.2 (top-30), 1.0 (default).
    """
    rank: float | None = None
    for fact in facts:
        if _norm(fact.get("team", "")) != team_norm:
            continue
        if fact.get("kind") != "team_stat":
            continue
        stat_name = _norm(fact.get("stat_name", ""))
        if "rank" not in stat_name and "fifa" not in stat_name:
            continue
        rank = _number(fact.get("stat_value"))
        if rank is not None:
            break

    if rank is None:
        return 1.0
    if rank <= 10:
        return 1.5
    if rank <= 30:
        return 1.2
    return 1.0
