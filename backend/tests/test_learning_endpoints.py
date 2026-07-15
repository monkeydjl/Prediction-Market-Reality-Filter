"""Tests for learning dashboard DB functions and endpoints."""
import json
from datetime import datetime, timezone

import pytest

from app.kernel.kernel_db import (
    close_kernel_session,
    get_kernel_session,
    init_kernel_db,
    KernelCalibration,
    KernelEngineScore,
    KernelMatchOutcome,
    KernelPrediction,
    KernelPredictionHistory,
    get_engine_scores,
    get_prediction_history,
    get_prediction_history_by_match,
    get_calibrations,
    compute_reliability_bins,
)


@pytest.fixture
def db(tmp_path):
    """Fresh SQLite DB per test."""
    init_kernel_db(str(tmp_path / "test_kernel.db"))
    session = get_kernel_session()
    yield session
    session.close()
    close_kernel_session()


def _insert_prediction(session, match_id="nba-1", sport="basketball", competition="nba",
                       engine="basketball", probs=None, confidence=0.6):
    """Insert a KernelPrediction row."""
    if probs is None:
        probs = {"home_win": 0.62, "away_win": 0.38}
    pred = KernelPrediction(
        match_id=match_id, sport=sport, competition=competition, season="2025",
        engine=engine, predicted_scores={"home": 112, "away": 108},
        outcome_probabilities=probs, confidence=confidence,
        feature_version="nba-1.0", explanation=[],
        created_at=datetime(2026, 7, 14, 18, 30, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 14, 18, 30, tzinfo=timezone.utc),
    )
    session.add(pred)
    session.commit()
    return pred


def _insert_history(session, match_id="nba-1", engine="basketball", trigger="initial",
                    created_at=None):
    """Insert a KernelPredictionHistory row."""
    if created_at is None:
        created_at = datetime(2026, 7, 14, 18, 30, tzinfo=timezone.utc)
    hist = KernelPredictionHistory(
        match_id=match_id, engine=engine,
        predicted_scores={"home": 112, "away": 108},
        outcome_probabilities={"home_win": 0.62, "away_win": 0.38},
        confidence=0.6, feature_version="nba-1.0", trigger=trigger,
        created_at=created_at,
    )
    session.add(hist)
    session.commit()
    return hist


def _insert_outcome(session, match_id="nba-1", outcome="home_win", correct=1,
                    score_mae=2.5, brier=0.19):
    """Insert a KernelMatchOutcome row."""
    o = KernelMatchOutcome(
        match_id=match_id, home_score=113, away_score=107,
        outcome=outcome, engine="basketball",
        score_mae=score_mae, outcome_correct=correct, brier_score=brier,
        finished_at=datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc),
    )
    session.add(o)
    session.commit()
    return o


def _insert_engine_score(session, engine="basketball", competition="nba",
                         accuracy=0.625, avg_mae=3.2, brier=0.21, count=48, cal=0.94):
    """Insert a KernelEngineScore row."""
    s = KernelEngineScore(
        engine=engine, competition=competition, accuracy=accuracy,
        avg_mae=avg_mae, brier_score=brier, sample_count=count,
        confidence_calibration=cal,
        last_updated=datetime(2026, 7, 14, 18, 30, tzinfo=timezone.utc),
    )
    session.add(s)
    session.commit()
    return s


def _insert_calibration(session, engine="basketball", competition="nba",
                        slope=0.85, intercept=0.05, count=48, avg_conf=0.62, avg_acc=0.625):
    """Insert a KernelCalibration row."""
    c = KernelCalibration(
        engine=engine, competition=competition, slope=slope, intercept=intercept,
        sample_count=count, avg_confidence=avg_conf, avg_accuracy=avg_acc,
        last_updated=datetime(2026, 7, 14, 18, 30, tzinfo=timezone.utc),
    )
    session.add(c)
    session.commit()
    return c


