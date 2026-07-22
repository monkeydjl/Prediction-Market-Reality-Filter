# backend/tests/test_kernel_engine_registry.py
"""Tests for EngineRegistry."""
from datetime import datetime, timezone

import pytest

from app.kernel.domain import (
    EngineScore,
    PredictionResult,
)
from app.kernel.engine_registry import EngineRegistry
from app.kernel.engines.elo_odds_engine import EloOddsEngine
from unittest.mock import MagicMock


class _NamedEngine:
    def __init__(self, name_str: str, sports: list[str]):
        self._name = name_str
        self._sports = sports

    def name(self) -> str:
        return self._name

    def supported_sports(self) -> list[str]:
        return list(self._sports)

    def predict(self, features, match):
        return PredictionResult(
            predicted_scores={},
            outcome_probabilities={"home_win": 0.5, "away_win": 0.5},
            confidence=0.1,
            engine_name=self._name,
            explanation=[],
            betting_analysis=None,
            feature_version="1.0",
            prediction_timestamp=datetime.now(timezone.utc),
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
        engine = reg.select("auto", competition="world_cup")
        assert engine.name() == "elo_odds"

    def test_select_by_name(self):
        reg = EngineRegistry()
        reg.register(EloOddsEngine())
        engine = reg.select("elo_odds", competition="world_cup")
        assert engine.name() == "elo_odds"

    def test_select_unknown_strategy_raises(self):
        reg = EngineRegistry()
        reg.register(EloOddsEngine())
        with pytest.raises(KeyError):
            reg.select("nonexistent", competition="world_cup")

    def test_select_auto_prefers_sport_compatible_engine(self):
        reg = EngineRegistry()
        reg.register(_NamedEngine("football_first", ["football"]))
        reg.register(_NamedEngine("lol_market_only", ["lol"]))
        chosen = reg.select("auto", sport="lol", competition="lol_lck")
        assert chosen.name() == "lol_market_only"

    def test_select_auto_resolves_sport_from_competition(self):
        reg = EngineRegistry()
        reg.register(_NamedEngine("football_first", ["football"]))
        reg.register(_NamedEngine("lol_market_only", ["lol"]))
        chosen = reg.select("auto", competition="lol_lck")
        assert chosen.name() == "lol_market_only"

    def test_select_auto_learning_respects_sport_filter(self):
        mock_ls = MagicMock()

        def engine_score(name, comp=None):
            # Football engine has higher accuracy but wrong sport.
            if name == "football_first":
                return EngineScore(
                    name, comp, 0.99, 0.1, 0.1, 20, 1.0,
                    datetime.now(timezone.utc),
                )
            return EngineScore(
                name, comp, 0.55, 0.4, 0.3, 20, 1.0,
                datetime.now(timezone.utc),
            )

        mock_ls.engine_score.side_effect = engine_score
        reg = EngineRegistry(learning_service=mock_ls)
        reg.register(_NamedEngine("football_first", ["football"]))
        reg.register(_NamedEngine("lol_market_only", ["lol"]))
        chosen = reg.select("auto", sport="lol", competition="lol")
        assert chosen.name() == "lol_market_only"

    def test_select_auto_raises_when_no_sport_match(self):
        reg = EngineRegistry()
        reg.register(_NamedEngine("football_only", ["football"]))
        with pytest.raises(KeyError, match="No engine supports sport"):
            reg.select("auto", sport="lol")
