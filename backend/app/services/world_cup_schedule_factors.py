"""Schedule-derived World Cup prediction factors."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.world_cup_prediction import MatchFixture


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _team_schedule(team: str, match: MatchFixture, session: Session) -> dict[str, Any]:
    kickoff = _utc_naive(match.kickoff_utc)
    if kickoff is None:
        return {
            "days_since_last_match": 7,
            "matches_last_14_days": 0,
            "schedule_density": "normal",
            "last_match_id": None,
            "last_match_utc": None,
        }

    previous_matches = session.query(MatchFixture).filter(
        MatchFixture.match_id != match.match_id,
        MatchFixture.kickoff_utc < kickoff,
        or_(MatchFixture.home_team == team, MatchFixture.away_team == team),
    ).order_by(MatchFixture.kickoff_utc.desc()).all()

    last_match = previous_matches[0] if previous_matches else None
    if last_match and last_match.kickoff_utc:
        last_kickoff = _utc_naive(last_match.kickoff_utc)
        days_since_last = max(0.0, (kickoff - last_kickoff).total_seconds() / 86400)
    else:
        last_kickoff = None
        days_since_last = 7.0

    matches_last_14 = sum(
        1
        for previous in previous_matches
        if previous.kickoff_utc
        and 0 <= (kickoff - _utc_naive(previous.kickoff_utc)).total_seconds() <= 14 * 86400
    )
    if days_since_last < 3 or matches_last_14 >= 4:
        density = "high"
    elif days_since_last < 5 or matches_last_14 >= 3:
        density = "medium"
    else:
        density = "normal"

    return {
        "days_since_last_match": round(days_since_last, 2),
        "matches_last_14_days": matches_last_14,
        "schedule_density": density,
        "last_match_id": last_match.match_id if last_match else None,
        "last_match_utc": last_kickoff.isoformat() if last_kickoff else None,
    }


def build_schedule_factors(match: MatchFixture, session: Session) -> dict[str, Any]:
    """Build schedule factors for both teams in a match."""
    home = _team_schedule(match.home_team, match, session)
    away = _team_schedule(match.away_team, match, session)
    rest_diff = float(home["days_since_last_match"]) - float(away["days_since_last_match"])

    if rest_diff >= 2:
        advantage = "home"
    elif rest_diff <= -2:
        advantage = "away"
    else:
        advantage = "balanced"

    return {
        "home": home,
        "away": away,
        "rest_days_difference": round(rest_diff, 2),
        "rest_advantage": advantage,
    }
