"""Tests for sport-odds API routes.

All endpoints gated by PHASE7_SPORT_MARKET_BRIDGE_ENABLED (503 when false).
Both are GET (read-only) — no require_write_key auth.
"""
import pytest
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config
from app.kernel.kernel_db import init_kernel_db, close_kernel_db
from app.kernel.traditional_odds_store import TraditionalOddsStore


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
    *already* aware because ``TraditionalOddsStore`` re-attaches the offset
    (``_ensure_utc``, traditional_odds_store.py:24). The column carries none --
    which is what the assertion below pins. If SQLite ever starts round-tripping
    an offset, this fails first, and the offset assertions in the route tests
    would then be measuring the writer instead of the route.
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
    re-attaches it -- unlike ``edge_store._row_to_dict``, which hands the column
    value through untouched and left the same-looking code in ``sport_edges``
    emitting naive strings. Pinning the converter here localises a regression:
    delete ``_ensure_utc`` and this fails, naming the layer that dropped it,
    instead of only the two endpoints going bare.
    """
    _seed_odds(match_id="m1")
    naive = _row_stamp()
    stored = TraditionalOddsStore().get_snapshots(match_id="m1")[0]["captured_at"]

    assert stored.tzinfo is not None, "the store no longer re-attaches the offset"
    assert stored == naive.replace(tzinfo=timezone.utc)
