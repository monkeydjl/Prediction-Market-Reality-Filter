# backend/tests/test_factor_registry_persistence.py
"""Tests for FactorRegistry DB persistence (Phase 3)."""
from datetime import datetime, timezone
import pytest
from sqlalchemy import text

from app.kernel.kernel_db import init_kernel_db, close_kernel_session, get_kernel_session, KernelFactor
from app.kernel.factor_registry import FactorRegistry, FactorConfig


@pytest.fixture
def db_registry(tmp_path):
    """Create a FactorRegistry backed by a temp DB."""
    db_path = str(tmp_path / "kernel_test.db")
    init_kernel_db(db_path)
    registry = FactorRegistry()
    yield registry
    close_kernel_session()


class TestFactorRegistryPersistence:
    def test_init_default_factors_on_empty_db(self, db_registry):
        """FactorRegistry() on empty DB initializes elo=0.30, odds=0.70."""
        assert db_registry.get_weight("elo", "world_cup") == 0.30
        assert db_registry.get_weight("odds", "world_cup") == 0.70

    def test_load_from_db_on_construction(self, tmp_path):
        """FactorRegistry loads existing factors from DB on construction."""
        db_path = str(tmp_path / "kernel_test.db")
        init_kernel_db(db_path)

        # Write a factor directly to DB
        session = get_kernel_session()
        session.add(KernelFactor(
            factor_id="elo", category="elo_rating", version="1.0",
            weight=0.45, competition="epl", enabled=1,
            source="manual", updated_at=datetime.now(timezone.utc),
        ))
        session.commit()
        session.close()

        # New FactorRegistry should load it
        registry = FactorRegistry()
        assert registry.get_weight("elo", "epl") == 0.45
        close_kernel_session()

    def test_update_weight_persists_to_db(self, db_registry):
        """update_weight writes to KernelFactor table."""
        db_registry.update_weight("elo", "epl", 0.40, "ewma")

        session = get_kernel_session()
        row = session.query(KernelFactor).filter_by(
            factor_id="elo", competition="epl"
        ).first()
        assert row is not None
        assert row.weight == 0.40
        assert row.source == "ewma"
        session.close()

        # New registry instance sees the persisted weight (re-reads from same DB)
        registry2 = FactorRegistry()
        assert registry2.get_weight("elo", "epl") == 0.40

    def test_competition_fallback_to_global(self, db_registry):
        """get_weight falls back to global (competition=None) when no competition-specific weight."""
        # Default factors are global (competition=None)
        assert db_registry.get_weight("elo", "unknown_comp") == 0.30

    def test_update_weight_upsert(self, db_registry):
        """update_weight on existing factor updates it, doesn't create duplicate."""
        db_registry.update_weight("elo", "epl", 0.35, "ewma")
        db_registry.update_weight("elo", "epl", 0.40, "ewma")

        session = get_kernel_session()
        rows = session.query(KernelFactor).filter_by(
            factor_id="elo", competition="epl"
        ).all()
        assert len(rows) == 1
        assert rows[0].weight == 0.40
        session.close()

    def test_null_weight_and_source_fall_back_to_column_defaults(self, tmp_path):
        """A row with NULL weight/source loads as 1.0 / "manual", not None.

        KernelFactor.weight and .source are nullable with Python-side defaults,
        so only an ORM insert fills them; a row written any other way can hold
        NULL. FactorConfig declares both non-Optional and get_weight is annotated
        -> float, so a None weight would flow straight into the caller's
        arithmetic and raise TypeError there instead of here.
        """
        db_path = str(tmp_path / "kernel_test.db")
        init_kernel_db(db_path)

        session = get_kernel_session()
        session.execute(text(
            "INSERT INTO kernel_factors "
            "(factor_id, category, version, weight, competition, enabled, source, updated_at) "
            "VALUES ('elo', 'elo_rating', '1.0', NULL, 'epl', 1, NULL, NULL)"
        ))
        session.commit()
        session.close()

        registry = FactorRegistry()
        configs = {fc.factor_id: fc for fc in registry.list_active("epl")}
        assert configs["elo"].weight == 1.0
        assert configs["elo"].source == "manual"
        assert registry.get_weight("elo", "epl") == 1.0
        close_kernel_session()
