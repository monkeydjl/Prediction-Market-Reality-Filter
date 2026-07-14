# backend/tests/test_db_migration.py
"""Tests for Phase 3 DB schema migration."""
import pytest
from sqlalchemy import inspect, text

from app.kernel.kernel_db import (
    init_kernel_db, close_kernel_session, get_kernel_session,
    KernelBase, KernelFactor, KernelEngineScore, KernelPredictionHistory,
    KernelCalibration,
)


@pytest.fixture
def fresh_db(tmp_path):
    """Create a fresh kernel DB."""
    db_path = str(tmp_path / "kernel_test.db")
    init_kernel_db(db_path)
    yield db_path
    close_kernel_session()


class TestDBMigration:
    def test_new_schema_has_expected_columns(self, fresh_db):
        """After init_kernel_db, all Phase 3 schema changes are present."""
        session = get_kernel_session()
        engine = session.bind

        # KernelFactor: should have 'id' PK + no single factor_id PK
        inspector = inspect(engine)
        factors_cols = {c['name']: c for c in inspector.get_columns('kernel_factors')}
        assert 'id' in factors_cols
        assert 'factor_id' in factors_cols
        assert 'competition' in factors_cols

        # KernelEngineScore: should have 'confidence_calibration'
        scores_cols = {c['name']: c for c in inspector.get_columns('kernel_engine_scores')}
        assert 'confidence_calibration' in scores_cols

        # KernelPredictionHistory: should have 'feature_version'
        history_cols = {c['name']: c for c in inspector.get_columns('kernel_prediction_history')}
        assert 'feature_version' in history_cols

        # KernelCalibration: new table exists
        cal_tables = [t for t in inspector.get_table_names() if t == 'kernel_calibration']
        assert len(cal_tables) == 1
        cal_cols = {c['name']: c for c in inspector.get_columns('kernel_calibration')}
        assert 'slope' in cal_cols
        assert 'intercept' in cal_cols
        assert 'sample_count' in cal_cols

        session.close()

    def test_calibration_upsert_roundtrip(self, fresh_db):
        """KernelCalibration rows can be inserted and queried."""
        from datetime import datetime, timezone
        session = get_kernel_session()
        try:
            cal = KernelCalibration(
                engine="elo_odds", competition="world_cup",
                slope=1.1, intercept=-0.05,
                sample_count=15, avg_confidence=0.65, avg_accuracy=0.70,
                last_updated=datetime.now(timezone.utc),
            )
            session.add(cal)
            session.commit()

            result = session.query(KernelCalibration).filter_by(
                engine="elo_odds", competition="world_cup"
            ).first()
            assert result is not None
            assert result.slope == 1.1
            assert result.intercept == -0.05
            assert result.sample_count == 15
        finally:
            session.close()


class TestKernelEloRatingTable:
    """Phase 4: kernel_elo_ratings table for self-computed NBA Elo."""

    def test_elo_ratings_table_created(self, tmp_path):
        """KernelEloRating table is created by init_kernel_db()."""
        from app.kernel.kernel_db import (
            init_kernel_db, close_kernel_session, get_kernel_session,
            KernelEloRating,
        )
        db_path = str(tmp_path / "kernel_elo_test.db")
        init_kernel_db(db_path)
        try:
            session = get_kernel_session()
            # Verify table exists by inserting and querying a row
            from datetime import datetime, timezone
            row = KernelEloRating(
                team_name="Boston Celtics",
                sport="basketball",
                competition="nba",
                elo_rating=1650.0,
                source="self_computed",
                updated_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.commit()
            fetched = session.get(KernelEloRating, "Boston Celtics")
            assert fetched is not None
            assert fetched.elo_rating == 1650.0
            assert fetched.competition == "nba"
            session.close()
        finally:
            close_kernel_session()
