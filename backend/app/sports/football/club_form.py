"""Club form / rest from kernel_match_results (league fixtures).

Used when international historical CSV has no data for club team names.
Best-effort: returns None fields when DB empty or team unmatched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _normalize(name: str) -> str:
    return " ".join((name or "").lower().split())


def _points_result(home: str, away: str, home_score: int, away_score: int, team: str) -> str:
    t = _normalize(team)
    is_home = _normalize(home) == t
    is_away = _normalize(away) == t
    if not is_home and not is_away:
        return "U"
    if is_home:
        gf, ga = home_score, away_score
    else:
        gf, ga = away_score, home_score
    if gf > ga:
        return "W"
    if gf < ga:
        return "L"
    return "D"


def team_form_from_kernel(
    team_name: str,
    *,
    competition: str | None = None,
    before: datetime | None = None,
    max_matches: int = 10,
) -> dict[str, Any] | None:
    """Return form stats for a club team from kernel fixtures+results.

    Shape compatible with historical team stats enrichment:
      wins, draws, losses, played, goals_per_game, last_match_date
    """
    from app.kernel.kernel_db import (
        KernelMatchFixture,
        KernelMatchResult,
        get_kernel_session,
    )

    if not team_name:
        return None
    before = before or datetime.now(timezone.utc)
    if before.tzinfo is None:
        before = before.replace(tzinfo=timezone.utc)

    session = get_kernel_session()
    try:
        q = (
            session.query(KernelMatchFixture, KernelMatchResult)
            .join(
                KernelMatchResult,
                KernelMatchFixture.match_id == KernelMatchResult.match_id,
            )
        )
        if competition:
            q = q.filter(KernelMatchFixture.competition == competition)
        rows = q.all()

        key = _normalize(team_name)
        played_rows: list[tuple[datetime | None, str, int, int, str, str]] = []
        for fixture, result in rows:
            if result.home_score is None or result.away_score is None:
                continue
            kickoff = fixture.kickoff_utc or result.finished_at
            if kickoff is not None:
                if kickoff.tzinfo is None:
                    kickoff = kickoff.replace(tzinfo=timezone.utc)
                if kickoff >= before:
                    continue
            h = fixture.home_team or ""
            a = fixture.away_team or ""
            if _normalize(h) != key and _normalize(a) != key:
                continue
            played_rows.append((
                kickoff,
                _points_result(h, a, int(result.home_score), int(result.away_score), team_name),
                int(result.home_score),
                int(result.away_score),
                h,
                a,
            ))

        if not played_rows:
            return None

        played_rows.sort(
            key=lambda r: r[0].isoformat() if r[0] else "",
            reverse=True,
        )
        played_rows = played_rows[:max_matches]

        wins = draws = losses = 0
        goals_for = 0
        for kickoff, res, hs, aws, h, a in played_rows:
            if res == "W":
                wins += 1
            elif res == "D":
                draws += 1
            elif res == "L":
                losses += 1
            if _normalize(h) == key:
                goals_for += hs
            else:
                goals_for += aws

        played = len(played_rows)
        last = played_rows[0][0]
        return {
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "played": played,
            "goals_per_game": round(goals_for / played, 2) if played else None,
            "last_match_date": last.date().isoformat() if last else None,
            "data_source": "kernel_match_results",
        }
    finally:
        session.close()
