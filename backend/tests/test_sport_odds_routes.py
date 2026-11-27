"""Tests for sport-odds API routes.

All endpoints gated by PHASE7_SPORT_MARKET_BRIDGE_ENABLED (503 when false).
Both are GET (read-only) — no require_write_key auth.
"""
import logging

import pytest
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config
from app.kernel.kernel_db import init_kernel_db, close_kernel_db
from app.kernel.traditional_odds_store import TraditionalOddsStore
from app.utils.failure_policy import READ_FAILED


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "sport_odds_routes_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


@pytest.fixture
def client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", True)
    from app.api.routes import sport_odds
    app = FastAPI()
    app.include_router(sport_odds.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def disabled_client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", False)
    from app.api.routes import sport_odds
    app = FastAPI()
    app.include_router(sport_odds.router, prefix="/api")
    return TestClient(app)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _seed_odds(match_id="m1", outcomes=None):
    """Helper: seed traditional odds snapshots for a match."""
    store = TraditionalOddsStore()
    now = _utcnow()
    if outcomes is None:
        outcomes = [
            ("home_win", 0.65, 1.538),
            ("away_win", 0.35, 2.857),
        ]
    for outcome, prob, decimal in outcomes:
        store.append_snapshot(
            match_id=match_id, mapped_outcome=outcome, competition="nba",
            implied_prob=prob, decimal_odds=decimal,
            bookmaker="pinnacle", bookmakers_count=12, captured_at=now,
        )


def test_latest_returns_503_when_disabled(disabled_client):
    res = disabled_client.get("/api/sport-odds/m1/latest")
    assert res.status_code == 503


def test_latest_returns_odds(client):
    _seed_odds(match_id="m1")
    res = client.get("/api/sport-odds/m1/latest")
    assert res.status_code == 200
    data = res.json()
    assert data["match_id"] == "m1"
    assert data["skipped"] is False
    assert len(data["outcomes"]) == 2
    home = next(o for o in data["outcomes"] if o["mapped_outcome"] == "home_win")
    assert home["implied_prob"] == pytest.approx(0.65)
    assert home["decimal_odds"] == pytest.approx(1.538)
    assert home["bookmaker"] == "pinnacle"
    assert home["bookmakers_count"] == 12


def test_latest_returns_empty_when_no_data(client):
    """Match with no odds → skipped=true, skip_reason='no_odds'."""
    res = client.get("/api/sport-odds/nonexistent/latest")
    assert res.status_code == 200
    data = res.json()
    assert data["skipped"] is True
    assert data["skip_reason"] == "no_odds"
    assert data["outcomes"] == []


def test_history_returns_timeseries(client):
    _seed_odds(match_id="m1")
    # Add a second snapshot 10 minutes later
    store = TraditionalOddsStore()
    store.append_snapshot(
        match_id="m1", mapped_outcome="home_win", competition="nba",
        implied_prob=0.70, decimal_odds=1.429,
        bookmaker="pinnacle", bookmakers_count=12,
        captured_at=_utcnow() + timedelta(minutes=10),
    )
    res = client.get("/api/sport-odds/m1/history")
    assert res.status_code == 200
    data = res.json()
    assert data["match_id"] == "m1"
    assert len(data["series"]) >= 1
    home_series = next(s for s in data["series"] if s["mapped_outcome"] == "home_win")
    assert len(home_series["snapshots"]) == 2


def test_history_filtered_by_outcome(client):
    _seed_odds(match_id="m1")
    res = client.get("/api/sport-odds/m1/history", params={"mapped_outcome": "home_win"})
    assert res.status_code == 200
    data = res.json()
    assert len(data["series"]) == 1
    assert data["series"][0]["mapped_outcome"] == "home_win"


def _row_stamp(match_id="m1", outcome="home_win"):
    """Read ``captured_at`` straight from the row, asserting it comes back naive.

    These routes were never broken (audit 53.178): ``snap["captured_at"]`` is
    *already* aware because ``TraditionalOddsStore`` re-attaches the offset in
    its converter (``app.utils.helpers.as_utc``, audit 53.183). The column
    carries none -- which is what the assertion below pins. If SQLite ever
    starts round-tripping an offset, this fails first, and the offset assertions
    in the route tests would then be measuring the writer instead of the route.
    """
    from app.kernel.kernel_db import KernelTraditionalOddsSnapshot, get_kernel_session

    session = get_kernel_session()
    try:
        row = (
            session.query(KernelTraditionalOddsSnapshot)
            .filter_by(match_id=match_id, mapped_outcome=outcome)
            .order_by(KernelTraditionalOddsSnapshot.captured_at.asc())
            .first()
        )
    finally:
        session.close()
    assert row is not None, "seed wrote no row to read back"
    assert row.captured_at is not None
    assert row.captured_at.tzinfo is None, (
        "premise broken: SQLite now round-trips the offset, so an offset in the "
        "response no longer proves anything about the route or the store"
    )
    return row.captured_at


def test_latest_captured_at_carries_the_utc_offset(client):
    from app.utils.helpers import utc_isoformat

    _seed_odds(match_id="m1")
    data = client.get("/api/sport-odds/m1/latest").json()
    home = next(o for o in data["outcomes"] if o["mapped_outcome"] == "home_win")

    assert home["captured_at"] == utc_isoformat(_row_stamp())
    assert home["captured_at"].endswith("+00:00"), home["captured_at"]


def test_history_captured_at_carries_the_utc_offset(client):
    from app.utils.helpers import utc_isoformat

    _seed_odds(match_id="m1")
    data = client.get("/api/sport-odds/m1/history").json()
    home = next(s for s in data["series"] if s["mapped_outcome"] == "home_win")

    assert home["snapshots"][0]["captured_at"] == utc_isoformat(_row_stamp())
    assert home["snapshots"][0]["captured_at"].endswith("+00:00")


def test_the_offset_comes_from_the_store_not_the_column(client):
    """The property that made these two routes correct all along.

    ``captured_at`` reaches the route aware because ``TraditionalOddsStore``
    re-attaches it -- unlike ``edge_store._row_to_dict`` before audit 53.183,
    which handed the column value through untouched and left the same-looking
    code in ``sport_edges`` emitting naive strings. Pinning the converter here
    localises a regression: drop the ``as_utc`` call and this fails, naming the
    layer that dropped it, instead of only the two endpoints going bare.
    """
    _seed_odds(match_id="m1")
    naive = _row_stamp()
    stored = TraditionalOddsStore().get_snapshots(match_id="m1")[0]["captured_at"]

    assert stored.tzinfo is not None, "the store no longer re-attaches the offset"
    assert stored == naive.replace(tzinfo=timezone.utc)


def _drop_odds_table():
    """Drop ``kernel_traditional_odds_snapshots`` through the live session.

    ``init_kernel_db`` creates the table at fixture setup, so dropping it makes
    every query in the store fail for real rather than through a mock. Same door
    as ``tests/test_sport_edge_routes.py::_drop_edges_table`` (audit 53.180).
    """
    from sqlalchemy import text
    from app.kernel.kernel_db import get_kernel_session

    session = get_kernel_session()
    try:
        session.execute(text("DROP TABLE kernel_traditional_odds_snapshots"))
        session.commit()
    finally:
        session.close()


def test_a_failed_read_is_not_reported_as_no_odds(client):
    """A broken table must not answer the body a quiet day answers.

    Before audit 53.180 both routes returned ``200 {"skipped": true,
    "skip_reason": "no_odds"}`` with the table missing -- byte-identical to a
    match nobody had odds for, and with no log line anywhere.
    """
    _drop_odds_table()
    for url, empty_key in (
        ("/api/sport-odds/m1/latest", "outcomes"),
        ("/api/sport-odds/m1/history", "series"),
    ):
        res = client.get(url)
        assert res.status_code == 200, url
        body = res.json()
        assert body["skip_reason"] == READ_FAILED, url
        assert body["skipped"] is True, url
        assert body[empty_key] == [], url


def test_a_healthy_empty_read_still_says_no_odds(client):
    """The other zero keeps its own word: the fix must not over-report.

    Without this, "always answer read_failed" would pass the test above.
    """
    for url in ("/api/sport-odds/m1/latest", "/api/sport-odds/m1/history"):
        body = client.get(url).json()
        assert body["skip_reason"] == "no_odds", url
        assert body["skipped"] is True, url


def test_the_store_propagates_a_read_failure(kernel_db):
    """Both store reads raise instead of inventing an empty value."""
    from sqlalchemy.exc import OperationalError

    _drop_odds_table()
    store = TraditionalOddsStore()
    with pytest.raises(OperationalError, match="no such table"):
        store.get_snapshots(match_id="m1")
    with pytest.raises(OperationalError, match="no such table"):
        store.get_latest_snapshot(match_id="m1")


def test_a_failed_read_leaves_a_trace(client, caplog):
    """The third zero is only useful if the failure is also recorded.

    The route is now the layer that names it, so the route is where the record
    has to exist -- a silent 200 with a reason word would still leave an
    operator nothing to grep for.
    """
    _drop_odds_table()
    with caplog.at_level(logging.WARNING, logger="app.api.routes.sport_odds"):
        client.get("/api/sport-odds/m1/latest")

    ours = [
        record.getMessage()
        for record in caplog.records
        if record.name == "app.api.routes.sport_odds" and record.levelno >= logging.WARNING
    ]
    assert ours, "the failed read produced no warning from the route"
    assert any("read failed" in message and "m1" in message for message in ours), ours
