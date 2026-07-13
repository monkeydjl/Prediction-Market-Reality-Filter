# backend/tests/test_kernel_world_cup_adapter.py
"""Tests for WorldCupAdapter bridge and FootballFeatureBuilder.

These are the bridge tests: they verify that the WorldCupAdapter correctly
implements the DataAdapter Protocol by wrapping existing world_cup_* services,
and that FootballFeatureBuilder correctly implements the FeatureBuilder Protocol.
"""
import pytest
from datetime import datetime, timezone

from app.kernel.domain import (
    MatchIdentity,
    MatchOutcome,
    SportIdentity,
    CompetitionIdentity,
    SeasonIdentity,
    TeamIdentity,
    FeatureSet,
)
from app.kernel.protocols import DataAdapter, FeatureBuilder
from app.kernel.adapters.world_cup_adapter import WorldCupAdapter
from app.kernel.adapters.football_feature_builder import FootballFeatureBuilder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_match() -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id="m1",
        season=season,
        stage="group",
        round=None,
        home=home,
        away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


# ===========================================================================
# WorldCupAdapter tests
# ===========================================================================

class TestWorldCupAdapter:
    def test_implements_data_adapter_protocol(self):
        adapter = WorldCupAdapter()
        assert isinstance(adapter, DataAdapter)

    def test_get_match_identity_returns_match_identity(self):
        adapter = WorldCupAdapter()
        # Use a known match_id from the DB; if DB is empty, test creation logic
        match = adapter.get_match_identity("test_match_1")
        assert isinstance(match, MatchIdentity)
        assert match.season.competition.sport.code == "football"
        assert match.season.competition.code == "world_cup"

    def test_fetch_outcome_returns_none_for_unknown(self):
        adapter = WorldCupAdapter()
        result = adapter.fetch_outcome("nonexistent_match_id")
        assert result is None

    def test_sync_schedule_returns_int(self):
        adapter = WorldCupAdapter()
        # sync may fail if API keys not configured, but should return int
        result = adapter.sync_schedule()
        assert isinstance(result, int)

    def test_fetch_all_data_extracts_elo_and_odds(self):
        """fetch_all_data correctly extracts elo ratings and odds from services."""
        from unittest.mock import patch

        adapter = WorldCupAdapter()
        match = _make_match()

        async def mock_elo(team_name):
            return {"elo_rating": 1850.0}

        async def mock_odds(home, away):
            return {"home": 2.0, "draw": 3.5, "away": 3.0, "stale": False}

        with patch(
            "app.services.elo_ratings_service.get_elo_rating",
            side_effect=mock_elo,
        ), patch(
            "app.services.odds_cache_service.get_cached_odds",
            side_effect=mock_odds,
        ):
            data = adapter.fetch_all_data(match)

        # Elo ratings extracted into the "team" sub-dict (matches the
        # documented return shape of fetch_all_data).
        assert data["team"]["elo_home"] == 1850.0
        assert data["team"]["elo_away"] == 1850.0
        # Odds extracted into the "market" sub-dict.
        assert data["market"]["odds_home"] == 2.0
        assert data["market"]["odds_draw"] == 3.5
        assert data["market"]["odds_away"] == 3.0
        # ``stale=False`` -> ``odds_fresh=True``.
        assert data["market"]["odds_fresh"] is True

    def test_fetch_all_data_degrades_when_odds_service_fails(self):
        """fetch_all_data still returns elo when the odds service raises.

        Verifies the consolidated ``asyncio.gather(..., return_exceptions=True)``
        preserves the per-service graceful degradation: a failure in the odds
        service does not abort the Elo lookups.
        """
        from unittest.mock import patch

        adapter = WorldCupAdapter()
        match = _make_match()

        async def mock_elo(team_name):
            return {"elo_rating": 1850.0}

        async def mock_odds(home, away):
            raise RuntimeError("odds service down")

        with patch(
            "app.services.elo_ratings_service.get_elo_rating",
            side_effect=mock_elo,
        ), patch(
            "app.services.odds_cache_service.get_cached_odds",
            side_effect=mock_odds,
        ):
            data = adapter.fetch_all_data(match)

        # Elo still present despite the odds failure.
        assert data["team"]["elo_home"] == 1850.0
        assert data["team"]["elo_away"] == 1850.0
        # Odds market left empty (graceful degradation).
        assert data["market"] == {}


# ===========================================================================
# FootballFeatureBuilder tests
# ===========================================================================

class TestFootballFeatureBuilder:
    def test_implements_feature_builder_protocol(self):
        builder = FootballFeatureBuilder()
        assert isinstance(builder, FeatureBuilder)

    def test_sport_returns_football(self):
        builder = FootballFeatureBuilder()
        sport = builder.sport()
        assert sport.code == "football"

    def test_build_returns_feature_set(self):
        builder = FootballFeatureBuilder()
        match = _make_match()
        raw = {
            "team": {"elo_home": 1900, "elo_away": 1800},
            "market": {"odds_home": 2.0, "odds_draw": 3.0, "odds_away": 4.0},
            "player": {},
            "environment": {},
            "general": {},
        }
        features = builder.build(match, raw)
        assert isinstance(features, FeatureSet)
        assert features.team.elo_rating_home == 1900
        assert features.market.odds_home == 2.0
        assert features.feature_version == "1.0"

    def test_build_with_missing_data_uses_none(self):
        builder = FootballFeatureBuilder()
        match = _make_match()
        raw = {}
        features = builder.build(match, raw)
        assert features.team.elo_rating_home is None
        assert features.market.odds_home is None
        assert features.data_quality == "partial"

    def test_build_with_full_data_quality_real(self):
        builder = FootballFeatureBuilder()
        match = _make_match()
        raw = {
            "team": {"elo_home": 1900, "elo_away": 1800},
            "market": {"odds_home": 2.0, "odds_draw": 3.0, "odds_away": 4.0},
        }
        features = builder.build(match, raw)
        assert features.data_quality == "real"
