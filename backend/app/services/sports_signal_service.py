"""Turn structured sports facts into World Cup analysis signals."""

from __future__ import annotations

import re
from typing import Any

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
    if category == "player_awards" and kind in {"injury", "availability", "lineup"}:
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
    red_total = sum(float(fact.get("red_cards", 0.0)) for fact in discipline_facts)
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


def _fact_summary(fact: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "fact_id",
        "kind",
        "team",
        "player",
        "match_id",
        "status",
        "severity",
        "source",
        "confidence",
        "observed_at",
        "red_cards",
        "extra_time",
        "penalty_shootout",
        "already_qualified",
        "already_eliminated",
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


def _is_yes_team_progression(event_question: str, source: dict[str, Any]) -> bool:
    category = str(source.get("category") or "").lower()
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


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())
