# backend/tests/test_api_predictions.py
"""Tests for /api/predictions API routes and DB query functions."""
import pytest

from app.kernel.kernel_db import (
    init_kernel_db, close_kernel_session, get_kernel_session,
    get_latest_prediction, get_match_ids_with_predictions,
    KernelPrediction,
)
from datetime import datetime, timezone


@pytest.fixture
def db(tmp_path):
    """Initialize a temporary kernel DB for each test."""
    db_path = str(tmp_path / "test_api_predictions.db")
    init_kernel_db(db_path)
    yield
    close_kernel_session()


def _insert_prediction(match_id: str, engine: str = "elo_odds"):
    """Insert a prediction row for testing."""
    session = get_kernel_session()
    pred = KernelPrediction(
        match_id=match_id,
        sport="football",
        competition="world_cup",
        season="2026",
        engine=engine,
        predicted_scores={"home": 2, "away": 1},
        outcome_probabilities={"home_win": 0.6, "draw": 0.2, "away_win": 0.2},
        confidence=0.72,
        feature_version="1.0",
        explanation=[],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(pred)
    session.commit()
    return pred


class TestGetLatestPrediction:
    def test_get_latest_prediction_returns_row(self, db):
        """Table has data → returns the row."""
        _insert_prediction("wc-123")
        result = get_latest_prediction("wc-123")
        assert result is not None
        assert result.match_id == "wc-123"
        assert result.engine == "elo_odds"

    def test_get_latest_prediction_returns_none(self, db):
        """Table empty for this match_id → returns None."""
        result = get_latest_prediction("wc-nonexistent")
        assert result is None


class TestGetMatchIdsWithPredictions:
    def test_returns_subset_with_predictions(self, db):
        """3 matches, 2 have predictions → returns 2."""
        _insert_prediction("wc-1")
        _insert_prediction("nba-2")
        # wc-3 has no prediction
        result = get_match_ids_with_predictions(["wc-1", "nba-2", "wc-3"])
        assert result == {"wc-1", "nba-2"}

    def test_empty_input_returns_empty_set(self, db):
        """Empty list → empty set."""
        result = get_match_ids_with_predictions([])
        assert result == set()

    def test_no_predictions_returns_empty_set(self, db):
        """Matches exist but no predictions → empty set."""
        result = get_match_ids_with_predictions(["wc-1", "wc-2"])
        assert result == set()
