"""Read-only audit for World Cup result facts vs prediction fixtures."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.world_cup_prediction import MatchFixture
from app.services.sports_fact_service import (
    WORLD_CUP_TOURNAMENT,
    load_sports_facts,
    sports_fact_status,
)
from app.utils.prediction_db import close_prediction_session, get_prediction_session


def audit_world_cup_result_consistency(
    session: Session | None = None,
    *,
    facts: list[dict[str, Any]] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Compare stored match-result facts with prediction DB fixtures.

    This is intentionally read-only. It reports drift between the normalized
    facts store and ``match_fixtures`` without trying to repair either side.
    """

    should_close = session is None
    if session is None:
        session = get_prediction_session()

    try:
        source = "provided_facts" if facts is not None else "stored_sports_facts"
        result_facts = _result_facts(facts)
        latest_facts = _latest_fact_by_match(result_facts)
        fixtures = session.query(MatchFixture).all()
        fixture_by_match_id = {str(fixture.match_id): fixture for fixture in fixtures}

        issues: list[dict[str, Any]] = []
        for match_id in sorted(latest_facts):
            fact = latest_facts[match_id]
            fixture = fixture_by_match_id.get(match_id)
            if fixture is None:
                issues.append(_fixture_missing_issue(match_id, fact))
                continue
            issues.extend(_compare_fact_to_fixture(match_id, fact, fixture))

        for match_id in sorted(fixture_by_match_id):
            if match_id in latest_facts:
                continue
            fixture = fixture_by_match_id[match_id]
            if _normalize_status(fixture.status) == "finished":
                issues.append(_fact_missing_issue(match_id, fixture))

        issue_count = len(issues)
        capped_limit = max(1, int(limit))
        return {
            "status": "ok",
            "dry_run": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "fact_store": (
                sports_fact_status(tournament=WORLD_CUP_TOURNAMENT)
                if facts is None else None
            ),
            "fact_count": len(result_facts),
            "fixture_count": len(fixtures),
            "checked": len(set(latest_facts) & set(fixture_by_match_id)),
            "issue_count": issue_count,
            "returned_issue_count": min(issue_count, capped_limit),
            "issues": issues[:capped_limit],
        }
    finally:
        if should_close:
            close_prediction_session(session)


def _result_facts(facts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    raw_facts = (
        facts
        if facts is not None
        else load_sports_facts(tournament=WORLD_CUP_TOURNAMENT, kind="match_result")
    )
    return [
        fact for fact in raw_facts
        if isinstance(fact, dict)
        and fact.get("kind") == "match_result"
        and _clean(fact.get("match_id"))
    ]


def _latest_fact_by_match(facts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for fact in facts:
        match_id = _clean(fact.get("match_id"))
        current = latest.get(match_id)
        if current is None or _fact_sort_key(fact) > _fact_sort_key(current):
            latest[match_id] = fact
    return latest


def _fact_sort_key(fact: dict[str, Any]) -> tuple[str, str]:
    return (
        _clean(fact.get("observed_at")),
        _clean(fact.get("fact_id")),
    )


def _compare_fact_to_fixture(
    match_id: str,
    fact: dict[str, Any],
    fixture: MatchFixture,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    fact_status = _normalize_status(fact.get("status"))
    fixture_status = _normalize_status(fixture.status)

    if fact_status and fixture_status and fact_status != fixture_status:
        issues.append({
            "type": "status_mismatch",
            "severity": "warn",
            "match_id": match_id,
            "fact": _fact_snapshot(fact),
            "fixture": _fixture_snapshot(fixture),
            "message": (
                f"Result fact status is {fact_status}, but prediction fixture "
                f"status is {fixture_status}."
            ),
        })

    fact_score = _fact_score(fact)
    fixture_score = _fixture_score(fixture)
    if fact_score is not None and fixture_score is not None and fact_score != fixture_score:
        issues.append({
            "type": "score_mismatch",
            "severity": "error",
            "match_id": match_id,
            "fact": _fact_snapshot(fact),
            "fixture": _fixture_snapshot(fixture),
            "message": (
                f"Result fact score is {fact_score[0]}-{fact_score[1]}, but "
                f"prediction fixture score is {fixture_score[0]}-{fixture_score[1]}."
            ),
        })

    return issues


def _fixture_missing_issue(match_id: str, fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "fixture_missing_in_prediction_db",
        "severity": "warn",
        "match_id": match_id,
        "fact": _fact_snapshot(fact),
        "fixture": None,
        "message": "Result fact exists, but prediction fixture is missing.",
    }


def _fact_missing_issue(match_id: str, fixture: MatchFixture) -> dict[str, Any]:
    return {
        "type": "result_fact_missing_for_finished_fixture",
        "severity": "warn",
        "match_id": match_id,
        "fact": None,
        "fixture": _fixture_snapshot(fixture),
        "message": "Prediction fixture is finished, but no result fact was found.",
    }


def _fact_snapshot(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "fact_id": _clean(fact.get("fact_id")),
        "source": _clean(fact.get("source")),
        "observed_at": _clean(fact.get("observed_at")),
        "status": _normalize_status(fact.get("status")),
        "score": _score_payload(_fact_score(fact)),
    }


def _fixture_snapshot(fixture: MatchFixture) -> dict[str, Any]:
    return {
        "match_id": fixture.match_id,
        "fixture_id": fixture.fixture_id,
        "home_team": fixture.home_team,
        "away_team": fixture.away_team,
        "status": _normalize_status(fixture.status),
        "score": _score_payload(_fixture_score(fixture)),
        "updated_at": fixture.updated_at.isoformat() if fixture.updated_at else None,
    }


def _score_payload(score: tuple[int, int] | None) -> dict[str, int] | None:
    if score is None:
        return None
    return {"home": score[0], "away": score[1]}


def _fact_score(fact: dict[str, Any]) -> tuple[int, int] | None:
    score = fact.get("score")
    if isinstance(score, dict):
        home = _score_number(score.get("home"))
        away = _score_number(score.get("away"))
    else:
        home = _score_number(_first_present(fact, "home_score", "home_goals"))
        away = _score_number(_first_present(fact, "away_score", "away_goals"))
    if home is None or away is None:
        return None
    return home, away


def _fixture_score(fixture: MatchFixture) -> tuple[int, int] | None:
    home = _score_number(fixture.home_score)
    away = _score_number(fixture.away_score)
    if home is None or away is None:
        return None
    return home, away


def _score_number(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or not number.is_integer():
        return None
    return int(number)


def _first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def _normalize_status(value: Any) -> str:
    text = _clean(value).lower().replace("-", "_")
    if not text:
        return ""

    finished = {
        "aet",
        "after_extra_time",
        "complete",
        "completed",
        "finished",
        "ft",
        "full_time",
        "fulltime",
        "pen",
        "penalties",
    }
    scheduled = {
        "not_started",
        "ns",
        "scheduled",
        "tbd",
    }
    in_play = {
        "1h",
        "2h",
        "et",
        "half_time",
        "ht",
        "in_play",
        "in_progress",
        "live",
        "p",
    }
    postponed = {
        "abandoned",
        "canceled",
        "cancelled",
        "postponed",
        "suspended",
    }
    normalized_text = text.replace(" ", "_")
    if normalized_text in finished:
        return "finished"
    if normalized_text in scheduled:
        return "scheduled"
    if normalized_text in in_play:
        return "in_play"
    if normalized_text in postponed:
        return "postponed"
    return normalized_text


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())
