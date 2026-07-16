"""Tests for sport settlement API routes.

All endpoints gated by PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED (503 when false).
3 GET endpoints are read-only. 1 POST endpoint requires require_write_key.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config
from app.kernel.kernel_db import init_kernel_db, close_kernel_db


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "settlement_routes_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


@pytest.fixture
def client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED", True)
    monkeypatch.setattr(config.settings, "API_WRITE_KEY", "test-key")
    from app.api.routes import sport_settlements
    app = FastAPI()
    app.include_router(sport_settlements.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def disabled_client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED", False)
    # Bypass write-key auth so the 503 comes from _ensure_enabled(), not require_write_key.
    # Explicitly clear API_WRITE_KEY (in case a prior client test set it) so
    # require_write_key takes the ALLOW_OPEN_WRITES branch and returns.
    monkeypatch.setattr(config.settings, "API_WRITE_KEY", "")
    monkeypatch.setattr(config.settings, "ALLOW_OPEN_WRITES", True)
    from app.api.routes import sport_settlements
    app = FastAPI()
    app.include_router(sport_settlements.router, prefix="/api")
    return TestClient(app)


def _seed_full_scenario(match_id="m1"):
    """Seed prediction + outcome + link + snapshot + edge for a complete settlement.

    Snapshot is backdated 1s so it precedes the match's finished_at (same fix
    as Task 1's _seed_verified_link) to avoid a timing race where
    captured_at > finished_at causes skipped_no_snapshot.
    """
    from datetime import datetime, timezone, timedelta
    from app.kernel.kernel_db import KernelPrediction, KernelMatchOutcome, get_kernel_session
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    from app.kernel.edge_store import EdgeStore
    now = datetime.now(timezone.utc)
    snapshot_at = now - timedelta(seconds=1)  # backdate snapshot
    session = get_kernel_session()
    try:
        session.add(KernelPrediction(
            match_id=match_id, sport="basketball", competition="nba",
            season="2025-26", engine="BasketballEngine", predicted_scores={},
            outcome_probabilities={"home_win": 0.65, "away_win": 0.35}, confidence=0.7,
            feature_version="nba-1.0", explanation={}, created_at=now, updated_at=now,
        ))
        session.add(KernelMatchOutcome(
            match_id=match_id, home_score=2, away_score=1, outcome="home_win",
            engine=None, score_mae=None, outcome_correct=None, brier_score=None,
            finished_at=now, created_at=now,
        ))
        session.commit()
    finally:
        session.close()
    link_store = SportMarketLinkStore()
    link = link_store.upsert_link(
        match_id=match_id, contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=True, market_question="q", implied_prob=0.6,
    )
    snap_store = MarketSnapshotStore()
    snap_store.append_snapshot(
        link_id=link["id"], implied_prob=0.9, price=0.9,
        liquidity=None, volume=None, captured_at=snapshot_at,
    )
    edge_store = EdgeStore()
    edge_store.append_edge(
        match_id=match_id, mapped_outcome="home_win",
        model_prob=0.65, market_prob=0.6, raw_edge=0.05,
        trust=0.8, liquidity_factor=1.0, adjusted_edge=0.04,
        spread=None, sources_count=1, stale=False,
    )


def test_get_settlement_returns_503_when_disabled(disabled_client):
    res = disabled_client.get("/api/sport-settlements/m1")
    assert res.status_code == 503


def test_get_settlement_returns_404_when_no_settlements(client):
    res = client.get("/api/sport-settlements/m1")
    assert res.status_code == 404


def test_get_settlement_returns_settlement(client):
    _seed_full_scenario("m1")
    # Process settlement first
    res = client.post("/api/sport-settlements/process/m1", headers={"X-Api-Key": "test-key"})
    assert res.status_code == 200
    # Now GET should return the settlement
    res = client.get("/api/sport-settlements/m1")
    assert res.status_code == 200
    data = res.json()
    assert data["match_id"] == "m1"
    assert data["total"] == 1
    assert data["items"][0]["mapped_outcome"] == "home_win"
    assert data["items"][0]["status"] == "processed"


def test_get_calibrations_returns_503_when_disabled(disabled_client):
    res = disabled_client.get("/api/sport-settlements/calibrations")
    assert res.status_code == 503


def test_get_calibrations_returns_empty(client):
    res = client.get("/api/sport-settlements/calibrations")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_get_calibrations_with_filter(client):
    _seed_full_scenario("m1")
    client.post("/api/sport-settlements/process/m1", headers={"X-Api-Key": "test-key"})
    res = client.get("/api/sport-settlements/calibrations?engine=BasketballEngine")
    assert res.status_code == 200


def test_get_history_returns_503_when_disabled(disabled_client):
    res = disabled_client.get("/api/sport-settlements/history")
    assert res.status_code == 503


def test_get_history_returns_history(client):
    _seed_full_scenario("m1")
    client.post("/api/sport-settlements/process/m1", headers={"X-Api-Key": "test-key"})
    res = client.get("/api/sport-settlements/history?limit=10")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["match_id"] == "m1"


def test_process_returns_503_when_disabled(disabled_client):
    res = disabled_client.post("/api/sport-settlements/process/m1")
    assert res.status_code == 503


def test_process_requires_write_key(client):
    """POST /process without write key → 401/403."""
    _seed_full_scenario("m1")
    res = client.post("/api/sport-settlements/process/m1")  # no X-Api-Key header
    assert res.status_code in (401, 403)


def test_process_with_write_key_succeeds(client):
    _seed_full_scenario("m1")
    res = client.post(
        "/api/sport-settlements/process/m1", headers={"X-Api-Key": "test-key"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["match_id"] == "m1"
    assert data["status"] == "processed"
