# backend/tests/test_kernel_learning_service.py
"""Tests for KernelLearningService."""
from datetime import datetime, timezone
import pytest
import tempfile
import os

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome, PredictionResult,
    PredictionError, EngineScore,
)
from app.kernel.kernel_db import init_kernel_db, get_kernel_session, close_kernel_session
from app.kernel.learning_service import KernelLearningService


def _make_match(match_id="m1") -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id=match_id, season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


def _make_prediction(engine="elo_odds") -> PredictionResult:
    return PredictionResult(
        predicted_scores={"home": 2.0, "away": 1.0},
        outcome_probabilities={"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
        confidence=0.72, engine_name=engine, explanation=[],
        betting_analysis=None, feature_version="1.0",
        prediction_timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )


def _make_outcome(match_id="m1") -> MatchOutcome:
    return MatchOutcome(
        match_id=match_id, home_score=2, away_score=1,
        outcome="home_win",
        finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def learning_service(tmp_path):
    """Create a learning service with a temp database."""
    db_path = str(tmp_path / "kernel_test.db")
    init_kernel_db(db_path)
    yield KernelLearningService()
    close_kernel_session()


class TestKernelLearningService:
    def test_record_prediction(self, learning_service):
        match = _make_match()
        pred = _make_prediction()
        learning_service.record_prediction(match, pred)
        # Verify the prediction was stored: compute_error should return None
        # (prediction exists but no outcome recorded yet)
        error = learning_service.compute_error("m1")
        assert error is None

    def test_record_outcome(self, learning_service):
        # Record a prediction first, then an outcome
        match = _make_match()
        pred = _make_prediction()
        learning_service.record_prediction(match, pred)
        outcome = _make_outcome()
        learning_service.record_outcome(outcome)
        # Verify the outcome was stored: compute_error should now return a result
        error = learning_service.compute_error("m1")
        assert error is not None
        assert error.match_id == "m1"

    def test_compute_error_correct_prediction(self, learning_service):
        match = _make_match()
        pred = _make_prediction()
        learning_service.record_prediction(match, pred)
        outcome = _make_outcome()
        learning_service.record_outcome(outcome)
        error = learning_service.compute_error("m1")
        assert error is not None
        assert error.match_id == "m1"
        assert error.outcome_correct is True  # predicted home_win, actual home_win
        assert error.score_mae >= 0

    def test_compute_error_wrong_prediction(self, learning_service):
        match = _make_match()
        pred = PredictionResult(
            predicted_scores={"home": 0.0, "away": 2.0},
            outcome_probabilities={"home_win": 0.10, "draw": 0.20, "away_win": 0.70},
            confidence=0.70, engine_name="elo_odds", explanation=[],
            betting_analysis=None, feature_version="1.0",
            prediction_timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
        )
        learning_service.record_prediction(match, pred)
        outcome = _make_outcome()  # home_win 2-1
        learning_service.record_outcome(outcome)
        error = learning_service.compute_error("m1")
        assert error is not None
        assert error.outcome_correct is False

    def test_compute_error_no_prediction_returns_none(self, learning_service):
        outcome = _make_outcome()
        learning_service.record_outcome(outcome)
        error = learning_service.compute_error("m1")
        assert error is None

    def test_engine_score(self, learning_service):
        # Record multiple predictions and outcomes
        for i in range(5):
            match = _make_match(f"m{i}")
            pred = _make_prediction()
            learning_service.record_prediction(match, pred)
            outcome = MatchOutcome(
                match_id=f"m{i}", home_score=2, away_score=1,
                outcome="home_win",
                finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
            )
            learning_service.record_outcome(outcome)
            learning_service.compute_error(f"m{i}")
        score = learning_service.engine_score("elo_odds", "world_cup")
        assert score is not None
        assert score.engine == "elo_odds"
        assert score.sample_count == 5
        assert 0.0 <= score.accuracy <= 1.0

    def test_engine_score_empty_returns_none(self, learning_service):
        score = learning_service.engine_score("nonexistent", "world_cup")
        assert score is None
