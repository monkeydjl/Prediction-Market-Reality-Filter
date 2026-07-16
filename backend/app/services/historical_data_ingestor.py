# backend/app/services/historical_data_ingestor.py
"""HistoricalDataIngestor — fetches historical matches + results for backtesting.

Delegates to existing sport-specific API clients (balldontlie for NBA,
statsapi.mlb.com for MLB, api-web.nhle.com for NHL). Stores results in
existing kernel_match_fixtures + kernel_match_results tables (additive).
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Any

from app.kernel.kernel_db import get_kernel_session, KernelMatchFixture, KernelMatchResult

logger = logging.getLogger(__name__)


async def fetch_nba_season_games(season: str) -> list[dict[str, Any]]:
    """Fetch NBA games for a season from balldontlie.io.

    Args:
        season: e.g., "2024-25"

    Returns:
        List of game dicts with home_team, away_team, home_score, away_score, season, date.
    """
    # Delegates to existing NBA adapter's fetch logic.
    # This is a thin wrapper that the ingestor calls.
    from app.sports.basketball.nba_adapter import NBAAdapter
    adapter = NBAAdapter()
    games = await adapter.fetch_historical_games(season)
    return games


async def fetch_mlb_season_games(season: str) -> list[dict[str, Any]]:
    """Fetch MLB games for a season from statsapi.mlb.com."""
    from app.sports.baseball.mlb_adapter import MLBDataAdapter
    adapter = MLBDataAdapter()
    games = await adapter.fetch_historical_games(season)
    return games


async def fetch_nhl_season_games(season: str) -> list[dict[str, Any]]:
    """Fetch NHL games for a season from api-web.nhle.com."""
    from app.sports.hockey.nhl_adapter import NHLDataAdapter
    adapter = NHLDataAdapter()
    games = await adapter.fetch_historical_games(season)
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
            for game in games:
                match_id = f"{sport}-{season}-{game['game_id']}"
                # Check if already exists (idempotent)
                existing = session.query(KernelMatchFixture).filter_by(match_id=match_id).first()
                if existing is None:
                    # Parse date string (e.g. "2024-01-01") into DateTime for
                    # the kickoff_utc column (KernelMatchFixture has no match_date).
                    kickoff_utc = None
                    date_str = game.get("date")
                    if date_str:
                        try:
                            kickoff_utc = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
                        except (ValueError, TypeError):
                            kickoff_utc = None
                    fixture = KernelMatchFixture(
                        match_id=match_id,
                        competition=sport,
                        home_team=game["home_team"],
                        away_team=game["away_team"],
                        kickoff_utc=kickoff_utc,
                        season=season,
                    )
                    session.add(fixture)
                    matches_stored += 1

                # Store result if scores available
                if game.get("home_score") is not None and game.get("away_score") is not None:
                    existing_result = session.query(KernelMatchResult).filter_by(match_id=match_id).first()
                    if existing_result is None:
                        # KernelMatchResult has finished_at (DateTime), not finished (bool).
                        result = KernelMatchResult(
                            match_id=match_id,
                            home_score=game["home_score"],
                            away_score=game["away_score"],
                            finished_at=datetime.now(timezone.utc),
                        )
                        session.add(result)
                        results_stored += 1

            session.commit()
        except Exception as exc:
            session.rollback()
            errors.append(str(exc))
            logger.exception("Failed to store %s season %s", sport, season)
        finally:
            session.close()

        return {"matches": matches_stored, "results": results_stored, "errors": errors}
