"""Shared football head-to-head meeting merge and aggregation helpers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class H2HMeeting:
    """One completed meeting mapped to the current fixture-home perspective.

    ``played_on`` is absent only for legacy kernel rows that have neither a
    kickoff nor a finished timestamp. Meetings from separate sources are
    considered duplicates only when they have the same non-null calendar date,
    current-perspective scoreline, and current-home hosting designation. This
    deliberately avoids assuming fixture IDs are compatible across sources.
    """

    played_on: date | None
    home_goals: int
    away_goals: int
    current_home_hosted: bool


def merge_h2h_meetings(*sources: list[H2HMeeting]) -> list[H2HMeeting]:
    """Sort source records newest first and remove deterministic overlaps."""
    meetings = [meeting for source in sources for meeting in source]
    meetings.sort(
        key=lambda meeting: meeting.played_on or date.min,
        reverse=True,
    )

    merged: list[H2HMeeting] = []
    seen: set[tuple[date, int, int, bool]] = set()
    for meeting in meetings:
        if meeting.played_on is None:
            merged.append(meeting)
            continue
        identity = (
            meeting.played_on,
            meeting.home_goals,
            meeting.away_goals,
            meeting.current_home_hosted,
        )
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(meeting)
    return merged


def aggregate_h2h_meetings(
    meetings: list[H2HMeeting],
    *,
    max_matches: int = 20,
    data_source: str | None = None,
) -> dict[str, Any] | None:
    """Aggregate merged meetings into the existing H2H factor shape."""
    if not meetings:
        return None

    selected = meetings[: max(1, max_matches)]
    home_wins = draws = away_wins = 0
    home_goals = away_goals = 0
    venue_matches = venue_home_wins = venue_draws = venue_away_wins = 0

    for meeting in selected:
        gf = meeting.home_goals
        ga = meeting.away_goals
        home_goals += gf
        away_goals += ga
        if gf > ga:
            home_wins += 1
        elif gf < ga:
            away_wins += 1
        else:
            draws += 1

        if not meeting.current_home_hosted:
            continue
        venue_matches += 1
        if gf > ga:
            venue_home_wins += 1
        elif gf < ga:
            venue_away_wins += 1
        else:
            venue_draws += 1

    played = len(selected)
    result: dict[str, Any] = {
        "matches_played": played,
        "home_wins": home_wins,
        "draws": draws,
        "away_wins": away_wins,
        "avg_goals_home": round(home_goals / played, 2),
        "avg_goals_away": round(away_goals / played, 2),
        "home_venue_matches": venue_matches,
        "home_venue_home_wins": venue_home_wins,
        "home_venue_draws": venue_draws,
        "home_venue_away_wins": venue_away_wins,
    }
    if data_source is not None:
        result["data_source"] = data_source
    return result
