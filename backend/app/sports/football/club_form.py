"""Club form / rest from kernel_match_results (league fixtures).

Used when international historical CSV has no data for club team names.
Best-effort: returns None fields when DB empty or team unmatched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from typing import Any


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


def _normalize(name: str) -> str:
    return " ".join((name or "").lower().split())


@lru_cache(maxsize=32)
def _alias_index(competition: str | None) -> dict[str, str] | None:
    """Lowercased alias -> canonical id for one competition, or None.

    Returns None when the competition is empty or absent from the registry,
    which disables alias matching entirely for that lookup.
    """
    if not competition:
        return None
    from app.sports._shared.team_aliases import TEAM_ALIASES

    comp_map = TEAM_ALIASES.get(competition)
    if not comp_map:
        return None
    return {alias.lower(): canonical for alias, canonical in comp_map.items()}


def _match_key(name: str, alias_index: dict[str, str] | None) -> str:
    """Comparison key for a team name.

    Resolves through the competition's alias table when possible so that
    "Man City" and "Manchester City" compare equal; falls back to the plain
    normalized string when the name is not in the table, which preserves the
    pre-alias behaviour for teams the registry does not cover.

    The ``canon:`` prefix keeps a canonical id from colliding with a raw name
    that happens to spell the same thing.
    """
    normalized = _normalize(name)
    if alias_index is None or not normalized:
        return normalized
    canonical = alias_index.get(normalized)
    return f"canon:{canonical}" if canonical else normalized


def _points_result(
    home: str,
    away: str,
    home_score: int,
    away_score: int,
    team_key: str,
    alias_index: dict[str, str] | None,
) -> str:
    is_home = _match_key(home, alias_index) == team_key
    is_away = _match_key(away, alias_index) == team_key
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

        alias_index = _alias_index(competition)
        key = _match_key(team_name, alias_index)
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
            if _match_key(h, alias_index) != key and _match_key(a, alias_index) != key:
                continue
            played_rows.append((
                kickoff,
                _points_result(
                    h, a, int(result.home_score), int(result.away_score),
                    key, alias_index,
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
            if _match_key(h, alias_index) == key:
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
    alias_index = _alias_index(competition)
    home_key = _match_key(home_team, alias_index)
    away_key = _match_key(away_team, alias_index)
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
        meetings: list[tuple[datetime | None, int, int]] = []
        for fixture, result in rows:
            if result.home_score is None or result.away_score is None:
                continue
            kickoff = fixture.kickoff_utc or result.finished_at
            if kickoff is not None:
                if kickoff.tzinfo is None:
                    kickoff = kickoff.replace(tzinfo=timezone.utc)
                if kickoff >= before:
                    continue
            fh = _match_key(fixture.home_team or "", alias_index)
            fa = _match_key(fixture.away_team or "", alias_index)
            if {fh, fa} != pair:
                continue
            hs = int(result.home_score)
            aws = int(result.away_score)
            # Map to current-home perspective scores
            if fh == home_key:
                cur_home_gf, cur_home_ga = hs, aws
            else:
                cur_home_gf, cur_home_ga = aws, hs
            meetings.append((kickoff, cur_home_gf, cur_home_ga))

        if not meetings:
            return None

        meetings.sort(
            key=lambda r: r[0].isoformat() if r[0] else "",
            reverse=True,
        )
        meetings = meetings[: max(1, max_matches)]

        home_wins = draws = away_wins = 0
        for _, gf, ga in meetings:
            if gf > ga:
                home_wins += 1
            elif gf < ga:
                away_wins += 1
            else:
                draws += 1

        return {
            "matches_played": len(meetings),
            "home_wins": home_wins,
            "draws": draws,
            "away_wins": away_wins,
            "data_source": "kernel_match_results",
        }
    finally:
        session.close()
