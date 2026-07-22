# backend/app/sports/football/adapters/ucl_adapter.py
"""UCLAdapter — DataAdapter Protocol implementation for UEFA Champions League.

Bridges Football-Data.org (CL) and ClubElo.com data sources to the
sport-agnostic DataAdapter interface. The Kernel never sees UCL-specific
code — it only sees DataAdapter.
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
#   @patch("app.sports.football.adapters.ucl_adapter.fetch_competition_fixtures")
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
    code="ucl", name="UEFA Champions League", sport=_FOOTBALL
)
_DEFAULT_SEASON = "2026-27"
_DEFAULT_STAGE = "group_stage"
_DEFAULT_KICKOFF = datetime(2026, 9, 15, tzinfo=timezone.utc)

_STAGE_MAP = {
    "GROUP_STAGE": "group_stage",
    "ROUND_OF_16": "round_of_16",
    "QUARTER_FINALS": "quarterfinal",
    "SEMI_FINALS": "semifinal",
    "FINAL": "final",
}

_MATCH_ID_PREFIX = "ucl-"
_FD_COMPETITION = "CL"
_FD_SEASON = 2026

# Football-Data.org name → ClubElo.com URL name (spaces removed)
_TEAM_ALIASES = {
    "Real Madrid CF": "RealMadrid",
    "Manchester City FC": "ManCity",
    "FC Bayern München": "BayernMunich",
    "Bayern Munich": "BayernMunich",
    "Arsenal FC": "Arsenal",
    "Chelsea FC": "Chelsea",
    "Liverpool FC": "Liverpool",
    "FC Barcelona": "Barcelona",
    "Barcelona": "Barcelona",
    "Paris Saint-Germain": "ParisSG",
    "Paris SG": "ParisSG",
    "Atlético de Madrid": "AtleticoMadrid",
    "Atletico Madrid": "AtleticoMadrid",
    "Borussia Dortmund": "Dortmund",
    "Inter Milan": "Inter",
    "FC Internazionale Milano": "Inter",
    "AC Milan": "Milan",
    "Juventus FC": "Juventus",
    "SSC Napoli": "Napoli",
    "Napoli": "Napoli",
    "SL Benfica": "Benfica",
    "Benfica": "Benfica",
    "FC Porto": "Porto",
    "Porto": "Porto",
    "Sporting CP": "Sporting",
    "Sporting Lisbon": "Sporting",
    "Celtic FC": "Celtic",
    "Celtic": "Celtic",
    "Rangers FC": "Rangers",
    "Rangers": "Rangers",
    "Aston Villa FC": "AstonVilla",
    "Aston Villa": "AstonVilla",
    "Tottenham Hotspur FC": "Tottenham",
    "Tottenham": "Tottenham",
    "Newcastle United FC": "Newcastle",
    "Newcastle": "Newcastle",
    "RB Leipzig": "Leipzig",
    "VfB Stuttgart": "Stuttgart",
    "Bayer 04 Leverkusen": "Leverkusen",
    "Leverkusen": "Leverkusen",
    "Atalanta BC": "Atalanta",
    "Atalanta": "Atalanta",
    "Feyenoord Rotterdam": "Feyenoord",
    "Feyenoord": "Feyenoord",
    "PSV Eindhoven": "PSV",
    "PSV": "PSV",
    "Ajax Amsterdam": "Ajax",
    "Ajax": "Ajax",
    "Club Brugge KV": "ClubBrugge",
    "Club Brugge": "ClubBrugge",
    "FC Salzburg": "Salzburg",
    "RB Salzburg": "Salzburg",
    "Shakhtar Donetsk": "Shakhtar",
    "Dinamo Zagreb": "DinamoZagreb",
    "FK Crvena Zvezda": "CrvenaZvezda",
    "Red Star Belgrade": "CrvenaZvezda",
    "BSC Young Boys": "YoungBoys",
    "Young Boys": "YoungBoys",
    "Slovan Bratislava": "SlovanBratislava",
    "GNK Dinamo Zagreb": "DinamoZagreb",
    "Sturm Graz": "SturmGraz",
    "VfL Bochum": "Bochum",
    "Girona FC": "Girona",
    "Girona": "Girona",
    "Stade Brestois 29": "Brest",
    "Brest": "Brest",
    "Lille OSC": "Lille",
    "Lille": "Lille",
    "Olympique de Marseille": "Marseille",
    "Marseille": "Marseille",
    "AS Monaco": "Monaco",
    "Monaco": "Monaco",
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
    )


class UCLAdapter:
    """DataAdapter Protocol implementation for UEFA Champions League."""

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
                    save_fixture(parsed, "ucl", _DEFAULT_SEASON)
                    count += 1
            return count
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to sync UCL schedule: %s", exc)
            return 0

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        from app.kernel.kernel_db import get_kernel_session
        from sqlalchemy import select
        session = get_kernel_session()
        try:
            query = select(KernelMatchFixture).where(
                KernelMatchFixture.competition == "ucl"
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
            logger.warning("Failed to fetch UCL schedule: %s", exc)
            return []
        finally:
            session.close()

    def fetch_team_data(self, team) -> dict:
        return {}

    def fetch_player_data(self, team) -> dict:
        return {}

    def fetch_market_data(self, match) -> dict:
        return {}
