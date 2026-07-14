# backend/tests/test_prediction_history.py
"""Tests for KernelPredictionHistory writing (Phase 3)."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, PredictionResult, ContributionItem,
)
from app.kernel.kernel_db import (
    init_kernel_db, close_kernel_session, get_kernel_session,
    KernelPrediction, KernelPredictionHistory,
)
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
        confidence=0.72, engine_name=engine,
        explanation=[
            ContributionItem(factor="elo", direction="support", weight=0.30,
                             available=True, detail="Elo", predicted_outcome="home_win"),
        ],
        betting_analysis=None, feature_version="1.0",
        prediction_timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )


@pytest.fixture
def svc(tmp_path):
    db_path = str(tmp_path / "kernel_test.db")
    init_kernel_db(db_path)
    yield KernelLearningService()
    close_kernel_session()


class TestPredictionHistory:
    def test_record_prediction_writes_history(self, svc):
        """record_prediction writes a row to KernelPredictionHistory."""
        match = _make_match("m1")
        pred = _make_prediction()
        svc.record_prediction(match, pred)

        session = get_kernel_session()
        history = session.query(KernelPredictionHistory).filter_by(match_id="m1").first()
        assert history is not None
        assert history.engine == "elo_odds"
        assert history.feature_version == "1.0"
        assert history.trigger == "initial"
        session.close()

    def test_record_prediction_still_upserts_prediction(self, svc):
        """record_prediction still upserts KernelPrediction (existing behavior)."""
        match = _make_match("m1")
        pred = _make_prediction()
        svc.record_prediction(match, pred)

        session = get_kernel_session()
        kp = session.get(KernelPrediction, "m1")
        assert kp is not None
        assert kp.engine == "elo_odds"
        session.close()

    def test_multiple_predictions_create_multiple_history_rows(self, svc):
        """Each record_prediction call creates a new history row."""
        match = _make_match("m1")
        pred1 = _make_prediction()
        pred2 = _make_prediction()
        svc.record_prediction(match, pred1)
        svc.record_prediction(match, pred2)

        session = get_kernel_session()
        rows = session.query(KernelPredictionHistory).filter_by(match_id="m1").all()
        assert len(rows) == 2
        session.close()
