# backend/tests/test_kernel_engine_registry.py
"""Tests for EngineRegistry."""
import pytest

from app.kernel.engine_registry import EngineRegistry
from app.kernel.engines.elo_odds_engine import EloOddsEngine


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
