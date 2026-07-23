# backend/app/sports/football/adapters/league_adapter.py
"""Config-driven LeagueAdapter for league-format football competitions.

A single LeagueAdapter class serves all league-format competitions
(La Liga, Bundesliga, Serie A, Ligue 1, and any future league added
to LEAGUE_REGISTRY). Constants are encapsulated in LeagueConfig.

Does NOT modify or replace existing UCLAdapter or EPLAdapter — those
remain as-is for zero regression risk.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import ScheduleFilter, RawMatchData
from app.sports.football.adapters._shared import (
    fetch_elo_and_odds, query_fixture, query_result,
    build_match_identity, build_match_outcome, save_fixture,
)
from app.services.football_data_client import (
    fetch_competition_fixtures, parse_fixture,
)
from app.kernel.kernel_db import KernelMatchFixture, KernelMatchResult

logger = logging.getLogger(__name__)

_FOOTBALL = SportIdentity(code="football", name="Football")


@dataclass(frozen=True)
class LeagueConfig:
    """Configuration for a league-specific adapter.

    Encapsulates all league-specific constants so that a single
    LeagueAdapter class can serve any league-format competition.
    """
    code: str                    # Internal competition code (e.g., "laliga")
    name: str                    # Display name (e.g., "La Liga")
    match_id_prefix: str         # Prefix for match IDs (e.g., "laliga-")
    fd_competition: str          # Football-Data.org competition code (e.g., "PD")
    fd_season: int               # Football-Data.org season year (e.g., 2026)
    default_season: str          # Internal season key (e.g., "2026-27")
    default_stage: str           # Default stage (e.g., "regular_season")
    default_kickoff: datetime    # Default kickoff for stub identity
    stage_map: dict[str, str]    # Stage mapping (empty for league-format)
    team_aliases: dict[str, str] # FD team name → ClubElo lookup name


# ---------------------------------------------------------------------------
# League Configurations
# ---------------------------------------------------------------------------

_LALIGA_CONFIG = LeagueConfig(
    code="laliga",
    name="La Liga",
    match_id_prefix="laliga-",
    fd_competition="PD",
    fd_season=2026,
    default_season="2026-27",
    default_stage="regular_season",
    default_kickoff=datetime(2026, 8, 16, 20, 0, tzinfo=timezone.utc),
    stage_map={},
    team_aliases={
        "Real Madrid CF": "RealMadrid",
        "FC Barcelona": "Barcelona",
        "Atlético de Madrid": "AtleticoMadrid",
        "Athletic Club": "AthleticBilbao",
        "Real Sociedad": "RealSociedad",
        "Villarreal CF": "Villarreal",
        "Real Betis": "RealBetis",
        "Valencia CF": "Valencia",
        "Sevilla FC": "Sevilla",
        "Girona FC": "Girona",
        "Rayo Vallecano": "RayoVallecano",
        "Getafe CF": "Getafe",
        "CA Osasuna": "Osasuna",
        "Celta de Vigo": "CeltaVigo",
        "RCD Mallorca": "Mallorca",
        "UD Las Palmas": "LasPalmas",
        "Deportivo Alavés": "Alaves",
        "RCD Espanyol": "Espanyol",
        "CD Leganés": "Leganes",
        "Valladolid CF": "Valladolid",
    },
)

_BUNDESLIGA_CONFIG = LeagueConfig(
    code="bundesliga",
    name="Bundesliga",
    match_id_prefix="bundesliga-",
    fd_competition="BL1",
    fd_season=2025,
    default_season="2025-26",
    default_stage="regular_season",
    default_kickoff=datetime(2025, 8, 23, 15, 30, tzinfo=timezone.utc),
    stage_map={},
    team_aliases={
        "FC Bayern München": "BayernMunich",
        "Borussia Dortmund": "Dortmund",
        "Bayer 04 Leverkusen": "Leverkusen",
        "RB Leipzig": "RBLeipzig",
        "Eintracht Frankfurt": "Frankfurt",
        "VfL Wolfsburg": "Wolfsburg",
        "SC Freiburg": "Freiburg",
        "1. FSV Mainz 05": "Mainz",
        "VfB Stuttgart": "Stuttgart",
        "FC Augsburg": "Augsburg",
        "Borussia Mönchengladbach": "Mönchengladbach",
        "SV Werder Bremen": "WerderBremen",
        "TSG 1899 Hoffenheim": "Hoffenheim",
        "1. FC Union Berlin": "UnionBerlin",
        "VfL Bochum": "Bochum",
        "FC St. Pauli": "StPauli",
        "Holstein Kiel": "HolsteinKiel",
        "1. FC Heidenheim 1846": "Heidenheim",
    },
)

_SERIEA_CONFIG = LeagueConfig(
    code="seriea",
    name="Serie A",
    match_id_prefix="seriea-",
    fd_competition="SA",
    fd_season=2026,
    default_season="2026-27",
    default_stage="regular_season",
    default_kickoff=datetime(2026, 8, 17, 18, 0, tzinfo=timezone.utc),
    stage_map={},
    team_aliases={
        "FC Internazionale Milano": "Inter",
        "AC Milan": "Milan",
        "Juventus FC": "Juventus",
        "SSC Napoli": "Napoli",
        "AS Roma": "Roma",
        "Atalanta BC": "Atalanta",
        "ACF Fiorentina": "Fiorentina",
        "SS Lazio": "Lazio",
        "Bologna FC 1909": "Bologna",
        "Torino FC": "Torino",
        "Udinese Calcio": "Udinese",
        "US Sassuolo": "Sassuolo",
        "Genoa CFC": "Genoa",
        "US Lecce": "Lecce",
        "Cagliari Calcio": "Cagliari",
        "Hellas Verona": "Verona",
        "Empoli FC": "Empoli",
        "Parma Calcio 1913": "Parma",
        "Como 1907": "Como",
        "Venezia FC": "Venezia",
    },
)

_LIGUE1_CONFIG = LeagueConfig(
    code="ligue1",
    name="Ligue 1",
    match_id_prefix="ligue1-",
    fd_competition="FL1",
    fd_season=2026,
    default_season="2026-27",
    default_stage="regular_season",
    default_kickoff=datetime(2026, 8, 22, 15, 0, tzinfo=timezone.utc),
    stage_map={},
    team_aliases={
        "Paris Saint-Germain": "ParisSG",
        "AS Monaco": "Monaco",
        "Olympique de Marseille": "Marseille",
        "Olympique Lyonnais": "Lyon",
        "LOSC Lille": "Lille",
        "OGC Nice": "Nice",
        "Stade Rennais FC": "Rennes",
        "RC Lens": "Lens",
        "Stade Brestois 29": "Brest",
        "Toulouse FC": "Toulouse",
        "Montpellier HSC": "Montpellier",
        "FC Nantes": "Nantes",
        "Stade de Reims": "Reims",
        "RC Strasbourg Alsace": "Strasbourg",
        "Le Havre AC": "LeHavre",
        "AJ Auxerre": "Auxerre",
        "Angers SCO": "Angers",
        "AS Saint-Étienne": "Saint-Etienne",
    },
)


LEAGUE_REGISTRY: dict[str, LeagueConfig] = {
    "laliga-":     _LALIGA_CONFIG,
    "bundesliga-": _BUNDESLIGA_CONFIG,
    "seriea-":     _SERIEA_CONFIG,
    "ligue1-":     _LIGUE1_CONFIG,
}


# ---------------------------------------------------------------------------
# LeagueAdapter
# ---------------------------------------------------------------------------

class LeagueAdapter:
    """DataAdapter implementation for league-format football competitions.

    Driven by LeagueConfig — a single class serves all league-format
    competitions (La Liga, Bundesliga, Serie A, Ligue 1, and any future
    league added to LEAGUE_REGISTRY).
    """

    def __init__(self, config: LeagueConfig) -> None:
        self._config = config
        self._competition = CompetitionIdentity(
            code=config.code, name=config.name, sport=_FOOTBALL
        )

    def _stub_identity(self, match_id: str) -> MatchIdentity:
        """Return a stub MatchIdentity when fixture data is unavailable."""
        home = TeamIdentity(code="HOME", name="Home", competition=self._competition)
        away = TeamIdentity(code="AWAY", name="Away", competition=self._competition)
        return MatchIdentity(
            match_id=match_id,
            season=SeasonIdentity(competition=self._competition, season_key=self._config.default_season),
            stage=self._config.default_stage,
            round=None,
            home=home,
            away=away,
            kickoff_utc=self._config.default_kickoff,
        )

    def get_match_identity(self, match_id: str) -> MatchIdentity:
        fixture = query_fixture(match_id, KernelMatchFixture)
        if fixture is None:
            return self._stub_identity(match_id)
        return build_match_identity(
            fixture, self._competition, self._config.default_season, self._config.default_stage
        )

    def fetch_all_data(self, match: MatchIdentity) -> dict:
        return fetch_elo_and_odds(
            match, elo_scope="club", team_aliases=self._config.team_aliases
        )

    def fetch_outcome(self, match_id: str) -> MatchOutcome | None:
        result = query_result(match_id, KernelMatchResult)
        return build_match_outcome(result)

    def sync_schedule(self) -> int:
        try:
            fixtures_raw = fetch_competition_fixtures(
                self._config.fd_competition, season=self._config.fd_season
            )
            count = 0
            for raw in fixtures_raw:
                parsed = parse_fixture(
                    raw, stage_mapping=self._config.stage_map,
                    match_id_prefix=self._config.match_id_prefix,
                )
                if parsed:
                    save_fixture(parsed, self._config.code, self._config.default_season)
                    count += 1
            return count
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to sync %s schedule: %s", self._config.code, exc)
            return 0

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        from app.kernel.kernel_db import get_kernel_session
        from sqlalchemy import select
        session = get_kernel_session()
        try:
            query = select(KernelMatchFixture).where(
                KernelMatchFixture.competition == self._config.code
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
            logger.warning("Failed to fetch %s schedule: %s", self._config.code, exc)
            return []
        finally:
            session.close()

    def fetch_team_data(self, team: Any) -> dict:
        return {}

    def fetch_player_data(self, team: Any) -> dict:
        return {}

    def fetch_market_data(self, match: Any) -> dict:
        return {}
