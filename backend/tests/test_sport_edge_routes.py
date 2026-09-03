"""Tests for sport edge API routes.

All endpoints gated by PHASE7_EDGE_DETECTOR_ENABLED (503 when false).
All are GET (read-only) — no require_write_key auth.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core import config
from app.kernel.kernel_db import init_kernel_db, close_kernel_db


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "edge_routes_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


@pytest.fixture
def client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_EDGE_DETECTOR_ENABLED", True)
    from app.api.routes import sport_edges
    app = FastAPI()
    app.include_router(sport_edges.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def disabled_client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_EDGE_DETECTOR_ENABLED", False)
    from app.api.routes import sport_edges
    app = FastAPI()
    app.include_router(sport_edges.router, prefix="/api")
    return TestClient(app)


def _seed_prediction_and_link(match_id="m1", probs=None, implied=0.55):
    """Helper: seed prediction + verified link + snapshot + calibration."""
    from datetime import datetime, timezone
    from app.kernel.kernel_db import KernelPrediction, KernelCalibration, get_kernel_session
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    if probs is None:
        probs = {"home_win": 0.65, "away_win": 0.35}
    now = datetime.now(timezone.utc)
    session = get_kernel_session()
    try:
        session.add(KernelPrediction(
            match_id=match_id, sport="basketball", competition="nba",
            season="2025-26", engine="BasketballEngine", predicted_scores={},
            outcome_probabilities=probs, confidence=0.7, feature_version="nba-1.0",
            explanation={}, created_at=now, updated_at=now,
        ))
        # Calibration row is keyed by (engine, competition) — only insert if
        # not already present (helper may be called multiple times per test).
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
    store = SportMarketLinkStore()
    link = store.upsert_link(
        match_id=match_id, contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=True, market_question="q", implied_prob=implied,
    )
    snap_store = MarketSnapshotStore()
    snap_store.append_snapshot(
        link_id=link["id"], implied_prob=implied, price=implied,
        liquidity=None, volume=None, captured_at=now,
    )


def test_latest_returns_503_when_disabled(disabled_client):
    res = disabled_client.get("/api/sport-edges/m1/latest")
    assert res.status_code == 503


def test_latest_returns_edges(client):
    _seed_prediction_and_link(match_id="m1", implied=0.58)
    # Trigger edge computation
    from app.kernel.edge_detector_service import EdgeDetectorService
    EdgeDetectorService().detect_edges("m1")
    res = client.get("/api/sport-edges/m1/latest")
    assert res.status_code == 200
    data = res.json()
    assert data["match_id"] == "m1"
    assert data["skipped"] is False
    assert len(data["outcomes"]) == 1
    edge = data["outcomes"][0]
    assert edge["mapped_outcome"] == "home_win"
    assert edge["model_prob"] == pytest.approx(0.65)
    assert edge["market_prob"] == pytest.approx(0.58)
    assert edge["raw_edge"] == pytest.approx(0.07)


def test_latest_returns_skipped_summary(client):
    """Match with no prediction -> skipped=true."""
    res = client.get("/api/sport-edges/m1/latest")
    assert res.status_code == 200
    data = res.json()
    assert data["skipped"] is True
    assert data["skip_reason"] == "no_prediction"
    assert data["outcomes"] == []


def test_history_returns_timeseries(client):
    _seed_prediction_and_link(match_id="m1", implied=0.58)
    from app.kernel.edge_detector_service import EdgeDetectorService
    svc = EdgeDetectorService()
    # Compute twice to create 2 snapshots
    svc.detect_edges("m1")
    svc.detect_edges("m1")
    res = client.get("/api/sport-edges/m1/history")
    assert res.status_code == 200
    data = res.json()
    assert data["match_id"] == "m1"
    assert len(data["series"]) >= 1
    home_series = next(s for s in data["series"] if s["mapped_outcome"] == "home_win")
    assert len(home_series["snapshots"]) == 2


def test_history_filtered_by_outcome(client):
    _seed_prediction_and_link(match_id="m1", implied=0.58)
    from app.kernel.edge_detector_service import EdgeDetectorService
    EdgeDetectorService().detect_edges("m1")
    res = client.get("/api/sport-edges/m1/history", params={"mapped_outcome": "home_win"})
    assert res.status_code == 200
    data = res.json()
    assert len(data["series"]) == 1
    assert data["series"][0]["mapped_outcome"] == "home_win"


def test_discrepancies_returns_top_edges(client):
    _seed_prediction_and_link(match_id="m1", implied=0.50)  # large edge
    from app.kernel.edge_detector_service import EdgeDetectorService
    EdgeDetectorService().detect_edges("m1")
    res = client.get("/api/sport-edges/discrepancies")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 1
    assert len(data["items"]) >= 1
    assert data["items"][0]["match_id"] == "m1"


def test_discrepancies_respects_limit(client):
    _seed_prediction_and_link(match_id="m1", implied=0.50)
    _seed_prediction_and_link(match_id="m2", implied=0.45)
    from app.kernel.edge_detector_service import EdgeDetectorService
    EdgeDetectorService().detect_edges("m1")
    EdgeDetectorService().detect_edges("m2")
    res = client.get("/api/sport-edges/discrepancies", params={"limit": 1})
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 1


def test_discrepancies_respects_min_abs_edge(client):
    _seed_prediction_and_link(match_id="m1", implied=0.64)  # small edge (0.01)
    from app.kernel.edge_detector_service import EdgeDetectorService
    EdgeDetectorService().detect_edges("m1")
    res = client.get("/api/sport-edges/discrepancies", params={"min_abs_edge": 0.05})
    assert res.status_code == 200
    data = res.json()
    # edge is 0.01 * 0.72 * 1.0 = 0.0072, below 0.05 threshold
    assert data["total"] == 0
    assert data["items"] == []


def test_detect_endpoint_computes_and_returns(client, monkeypatch):
    """POST /detect runs EdgeDetectorService and returns summary."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "API_WRITE_KEY", "")
    monkeypatch.setattr(settings, "ALLOW_OPEN_WRITES", True)

    _seed_prediction_and_link(match_id="m1", implied=0.58)
    res = client.post("/api/sport-edges/m1/detect")
    assert res.status_code == 200
    data = res.json()
    assert data["match_id"] == "m1"
    assert data["skipped"] is False
    assert len(data["outcomes"]) >= 1


