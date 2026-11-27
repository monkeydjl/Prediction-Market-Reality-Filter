"""Tests for sport market bridge API routes.

All endpoints gated by PHASE7_SPORT_MARKET_BRIDGE_ENABLED (503 when false).
/latest returns only verified links (fail-closed). /verify is the only write.
"""
import re
from datetime import datetime

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
    # Nothing was dropped, and the door says so rather than staying silent.
    assert data["series"][0]["untimestamped_count"] == 0


def test_snapshots_timeseries_drops_and_counts_untimestamped_rows(client):
    """A row with no timestamp is not a point on the price path.

    ``kernel_market_snapshots.captured_at`` is nullable, so building such a row
    straight from the model is legal -- and SQLite sorts it *first*, which would
    draw an undatable price at the earliest x (audit section 53.48). The series
    door must leave it out and report how many it left out; what is asserted is
    the exact point list and the count, not that the request merely succeeded.
    """
    from app.kernel.kernel_db import KernelMarketSnapshot, get_kernel_session
    from app.kernel.market_snapshot_store import MarketSnapshotStore

    link = _seed_link(match_id="m1", contract_id="c1", verified=True)
    snap = MarketSnapshotStore()
    snap.append_snapshot(link_id=link["id"], implied_prob=0.6, price=0.6)
    session = get_kernel_session()
    try:
        session.add(
            KernelMarketSnapshot(link_id=link["id"], implied_prob=0.9, price=0.9)
        )
        session.commit()
    finally:
        session.close()

    res = client.get("/api/sport-markets/snapshots/m1")
    assert res.status_code == 200
    entry = res.json()["series"][0]
    assert len(entry["snapshots"]) == 1
    assert entry["snapshots"][0]["implied_prob"] == 0.6
    assert entry["snapshots"][0]["captured_at"] is not None
    assert entry["untimestamped_count"] == 1


def test_snapshots_route_publishes_its_response_model():
    """The named model *is* the contract, so pin the wiring, not just the payload.

    Removing ``response_model=MarketSnapshotsResponse`` leaves every behavioural
    assertion in this file green -- the dict the route returns is unchanged --
    while the OpenAPI schema silently loses the field contract and the generated
    TypeScript stops describing the payload at all. The sibling check in
    ``test_operational_readiness`` only walks the *events* router, so this route
    had no guard (audit section 53.49).
    """
    from fastapi.routing import APIRoute
    from app.api.routes import sport_markets
    from app.models.event import MarketSnapshotsResponse

    # `APIRoute.path` already carries the router prefix, so it reads
    # "/sport-markets/snapshots/{match_id}" -- not the bare path on the decorator.
    routes = [
        route
        for route in sport_markets.router.routes
        if isinstance(route, APIRoute)
        and route.path == "/sport-markets/snapshots/{match_id}"
    ]
    assert len(routes) == 1, "expected exactly one /snapshots/{match_id} route"
    assert routes[0].response_model is MarketSnapshotsResponse


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


# --- The snapshot store's own read, measured separately (audit 53.180) ---

def _drop_snapshots_table():
    """Real DDL via the live ORM session, same door as ``_drop_links_table``."""
    from sqlalchemy import text
    from app.kernel.kernel_db import get_kernel_session
    session = get_kernel_session()
    try:
        session.execute(text("DROP TABLE kernel_market_snapshots"))
        session.commit()
    finally:
        session.close()


def test_a_degraded_snapshots_table_surfaces_as_500(error_client, subtests):
    """The two bodies with no switch vocabulary still refuse to answer.

    ``test_a_degraded_links_table_surfaces_as_500`` breaks the *links* read, so
    on every one of its eight urls ``MarketSnapshotStore`` is never reached.
    With only the links table broken, both urls below answered 200:
    ``latest_snapshot: null`` per item and an empty ``series`` -- the same
    sentence a match nobody had priced returns, on the strength of a query that
    never reached the table. Neither body carries a word for "the read failed",
    and inventing one on the closed ``MarketSnapshotSeries`` element type is the
    expensive shape, so the error travels instead. This is also the convention
    the rest of this module already follows.
    """
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    link = _seed_link(match_id="m1", contract_id="c1", verified=True)
    MarketSnapshotStore().append_snapshot(link_id=link["id"], implied_prob=0.6, price=0.6)
    _drop_snapshots_table()

    for url in (
        "/api/sport-markets/links/m1/latest",
        "/api/sport-markets/snapshots/m1",
    ):
        with subtests.test(url=url):
            assert error_client.get(url).status_code == 500


