# backend/tests/test_kernel_engine_registry.py
"""Tests for EngineRegistry."""
import pytest
from datetime import datetime, timezone

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures, PredictionResult,
)
from app.kernel.engine_registry import EngineRegistry
from app.kernel.engines.elo_odds_engine import EloOddsEngine


def _make_features() -> FeatureSet:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    match = MatchIdentity(
        match_id="m1", season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )
    return FeatureSet(
        match=match,
        general=GeneralFeatures(None, None, None, None),
        team=TeamFeatures(1900, 1800, None, None, None, None, None, None),
        market=MarketFeatures(2.0, 3.0, 4.0, "test", True),
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures(None, None, None, False),
        custom={}, data_quality="real", quality_notes=[], feature_version="1.0",
    )


class TestEngineRegistry:
    def test_register_and_get(self):
        reg = EngineRegistry()
        engine = EloOddsEngine()
        reg.register(engine)
        assert reg.get("elo_odds") is engine

    def test_list_engines(self):
        reg = EngineRegistry()
        reg.register(EloOddsEngine())
        names = reg.list_engines()
        assert "elo_odds" in names

    def test_get_unknown_raises(self):
        reg = EngineRegistry()
        with pytest.raises(KeyError):
            reg.get("nonexistent")

    def test_select_auto_returns_default(self):
        reg = EngineRegistry()
        reg.register(EloOddsEngine())
        features = _make_features()
        engine = reg.select("auto", features)
        assert engine.name() == "elo_odds"

    def test_select_by_name(self):
        reg = EngineRegistry()
        reg.register(EloOddsEngine())
        features = _make_features()
        engine = reg.select("elo_odds", features)
        assert engine.name() == "elo_odds"

    def test_select_unknown_strategy_raises(self):
        reg = EngineRegistry()
        reg.register(EloOddsEngine())
        features = _make_features()
        with pytest.raises(KeyError):
            reg.select("nonexistent", features)