def _drop_edges_table():
    """Drop kernel_sport_edges through the live ORM session.

    ``_migrate_dormant_tables`` issues DROP TABLE at init, so a missing kernel
    table is a state this repo already produces — the failure is a real query
    error, not a mocked session.
    """
    from sqlalchemy import text
    from app.kernel.kernel_db import get_kernel_session
    session = get_kernel_session()
    try:
        session.execute(text("DROP TABLE kernel_sport_edges"))
        session.commit()
    finally:
        session.close()


def test_a_degraded_read_is_not_reported_as_no_edges(client):
    """A query failure must not surface as skipped=true or a 0-row list.

    All three GETs have an empty result as their normal answer — a match nobody
    has detected edges for — so the swallowed version answered a question the
    query never resolved. ``/latest`` is the sharpest: it named
    ``no_verified_links`` as the reason while the link and its snapshot were
    both intact.
    """
    _seed_prediction_and_link(match_id="m1", implied=0.50)
    from app.kernel.edge_detector_service import EdgeDetectorService
    EdgeDetectorService().detect_edges("m1")
    assert client.get("/api/sport-edges/m1/latest").json()["skipped"] is False

    _drop_edges_table()

    for url in (
        "/api/sport-edges/m1/latest",
        "/api/sport-edges/m1/history",
        "/api/sport-edges/discrepancies",
    ):
        with pytest.raises(OperationalError, match="no such table"):
            client.get(url)


def test_a_degraded_read_surfaces_as_500(kernel_db, monkeypatch):
    """The same three endpoints, seen the way a caller over HTTP sees them.

    ``app/main.py`` registers no exception handler and
    ``raise_server_exceptions=False`` makes TestClient behave like a real
    server, so the escaping read error is a 500. Previously the responses were
    ``200 {"skipped": true, "skip_reason": "no_verified_links"}``,
    ``200 {"series": []}`` and ``200 {"items": [], "total": 0}`` — every one of
    them a 200 stating that this match has nothing worth looking at.
    """
    monkeypatch.setattr(config.settings, "PHASE7_EDGE_DETECTOR_ENABLED", True)
    from app.api.routes import sport_edges
    app = FastAPI()
    app.include_router(sport_edges.router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=False)

    _drop_edges_table()

    for url in (
        "/api/sport-edges/m1/latest",
        "/api/sport-edges/m1/history",
        "/api/sport-edges/discrepancies",
    ):
        res = client.get(url)
        assert res.status_code == 500, url
