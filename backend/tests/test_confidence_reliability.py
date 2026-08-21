"""Confidence-reliability curve + ECE (P1-X1).

The point of these tests is that confidence and max(outcome_probabilities) are
*different numbers* in every fixture below. A curve built from the wrong column
lands in a different bin and reports a different ECE, so each assertion can tell
the two apart — which the pre-existing reliability tests could not, since they
only ever asserted bin counts and total_samples.
"""
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.predictions import router
from app.core import config
from app.kernel.kernel_db import (
    KernelMatchOutcome,
    KernelPrediction,
    _reliability_curve,
    close_kernel_session,
    compute_confidence_reliability_bins,
    compute_reliability_bins,
    get_kernel_session,
    init_kernel_db,
)


@pytest.fixture
def db(tmp_path):
    """Fresh SQLite DB per test."""
    init_kernel_db(str(tmp_path / "test_kernel.db"))
    session = get_kernel_session()
    yield session
    session.close()
    close_kernel_session()


def _seed(session, match_id, *, confidence, max_prob, correct,
          engine="basketball", competition="nba"):
    """One prediction + its outcome, with confidence != max_prob by design."""
    session.add(KernelPrediction(
        match_id=match_id, sport="basketball", competition=competition,
        season="2025", engine=engine,
        predicted_scores={"home": 112, "away": 108},
        outcome_probabilities={"home_win": max_prob, "away_win": 1.0 - max_prob},
        confidence=confidence, feature_version="nba-1.0", explanation=[],
        created_at=datetime(2026, 7, 14, 18, 30, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 14, 18, 30, tzinfo=timezone.utc),
    ))
    session.add(KernelMatchOutcome(
        match_id=match_id, home_score=113, away_score=107,
        outcome="home_win", engine=engine,
        score_mae=2.5, outcome_correct=correct, brier_score=0.19,
        finished_at=datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc),
    ))
    session.commit()


def _bin_with_lower(result, lower):
    return next(b for b in result["bins"] if b["lower"] == lower)


class TestReliabilityCurve:
    """The shared binning helper, no DB."""

    def test_ece_is_the_sample_weighted_mean_bin_gap(self):
        # bin 2: two samples, avg_p 0.2 vs avg_a 0.5 -> gap 0.3
        # bin 8: one sample,  avg_p 0.8 vs avg_a 1.0 -> gap 0.2
        # ECE = (2 * 0.3 + 1 * 0.2) / 3
        curve = _reliability_curve([(0.2, 0.0), (0.2, 1.0), (0.8, 1.0)], 10)
        assert curve["ece"] == pytest.approx(0.2667, abs=1e-4)
        assert curve["max_calibration_error"] == pytest.approx(0.3, abs=1e-4)
        assert curve["total_samples"] == 3
        assert curve["sample_count"] == 3

    def test_perfect_calibration_is_zero_ece(self):
        curve = _reliability_curve([(1.0, 1.0), (0.0, 0.0)], 10)
        assert curve["ece"] == pytest.approx(0.0, abs=1e-9)
        assert curve["max_calibration_error"] == pytest.approx(0.0, abs=1e-9)

    def test_no_pairs_leaves_every_metric_undefined(self):
        curve = _reliability_curve([], 10)
        assert curve["ece"] is None
        assert curve["max_calibration_error"] is None
        assert curve["total_samples"] == 0
        assert len(curve["bins"]) == 10
        assert all(b["count"] == 0 for b in curve["bins"])
        assert all(b["avg_predicted"] is None for b in curve["bins"])

    def test_exact_boundary_lands_in_the_upper_bin(self):
        # Regression carried over from compute_reliability_bins: dividing by
        # bin_width put 0.3 in bin 2 (0.3 / 0.1 = 2.9999...).
        curve = _reliability_curve([(0.3, 1.0)], 10)
        assert _bin_with_lower(curve, 0.3)["count"] == 1
        assert _bin_with_lower(curve, 0.2)["count"] == 0

    def test_one_point_zero_clamps_into_the_last_bin(self):
        curve = _reliability_curve([(1.0, 1.0)], 10)
        assert _bin_with_lower(curve, 0.9)["count"] == 1