class TestGetEngineScores:
    def test_returns_all(self, db):
        _insert_engine_score(db, engine="basketball", competition="nba")
        _insert_engine_score(db, engine="elo_odds", competition="wc")
        result = get_engine_scores()
        assert len(result) == 2

    def test_filter_by_engine(self, db):
        _insert_engine_score(db, engine="basketball", competition="nba")
        _insert_engine_score(db, engine="elo_odds", competition="wc")
        result = get_engine_scores(engine="basketball")
        assert len(result) == 1
        assert result[0].engine == "basketball"

    def test_filter_by_competition(self, db):
        _insert_engine_score(db, engine="basketball", competition="nba")
        _insert_engine_score(db, engine="elo_odds", competition="wc")
        result = get_engine_scores(competition="nba")
        assert len(result) == 1
        assert result[0].competition == "nba"

    def test_filter_by_sport_reverse_lookup(self, db):
        _insert_engine_score(db, engine="basketball", competition="nba")
        _insert_engine_score(db, engine="elo_odds", competition="wc")
        result = get_engine_scores(sport="basketball")
        assert len(result) == 1
        assert result[0].competition == "nba"

    def test_empty_table_returns_empty_list(self, db):
        result = get_engine_scores()
        assert result == []


class TestGetPredictionHistory:
    def test_pagination(self, db):
        _insert_prediction(db, match_id="nba-1")
        for i in range(3):
            _insert_history(db, match_id="nba-1", created_at=datetime(2026, 7, 14, 18 + i, tzinfo=timezone.utc))
        items, total = get_prediction_history(limit=2, offset=0)
        assert len(items) == 2
        assert total == 3

    def test_total_count(self, db):
        _insert_prediction(db, match_id="nba-1")
        _insert_history(db, match_id="nba-1")
        _insert_history(db, match_id="nba-1", created_at=datetime(2026, 7, 14, 19, tzinfo=timezone.utc))
        _, total = get_prediction_history()
        assert total == 2

    def test_sport_filter(self, db):
        _insert_prediction(db, match_id="nba-1", sport="basketball", competition="nba")
        _insert_prediction(db, match_id="wc-1", sport="football", competition="wc")
        _insert_history(db, match_id="nba-1")
        _insert_history(db, match_id="wc-1")
        items, total = get_prediction_history(sport="basketball")
        assert total == 1
        assert items[0]["match_id"] == "nba-1"

    def test_outcome_null_when_unfinished(self, db):
        _insert_prediction(db, match_id="nba-1")
        _insert_history(db, match_id="nba-1")
        items, _ = get_prediction_history()
        assert items[0]["outcome"] is None

    def test_outcome_correct_null_when_uncomputed(self, db):
        _insert_prediction(db, match_id="nba-1")
        _insert_history(db, match_id="nba-1")
        # Insert outcome WITHOUT outcome_correct (null)
        o = KernelMatchOutcome(
            match_id="nba-1", home_score=113, away_score=107,
            outcome="home_win", engine=None,
            score_mae=None, outcome_correct=None, brier_score=None,
            finished_at=datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc),
        )
        db.add(o)
        db.commit()
        items, _ = get_prediction_history()
        assert items[0]["outcome"] is not None
        assert items[0]["outcome"]["outcome_correct"] is None

    def test_items_include_sport_and_competition(self, db):
        _insert_prediction(db, match_id="nba-1", sport="basketball", competition="nba")
        _insert_history(db, match_id="nba-1")
        items, _ = get_prediction_history()
        assert items[0]["sport"] == "basketball"
        assert items[0]["competition"] == "nba"


class TestGetPredictionHistoryByMatch:
    def test_returns_asc_list(self, db):
        _insert_prediction(db, match_id="nba-1")
        _insert_history(db, match_id="nba-1", created_at=datetime(2026, 7, 14, 20, tzinfo=timezone.utc))
        _insert_history(db, match_id="nba-1", created_at=datetime(2026, 7, 14, 18, tzinfo=timezone.utc))
        result = get_prediction_history_by_match("nba-1")
        assert result["count"] == 2
        assert result["items"][0]["created_at"] < result["items"][1]["created_at"]

    def test_nonexistent_returns_empty_not_404(self, db):
        result = get_prediction_history_by_match("nonexistent")
        assert result["count"] == 0
        assert result["items"] == []

    def test_includes_sport_competition(self, db):
        _insert_prediction(db, match_id="nba-1", sport="basketball", competition="nba")
        _insert_history(db, match_id="nba-1")
        result = get_prediction_history_by_match("nba-1")
        assert result["sport"] == "basketball"
        assert result["competition"] == "nba"

    def test_sport_null_when_no_kernel_prediction(self, db):
        # History exists but no KernelPrediction row
        _insert_history(db, match_id="orphan-1")
        result = get_prediction_history_by_match("orphan-1")
        assert result["sport"] is None
        assert result["competition"] is None
        assert result["count"] == 1


