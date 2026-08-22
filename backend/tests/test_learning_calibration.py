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


def _away_called_probs(i: int) -> dict:
    """Probabilities whose argmax is away_win.

    The home-win column still varies so the calibration regression keeps a
    non-zero denominator; only the engine's *call* is held constant.
    """
    home = 0.10 + 0.05 * (i % 5)  # 0.10 .. 0.30
    return {"home_win": home, "draw": 0.20, "away_win": 1.0 - home - 0.20}


def _home_called_probs(i: int) -> dict:
    """Probabilities whose argmax is home_win, varying the same way."""
    home = 0.50 + 0.05 * (i % 5)  # 0.50 .. 0.70
    return {"home_win": home, "draw": 0.15, "away_win": 1.0 - home - 0.15}


def _seed_rows(service, count, *, engine, prefix, probs, outcome_at,
               confidence, competition="world_cup"):
    """Seed count prediction+outcome pairs under one engine.

    ``probs(i)`` supplies the probabilities and ``outcome_at(i)`` the real
    result, so a test can make the engine's accuracy and the league's home-win
    rate differ by construction. ``_seed_predictions_with_outcomes`` cannot:
    there the argmax is always home_win and home wins occur at exactly the same
    rate, so the two quantities coincide and no assertion can tell them apart.
    """
    for i in range(count):
        mid = f"{prefix}_{i}"
        match = _make_match(mid, competition)
        service.record_prediction(match, PredictionResult(
            predicted_scores={"home": 1.0, "away": 2.0},
            outcome_probabilities=probs(i),
            confidence=confidence, engine_name=engine,
            explanation=[
                ContributionItem(factor="elo", direction="support", weight=1.0,
                                 available=True, detail="Elo",
                                 predicted_outcome="away_win"),
            ],
            betting_analysis=None, feature_version="1.0",
            prediction_timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
        ))
        result = outcome_at(i)
        service.record_outcome(MatchOutcome(
            match_id=mid,
            home_score=2 if result == "home_win" else 1,
            away_score=1 if result == "home_win" else 2,
            outcome=result,
            finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
        ))
        service.compute_error(mid)


def _home_win_rate(session) -> float:
    """Share of seeded fixtures that ended in a home win."""
    rows = session.query(KernelMatchOutcome).all()
    return sum(1 for r in rows if r.outcome == "home_win") / len(rows)


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


class TestCalibrationSummaryFields:
    """avg_accuracy / avg_confidence must describe the engine, not the league.

    Neither field is decoration. ``edge_detector_service._compute_trust_phase3``
    and ``calibration_fusion_service._compute_phase3_trust`` read avg_accuracy
    as engine trust, ``engine_score`` divides accuracy by confidence to report
    confidence calibration, and the learning panel renders both as
    平均置信度 / 平均准确率.

    Every fixture below is called away_win by the engine while one in four ends
    in a home win, so the engine's accuracy (0.75) and the home-win rate (0.25)
    are different numbers. Reading either field off the home-win column now
    fails these assertions.
    """

    @staticmethod
    def _seed_and_fit(svc, count=12, confidence=0.80, prefix="acc"):
        _seed_rows(
            svc, count, engine="elo_odds", prefix=prefix,
            probs=_away_called_probs,
            outcome_at=lambda i: "home_win" if i % 4 == 3 else "away_win",
            confidence=confidence,
        )
        svc.update_calibration("world_cup", "elo_odds")

    def test_avg_accuracy_is_engine_accuracy_not_home_win_rate(self, svc):
        self._seed_and_fit(svc)

        session = get_kernel_session()
        cal = session.query(KernelCalibration).filter_by(
            engine="elo_odds", competition="world_cup",
        ).first()
        base_rate = _home_win_rate(session)
        session.close()

        # The engine called away_win 12 times and was right 9 of them.
        assert cal.avg_accuracy == pytest.approx(0.75)
        # Guard the fixture: these must stay distinct or the assertion above
        # stops discriminating, which is how the old vacuous check survived.
        assert base_rate == pytest.approx(0.25)

    def test_avg_confidence_is_engine_confidence(self, svc):
        self._seed_and_fit(svc)

        session = get_kernel_session()
        cal = session.query(KernelCalibration).first()
        session.close()

        # KernelPrediction.confidence, not the mean predicted home-win
        # probability — which is 0.1875 over this fixture.
        assert cal.avg_confidence == pytest.approx(0.80)

    def test_avg_accuracy_matches_the_recorded_per_match_flag(self, svc):
        """The summary cannot drift from the flag compute_error wrote per match."""
        self._seed_and_fit(svc)

        session = get_kernel_session()
        cal = session.query(KernelCalibration).first()
        outcomes = session.query(KernelMatchOutcome).all()
        session.close()

        recorded = sum(1 for o in outcomes if o.outcome_correct) / len(outcomes)
        assert cal.avg_accuracy == pytest.approx(recorded)

    def test_confidence_bucket_row_reports_its_own_bucket(self, svc):
        """A #c_high row must not report a low confidence (P1-V5 coherence)."""
        from app.kernel.learning_service import competition_with_bucket

        _seed_rows(
            svc, 12, engine="elo_odds", prefix="bkt",
            probs=_away_called_probs,
            outcome_at=lambda i: "home_win" if i % 4 == 3 else "away_win",
            confidence=0.85,
        )
        svc.update_calibration_by_confidence("world_cup", "elo_odds")

        session = get_kernel_session()
        cal = session.query(KernelCalibration).filter_by(
            engine="elo_odds",
            competition=competition_with_bucket("world_cup", "high"),
        ).first()
        session.close()

        assert cal is not None
        # The bucket is defined by confidence >= 0.70, so a row inside it
        # reporting 0.1875 would contradict its own key.
        assert cal.avg_confidence == pytest.approx(0.85)
        assert cal.avg_accuracy == pytest.approx(0.75)

    def test_stage_bucket_row_uses_the_same_definition(self, svc):
        from app.kernel.learning_service import competition_with_stage

        _seed_rows(
            svc, 12, engine="elo_odds", prefix="stg",
            probs=_away_called_probs,
            outcome_at=lambda i: "home_win" if i % 4 == 3 else "away_win",
            confidence=0.80,
        )
        svc.update_calibration_by_stage("world_cup", "elo_odds")

        session = get_kernel_session()
        cal = session.query(KernelCalibration).filter_by(
            engine="elo_odds",
            competition=competition_with_stage("world_cup", "regular"),
        ).first()
        session.close()

        assert cal is not None
        assert cal.avg_confidence == pytest.approx(0.80)
        assert cal.avg_accuracy == pytest.approx(0.75)

    def test_trust_separates_a_right_engine_from_a_wrong_one(self, svc):
        """Same results, opposite calls, opposite trust.

        Every fixture here ends away_win, so the home-win rate is 0.0 for both
        engines. Trust read off that column gave the engine that called every
        match correctly and the engine that called every match wrong the
        identical value.
        """
        from app.kernel.calibration_fusion_service import _compute_phase3_trust

        for engine, probs, prefix in (
            ("right_engine", _away_called_probs, "r"),
            ("wrong_engine", _home_called_probs, "w"),
        ):
            _seed_rows(svc, 12, engine=engine, prefix=prefix, probs=probs,
                       outcome_at=lambda i: "away_win", confidence=0.80)
            svc.update_calibration("world_cup", engine)

        session = get_kernel_session()
        rows = {c.engine: c for c in session.query(KernelCalibration).all()}
        base_rate = _home_win_rate(session)
        session.close()

        assert base_rate == pytest.approx(0.0)
        assert rows["right_engine"].avg_accuracy == pytest.approx(1.0)
        assert rows["wrong_engine"].avg_accuracy == pytest.approx(0.0)

        right = _compute_phase3_trust(rows["right_engine"].avg_accuracy, 12)
        wrong = _compute_phase3_trust(rows["wrong_engine"].avg_accuracy, 12)
        assert right == pytest.approx(1.0)
        assert wrong < right


