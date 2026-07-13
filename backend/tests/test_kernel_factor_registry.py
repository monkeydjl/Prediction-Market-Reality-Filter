# backend/tests/test_kernel_factor_registry.py
"""Tests for FactorRegistry."""
from datetime import datetime, timezone
import pytest
from app.kernel.factor_registry import FactorConfig, FactorRegistry


class TestFactorRegistry:
    def test_register_and_get_weight(self):
        reg = FactorRegistry()
        reg.register_factor(FactorConfig(
            factor_id="elo", category="team", version="1.0",
            weight=0.30, competition=None, enabled=True,
            source="manual", updated_at=datetime.now(timezone.utc),
        ))
        assert reg.get_weight("elo", "world_cup") == 0.30

    def test_competition_specific_weight(self):
        reg = FactorRegistry()
        # Global weight
        reg.register_factor(FactorConfig(
            factor_id="elo", category="team", version="1.0",
            weight=0.30, competition=None, enabled=True,
            source="manual", updated_at=datetime.now(timezone.utc),
        ))
        # EPL-specific weight
        reg.register_factor(FactorConfig(
            factor_id="elo", category="team", version="1.0",
            weight=0.40, competition="epl", enabled=True,
            source="learning", updated_at=datetime.now(timezone.utc),
        ))
        assert reg.get_weight("elo", "epl") == 0.40
        assert reg.get_weight("elo", "world_cup") == 0.30  # falls back to global

    def test_update_weight(self):
        reg = FactorRegistry()
        reg.register_factor(FactorConfig(
            factor_id="elo", category="team", version="1.0",
            weight=0.30, competition=None, enabled=True,
            source="manual", updated_at=datetime.now(timezone.utc),
        ))
        reg.update_weight("elo", "epl", 0.45, "auto_tune")
        assert reg.get_weight("elo", "epl") == 0.45

    def test_update_weight_existing_factor(self):
        """Updating an existing factor's weight uses replace() (frozen-safe)."""
        reg = FactorRegistry()
        reg.register_factor(FactorConfig(
            factor_id="elo", category="team", version="1.0",
            weight=0.30, competition="world_cup", enabled=True,
            source="manual", updated_at=datetime.now(timezone.utc),
        ))
        # Update the existing world_cup entry (same key)
        reg.update_weight("elo", "world_cup", 0.50, "auto_tune")
        assert reg.get_weight("elo", "world_cup") == 0.50
        # Verify the factor was replaced (not mutated in place)
        factor = reg._factors[("elo", "world_cup")]
        assert factor.source == "auto_tune"
        assert factor.weight == 0.50

    def test_list_active(self):
        reg = FactorRegistry()
        reg.register_factor(FactorConfig(
            factor_id="elo", category="team", version="1.0",
            weight=0.30, competition=None, enabled=True,
            source="manual", updated_at=datetime.now(timezone.utc),
        ))
        reg.register_factor(FactorConfig(
            factor_id="xg", category="custom", version="1.0",
            weight=0.20, competition=None, enabled=False,
            source="manual", updated_at=datetime.now(timezone.utc),
        ))
        active = reg.list_active("world_cup")
        assert len(active) == 1
        assert active[0].factor_id == "elo"

    def test_list_active_prefers_competition_specific(self):
        """Competition-specific factor should override global in list_active."""
        reg = FactorRegistry()
        reg.register_factor(FactorConfig(
            factor_id="elo", category="team", version="1.0",
            weight=0.30, competition=None, enabled=True,
            source="manual", updated_at=datetime.now(timezone.utc),
        ))
        reg.register_factor(FactorConfig(
            factor_id="elo", category="team", version="1.0",
            weight=0.45, competition="epl", enabled=True,
            source="manual", updated_at=datetime.now(timezone.utc),
        ))
        active = reg.list_active("epl")
        assert len(active) == 1
        assert active[0].weight == 0.45  # epl-specific, not global 0.30

    def test_get_unknown_factor_returns_default(self):
        reg = FactorRegistry()
        assert reg.get_weight("nonexistent", "world_cup") == 1.0  # default weight
