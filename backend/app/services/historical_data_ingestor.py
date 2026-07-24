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
from datetime import datetime, timezone
from typing import Any

from app.kernel.kernel_db import (
    KernelEloRating,
    KernelMatchFixture,
    KernelMatchResult,
    get_kernel_session,
)

logger = logging.getLogger(__name__)

_SPORT_META: dict[str, dict[str, str]] = {
    "nba": {"sport": "basketball", "competition": "nba"},
    "mlb": {"sport": "baseball", "competition": "mlb"},
    "nhl": {"sport": "hockey", "competition": "nhl"},
}


def _binary_outcome(home_score: int, away_score: int) -> str:
    """Binary sports (NBA/MLB/NHL): home_win if home ahead, else away_win."""
    return "home_win" if home_score > away_score else "away_win"


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
    """Fetches historical matches + results from existing sports APIs."""

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

        session = get_kernel_session()
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
                    if existing_result is None:
                        result = KernelMatchResult(
                            match_id=match_id,
                            home_score=hs,
                            away_score=aws,
                            outcome=_binary_outcome(hs, aws),
                            finished_at=finished_at,
                            created_at=now,
                        )
                        session.add(result)
                        results_stored += 1
                    else:
                        existing_result.home_score = hs
                        existing_result.away_score = aws
                        existing_result.outcome = _binary_outcome(hs, aws)
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
        """Copy scored fixtures into kernel_match_results (idempotent).

        Adapter sync_schedule historically wrote scores only on fixtures.
        Phase 9 match_loader + learning paths need KernelMatchResult rows.

        Args:
            sport: "nba" / "mlb" / "nhl" or None for all three.

        Returns:
            {"results": N, "updated": N, "sports": {...}, "errors": [...]}
        """
        sports = ["nba", "mlb", "nhl"] if sport is None else [sport]
        total_new = 0
        total_updated = 0
        per_sport: dict[str, dict[str, int]] = {}
        errors: list[str] = []

        session = get_kernel_session()
        try:
            now = datetime.now(timezone.utc)
            for sp in sports:
                if sp not in _SPORT_META:
                    errors.append(f"Unknown sport: {sp}")
                    continue
                new_n = 0
                upd_n = 0
                fixtures = (
                    session.query(KernelMatchFixture)
                    .filter(
                        KernelMatchFixture.competition == sp,
                        KernelMatchFixture.home_score.isnot(None),
                        KernelMatchFixture.away_score.isnot(None),
                    )
                    .all()
                )
                for fix in fixtures:
                    try:
                        hs = int(fix.home_score)
                        aws = int(fix.away_score)
                    except (TypeError, ValueError):
                        continue
                    outcome = _binary_outcome(hs, aws)
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
                    if fix.status != "finished":
                        fix.status = "finished"
                        fix.updated_at = now
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
            sport: "nba" / "mlb" / "nhl" or None for all three.

        Returns:
            {"teams": N, "sports": {...}, "errors": [...]}
        """
        from app.kernel.backtest.match_loader import load_sport_matches_for_backtest
        from app.sports._shared.elo_calculator import seed_elo_from_games

        sports = ["nba", "mlb", "nhl"] if sport is None else [sport]
        total_teams = 0
        per_sport: dict[str, dict[str, Any]] = {}
        errors: list[str] = []

        session = get_kernel_session()
        try:
            now = datetime.now(timezone.utc)
            for sp in sports:
                meta = _SPORT_META.get(sp)
                if meta is None:
                    errors.append(f"Unknown sport: {sp}")
                    continue
                matches = load_sport_matches_for_backtest(sp)
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
                    .filter(KernelEloRating.competition == meta["competition"])
                    .all()
                )
                for row in existing_rows:
                    session.delete(row)
                session.flush()

                for team_name, elo in ratings.items():
                    session.add(
                        KernelEloRating(
                            team_name=team_name,
                            sport=meta["sport"],
                            competition=meta["competition"],
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
