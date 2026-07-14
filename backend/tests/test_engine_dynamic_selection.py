# backend/tests/test_engine_dynamic_selection.py
"""Tests for dynamic engine selection (Phase 3)."""
from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet, PredictionResult,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures, EngineScore,
)
from app.kernel.engine_registry import EngineRegistry
from app.kernel.engines.elo_odds_engine import EloOddsEngine


class FakeEngine:
    """Minimal engine for testing."""
    def __init__(self, name_str):
        self._name = name_str
    def name(self): return self._name
    def supported_sports(self): return ["*"]
    def predict(self, features, match):
        return PredictionResult(
            predicted_scores={"home": 1.0, "away": 0.0},
            outcome_probabilities={"home_win": 0.5, "draw": 0.3, "away_win": 0.2},
            confidence=0.5, engine_name=self._name, explanation=[],
            betting_analysis=None, feature_version="1.0",
            prediction_timestamp=datetime.now(timezone.utc),
        )


class TestDynamicEngineSelection:
    def test_auto_returns_default_without_learning_service(self):
        """Without LearningService, select('auto') returns default engine."""
        reg = EngineRegistry()
        reg.register(FakeEngine("engine_a"))
        engine = reg.select("auto", competition="world_cup")
        assert engine.name() == "engine_a"

    def test_auto_returns_default_with_insufficient_samples(self):
        """With < 5 samples, select('auto') returns default."""
        mock_ls = MagicMock()
        mock_ls.engine_score.return_value = EngineScore(
            engine="engine_a", competition="world_cup",
            accuracy=0.8, avg_mae=0.5, brier_score=0.3,
            sample_count=3, confidence_calibration=1.0,
            last_updated=datetime.now(timezone.utc),
        )
        reg = EngineRegistry(learning_service=mock_ls)
        reg.register(FakeEngine("engine_a"))
        engine = reg.select("auto", competition="world_cup")
        assert engine.name() == "engine_a"

    def test_auto_selects_best_engine(self):
        """With sufficient samples, select('auto') picks highest accuracy."""
        mock_ls = MagicMock()
        def engine_score(name, comp=None):
            if name == "engine_a":
                return EngineScore(name, comp, 0.6, 0.5, 0.3, 10, 1.0,
                                   datetime.now(timezone.utc))
            return EngineScore(name, comp, 0.8, 0.3, 0.2, 10, 1.0,
                               datetime.now(timezone.utc))
        mock_ls.engine_score.side_effect = engine_score

        reg = EngineRegistry(learning_service=mock_ls)
        reg.register(FakeEngine("engine_a"))
        reg.register(FakeEngine("engine_b"))
        engine = reg.select("auto", competition="world_cup")
        assert engine.name() == "engine_b"

    def test_select_by_name_ignores_learning(self):
        """select('engine_a') returns that engine regardless of scores."""
        mock_ls = MagicMock()
        reg = EngineRegistry(learning_service=mock_ls)
        reg.register(FakeEngine("engine_a"))
        engine = reg.select("engine_a", competition="world_cup")
        assert engine.name() == "engine_a"
        mock_ls.engine_score.assert_not_called()
