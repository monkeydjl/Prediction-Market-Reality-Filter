"""Club form / rest from kernel_match_results (league fixtures).

Used when international historical CSV has no data for club team names.
Best-effort: returns None fields when DB empty or team unmatched.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from app.sports._shared.team_aliases import comparison_key


def points_form_rate(
    wins: int | float | None,
    draws: int | float | None,
    played: int | float | None,
) -> float | None:
    """Football points rate in [0, 1]: (3W + D) / (3N). None if N <= 0."""
    try:
        n = int(played or 0)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    try:
        w = max(0, int(wins or 0))
    except (TypeError, ValueError):
        w = 0
    try:
        d = max(0, int(draws or 0))
    except (TypeError, ValueError):
        d = 0
    rate = (3 * w + d) / (3 * n)
    if rate < 0.0:
        rate = 0.0
    elif rate > 1.0:
        rate = 1.0
    return round(rate, 4)


_RESULT_POINTS = {"W": 1.0, "D": 1.0 / 3.0, "L": 0.0}


def weighted_points_form_rate(
    results: Sequence[str],
    *,
    half_life: float = 5.0,
) -> float | None:
    """Recency-weighted points rate in [0, 1], same scale as points_form_rate.

    ``results`` is ordered most recent first; ``results[i]`` gets weight
    ``0.5 ** (i / half_life)``, so a match ``half_life`` games back counts half
    as much as the latest one. Entries outside W/D/L (notably the "U" that
    _points_result emits for an unmatched row) are dropped rather than scored
    zero, which would otherwise read as a loss.

    Returns None when nothing scorable is left or half_life is non-positive.
    """
    if not results:
        return None
    try:
        hl = float(half_life)
    except (TypeError, ValueError):
        return None
    if hl <= 0.0:
        return None

    total_weight = 0.0
    total_score = 0.0
    for i, res in enumerate(results):
        points = _RESULT_POINTS.get(res)
        if points is None:
            continue
        weight = 0.5 ** (i / hl)
        total_weight += weight
        total_score += weight * points
    if total_weight <= 0.0:
        return None
    rate = total_score / total_weight
    if rate < 0.0:
        rate = 0.0
    elif rate > 1.0:
        rate = 1.0
    return round(rate, 4)


def _match_key(name: str, competition: str | None) -> str:
    """Comparison key for a team name, scoped to one competition.

    Thin alias for the shared key so the schedule-density path and this module
    cannot drift apart; see ``team_aliases.comparison_key`` for the semantics.
    """
    return comparison_key(name, competition)


def _points_result(
    home: str,
    away: str,
    home_score: int,
    away_score: int,
    team_key: str,
    competition: str | None,
) -> str:
    is_home = _match_key(home, competition) == team_key
    is_away = _match_key(away, competition) == team_key
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

        key = _match_key(team_name, competition)
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
            if _match_key(h, competition) != key and _match_key(a, competition) != key:
                continue
            played_rows.append((
                kickoff,
                _points_result(
                    h, a, int(result.home_score), int(result.away_score),
                    key, competition,
                ),
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
            if _match_key(h, competition) == key:
                goals_for += hs
            else:
                goals_for += aws

        played = len(played_rows)
        last = played_rows[0][0]
        recent_results = [row[1] for row in played_rows]
        return {
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "played": played,
            "goals_per_game": round(goals_for / played, 2) if played else None,
            "last_match_date": last.date().isoformat() if last else None,
            "recent_results": recent_results,
            "form_rate_weighted": weighted_points_form_rate(recent_results),
            "data_source": "kernel_match_results",
        }
    finally:
        session.close()


def h2h_from_kernel(
    home_team: str,
    away_team: str,
    *,
    competition: str | None = None,
    before: datetime | None = None,
    max_matches: int = 20,
) -> dict[str, Any] | None:
    """Pairwise H2H from kernel fixtures+results.

    Counts wins/draws/losses from the perspective of ``home_team`` (current
    match home), regardless of which side hosted historically.
    Shape compatible with get_historical_h2h enrich write path.
    """
    from app.kernel.kernel_db import (
        KernelMatchFixture,
        KernelMatchResult,
        get_kernel_session,
    )

    if not home_team or not away_team:
        return None
    home_key = _match_key(home_team, competition)
    away_key = _match_key(away_team, competition)
    # Checked after alias resolution so that two spellings of one club - say
    # "Spurs" and "Tottenham" - are rejected rather than counted as a pairing.
    if not home_key or not away_key or home_key == away_key:
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

        pair = {home_key, away_key}
        meetings: list[tuple[datetime | None, int, int, bool]] = []
        for fixture, result in rows:
            if result.home_score is None or result.away_score is None:
                continue
            kickoff = fixture.kickoff_utc or result.finished_at
            if kickoff is not None:
                if kickoff.tzinfo is None:
                    kickoff = kickoff.replace(tzinfo=timezone.utc)
                if kickoff >= before:
                    continue
            fh = _match_key(fixture.home_team or "", competition)
            fa = _match_key(fixture.away_team or "", competition)
            if {fh, fa} != pair:
                continue
            hs = int(result.home_score)
            aws = int(result.away_score)
            # Map to current-home perspective scores
            at_home_venue = fh == home_key
            if at_home_venue:
                cur_home_gf, cur_home_ga = hs, aws
            else:
                cur_home_gf, cur_home_ga = aws, hs
            meetings.append((kickoff, cur_home_gf, cur_home_ga, at_home_venue))

        if not meetings:
            return None

        meetings.sort(
            key=lambda r: r[0].isoformat() if r[0] else "",
            reverse=True,
        )
        meetings = meetings[: max(1, max_matches)]

        home_wins = draws = away_wins = 0
        # Same tallies restricted to meetings the current home team hosted, so
        # callers can separate home advantage from the pairing itself.
        venue_matches = venue_home_wins = venue_draws = venue_away_wins = 0
        for _, gf, ga, at_home_venue in meetings:
            if gf > ga:
                home_wins += 1
            elif gf < ga:
                away_wins += 1
            else:
                draws += 1
            if not at_home_venue:
                continue
            venue_matches += 1
            if gf > ga:
                venue_home_wins += 1
            elif gf < ga:
                venue_away_wins += 1
            else:
                venue_draws += 1

        return {
            "matches_played": len(meetings),
            "home_wins": home_wins,
            "draws": draws,
            "away_wins": away_wins,
            "home_venue_matches": venue_matches,
            "home_venue_home_wins": venue_home_wins,
            "home_venue_draws": venue_draws,
            "home_venue_away_wins": venue_away_wins,
            "data_source": "kernel_match_results",
        }
    finally:
        session.close()
