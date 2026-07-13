# backend/app/kernel/adapters/world_cup_adapter.py
"""WorldCupAdapter — bridges existing world_cup_* services to DataAdapter Protocol.

This adapter calls existing ``world_cup_*`` services internally but exposes them
through the sport-agnostic :class:`~app.kernel.protocols.DataAdapter` interface.
The Kernel never sees ``world_cup_*`` — it only sees ``DataAdapter``.

Design notes
------------
* Existing ``world_cup_*`` services are **not modified**. The adapter wraps them.
* Some wrapped services (``get_elo_rating``, ``get_cached_odds``) are
  ``async``. Because the :class:`DataAdapter` Protocol is synchronous, the
  adapter bridges them via :func:`asyncio.run` inside ``fetch_all_data``.
  This is acceptable for batch/CLI usage; in a long-running async server the
  caller should manage the event loop explicitly.
* Database lookups (``get_match_identity``, ``fetch_outcome``) gracefully
  degrade when tables are absent or the DB is not yet initialised, returning
  stub identities or ``None`` respectively.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.kernel.domain import (
    SportIdentity,
    CompetitionIdentity,
    SeasonIdentity,
    TeamIdentity,
    MatchIdentity,
    MatchOutcome,
)
from app.kernel.protocols import ScheduleFilter, RawMatchData

logger = logging.getLogger(__name__)

# Module-level singletons for the World Cup competition identity.
_SPORT = SportIdentity(code="football", name="Football")
_COMPETITION = CompetitionIdentity(
    code="world_cup", name="FIFA World Cup", sport=_SPORT
)

# Fallback values used when DB data is unavailable.
_DEFAULT_SEASON_KEY = "2026"
_DEFAULT_KICKOFF = datetime(2026, 6, 13, tzinfo=timezone.utc)


class WorldCupAdapter:
    """Bridges existing ``world_cup_*`` services to the DataAdapter Protocol.

    Implements every method defined on
    :class:`~app.kernel.protocols.DataAdapter` by delegating to existing
    services and translating results into Kernel domain types.
    """

    # ------------------------------------------------------------------
    # Schedule + identity
    # ------------------------------------------------------------------

    def get_match_identity(self, match_id: str) -> MatchIdentity:
        """Get :class:`MatchIdentity` from the ``match_fixtures`` table.

        If the fixture is not found (or the DB / table is unavailable), a
        stub :class:`MatchIdentity` is returned so that callers always get
        a valid identity object.
        """
        from app.utils.prediction_db import get_prediction_session
        from app.models.world_cup_prediction import MatchFixture

        fixture = None
        session = None
        try:
            session = get_prediction_session()
            fixture = session.get(MatchFixture, match_id)
        except Exception as exc:  # noqa: BLE001 — table may not exist yet
            logger.warning(
                "Failed to query match_fixtures for %s: %s", match_id, exc
            )
        finally:
            if session is not None:
                session.close()

        season = SeasonIdentity(
            competition=_COMPETITION, season_key=_DEFAULT_SEASON_KEY
        )

        if fixture is None:
            # Return a stub — real usage requires DB data.
            home = TeamIdentity(
                code="HOME", name="Home", competition=_COMPETITION
            )
            away = TeamIdentity(
                code="AWAY", name="Away", competition=_COMPETITION
            )
            return MatchIdentity(
                match_id=match_id,
                season=season,
                stage="group_stage",
                round=None,
                home=home,
                away=away,
                kickoff_utc=_DEFAULT_KICKOFF,
            )

        home = TeamIdentity(
            code=fixture.home_team[:3].upper(),
            name=fixture.home_team,
            competition=_COMPETITION,
        )
        away = TeamIdentity(
            code=fixture.away_team[:3].upper(),
            name=fixture.away_team,
            competition=_COMPETITION,
        )
        return MatchIdentity(
            match_id=fixture.match_id,
            season=season,
            stage=fixture.stage or "group_stage",
            round=None,
            home=home,
            away=away,
            kickoff_utc=fixture.kickoff_utc or _DEFAULT_KICKOFF,
        )

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        """Fetch match schedule from existing ``world_cup`` services."""
        from app.utils.prediction_db import get_prediction_session
        from app.models.world_cup_prediction import MatchFixture
        from sqlalchemy import select

        session = None
        try:
            session = get_prediction_session()
            query = select(MatchFixture)
            if filters.status:
                query = query.where(MatchFixture.status == filters.status)
            if filters.stage:
                query = query.where(MatchFixture.stage == filters.stage)
            if filters.limit:
                query = query.limit(filters.limit)

            fixtures = session.execute(query).scalars().all()
            return [
                RawMatchData(
                    match=self.get_match_identity(f.match_id), raw_json={}
                )
                for f in fixtures
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch schedule: %s", exc)
            return []
        finally:
            if session is not None:
                session.close()

    def sync_schedule(self) -> int:
        """Sync fixtures from external sources.

        Returns the number of fixtures synced (an ``int``). If the sync
        fails for any reason, returns ``0``.
        """
        try:
            from app.services.world_cup_match_service import (
                sync_world_cup_fixtures,
            )

            result = sync_world_cup_fixtures()
            # ``sync_world_cup_fixtures`` always returns a dict (it catches
            # all exceptions internally). Extract the count of synced
            # fixtures; on error the count is 0.
            if isinstance(result, dict):
                if result.get("status") == "ok":
                    return int(result.get("fixtures_synced", 0))
                return 0
            if isinstance(result, int):
                return result
            return 0
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to sync schedule: %s", exc)
            return 0

    # ------------------------------------------------------------------
    # Outcome
    # ------------------------------------------------------------------

    def fetch_outcome(self, match_id: str) -> MatchOutcome | None:
        """Fetch :class:`MatchOutcome` from the ``match_results`` table.

        Returns ``None`` if the result is not found or the DB is
        unavailable.
        """
        from app.utils.prediction_db import get_prediction_session
        from app.models.world_cup_prediction import MatchResult

        result = None
        session = None
        try:
            session = get_prediction_session()
            result = session.get(MatchResult, match_id)
        except Exception as exc:  # noqa: BLE001 — table may not exist yet
            logger.warning(
                "Failed to query match_results for %s: %s", match_id, exc
            )
        finally:
            if session is not None:
                session.close()

        if result is None:
            return None

        return MatchOutcome(
            match_id=match_id,
            home_score=result.final_home_score,
            away_score=result.final_away_score,
            outcome=result.outcome,
            finished_at=result.finished_at or datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Raw data fetching
    # ------------------------------------------------------------------

    def fetch_all_data(self, match: MatchIdentity) -> dict:
        """Fetch all raw data for a match from existing ``world_cup`` services.

        The returned dict has the shape::

            {
                "team": {"elo_home": float, "elo_away": float},
                "market": {"odds_home": float, "odds_draw": float, "odds_away": float},
                "player": {},
                "environment": {},
                "general": {},
            }

        Some wrapped services are ``async``; they are bridged via
        :func:`asyncio.run`. If a service fails, the corresponding sub-dict
        is left empty.
        """
        raw: dict = {
            "team": {},
            "market": {},
            "player": {},
            "environment": {},
            "general": {},
        }

        # --- Fetch Elo ratings + odds in a single event loop ---
        # Both ``get_elo_rating`` and ``get_cached_odds`` are async. They are
        # bridged into the synchronous DataAdapter Protocol via a single
        # :func:`asyncio.run` call that runs them concurrently with
        # :func:`asyncio.gather`. ``return_exceptions=True`` keeps an
        # individual service failure from aborting the whole batch, preserving
        # the per-service graceful degradation of the previous implementation.
        try:
            from app.services.elo_ratings_service import get_elo_rating
            from app.services.odds_cache_service import get_cached_odds

            async def _gather():
                return await asyncio.gather(
                    get_elo_rating(match.home.name),
                    get_elo_rating(match.away.name),
                    get_cached_odds(match.home.name, match.away.name),
                    return_exceptions=True,
                )

            elo_home_raw, elo_away_raw, odds = asyncio.run(_gather())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch raw match data: %s", exc)
            return raw

        # --- Elo ratings ---
        if isinstance(elo_home_raw, dict):
            raw["team"]["elo_home"] = elo_home_raw.get("elo_rating")
        elif isinstance(elo_home_raw, BaseException):
            logger.warning(
                "Failed to fetch Elo rating for %s: %s",
                match.home.name, elo_home_raw,
            )

        if isinstance(elo_away_raw, dict):
            raw["team"]["elo_away"] = elo_away_raw.get("elo_rating")
        elif isinstance(elo_away_raw, BaseException):
            logger.warning(
                "Failed to fetch Elo rating for %s: %s",
                match.away.name, elo_away_raw,
            )

        # --- Odds ---
        if isinstance(odds, dict) and odds:
            raw["market"]["odds_home"] = odds.get("home")
            raw["market"]["odds_draw"] = odds.get("draw")
            raw["market"]["odds_away"] = odds.get("away")
            raw["market"]["odds_source"] = odds.get("source")
            raw["market"]["odds_fresh"] = not odds.get("stale", True)
        elif isinstance(odds, BaseException):
            logger.warning("Failed to fetch odds: %s", odds)

        return raw

    def fetch_team_data(self, team: TeamIdentity) -> dict:
        """Fetch team-level data (stub — extend as needed)."""
        return {}

    def fetch_player_data(self, team: TeamIdentity) -> dict:
        """Fetch player-level data (stub — extend as needed)."""
        return {}

    def fetch_market_data(self, match: MatchIdentity) -> dict:
        """Fetch market-level data (stub — extend as needed)."""
        return {}
