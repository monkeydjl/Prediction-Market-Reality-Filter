# backend/tests/test_engine_score_persistence.py
"""Tests for EngineScore DB persistence (Phase 3)."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome, PredictionResult,
    ContributionItem,
)
from app.kernel.kernel_db import (
    init_kernel_db, close_kernel_session, get_kernel_session,
    KernelPrediction, KernelMatchOutcome, KernelEngineScore, KernelCalibration,
)
from app.kernel.learning_service import KernelLearningService


def _make_match(match_id="m1", competition="world_cup") -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code=competition, name="Test", sport=sport)
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
            ContributionItem(factor="odds", direction="support", weight=0.70,
                             available=True, detail="Odds", predicted_outcome="home_win"),
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


def _seed_and_process(service, count, competition="world_cup"):
    for i in range(count):
        match = _make_match(f"m{i}", competition)
        pred = _make_prediction()
        service.record_prediction(match, pred)
        outcome = MatchOutcome(
            match_id=f"m{i}", home_score=2, away_score=1,
            outcome="home_win",
            finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
        )
        service.record_outcome(outcome)
        service.compute_error(f"m{i}")


class TestEngineScorePersistence:
    def test_engine_score_persists_to_db(self, svc):
        """engine_score writes to KernelEngineScore table."""
        _seed_and_process(svc, 3)
        score = svc.engine_score("elo_odds", "world_cup")

        session = get_kernel_session()
        row = session.query(KernelEngineScore).filter_by(
            engine="elo_odds", competition="world_cup"
        ).first()
        assert row is not None
        assert row.sample_count == 3
        assert row.accuracy is not None
        session.close()

    def test_confidence_calibration_from_calibration_table(self, svc):
        """confidence_calibration reads from KernelCalibration if available."""
        _seed_and_process(svc, 3)

        # Insert a calibration row
        session = get_kernel_session()
        session.add(KernelCalibration(
            engine="elo_odds", competition="world_cup",
            slope=1.0, intercept=0.0,
            sample_count=15, avg_confidence=0.60, avg_accuracy=0.72,
            last_updated=datetime.now(timezone.utc),
        ))
        session.commit()
        session.close()

        score = svc.engine_score("elo_odds", "world_cup")
        # confidence_calibration = avg_accuracy / avg_confidence = 0.72 / 0.60 = 1.2
        assert abs(score.confidence_calibration - 1.2) < 0.01

    def test_confidence_calibration_zero_when_no_calibration(self, svc):
        """confidence_calibration is 0.0 when no calibration row exists."""
        _seed_and_process(svc, 3)
        score = svc.engine_score("elo_odds", "world_cup")
        assert score.confidence_calibration == 0.0

    def test_engine_score_upsert(self, svc):
        """Running engine_score twice updates the same row."""
        _seed_and_process(svc, 3)
        svc.engine_score("elo_odds", "world_cup")
        svc.engine_score("elo_odds", "world_cup")

        session = get_kernel_session()
        rows = session.query(KernelEngineScore).filter_by(
            engine="elo_odds", competition="world_cup"
        ).all()
        assert len(rows) == 1
        session.close()

    def test_engine_score_returns_correct_values(self, svc):
        """engine_score returns EngineScore with correct aggregated values."""
        _seed_and_process(svc, 5)
        score = svc.engine_score("elo_odds", "world_cup")
        assert score.engine == "elo_odds"
        assert score.competition == "world_cup"
        assert score.sample_count == 5
        assert 0.0 <= score.accuracy <= 1.0
