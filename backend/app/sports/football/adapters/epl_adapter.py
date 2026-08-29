# backend/app/sports/football/adapters/epl_adapter.py
"""EPLAdapter — DataAdapter Protocol implementation for English Premier League.

Structurally identical to UCLAdapter with different constants:
- Competition code: "epl" (Football-Data.org code: "PL")
- No stage mapping (all stages → "regular_season")
- EPL-specific team aliases for ClubElo.com
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import ScheduleFilter, RawMatchData
from app.sports.football.adapters._shared import (
    fetch_elo_and_odds, query_fixture, query_result,
    build_match_identity, build_match_outcome, save_fixture,
)
# Imported at module level (rather than lazily inside sync_schedule) so that
# unit tests can patch them via
#   @patch("app.sports.football.adapters.epl_adapter.fetch_competition_fixtures")
#   @patch("app.sports.football.adapters.epl_adapter.parse_fixture")
# and so sync_schedule resolves the (possibly patched) module-global name.
# A lazy in-function import would both break the patch (AttributeError on the
# module attribute) and shadow the mock with a fresh local binding.
from app.services.football_data_client import (
    fetch_competition_fixtures, parse_fixture,
)
from app.kernel.kernel_db import KernelMatchFixture, KernelMatchResult

logger = logging.getLogger(__name__)

_FOOTBALL = SportIdentity(code="football", name="Football")
_COMPETITION = CompetitionIdentity(
    code="epl", name="English Premier League", sport=_FOOTBALL
)
_DEFAULT_SEASON = "2026-27"
_DEFAULT_STAGE = "regular_season"
_DEFAULT_KICKOFF = datetime(2026, 8, 21, tzinfo=timezone.utc)

# EPL has no knockout stages — all fixtures are regular season
_STAGE_MAP: dict[str, str] = {}

_MATCH_ID_PREFIX = "epl-"
_FD_COMPETITION = "PL"
# Football-Data.org season year = autumn start year (2026 → 2026-27).
_FD_SEASON = 2026

# Football-Data.org name → ClubElo.com URL name
_TEAM_ALIASES = {
    "Arsenal FC": "Arsenal",
    "Arsenal": "Arsenal",
    "Aston Villa FC": "AstonVilla",
    "Aston Villa": "AstonVilla",
    " AFC Bournemouth": "Bournemouth",
    "Bournemouth": "Bournemouth",
    "Brentford FC": "Brentford",
    "Brentford": "Brentford",
    "Brighton & Hove Albion FC": "Brighton",
    "Brighton": "Brighton",
    "Chelsea FC": "Chelsea",
    "Chelsea": "Chelsea",
    "Crystal Palace FC": "CrystalPalace",
    "Crystal Palace": "CrystalPalace",
    "Everton FC": "Everton",
    "Everton": "Everton",
    "Fulham FC": "Fulham",
    "Fulham": "Fulham",
    "Liverpool FC": "Liverpool",
    "Liverpool": "Liverpool",
    "Luton Town FC": "LutonTown",
    "Luton Town": "LutonTown",
    "Manchester City FC": "ManCity",
    "Manchester City": "ManCity",
    "Manchester United FC": "ManUnited",
    "Manchester United": "ManUnited",
    "Newcastle United FC": "Newcastle",
    "Newcastle": "Newcastle",
    "Nottingham Forest FC": "NottinghamForest",
    "Nottingham Forest": "NottinghamForest",
    "Sheffield United FC": "SheffieldUnited",
    "Sheffield United": "SheffieldUnited",
    "Tottenham Hotspur FC": "Tottenham",
    "Tottenham": "Tottenham",
    "West Ham United FC": "WestHam",
    "West Ham": "WestHam",
    "Wolverhampton Wanderers FC": "Wolves",
    "Wolves": "Wolves",
    "Burnley FC": "Burnley",
    "Burnley": "Burnley",
    "Leicester City FC": "Leicester",
    "Leicester City": "Leicester",
    "Ipswich Town FC": "Ipswich",
    "Ipswich Town": "Ipswich",
    "Southampton FC": "Southampton",
    "Southampton": "Southampton",
    "Leeds United FC": "Leeds",
    "Leeds United": "Leeds",
}


def _stub_identity(match_id: str) -> MatchIdentity:
    """Return a stub MatchIdentity when fixture data is unavailable."""
    home = TeamIdentity(code="HOME", name="Home", competition=_COMPETITION)
    away = TeamIdentity(code="AWAY", name="Away", competition=_COMPETITION)
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=_COMPETITION, season_key=_DEFAULT_SEASON),
        stage=_DEFAULT_STAGE,
        round=None,
        home=home,
        away=away,
        kickoff_utc=_DEFAULT_KICKOFF,
        is_stub=True,
    )


class EPLAdapter:
    """DataAdapter Protocol implementation for English Premier League."""

    def get_match_identity(self, match_id: str) -> MatchIdentity:
        fixture = query_fixture(match_id, KernelMatchFixture)
        if fixture is None:
            return _stub_identity(match_id)
        return build_match_identity(
            fixture, _COMPETITION, _DEFAULT_SEASON, _DEFAULT_STAGE
        )

    def fetch_all_data(self, match: MatchIdentity) -> dict:
        return fetch_elo_and_odds(
            match, elo_scope="club", team_aliases=_TEAM_ALIASES
        )

    def fetch_outcome(self, match_id: str) -> MatchOutcome | None:
        result = query_result(match_id, KernelMatchResult)
        return build_match_outcome(result)

    def sync_schedule(self) -> int:
        try:
            fixtures_raw = fetch_competition_fixtures(_FD_COMPETITION, season=_FD_SEASON)
            count = 0
            for raw in fixtures_raw:
                parsed = parse_fixture(
                    raw, stage_mapping=_STAGE_MAP,
                    match_id_prefix=_MATCH_ID_PREFIX,
                )
                if parsed:
                    save_fixture(parsed, "epl", _DEFAULT_SEASON)
                    count += 1
            return count
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to sync EPL schedule: %s", exc)
            return 0

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        from app.kernel.kernel_db import get_kernel_session
        from sqlalchemy import select
        session = get_kernel_session()
        try:
            query = select(KernelMatchFixture).where(
                KernelMatchFixture.competition == "epl"
            )
            if filters.status:
                query = query.where(KernelMatchFixture.status == filters.status)
            if filters.stage:
                query = query.where(KernelMatchFixture.stage == filters.stage)
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
            logger.warning("Failed to fetch EPL schedule: %s", exc)
            return []
        finally:
            session.close()

    def fetch_team_data(self, team: TeamIdentity) -> dict:
        return {}

    def fetch_player_data(self, team: TeamIdentity) -> dict:
        return {}

    def fetch_market_data(self, match: MatchIdentity) -> dict:
        return {}
