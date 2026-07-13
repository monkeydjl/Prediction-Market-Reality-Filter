# backend/tests/test_kernel_feature_registry.py
"""Tests for FeatureRegistry."""
import pytest
from app.kernel.feature_registry import FeatureDefinition, FeatureRegistry


class TestFeatureRegistry:
    def test_register_and_get(self):
        reg = FeatureRegistry()
        reg.register("elo_rating_home", "team", "1.0", "Home team Elo rating")
        fd = reg.get("elo_rating_home")
        assert fd is not None
        assert fd.category == "team"
        assert fd.sport is None  # universal

    def test_register_with_sport(self):
        reg = FeatureRegistry()
        reg.register("xg_home", "custom", "1.0", "Expected goals home", sport="football")
        fd = reg.get("xg_home")
        assert fd.sport == "football"

    def test_get_unknown_returns_none(self):
        reg = FeatureRegistry()
        assert reg.get("nonexistent") is None

    def test_list_by_category(self):
        reg = FeatureRegistry()
        reg.register("elo_rating_home", "team", "1.0", "Elo home")
        reg.register("elo_rating_away", "team", "1.0", "Elo away")
        reg.register("odds_home", "market", "1.0", "Odds home")
        team_features = reg.list_by_category("team")
        assert len(team_features) == 2

    def test_list_by_sport(self):
        reg = FeatureRegistry()
        reg.register("elo_rating_home", "team", "1.0", "Elo home")
        reg.register("xg_home", "custom", "1.0", "xG home", sport="football")
        reg.register("pace_home", "custom", "1.0", "Pace home", sport="basketball")
        football = reg.list_by_sport("football")
        # Should include universal (sport=None) + football-specific
        keys = [f.key for f in football]
        assert "elo_rating_home" in keys
        assert "xg_home" in keys
        assert "pace_home" not in keys
