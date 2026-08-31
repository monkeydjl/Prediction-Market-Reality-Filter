# backend/app/services/historical_data_ingestor.py
"""HistoricalDataIngestor — fetches historical matches + results for backtesting.

Delegates to existing sport-specific API clients (balldontlie for NBA,
statsapi.mlb.com for MLB, api-web.nhle.com for NHL). Stores results in
existing kernel_match_fixtures + kernel_match_results tables (additive).

Also supports:
  - backfill_results_from_fixtures: copy scores from fixtures into results
    (covers adapter sync_schedule paths that only wrote fixture scores)
  - seed_elo_ratings: replay finished games into kernel_elo_ratings
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.kernel.kernel_db import (
    KernelEloRating,
    KernelMatchFixture,
    KernelMatchResult,
    get_kernel_session,
)
from app.sports._shared.match_outcome import outcome_from_scores

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompetitionMeta:
    """What the result/Elo backfills need to know about one competition code.

    ``draws`` is the whole reason this is a record rather than a pair of
    strings: it decides which ``outcome`` token a level score gets, and it
    decides whether the competition may be replayed into
    ``kernel_elo_ratings`` at all.
    """

    sport: str
    competition: str
    draws: bool


#: Every competition code the result backfill understands, and its draw rule.
#: The two scopes below are derived from this one table so that adding a
#: competition cannot leave a hand-maintained list behind.
_SPORT_META: dict[str, CompetitionMeta] = {
    "nba": CompetitionMeta("basketball", "nba", draws=False),
    "mlb": CompetitionMeta("baseball", "mlb", draws=False),
    "nhl": CompetitionMeta("hockey", "nhl", draws=False),
    # P1-E9: football fixtures carried scores that no kernel_match_results row
    # ever received, so fetch_outcome returned None for all 1181 finished club
    # matches and club form/h2h read an empty join. Codes match
    # KernelMatchFixture.competition, which the football adapters write
    # lowercase.
    "epl": CompetitionMeta("football", "epl", draws=True),
    "laliga": CompetitionMeta("football", "laliga", draws=True),
    "seriea": CompetitionMeta("football", "seriea", draws=True),
    "bundesliga": CompetitionMeta("football", "bundesliga", draws=True),
    "ligue1": CompetitionMeta("football", "ligue1", draws=True),
    "ucl": CompetitionMeta("football", "ucl", draws=True),
}

#: Competitions whose scored fixtures may be copied into kernel_match_results.
BACKFILLABLE_COMPETITIONS: frozenset[str] = frozenset(_SPORT_META)

#: Competitions that may be replayed into kernel_elo_ratings.
#:
#: Draw-capable competitions are excluded, not pending: ``seed_elo_from_games``
#: scores a game as ``home_score > away_score`` and has no third bucket, so a
#: level football score would be replayed as an away win. Football Elo comes
#: from ClubElo through the club Elo cache -- a measured source -- and seeding
#: self-computed ratings over it would replace measured values with invented
#: ones.
ELO_SEEDABLE_COMPETITIONS: frozenset[str] = frozenset(
    code for code, meta in _SPORT_META.items() if not meta.draws
)

#: A fixture's score is a *result* only once the fixture is over.
#: ``football_data_client.parse_fixture`` reads ``score.fullTime``, which
#: Football-Data.org also populates while a match is IN_PLAY, so a scored row is
#: not by itself a finished one.
FINAL_FIXTURE_STATUS = "finished"


def _elo_params_for_sport(sport: str) -> dict[str, float | int]:
    """Elo params: applied Optuna row first, then settings defaults."""
    from app.kernel.elo_params_resolve import resolve_elo_params

    return resolve_elo_params(sport)


async def fetch_nba_season_games(season: str) -> list[dict[str, Any]]:
    """Fetch NBA games for a season from balldontlie.io.

    Args:
        season: e.g., "2024-25"

    Returns:
        List of game dicts with home_team, away_team, home_score, away_score, season, date.
    """
    from app.sports.basketball.balldontlie_client import fetch_nba_games
    from app.sports.basketball.nba_adapter import parse_nba_game

    season_year = int(season.split("-")[0])
    raw_games = fetch_nba_games(season_year)
    games: list[dict[str, Any]] = []
    for raw in raw_games:
        parsed = parse_nba_game(raw)
        if parsed:
            games.append({
                "game_id": raw.get("id"),
                "home_team": parsed["home_team"],
                "away_team": parsed["away_team"],
                "home_score": parsed["home_score"],
                "away_score": parsed["away_score"],
                "stage": parsed.get("stage") or "regular_season",
                "status": parsed.get("status") or "scheduled",
                "date": parsed["kickoff_utc"].isoformat() if parsed.get("kickoff_utc") else None,
            })
    return games


async def fetch_mlb_season_games(season: str) -> list[dict[str, Any]]:
    """Fetch MLB games for a season from statsapi.mlb.com."""
    from app.sports.baseball.mlb_stats_client import fetch_mlb_schedule
    from app.sports.baseball.mlb_adapter import parse_mlb_game

    start = f"{season}-01-01"
    end = f"{season}-12-31"
    raw_games = fetch_mlb_schedule(start, end)
    games: list[dict[str, Any]] = []
    for raw in raw_games:
        parsed = parse_mlb_game(raw)
        if parsed:
            games.append({
                "game_id": raw.get("gamePk"),
                "home_team": parsed["home_team"],
                "away_team": parsed["away_team"],
                "home_score": parsed["home_score"],
                "away_score": parsed["away_score"],
                "stage": parsed.get("stage") or "regular_season",
                "status": parsed.get("status") or "scheduled",
                "date": parsed["kickoff_utc"].isoformat() if parsed.get("kickoff_utc") else None,
            })
    return games


async def fetch_nhl_season_games(season: str) -> list[dict[str, Any]]:
    """Fetch NHL games for a season from api-web.nhle.com."""
    from app.sports.hockey.nhl_stats_client import fetch_nhl_schedule
    from app.sports.hockey.nhl_adapter import parse_nhl_game

    # Convert "2023-24" / "2024-25" to NHL season key "20232024" / "20242025"
    # (eight digits: startYYYY + endYYYY). A naive suffix concat yields
    # "202425" which 404s on club-schedule-season.
    parts = season.split("-")
    if len(parts) == 2 and parts[0].isdigit():
        y0 = int(parts[0])
        suffix = parts[1]
        if suffix.isdigit() and len(suffix) <= 2:
            y1 = (y0 // 100) * 100 + int(suffix)
            if y1 < y0:
                y1 += 100
            nhl_season = f"{y0}{y1:04d}"
        elif suffix.isdigit():
            nhl_season = f"{y0}{int(suffix):04d}"
        else:
            nhl_season = season
    else:
        nhl_season = season
    raw_games = fetch_nhl_schedule(nhl_season)
    games: list[dict[str, Any]] = []
    for raw in raw_games:
        parsed = parse_nhl_game(raw)
        if parsed:
            games.append({
                "game_id": raw.get("id"),
                "home_team": parsed["home_team"],
                "away_team": parsed["away_team"],
                "home_score": parsed["home_score"],
                "away_score": parsed["away_score"],
                "stage": parsed.get("stage") or "regular_season",
                "status": parsed.get("status") or "scheduled",
                "date": parsed["kickoff_utc"].isoformat() if parsed.get("kickoff_utc") else None,
            })
    return games


# Map sport -> fetcher function name. Looked up dynamically at call time
# (via getattr on this module) so tests can patch the module-level function
# attribute and have the ingestor pick up the mock.
_FETCHER_NAMES = {
    "nba": "fetch_nba_season_games",
    "mlb": "fetch_mlb_season_games",
    "nhl": "fetch_nhl_season_games",
}


class HistoricalDataIngestor:
    """Fetches historical matches + results from existing sports APIs.

    ``session_factory=None`` uses the global kernel session, which is what the
    four production call sites want. It is injectable because ``seed_elo_ratings``
    *overwrites* ``kernel_elo_ratings`` for a competition, and one of its callers
    (``OptimizedParamsStore``) can be scoped to a specific ``db_path``: without
    the parameter that caller silently overwrote the ratings in
    ``settings.KERNEL_DB_FILE`` instead of the ones in its own database.
    """

    def __init__(self, session_factory: Callable[[], Any] | None = None) -> None:
        self._session_factory = session_factory or get_kernel_session

    async def ingest_season(self, sport: str, season: str) -> dict[str, Any]:
        """Fetch + store historical matches + results for one season.

        Args:
            sport: "nba" / "mlb" / "nhl"
            season: e.g., "2024-25" for NBA/NHL, "2024" for MLB

        Returns:
            {"matches": N, "results": N, "errors": [...]}
        """
        fetcher_name = _FETCHER_NAMES.get(sport)
        if fetcher_name is None:
            return {"matches": 0, "results": 0, "errors": [f"Unknown sport: {sport}"]}
        # Dynamic lookup so tests can patch the module attribute.
        fetcher = getattr(sys.modules[__name__], fetcher_name, None)
        if fetcher is None:
            return {"matches": 0, "results": 0, "errors": [f"Fetcher not found: {fetcher_name}"]}

        try:
            games = await fetcher(season)
        except Exception as exc:
            logger.exception("Failed to fetch %s season %s", sport, season)
            return {"matches": 0, "results": 0, "errors": [str(exc)]}

        matches_stored = 0
        results_stored = 0
        errors: list[str] = []

        session = self._session_factory()
        try:
            now = datetime.now(timezone.utc)
            for game in games:
                # Align with adapter match_id format: {sport}-{game_id}
                match_id = f"{sport}-{game['game_id']}"
                kickoff_utc = None
                date_str = game.get("date")
                if date_str:
                    try:
                        kickoff_utc = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        kickoff_utc = None

                stage = game.get("stage") or "regular_season"
                status = game.get("status") or "scheduled"
                home_score = game.get("home_score")
                away_score = game.get("away_score")
                if (
                    home_score is not None
                    and away_score is not None
                    and status != "finished"
                ):
                    status = "finished"

                existing = session.query(KernelMatchFixture).filter_by(match_id=match_id).first()
                if existing is None:
                    fixture = KernelMatchFixture(
                        match_id=match_id,
                        competition=sport,
                        home_team=game["home_team"],
                        away_team=game["away_team"],
                        kickoff_utc=kickoff_utc,
                        season=season,
                        stage=stage,
                        status=status,
                        home_score=home_score,
                        away_score=away_score,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(fixture)
                    matches_stored += 1
                else:
                    existing.home_team = game["home_team"]
                    existing.away_team = game["away_team"]
                    if kickoff_utc is not None:
                        existing.kickoff_utc = kickoff_utc
                    existing.season = season
                    existing.stage = stage
                    existing.status = status
                    if home_score is not None:
                        existing.home_score = home_score
                    if away_score is not None:
                        existing.away_score = away_score
                    existing.updated_at = now

                if home_score is not None and away_score is not None:
                    try:
                        hs = int(home_score)
                        aws = int(away_score)
                    except (TypeError, ValueError):
                        continue
                    existing_result = (
                        session.query(KernelMatchResult).filter_by(match_id=match_id).first()
                    )
                    finished_at = kickoff_utc or now
                    # Read the draw rule from the table rather than hardcoding
                    # False: _FETCHER_NAMES admits only the three binary sports
                    # today, so the two agree, but a hardcoded rule here would
                    # not follow the declaration if a fetcher were added.
                    ingest_meta = _SPORT_META.get(sport)
                    outcome = outcome_from_scores(
                        hs, aws, allow_draw=bool(ingest_meta and ingest_meta.draws),
                    )
                    if existing_result is None:
                        result = KernelMatchResult(
                            match_id=match_id,
                            home_score=hs,
                            away_score=aws,
                            outcome=outcome,
                            finished_at=finished_at,
                            created_at=now,
                        )
                        session.add(result)
                        results_stored += 1
                    else:
                        existing_result.home_score = hs
                        existing_result.away_score = aws
                        existing_result.outcome = outcome
                        if existing_result.finished_at is None:
                            existing_result.finished_at = finished_at

            session.commit()
        except Exception as exc:
            session.rollback()
            errors.append(str(exc))
            logger.exception("Failed to store %s season %s", sport, season)
        finally:
            session.close()

        return {"matches": matches_stored, "results": results_stored, "errors": errors}

    def backfill_results_from_fixtures(self, sport: str | None = None) -> dict[str, Any]:
        """Copy finished, scored fixtures into kernel_match_results (idempotent).

        Adapter sync_schedule historically wrote scores only on fixtures.
        Phase 9 match_loader + learning paths need KernelMatchResult rows.

        Args:
            sport: any code in :data:`BACKFILLABLE_COMPETITIONS`, or None for
                all of them. Football competitions are included as of P1-E9:
                their fixtures carried scores that no result row ever received,
                so ``fetch_outcome`` returned None for every finished club
                match.

        Returns:
            {"results": N, "updated": N, "sports": {...}, "errors": [...]}
        """
        sports = sorted(BACKFILLABLE_COMPETITIONS) if sport is None else [sport]
        total_new = 0
        total_updated = 0
        per_sport: dict[str, dict[str, int]] = {}
        errors: list[str] = []

        session = self._session_factory()
        try:
            now = datetime.now(timezone.utc)
            for sp in sports:
                meta = _SPORT_META.get(sp)
                if meta is None:
                    errors.append(f"Unknown sport: {sp}")
                    continue
                new_n = 0
                upd_n = 0
                # status is part of the filter, not a thing to normalise
                # afterwards: parse_fixture copies score.fullTime while a match
                # is IN_PLAY, so a scored row is not yet a final one, and
                # copying it would let fetch_outcome settle a prediction
                # against a partial score. Measured on the live kernel DB:
                # zero scored fixtures in any sport are currently non-finished,
                # so this narrows a reachable window rather than dropping rows.
                fixtures = (
                    session.query(KernelMatchFixture)
                    .filter(
                        KernelMatchFixture.competition == sp,
                        KernelMatchFixture.status == FINAL_FIXTURE_STATUS,
                        KernelMatchFixture.home_score.isnot(None),
                        KernelMatchFixture.away_score.isnot(None),
                    )
                    .all()
                )
                for fix in fixtures:
                    # Explicit None checks instead of int() inside try/except:
                    # the isnot(None) filter above is the real guard but does not
                    # reach the type checker, and on an Integer column None was
                    # the only thing int() could have raised on.
                    if fix.home_score is None or fix.away_score is None:
                        continue
                    hs = int(fix.home_score)
                    aws = int(fix.away_score)
                    outcome = outcome_from_scores(hs, aws, allow_draw=meta.draws)
                    finished_at = fix.kickoff_utc or now
                    existing = session.get(KernelMatchResult, fix.match_id)
                    if existing is None:
                        session.add(
                            KernelMatchResult(
                                match_id=fix.match_id,
                                home_score=hs,
                                away_score=aws,
                                outcome=outcome,
                                finished_at=finished_at,
                                created_at=now,
                            )
                        )
                        new_n += 1
                    else:
                        changed = (
                            existing.home_score != hs
                            or existing.away_score != aws
                            or existing.outcome != outcome
                        )
                        if changed or existing.finished_at is None:
                            existing.home_score = hs
                            existing.away_score = aws
                            existing.outcome = outcome
                            if existing.finished_at is None:
                                existing.finished_at = finished_at
                            upd_n += 1
                per_sport[sp] = {"results": new_n, "updated": upd_n, "scanned": len(fixtures)}
                total_new += new_n
                total_updated += upd_n
            session.commit()
        except Exception as exc:
            session.rollback()
            errors.append(str(exc))
            logger.exception("Failed to backfill results from fixtures")
        finally:
            session.close()

        return {
            "results": total_new,
            "updated": total_updated,
            "sports": per_sport,
            "errors": errors,
        }

    def seed_elo_ratings(self, sport: str | None = None) -> dict[str, Any]:
        """Replay chronological results into kernel_elo_ratings (overwrite).

        Args:
            sport: any code in :data:`ELO_SEEDABLE_COMPETITIONS`, or None for
                all of them. A draw-capable competition is refused rather than
                replayed: the scope is narrower than
                :data:`BACKFILLABLE_COMPETITIONS` on purpose.

        Returns:
            {"teams": N, "sports": {...}, "errors": [...]}
        """
        from app.kernel.backtest.match_loader import load_sport_matches_for_backtest
        from app.sports._shared.elo_calculator import seed_elo_from_games

        sports = sorted(ELO_SEEDABLE_COMPETITIONS) if sport is None else [sport]
        total_teams = 0
        per_sport: dict[str, dict[str, Any]] = {}
        errors: list[str] = []

        session = self._session_factory()
        try:
            now = datetime.now(timezone.utc)
            for sp in sports:
                meta = _SPORT_META.get(sp)
                if meta is None:
                    errors.append(f"Unknown sport: {sp}")
                    continue
                # Refused, not skipped silently: seed_elo_from_games treats any
                # non-home-win as an away win, so replaying football would score
                # every draw as a loss for the home side -- and it would
                # overwrite the measured ClubElo ratings with self-computed
                # ones. An explicit error is what tells the caller the request
                # was wrong rather than empty.
                if meta.draws:
                    errors.append(
                        f"Elo seeding is binary-only; {sp} allows draws",
                    )
                    continue
                matches = load_sport_matches_for_backtest(
                    sp, session_factory=self._session_factory,
                )
                # Known asymmetry: _elo_params_for_sport -> resolve_elo_params
                # builds its own bare OptimizedParamsStore, so the *params* are
                # read from settings.KERNEL_DB_FILE even when the matches above
                # and the ratings below are this session's. Harmless for the
                # four production callers, which are all on the global DB; only
                # a caller that passed session_factory would see the split, and
                # closing it means giving OptimizedParamsStore a session_factory
                # of its own rather than just a db_path.
                if not matches:
                    per_sport[sp] = {"teams": 0, "matches": 0}
                    continue
                params = _elo_params_for_sport(sp)
                games = [
                    {
                        "home_team": m["home_team"],
                        "away_team": m["away_team"],
                        "home_score": m["home_score"],
                        "away_score": m["away_score"],
                        "is_playoff": bool(m.get("is_playoff")),
                        "season": m["season"],
                    }
                    for m in matches
                ]
                ratings = seed_elo_from_games(
                    games,
                    hfa=int(params["hfa"]),
                    k_regular=int(params["k_regular"]),
                    k_playoff=int(params["k_playoff"]),
                )
                # Apply final season-carry is already in seed_elo_from_games
                # when seasons change mid-replay; no extra regression needed.
                # Note: seed_elo_from_games uses fixed carry=0.75 via
                # apply_season_regression default. Override by replaying with
                # sport carry when carry != 0.75.
                if abs(float(params["season_carry"]) - 0.75) > 1e-9:
                    from app.sports._shared.elo_calculator import (
                        apply_season_regression,
                        compute_expected_score,
                        update_elo,
                    )

                    ratings = {}
                    current_season = None
                    carry = float(params["season_carry"])
                    initial = float(params["initial"])
                    hfa = int(params["hfa"])
                    k_reg = int(params["k_regular"])
                    k_po = int(params["k_playoff"])
                    for game in games:
                        season = game["season"]
                        if current_season is not None and season != current_season:
                            for team in ratings:
                                ratings[team] = apply_season_regression(
                                    ratings[team], mean=initial, carry=carry,
                                )
                        current_season = season
                        home = game["home_team"]
                        away = game["away_team"]
                        if home not in ratings:
                            ratings[home] = initial
                        if away not in ratings:
                            ratings[away] = initial
                        elo_home = ratings[home]
                        elo_away = ratings[away]
                        expected = compute_expected_score(elo_home, elo_away, hfa)
                        home_won = game["home_score"] > game["away_score"]
                        actual_home = 1.0 if home_won else 0.0
                        k = k_po if game.get("is_playoff") else k_reg
                        ratings[home] = update_elo(elo_home, expected, actual_home, k)
                        ratings[away] = update_elo(
                            elo_away, 1.0 - expected, 1.0 - actual_home, k,
                        )

                # Replace all rows for this competition
                existing_rows = (
                    session.query(KernelEloRating)
                    .filter(KernelEloRating.competition == meta.competition)
                    .all()
                )
                for row in existing_rows:
                    session.delete(row)
                session.flush()

                for team_name, elo in ratings.items():
                    session.add(
                        KernelEloRating(
                            team_name=team_name,
                            sport=meta.sport,
                            competition=meta.competition,
                            elo_rating=float(elo),
                            source="self_computed",
                            updated_at=now,
                        )
                    )
                n_teams = len(ratings)
                total_teams += n_teams
                per_sport[sp] = {
                    "teams": n_teams,
                    "matches": len(matches),
                    "params": {
                        "hfa": params["hfa"],
                        "k_regular": params["k_regular"],
                        "k_playoff": params["k_playoff"],
                        "season_carry": params["season_carry"],
                    },
                }
            session.commit()
        except Exception as exc:
            session.rollback()
            errors.append(str(exc))
            logger.exception("Failed to seed Elo ratings")
        finally:
            session.close()

        return {"teams": total_teams, "sports": per_sport, "errors": errors}