class TestGetCalibrations:
    def test_returns_all(self, db):
        _insert_calibration(db, engine="basketball", competition="nba")
        _insert_calibration(db, engine="elo_odds", competition="wc")
        result = get_calibrations()
        assert len(result) == 2

    def test_filter_by_engine(self, db):
        _insert_calibration(db, engine="basketball", competition="nba")
        _insert_calibration(db, engine="elo_odds", competition="wc")
        result = get_calibrations(engine="basketball")
        assert len(result) == 1
        assert result[0].engine == "basketball"

    def test_filter_by_competition(self, db):
        _insert_calibration(db, engine="basketball", competition="nba")
        _insert_calibration(db, engine="elo_odds", competition="wc")
        result = get_calibrations(competition="nba")
        assert len(result) == 1

    def test_empty_table_returns_empty_list(self, db):
        result = get_calibrations()
        assert result == []


class TestComputeReliabilityBins:
    def test_ten_bins_default(self, db):
        _insert_prediction(db, match_id="m1", probs={"home_win": 0.9, "away_win": 0.1})
        _insert_outcome(db, match_id="m1", correct=1)
        result = compute_reliability_bins()
        assert len(result["bins"]) == 10
        assert result["total_samples"] == 1

    def test_five_bins(self, db):
        _insert_prediction(db, match_id="m1", probs={"home_win": 0.9, "away_win": 0.1})
        _insert_outcome(db, match_id="m1", correct=1)
        result = compute_reliability_bins(bins=5)
        assert len(result["bins"]) == 5

    def test_empty_bins_return_null_values(self, db):
        _insert_prediction(db, match_id="m1", probs={"home_win": 0.95, "away_win": 0.05})
        _insert_outcome(db, match_id="m1", correct=1)
        result = compute_reliability_bins(bins=10)
        # First few bins should be empty (count=0, avg_predicted=null)
        empty_bins = [b for b in result["bins"] if b["count"] == 0]
        assert len(empty_bins) > 0
        assert all(b["avg_predicted"] is None for b in empty_bins)
        assert all(b["actual_frequency"] is None for b in empty_bins)

    def test_total_samples_correct(self, db):
        for i in range(5):
            _insert_prediction(db, match_id=f"m{i}", probs={"home_win": 0.6 + i * 0.05, "away_win": 0.4 - i * 0.05})
            _insert_outcome(db, match_id=f"m{i}", correct=1 if i % 2 == 0 else 0)
        result = compute_reliability_bins()
        assert result["total_samples"] == 5

    def test_filter_by_engine(self, db):
        _insert_prediction(db, match_id="m1", engine="basketball", probs={"home_win": 0.9, "away_win": 0.1})
        _insert_outcome(db, match_id="m1", correct=1)
        _insert_prediction(db, match_id="m2", engine="elo_odds", competition="wc", sport="football",
                          probs={"home_win": 0.5, "draw": 0.3, "away_win": 0.2})
        _insert_outcome(db, match_id="m2", correct=0)
        result = compute_reliability_bins(engine="basketball")
        assert result["total_samples"] == 1

    def test_no_samples_returns_empty_bins(self, db):
        result = compute_reliability_bins()
        assert len(result["bins"]) == 10
        assert result["total_samples"] == 0
        assert all(b["count"] == 0 for b in result["bins"])

    def test_avg_predicted_and_actual_frequency(self, db):
        _insert_prediction(db, match_id="m1", probs={"home_win": 0.55, "away_win": 0.45})
        _insert_outcome(db, match_id="m1", correct=1)
        _insert_prediction(db, match_id="m2", probs={"home_win": 0.58, "away_win": 0.42})
        _insert_outcome(db, match_id="m2", correct=0)
        result = compute_reliability_bins(bins=10)
        # Both predictions fall in bin [0.5, 0.6)
        bin_50_60 = [b for b in result["bins"] if b["lower"] == 0.5][0]
        assert bin_50_60["count"] == 2
        assert abs(bin_50_60["avg_predicted"] - 0.565) < 0.01
        assert abs(bin_50_60["actual_frequency"] - 0.5) < 0.01
