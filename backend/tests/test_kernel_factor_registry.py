# backend/tests/test_kernel_factor_registry.py
"""Tests for FactorRegistry."""
from datetime import datetime, timezone
import pytest
from app.kernel.factor_registry import FactorConfig, FactorRegistry
from app.kernel.kernel_db import close_kernel_session, init_kernel_db


@pytest.fixture(autouse=True)
def _isolated_kernel_db(tmp_path):
    """Give each test a fresh temp kernel DB.

    Since Task 2, FactorRegistry() is DB-backed: it loads from / seeds the
    KernelFactor table on construction. Without isolation these tests would
    write to the default kernel_predictions.db and leak persisted weights
    between tests (the init_kernel_db singleton silently reuses an existing
    engine). A per-test temp DB keeps them deterministic and hermetic.
    """
    close_kernel_session()
    init_kernel_db(str(tmp_path / "kernel_test.db"))
    yield
    close_kernel_session()


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
        assert factor.category == "team"

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
        factor_ids = {f.factor_id for f in active}
        # elo + odds are seeded as enabled global defaults; xg is disabled
        assert "elo" in factor_ids
        assert "xg" not in factor_ids  # disabled factor is excluded

    def test_list_active_prefers_competition_specific(self):
        """Competition-specific factor should override global in list_active."""
        reg = FactorRegistry()
        # Register competition-specific FIRST
        reg.register_factor(FactorConfig(
            factor_id="elo", category="team", version="1.0",
            weight=0.45, competition="epl", enabled=True,
            source="manual", updated_at=datetime.now(timezone.utc),
        ))
        # Then register global — must NOT overwrite the epl-specific entry
        reg.register_factor(FactorConfig(
            factor_id="elo", category="team", version="1.0",
            weight=0.30, competition=None, enabled=True,
            source="manual", updated_at=datetime.now(timezone.utc),
        ))
        active = reg.list_active("epl")
        elo_factor = next(f for f in active if f.factor_id == "elo")
        assert elo_factor.weight == 0.45  # epl-specific, not global 0.30

    def test_get_unknown_factor_returns_default(self):
        reg = FactorRegistry()
        assert reg.get_weight("nonexistent", "world_cup") == 1.0  # default weight
