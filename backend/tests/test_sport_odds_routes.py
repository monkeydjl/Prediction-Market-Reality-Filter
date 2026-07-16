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