def test_the_audit_doors_name_a_failed_snapshot_read(client, subtests):
    """The one body in this family that *can* name it, does.

    ``audit_summary`` already speaks in switches (``available`` + ``flags``), so
    the third zero costs no response model and no generated type. Both audit
    routes return that dict unmodelled, so the key reaches the caller.
    """
    link = _seed_link(match_id="m1", contract_id="c1", verified=True)
    _drop_snapshots_table()

    for url in (
        f"/api/sport-markets/links/{link['id']}/audit",
        "/api/sport-markets/matches/m1/audit",
    ):
        with subtests.test(url=url):
            res = client.get(url)
            assert res.status_code == 200
            body = res.json()
            entry = body["audits"][0] if "audits" in body else body
            assert entry["read_failed"] is True
            assert entry["available"] is False
            assert entry["flags"] == ["read_failed"]


def test_a_readable_but_empty_snapshots_table_keeps_its_own_word(client, subtests):
    """The fix must not over-report: cold start is not a read failure.

    Without this, "always answer read_failed" would pass the test above.
    """
    link = _seed_link(match_id="m1", contract_id="c1", verified=True)

    for url in (
        f"/api/sport-markets/links/{link['id']}/audit",
        "/api/sport-markets/matches/m1/audit",
    ):
        with subtests.test(url=url):
            body = client.get(url).json()
            entry = body["audits"][0] if "audits" in body else body
            assert entry["read_failed"] is False
            assert entry["available"] is False
            assert entry["flags"] == ["no_snapshots"]


# --- every timestamp on the wire carries an offset (audit 53.183) ---
#
# Deliberately not key by key. These four bodies publish timestamps under six
# different names (`captured_at`, `created_at`, `updated_at`,
# `latest_snapshot.captured_at`, `first_captured_at`, `last_captured_at`), and a
# key added later would be silently unguarded. So the rule is applied to *any*
# ISO-8601-shaped string in the body, whatever it happens to be called.
#
# The stamp is parsed, not suffix-matched. FastAPI renders an aware `datetime`
# object as `...Z`, but passes a pre-formatted `"+00:00"` string straight
# through -- both spellings occur in this API, and `sport_odds` is the second
# kind. Asserting `endswith("+00:00")` would pin the encoder's spelling rather
# than the property under test.

_ISO_SHAPED = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


def _stamp_strings(payload):
    """Every ISO-8601-shaped string anywhere in a decoded JSON body."""
    if isinstance(payload, str):
        return [payload] if _ISO_SHAPED.match(payload) else []
    if isinstance(payload, dict):
        return [s for value in payload.values() for s in _stamp_strings(value)]
    if isinstance(payload, list):
        return [s for value in payload for s in _stamp_strings(value)]
    return []


def _assert_every_stamp_is_zoned(payload, label):
    """Fail if any timestamp in the body parses to a naive datetime."""
    stamps = _stamp_strings(payload)
    assert stamps, f"{label}: no timestamp in the body, so this proves nothing"
    for stamp in stamps:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None, f"{label}: {stamp} carries no offset"


def test_snapshots_body_timestamps_carry_an_offset(client):
    """The response_model route: pydantic adds no offset the value does not have."""
    from app.kernel.market_snapshot_store import MarketSnapshotStore

    link = _seed_link(match_id="m1", contract_id="c1", verified=True)
    MarketSnapshotStore().append_snapshot(
        link_id=link["id"], implied_prob=0.6, price=0.6,
    )
    body = client.get("/api/sport-markets/snapshots/m1").json()
    _assert_every_stamp_is_zoned(body["series"], "snapshots")


def test_links_body_timestamps_carry_an_offset(client):
    _seed_link(match_id="m1", contract_id="c1", verified=True)
    body = client.get("/api/sport-markets/links/m1").json()
    _assert_every_stamp_is_zoned(body["items"], "links")


def test_latest_body_timestamps_carry_an_offset(client):
    """One body, two converters: ``link.created_at`` beside ``snapshot.captured_at``.

    Fixing only the snapshot converter would leave this body half-marked, which
    is harder to reason about than either state on its own.
    """
    from app.kernel.market_snapshot_store import MarketSnapshotStore

    link = _seed_link(match_id="m1", contract_id="c1", verified=True)
    MarketSnapshotStore().append_snapshot(
        link_id=link["id"], implied_prob=0.6, price=0.6,
    )
    body = client.get("/api/sport-markets/links/m1/latest").json()
    _assert_every_stamp_is_zoned(body["items"], "latest")


def test_audit_bodies_timestamps_carry_an_offset(client):
    """Both audit doors republish the row stamp as first/last_captured_at."""
    from app.kernel.market_snapshot_store import MarketSnapshotStore

    link = _seed_link(match_id="m1", contract_id="c1", verified=True)
    store = MarketSnapshotStore()
    store.append_snapshot(link_id=link["id"], implied_prob=0.6, price=0.6)
    store.append_snapshot(link_id=link["id"], implied_prob=0.7, price=0.7)

    for url in (
        f"/api/sport-markets/links/{link['id']}/audit",
        "/api/sport-markets/matches/m1/audit",
    ):
        body = client.get(url).json()
        _assert_every_stamp_is_zoned(body, url)
