# backend/tests/test_elo_engine_weights.py
"""Tests for EloOddsEngine FactorRegistry integration (Phase 3)."""
from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures,
)
from app.kernel.engines.elo_odds_engine import EloOddsEngine


def _make_match(competition_code="world_cup") -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code=competition_code, name="Test", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id="m1", season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


def _make_features() -> FeatureSet:
    return FeatureSet(
        match=_make_match(),
        general=GeneralFeatures(None, None, None, None),
        team=TeamFeatures(1900, 1800, None, None, None, None, None, None),
        market=MarketFeatures(2.0, 3.0, 4.0, "test", True),
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures(None, None, None, False),
        custom={}, data_quality="real", quality_notes=[], feature_version="1.0",
    )


class TestEloOddsEngineWeights:
    def test_reads_weights_from_registry(self):
        """Engine uses weights from FactorRegistry instead of hardcoded 0.30/0.70."""
        mock_reg = MagicMock()
        mock_reg.get_weight.side_effect = lambda fid, comp: 0.40 if fid == "elo" else 0.60
        engine = EloOddsEngine(factor_registry=mock_reg)
        result = engine.predict(_make_features(), _make_match())
        # Check explanation records the registry weights
        elo_item = next(e for e in result.explanation if e.factor == "elo")
        odds_item = next(e for e in result.explanation if e.factor == "odds")
        assert elo_item.weight == 0.40
        assert odds_item.weight == 0.60

    def test_fallback_to_hardcoded_without_registry(self):
        """Without FactorRegistry, engine uses 0.30/0.70."""
        engine = EloOddsEngine()
        result = engine.predict(_make_features(), _make_match())
        elo_item = next(e for e in result.explanation if e.factor == "elo")
        odds_item = next(e for e in result.explanation if e.factor == "odds")
        assert elo_item.weight == 0.30
        assert odds_item.weight == 0.70

    def test_competition_specific_weights(self):
        """Engine reads competition-specific weights from registry."""
        mock_reg = MagicMock()
        def get_weight(fid, comp):
            if comp == "epl":
                return 0.45 if fid == "elo" else 0.55
            return 0.30 if fid == "elo" else 0.70
        mock_reg.get_weight.side_effect = get_weight
        engine = EloOddsEngine(factor_registry=mock_reg)

        features = FeatureSet(
            match=_make_match("epl"),
            general=GeneralFeatures(None, None, None, None),
            team=TeamFeatures(1900, 1800, None, None, None, None, None, None),
            market=MarketFeatures(2.0, 3.0, 4.0, "test", True),
            player=PlayerFeatures(None, None, None, None),
            environment=EnvironmentFeatures(None, None, None, False),
            custom={}, data_quality="real", quality_notes=[], feature_version="1.0",
        )
        result = engine.predict(features, _make_match("epl"))
        elo_item = next(e for e in result.explanation if e.factor == "elo")
        assert elo_item.weight == 0.45

    def test_predicted_outcome_set_when_elo_available(self):
        """Explanation includes predicted_outcome for available factors."""
        engine = EloOddsEngine()
        result = engine.predict(_make_features(), _make_match())
        elo_item = next(e for e in result.explanation if e.factor == "elo")
        odds_item = next(e for e in result.explanation if e.factor == "odds")
        assert elo_item.predicted_outcome is not None
        assert elo_item.predicted_outcome in ("home_win", "draw", "away_win")
        assert odds_item.predicted_outcome is not None
        assert odds_item.predicted_outcome in ("home_win", "draw", "away_win")

    def test_predicted_outcome_none_when_factor_unavailable(self):
        """predicted_outcome is None when a factor is unavailable."""
        engine = EloOddsEngine()
        # Features with no Elo ratings and no odds
        features = FeatureSet(
            match=_make_match(),
            general=GeneralFeatures(None, None, None, None),
            team=TeamFeatures(None, None, None, None, None, None, None, None),
            market=MarketFeatures(None, None, None, None, False),
            player=PlayerFeatures(None, None, None, None),
            environment=EnvironmentFeatures(None, None, None, False),
            custom={}, data_quality="low", quality_notes=[], feature_version="1.0",
        )
        result = engine.predict(features, _make_match())
        elo_item = next(e for e in result.explanation if e.factor == "elo")
        odds_item = next(e for e in result.explanation if e.factor == "odds")
        assert elo_item.predicted_outcome is None
        assert odds_item.predicted_outcome is None