class TestPredictedOutcome:
    """One definition of "the outcome the engine called", shared with compute_error."""

    def test_picks_the_most_likely_outcome(self):
        from app.kernel.learning_service import predicted_outcome

        assert predicted_outcome(
            {"home_win": 0.2, "draw": 0.3, "away_win": 0.5}) == "away_win"

    def test_missing_probabilities_call_nothing(self):
        from app.kernel.learning_service import predicted_outcome

        assert predicted_outcome(None) is None
        assert predicted_outcome({}) is None
        assert predicted_outcome("home_win") is None

    def test_a_tie_keeps_the_first_key(self):
        """Pins the pre-existing tie-break so compute_error is unchanged."""
        from app.kernel.learning_service import predicted_outcome

        assert predicted_outcome({"draw": 0.4, "home_win": 0.4}) == "draw"


class TestConditionalCalibration:
    def test_confidence_bucket_edges(self):
        from app.kernel.learning_service import confidence_bucket

        assert confidence_bucket(0.2) == "low"
        assert confidence_bucket(0.5) == "mid"
        assert confidence_bucket(0.9) == "high"

    def test_conditional_calibration_buckets(self, svc):
        """Bucket rows stored under competition#c_* keys when samples allow."""
        from app.kernel.learning_service import competition_with_bucket

        # Seed with varying confidence via record_prediction
        for i in range(30):
            mid = f"wc_cond_{i}"
            match = _make_match(mid, "world_cup")
            conf = 0.30 if i < 10 else (0.55 if i < 20 else 0.85)
            pred = _make_prediction_with_probs(0.4 + (i % 5) * 0.1)
            # rebuild with specific confidence
            pred = PredictionResult(
                predicted_scores=pred.predicted_scores,
                outcome_probabilities=pred.outcome_probabilities,
                confidence=conf,
                engine_name=pred.engine_name,
                explanation=pred.explanation,
                betting_analysis=None,
                feature_version=pred.feature_version,
                prediction_timestamp=pred.prediction_timestamp,
            )
            svc.record_prediction(match, pred)
            outcome = MatchOutcome(
                match_id=mid,
                home_score=2 if i % 2 == 0 else 1,
                away_score=1 if i % 2 == 0 else 2,
                outcome="home_win" if i % 2 == 0 else "away_win",
                finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
            )
            svc.record_outcome(outcome)
            svc.compute_error(mid)

        written = svc.update_calibration_by_confidence("world_cup", "elo_odds")
        assert isinstance(written, dict)
        # At least one bucket should have been written with min samples
        assert sum(written.values()) > 0 or any(
            session_has_bucket(svc, b) for b in ("low", "mid", "high")
        )
        cal = svc.get_conditional_calibration("world_cup", "elo_odds", 0.9)
        assert cal is not None
        assert "slope" in cal and cal["bucket"] == "high"


def session_has_bucket(svc, bucket: str) -> bool:
    from app.kernel.kernel_db import get_kernel_session, KernelCalibration
    from app.kernel.learning_service import competition_with_bucket

    s = get_kernel_session()
    try:
        key = competition_with_bucket("world_cup", bucket)
        row = s.query(KernelCalibration).filter_by(
            engine="elo_odds", competition=key,
        ).first()
        return row is not None
    finally:
        s.close()
