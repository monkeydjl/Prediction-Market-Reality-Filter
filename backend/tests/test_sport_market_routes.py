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
    # Disable write-key auth so POST /verify is reachable without an API key.
    # Patches security.settings directly because require_write_key binds
    # ``settings`` at import time (matches test_predictions_route.py pattern).
    from app.api.security import settings as security_settings
    monkeypatch.setattr(security_settings, "API_WRITE_KEY", "")
    monkeypatch.setattr(security_settings, "ALLOW_OPEN_WRITES", True)
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


def test_link_audit_attaches_link_meta(client):
    """The audit summary carries the link's own metadata, not just prices.

    The route looked the link up through a store method that did not exist, and
    the AttributeError was swallowed by a bare ``except Exception``, so every
    audit response silently came back without match_id / source / verified.
    """
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    link = _seed_link(match_id="m1", contract_id="c1", verified=True)
    MarketSnapshotStore().append_snapshot(link_id=link["id"], implied_prob=0.6, price=0.6)
    res = client.get(f"/api/sport-markets/links/{link['id']}/audit")
    assert res.status_code == 200
    data = res.json()
    assert data["match_id"] == "m1"
    assert data["source"] == "polymarket"
    assert data["verified"] is True


def test_link_audit_unknown_link_omits_meta(client):
    """A link id with no row leaves the meta keys off rather than 500-ing."""
    res = client.get("/api/sport-markets/links/9999/audit")
    assert res.status_code == 200
    assert "match_id" not in res.json()


# --- A degraded links table must not read as an unlinked one ---

def _drop_links_table():
    """Real DDL through the live ORM session, visible to later ORM reads."""
    from sqlalchemy import text
    from app.kernel.kernel_db import get_kernel_session
    session = get_kernel_session()
    try:
        session.execute(text("DROP TABLE kernel_sport_market_links"))
        session.commit()
    finally:
        session.close()


@pytest.fixture
def error_client(kernel_db, monkeypatch):
    """The same app, seen the way a caller over HTTP sees it.

    ``app/main.py`` registers no exception handler and
    ``raise_server_exceptions=False`` makes TestClient behave like a real
    server, so an escaping read error arrives as a 500.
    """
    monkeypatch.setattr(config.settings, "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", True)
    from app.api.security import settings as security_settings
    monkeypatch.setattr(security_settings, "API_WRITE_KEY", "")
    monkeypatch.setattr(security_settings, "ALLOW_OPEN_WRITES", True)
    from app.api.routes import sport_markets
    app = FastAPI()
    app.include_router(sport_markets.router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def test_a_degraded_links_table_surfaces_as_500(error_client, subtests):
    """Every read door, measured against a table that is gone.

    Pre-fix each one answered 200 with its own cold-start shape:
    ``{"items": [], "total": 0}`` for the operator's board *and* for the
    reviewer's pending queue, ``{"series": []}`` for the price history, and
    ``{"link_count": 0, "audits": []}`` for the match audit — none of them
    distinguishable from a match nobody had linked.
    """
    link = _seed_link(match_id="m1", contract_id="c1", verified=True)
    _drop_links_table()

    for url in (
        "/api/sport-markets/links",
        "/api/sport-markets/links?match_id=m1",
        "/api/sport-markets/links/m1",
        "/api/sport-markets/links/m1/latest",
        "/api/sport-markets/pending",
        "/api/sport-markets/snapshots/m1",
        f"/api/sport-markets/links/{link['id']}/audit",
        "/api/sport-markets/matches/m1/audit",
    ):
        with subtests.test(url=url):
            assert error_client.get(url).status_code == 500


def test_a_degraded_links_table_does_not_report_an_empty_review_queue(error_client):
    """``POST /pending/auto-verify`` is the reviewer's bulk action.

    Pre-fix it answered ``200 {"pending_total": 0, "candidates": 0}`` — an
    explicit statement that the queue held nothing to promote.
    """
    _seed_link(match_id="m1", contract_id="c1", verified=False)
    _drop_links_table()
    res = error_client.post("/api/sport-markets/pending/auto-verify?dry_run=true")
    assert res.status_code == 500


def test_verify_does_not_answer_404_for_a_link_that_exists(error_client):
    """The write door's lookup, under one renamed column.

    ``get_links`` builds a full row dict, so a renamed ``implied_prob`` breaks
    it while the row is still there. Pre-fix the swallowed ``[]`` made the
    ``next(...)`` miss and the route answered ``404 "Link not found"`` about a
    link the operator could see in the DB.
    """
    from sqlalchemy import text
    from app.kernel.kernel_db import get_kernel_session
    _seed_link(match_id="m1", contract_id="c1", verified=False)
    session = get_kernel_session()
    try:
        session.execute(text(
            "ALTER TABLE kernel_sport_market_links "
            "RENAME COLUMN implied_prob TO implied_prob_old"
        ))
        session.commit()
    finally:
        session.close()

    res = error_client.post(
        "/api/sport-markets/links/m1/c1/verify", json={"verified": True},
    )
    assert res.status_code == 500


def test_verify_still_404s_for_a_missing_contract_on_a_healthy_table(client):
    """404 keeps its meaning: the link genuinely is not there."""
    _seed_link(match_id="m1", contract_id="c1", verified=False)
    res = client.post(
        "/api/sport-markets/links/m1/nosuch/verify", json={"verified": True},
    )
    assert res.status_code == 404


def test_a_readable_empty_table_still_answers_200_and_empty(client, subtests):
    """Cold start keeps its answers, so "raises when broken" is not "raises"."""
    for url, expected in (
        ("/api/sport-markets/links", {"items": [], "total": 0}),
        ("/api/sport-markets/links/m1", {"match_id": "m1", "items": [], "total": 0}),
        ("/api/sport-markets/links/m1/latest",
         {"match_id": "m1", "items": [], "total": 0}),
        ("/api/sport-markets/pending", {"items": [], "total": 0}),
        ("/api/sport-markets/snapshots/m1", {"match_id": "m1", "series": []}),
        ("/api/sport-markets/matches/m1/audit",
         {"match_id": "m1", "link_count": 0, "audits": []}),
    ):
        with subtests.test(url=url):
            res = client.get(url)
            assert res.status_code == 200
            assert res.json() == expected
