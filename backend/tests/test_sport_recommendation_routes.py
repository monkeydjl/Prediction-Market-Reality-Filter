"""Tests for sport recommendation API routes.

All endpoints gated by PHASE7_SPORT_RECOMMENDATION_ENABLED (503 when false).
All are GET (read-only) — no require_write_key auth.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config
from app.kernel.kernel_db import init_kernel_db, close_kernel_db


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "rec_routes_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


@pytest.fixture
def client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_SPORT_RECOMMENDATION_ENABLED", True)
    from app.api.routes import sport_recommendations
    app = FastAPI()
    app.include_router(sport_recommendations.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def disabled_client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_SPORT_RECOMMENDATION_ENABLED", False)
    from app.api.routes import sport_recommendations
    app = FastAPI()
    app.include_router(sport_recommendations.router, prefix="/api")
    return TestClient(app)


def _seed_prediction_and_edge(match_id="m1", implied=0.50, adjusted_edge=0.072):
    """Helper: seed prediction + calibration + edge row."""
    from datetime import datetime, timezone
    from app.kernel.kernel_db import KernelPrediction, KernelCalibration, get_kernel_session
    from app.kernel.edge_store import EdgeStore
    now = datetime.now(timezone.utc)
    session = get_kernel_session()
    try:
        session.add(KernelPrediction(
            match_id=match_id, sport="basketball", competition="nba",
            season="2025-26", engine="BasketballEngine", predicted_scores={},
            outcome_probabilities={"home_win": 0.65, "away_win": 0.35},
            confidence=0.7, feature_version="nba-1.0", explanation={},
            created_at=now, updated_at=now,
        ))
        existing_cal = (
            session.query(KernelCalibration)
            .filter_by(engine="BasketballEngine", competition="nba")
            .one_or_none()
        )
        if existing_cal is None:
            session.add(KernelCalibration(
                engine="BasketballEngine", competition="nba", slope=1.0, intercept=0.0,
                sample_count=20, avg_confidence=0.65, avg_accuracy=0.72, last_updated=now,
            ))
        session.commit()
    finally:
        session.close()
    EdgeStore().append_edge(
        match_id=match_id, mapped_outcome="home_win",
        model_prob=0.65, market_prob=implied,
        raw_edge=0.65 - implied, trust=0.72, liquidity_factor=1.0,
        adjusted_edge=adjusted_edge, spread=None, sources_count=1,
        stale=False, captured_at=now,
    )


def test_get_recommendation_returns_503_when_disabled(disabled_client):
    res = disabled_client.get("/api/sport-recommendations/m1")
    assert res.status_code == 503


def test_get_recommendation_returns_404_when_no_edges(client):
    res = client.get("/api/sport-recommendations/nonexistent")
    assert res.status_code == 404


def test_get_recommendation_returns_rec(client):
    _seed_prediction_and_edge(match_id="m1", implied=0.50, adjusted_edge=0.072)
    res = client.get("/api/sport-recommendations/m1")
    assert res.status_code == 200
    data = res.json()
    assert data["match_id"] == "m1"
    assert data["mapped_outcome"] == "home_win"
    assert data["direction"] == "YES"
    assert data["edge_pct"] == pytest.approx(7.2, abs=0.01)
    assert data["engine_name"] == "BasketballEngine"
    assert data["competition"] == "nba"
    assert "rationale" in data
    assert "仅供参考" in data["rationale"]


def test_open_returns_503_when_disabled(disabled_client):
    res = disabled_client.get("/api/sport-recommendations/open")
    assert res.status_code == 503


def test_open_returns_list(client):
    _seed_prediction_and_edge(match_id="m1", implied=0.50, adjusted_edge=0.072)
    res = client.get("/api/sport-recommendations/open")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 1
    assert any(item["match_id"] == "m1" for item in data["items"])


def test_open_filters_by_decision(client):
    _seed_prediction_and_edge(match_id="m1", implied=0.50, adjusted_edge=0.072)
    res = client.get("/api/sport-recommendations/open", params={"decision": "act"})
    assert res.status_code == 200
    data = res.json()
    assert all(item["decision"] == "act" for item in data["items"])


def test_discrepancies_returns_top_picks(client):
    _seed_prediction_and_edge(match_id="m1", implied=0.50, adjusted_edge=0.072)
    res = client.get("/api/sport-recommendations/discrepancies")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 1
    assert data["items"][0]["match_id"] == "m1"


def test_discrepancies_respects_min_abs_edge(client):
    # Small edge → below threshold
    _seed_prediction_and_edge(match_id="m2", implied=0.64, adjusted_edge=0.0007)
    res = client.get("/api/sport-recommendations/discrepancies", params={"min_abs_edge": 0.05})
    assert res.status_code == 200
    data = res.json()
    match_ids = [item["match_id"] for item in data["items"]]
    assert "m2" not in match_ids
