"""Tests for sport market bridge API routes.

All endpoints gated by PHASE7_SPORT_MARKET_BRIDGE_ENABLED (503 when false).
/latest returns only verified links (fail-closed). /verify is the only write.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config
from app.kernel.kernel_db import init_kernel_db, close_kernel_db


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "kernel_routes_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


@pytest.fixture
def client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", True)
    from app.api.routes import sport_markets
    app = FastAPI()
    app.include_router(sport_markets.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def disabled_client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", False)
    from app.api.routes import sport_markets
    app = FastAPI()
    app.include_router(sport_markets.router, prefix="/api")
    return TestClient(app)


def _seed_link(match_id="m1", contract_id="c1", verified=True, source="polymarket"):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    return store.upsert_link(
        match_id=match_id, contract_id=contract_id, source=source,
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=verified, market_question="q", implied_prob=0.6,
    )


def test_links_returns_503_when_disabled(disabled_client):
    res = disabled_client.get("/api/sport-markets/links")
    assert res.status_code == 503


def test_list_links_with_match_id_filter(client):
    _seed_link(match_id="m1", contract_id="c1")
    _seed_link(match_id="m2", contract_id="c2")
    res = client.get("/api/sport-markets/links", params={"match_id": "m1"})
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["match_id"] == "m1"


def test_get_links_by_match(client):
    _seed_link(match_id="m1", contract_id="c1", verified=False)
    _seed_link(match_id="m1", contract_id="c2", verified=True)
    res = client.get("/api/sport-markets/links/m1")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2


def test_latest_returns_only_verified_with_snapshot(client):
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    link = _seed_link(match_id="m1", contract_id="c1", verified=True)
    _seed_link(match_id="m1", contract_id="c2", verified=False)
    snap = MarketSnapshotStore()
    snap.append_snapshot(link_id=link["id"], implied_prob=0.62, price=0.62)
    res = client.get("/api/sport-markets/links/m1/latest")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1  # fail-closed: only verified
    assert data["items"][0]["contract_id"] == "c1"
    assert data["items"][0]["latest_snapshot"] is not None
    assert data["items"][0]["latest_snapshot"]["implied_prob"] == pytest.approx(0.62)


def test_pending_returns_unverified(client):
    _seed_link(match_id="m1", contract_id="c1", verified=False)
    _seed_link(match_id="m2", contract_id="c2", verified=True)
    res = client.get("/api/sport-markets/pending")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["verified"] is False


def test_verify_link(client):
    _seed_link(match_id="m1", contract_id="c1", verified=False)
    res = client.post(
        "/api/sport-markets/links/m1/c1/verify",
        json={"verified": True, "note": "ok"},
    )
    assert res.status_code == 200
    assert res.json()["verified"] is True
    # Persisted: now appears in /latest
    res2 = client.get("/api/sport-markets/links/m1/latest")
    assert res2.json()["total"] == 1


def test_snapshots_timeseries(client):
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    link = _seed_link(match_id="m1", contract_id="c1", verified=True)
    snap = MarketSnapshotStore()
    snap.append_snapshot(link_id=link["id"], implied_prob=0.6, price=0.6)
    snap.append_snapshot(link_id=link["id"], implied_prob=0.65, price=0.65)
    res = client.get("/api/sport-markets/snapshots/m1")
    assert res.status_code == 200
    data = res.json()
    assert len(data["series"]) == 1
    assert len(data["series"][0]["snapshots"]) == 2