class TestConfidenceReliabilityBins:
    def test_bins_by_confidence_not_by_max_probability(self, db):
        """The discriminating case: 0.90 confidence over a 0.55 top probability."""
        for i, correct in enumerate([1, 0, 0, 0]):
            _seed(db, f"m{i}", confidence=0.90, max_prob=0.55, correct=correct)

        conf = compute_confidence_reliability_bins(bins=10)
        assert _bin_with_lower(conf, 0.9)["count"] == 4
        assert _bin_with_lower(conf, 0.9)["avg_predicted"] == pytest.approx(0.90)
        assert _bin_with_lower(conf, 0.9)["actual_frequency"] == pytest.approx(0.25)
        assert _bin_with_lower(conf, 0.5)["count"] == 0

        # Same rows, probability curve: bin [0.5, 0.6) instead.
        prob = compute_reliability_bins(bins=10)
        assert _bin_with_lower(prob, 0.5)["count"] == 4
        assert _bin_with_lower(prob, 0.9)["count"] == 0

    def test_the_two_curves_report_different_ece_on_the_same_rows(self, db):
        for i, correct in enumerate([1, 0, 0, 0]):
            _seed(db, f"m{i}", confidence=0.90, max_prob=0.55, correct=correct)

        # |0.90 - 0.25| vs |0.55 - 0.25|
        assert compute_confidence_reliability_bins()["ece"] == pytest.approx(0.65)
        assert compute_reliability_bins()["ece"] == pytest.approx(0.30)

    def test_signed_gap_is_positive_when_overconfident(self, db):
        for i, correct in enumerate([1, 0, 0, 0]):
            _seed(db, f"m{i}", confidence=0.90, max_prob=0.55, correct=correct)

        result = compute_confidence_reliability_bins()
        assert result["mean_confidence"] == pytest.approx(0.90)
        assert result["mean_accuracy"] == pytest.approx(0.25)
        assert result["signed_gap"] == pytest.approx(0.65)

    def test_signed_gap_is_negative_when_underconfident(self, db):
        """ECE alone cannot say which way to move the formula; the sign can."""
        for i in range(4):
            _seed(db, f"m{i}", confidence=0.40, max_prob=0.55, correct=1)

        result = compute_confidence_reliability_bins()
        assert result["ece"] == pytest.approx(0.60)
        assert result["signed_gap"] == pytest.approx(-0.60)

    def test_spread_across_bins_weights_ece_by_bin_size(self, db):
        # Three at 0.80 with 2/3 correct -> gap 0.1333, weight 3
        # One at 0.40 that was wrong    -> gap 0.4,    weight 1
        for i, correct in enumerate([1, 1, 0]):
            _seed(db, f"hi{i}", confidence=0.80, max_prob=0.55, correct=correct)
        _seed(db, "lo0", confidence=0.40, max_prob=0.55, correct=0)

        result = compute_confidence_reliability_bins(bins=10)
        assert _bin_with_lower(result, 0.8)["count"] == 3
        assert _bin_with_lower(result, 0.4)["count"] == 1
        expected = (3 * abs(0.80 - 2 / 3) + 1 * 0.40) / 4
        assert result["ece"] == pytest.approx(expected, abs=1e-4)
        assert result["max_calibration_error"] == pytest.approx(0.40, abs=1e-4)
        assert result["total_samples"] == 4

    def test_no_samples_leaves_the_gap_undefined_rather_than_zero(self, db):
        result = compute_confidence_reliability_bins()
        assert len(result["bins"]) == 10
        assert result["total_samples"] == 0
        assert result["ece"] is None
        assert result["mean_confidence"] is None
        assert result["mean_accuracy"] is None
        assert result["signed_gap"] is None

    def test_ungraded_predictions_are_excluded(self, db):
        _seed(db, "graded", confidence=0.90, max_prob=0.55, correct=1)
        _seed(db, "ungraded", confidence=0.30, max_prob=0.55, correct=None)

        result = compute_confidence_reliability_bins()
        assert result["total_samples"] == 1
        assert result["mean_confidence"] == pytest.approx(0.90)

    def test_engine_and_competition_filters_apply(self, db):
        _seed(db, "nba1", confidence=0.90, max_prob=0.55, correct=1,
              engine="basketball", competition="nba")
        _seed(db, "wc1", confidence=0.40, max_prob=0.55, correct=0,
              engine="elo_odds", competition="wc")

        assert compute_confidence_reliability_bins(
            engine="basketball")["mean_confidence"] == pytest.approx(0.90)
        assert compute_confidence_reliability_bins(
            competition="wc")["mean_confidence"] == pytest.approx(0.40)
        assert compute_confidence_reliability_bins()["total_samples"] == 2

    def test_bin_count_is_configurable(self, db):
        _seed(db, "m0", confidence=0.90, max_prob=0.55, correct=1)
        assert len(compute_confidence_reliability_bins(bins=5)["bins"]) == 5


class TestConfidenceReliabilityEndpoint:
    @staticmethod
    def _client():
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_200_carries_the_bins_and_the_signed_gap(self, db):
        for i, correct in enumerate([1, 0, 0, 0]):
            _seed(db, f"m{i}", confidence=0.90, max_prob=0.55, correct=correct)

        original = config.settings.KERNEL_PREDICTION_ENABLED
        config.settings.KERNEL_PREDICTION_ENABLED = True
        try:
            resp = self._client().get("/predictions/calibration/confidence-reliability")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_samples"] == 4
            assert data["ece"] == pytest.approx(0.65)
            assert data["signed_gap"] == pytest.approx(0.65)
            assert len(data["bins"]) == 10
        finally:
            config.settings.KERNEL_PREDICTION_ENABLED = original

    def test_bins_out_of_range_is_422(self, db):
        original = config.settings.KERNEL_PREDICTION_ENABLED
        config.settings.KERNEL_PREDICTION_ENABLED = True
        try:
            client = self._client()
            base = "/predictions/calibration/confidence-reliability"
            assert client.get(f"{base}?bins=4").status_code == 422
            assert client.get(f"{base}?bins=21").status_code == 422
        finally:
            config.settings.KERNEL_PREDICTION_ENABLED = original

    def test_503_when_kernel_prediction_disabled(self):
        original = config.settings.KERNEL_PREDICTION_ENABLED
        config.settings.KERNEL_PREDICTION_ENABLED = False
        try:
            resp = self._client().get("/predictions/calibration/confidence-reliability")
            assert resp.status_code == 503
        finally:
            config.settings.KERNEL_PREDICTION_ENABLED = original
