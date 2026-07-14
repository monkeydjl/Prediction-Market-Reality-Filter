# backend/tests/test_learning_calibration.py
"""Tests for update_calibration (Phase 3)."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome, PredictionResult,
    ContributionItem,
)
from app.kernel.kernel_db import (
    init_kernel_db, close_kernel_session, get_kernel_session,
    KernelPrediction, KernelMatchOutcome, KernelCalibration,
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


def _make_prediction_with_probs(home_prob, engine="elo_odds") -> PredictionResult:
    return PredictionResult(
        predicted_scores={"home": 2.0, "away": 1.0},
        outcome_probabilities={"home_win": home_prob, "draw": 0.25, "away_win": 1 - home_prob - 0.25},
        confidence=0.72, engine_name=engine,
        explanation=[
            ContributionItem(factor="elo", direction="support", weight=0.30, available=True,
                             detail="Elo", predicted_outcome="home_win"),
            ContributionItem(factor="odds", direction="support", weight=0.70, available=True,
                             detail="Odds", predicted_outcome="home_win"),
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


def _seed_predictions_with_outcomes(service, count, competition="world_cup"):
    """Seed N predictions + outcomes where home_win probability varies.

    match_id is scoped by competition because KernelPrediction uses match_id
    as its sole primary key (Task 1 schema) — without scoping, seeding two
    competitions would collide on m0..m{count-1} and the second would overwrite
    the first, defeating per-competition isolation.
    """
    for i in range(count):
        mid = f"{competition}_m{i}"
        match = _make_match(mid, competition)
        home_prob = 0.4 + (i % 5) * 0.1  # 0.4, 0.5, 0.6, 0.7, 0.8 cycling
        pred = _make_prediction_with_probs(home_prob)
        service.record_prediction(match, pred)

        outcome = MatchOutcome(
            match_id=mid, home_score=2 if i % 3 != 2 else 1,
            away_score=1 if i % 3 != 2 else 2,
            outcome="home_win" if i % 3 != 2 else "away_win",
            finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
        )
        service.record_outcome(outcome)
        service.compute_error(mid)


class TestUpdateCalibration:
    def test_insufficient_samples_skips(self, svc):
        """With < 10 samples, update_calibration does nothing."""
        _seed_predictions_with_outcomes(svc, 5)
        svc.update_calibration("world_cup", "elo_odds")

        session = get_kernel_session()
        cals = session.query(KernelCalibration).all()
        assert len(cals) == 0
        session.close()

    def test_sufficient_samples_creates_calibration(self, svc):
        """With >= 10 samples, calibration row is created."""
        _seed_predictions_with_outcomes(svc, 12)
        svc.update_calibration("world_cup", "elo_odds")

        session = get_kernel_session()
        cal = session.query(KernelCalibration).filter_by(
            engine="elo_odds", competition="world_cup"
        ).first()
        assert cal is not None
        assert 0.0 <= cal.slope <= 2.0
        assert -0.5 <= cal.intercept <= 0.5
        assert cal.sample_count == 12
        session.close()

    def test_slope_clamped_to_max(self, svc):
        """Slope is clamped to [0.0, 2.0]."""
        _seed_predictions_with_outcomes(svc, 12)
        svc.update_calibration("world_cup", "elo_odds")

        session = get_kernel_session()
        cal = session.query(KernelCalibration).first()
        assert cal.slope <= 2.0
        assert cal.slope >= 0.0
        session.close()

    def test_intercept_clamped(self, svc):
        """Intercept is clamped to [-0.5, 0.5]."""
        _seed_predictions_with_outcomes(svc, 12)
        svc.update_calibration("world_cup", "elo_odds")

        session = get_kernel_session()
        cal = session.query(KernelCalibration).first()
        assert -0.5 <= cal.intercept <= 0.5
        session.close()

    def test_upsert_updates_existing(self, svc):
        """Running update_calibration twice updates the same row."""
        _seed_predictions_with_outcomes(svc, 12)
        svc.update_calibration("world_cup", "elo_odds")
        svc.update_calibration("world_cup", "elo_odds")

        session = get_kernel_session()
        cals = session.query(KernelCalibration).filter_by(
            engine="elo_odds", competition="world_cup"
        ).all()
        assert len(cals) == 1
        session.close()

    def test_per_competition_isolation(self, svc):
        """Calibration for different competitions are separate rows."""
        _seed_predictions_with_outcomes(svc, 12, "world_cup")
        _seed_predictions_with_outcomes(svc, 12, "epl")
        svc.update_calibration("world_cup", "elo_odds")
        svc.update_calibration("epl", "elo_odds")

        session = get_kernel_session()
        cals = session.query(KernelCalibration).all()
        assert len(cals) == 2
        comps = {c.competition for c in cals}
        assert comps == {"world_cup", "epl"}
        session.close()

    def test_avg_confidence_and_accuracy_stored(self, svc):
        """avg_confidence and avg_accuracy are stored in calibration row."""
        _seed_predictions_with_outcomes(svc, 12)
        svc.update_calibration("world_cup", "elo_odds")

        session = get_kernel_session()
        cal = session.query(KernelCalibration).first()
        assert cal.avg_confidence > 0
        assert cal.avg_accuracy >= 0
        session.close()
