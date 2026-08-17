"""Focused tests for read-only Phase 9 live evidence reporting."""
from datetime import datetime, timezone

import pytest

from app.kernel.kernel_db import (
    KernelMatchOutcome,
    KernelPrediction,
    close_kernel_db,
    get_kernel_session,
    init_kernel_db,
)
from app.services.phase9_live_evidence_service import build_live_evidence_report


@pytest.fixture
def db(tmp_path):
    init_kernel_db(str(tmp_path / "kernel.db"))
    yield get_kernel_session()
    close_kernel_db()


def add_prediction(session, match_id, sport="football", competition="epl", engine="elo"):
    session.add(
        KernelPrediction(
            match_id=match_id,
            sport=sport,
            competition=competition,
            season="2025-26",
            engine=engine,
            predicted_scores={"home": 1, "away": 0},
            outcome_probabilities={"home_win": 0.6, "away_win": 0.4},
            confidence=0.6,
            feature_version="test",
            explanation=[],
        )
    )


def add_outcome(session, match_id, correct=1, brier=0.2, finished_at=None, outcome="home_win"):
    session.add(
        KernelMatchOutcome(
            match_id=match_id,
            outcome=outcome,
            outcome_correct=correct,
            brier_score=brier,
            finished_at=finished_at,
        )
    )


def test_empty_store(db, monkeypatch):
    monkeypatch.setattr("app.services.phase9_live_evidence_service.settings.MIN_SAMPLES_FOR_CALIBRATION", 2)
    db.commit()
    assert build_live_evidence_report() == {
        "threshold": 2,
        "total_predictions": 0,
        "total_settled": 0,
        "group_count": 0,
        "ready_group_count": 0,
        "learning_ready": False,
        "groups": [],
    }


def test_unresolved_prediction_is_not_settled(db, monkeypatch):
    monkeypatch.setattr("app.services.phase9_live_evidence_service.settings.MIN_SAMPLES_FOR_CALIBRATION", 1)
    add_prediction(db, "unresolved")
    db.commit()
    report = build_live_evidence_report()
    group = report["groups"][0]
    assert group["prediction_count"] == 1
    assert group["settled_count"] == 0
    assert group["accuracy"] is None
    assert group["avg_brier_score"] is None
    assert group["readiness"] == "insufficient_samples"
    assert report["learning_ready"] is False


def test_threshold_and_aggregates(db, monkeypatch):
    monkeypatch.setattr("app.services.phase9_live_evidence_service.settings.MIN_SAMPLES_FOR_CALIBRATION", 2)
    add_prediction(db, "one")
    add_prediction(db, "two")
    add_outcome(db, "one", correct=1, brier=0.1, finished_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    add_outcome(db, "two", correct=0, brier=None, finished_at=datetime(2026, 1, 3, tzinfo=timezone.utc))
    db.commit()
    group = build_live_evidence_report()["groups"][0]
    assert group["settled_count"] == 2
    assert group["remaining_samples"] == 0
    assert group["readiness"] == "ready"
    assert group["accuracy"] == 0.5
    assert group["avg_brier_score"] == 0.1
    assert group["latest_settled_at"] == "2026-01-03T00:00:00"


def test_groups_are_separate_and_sorted(db, monkeypatch):
    monkeypatch.setattr("app.services.phase9_live_evidence_service.settings.MIN_SAMPLES_FOR_CALIBRATION", 1)
    add_prediction(db, "z", sport="football", competition="epl", engine="b")
    add_prediction(db, "a", sport="basketball", competition="nba", engine="a")
    add_prediction(db, "m", sport="football", competition="epl", engine="a")
    for match_id in ("z", "a", "m"):
        add_outcome(db, match_id)
    db.commit()
    groups = build_live_evidence_report()["groups"]
    assert [(g["sport"], g["competition"], g["engine"]) for g in groups] == [
        ("basketball", "nba", "a"),
        ("football", "epl", "a"),
        ("football", "epl", "b"),
    ]
    assert all(group["readiness"] == "ready" for group in groups)
    assert build_live_evidence_report()["ready_group_count"] == 3


def test_outcome_without_correct_is_unsettled(db, monkeypatch):
    monkeypatch.setattr("app.services.phase9_live_evidence_service.settings.MIN_SAMPLES_FOR_CALIBRATION", 1)
    add_prediction(db, "missing-correct")
    add_outcome(db, "missing-correct", correct=None)
    db.commit()
    group = build_live_evidence_report()["groups"][0]
    assert group["settled_count"] == 0
    assert group["readiness"] == "insufficient_samples"
