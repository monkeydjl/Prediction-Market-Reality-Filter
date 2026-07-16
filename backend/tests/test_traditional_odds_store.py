"""Tests for TraditionalOddsStore — CRUD for kernel_traditional_odds_snapshots."""
from datetime import datetime, timedelta, timezone

import pytest

from app.kernel.kernel_db import init_kernel_db, close_kernel_db
from app.kernel.traditional_odds_store import TraditionalOddsStore


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "traditional_odds_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def test_append_snapshot_returns_dict(kernel_db):
    """append_snapshot inserts a row and returns it as dict."""
    store = TraditionalOddsStore()
    now = _utcnow()
    result = store.append_snapshot(
        match_id="nba-2026-g1",
        mapped_outcome="home_win",
        competition="nba",
        implied_prob=0.65,
        decimal_odds=1.538,
        bookmaker="pinnacle",
        bookmakers_count=12,
        captured_at=now,
    )
    assert result["id"] is not None
    assert result["match_id"] == "nba-2026-g1"
    assert result["mapped_outcome"] == "home_win"
    assert result["competition"] == "nba"
    assert result["implied_prob"] == pytest.approx(0.65)
    assert result["decimal_odds"] == pytest.approx(1.538)
    assert result["bookmaker"] == "pinnacle"
    assert result["bookmakers_count"] == 12
    assert result["captured_at"] == now


def test_get_latest_snapshot_returns_most_recent(kernel_db):
    """get_latest_snapshot returns the most recent snapshot."""
    store = TraditionalOddsStore()
    t1 = _utcnow() - timedelta(minutes=10)
    t2 = _utcnow()
    store.append_snapshot(
        match_id="m1", mapped_outcome="home_win", competition="nba",
        implied_prob=0.60, decimal_odds=1.667, captured_at=t1,
    )
    store.append_snapshot(
        match_id="m1", mapped_outcome="home_win", competition="nba",
        implied_prob=0.65, decimal_odds=1.538, captured_at=t2,
    )
    latest = store.get_latest_snapshot(match_id="m1")
    assert latest is not None
    assert latest["implied_prob"] == pytest.approx(0.65)
    assert latest["captured_at"] == t2


def test_get_latest_snapshot_filtered_by_outcome(kernel_db):
    """get_latest_snapshot filters by mapped_outcome when provided."""
    store = TraditionalOddsStore()
    now = _utcnow()
    store.append_snapshot(
        match_id="m1", mapped_outcome="home_win", competition="epl",
        implied_prob=0.45, decimal_odds=2.222, captured_at=now,
    )
    store.append_snapshot(
        match_id="m1", mapped_outcome="away_win", competition="epl",
        implied_prob=0.30, decimal_odds=3.333, captured_at=now,
    )
    latest = store.get_latest_snapshot(match_id="m1", mapped_outcome="away_win")
    assert latest is not None
    assert latest["mapped_outcome"] == "away_win"
    assert latest["implied_prob"] == pytest.approx(0.30)


def test_get_latest_snapshot_returns_none_when_no_data(kernel_db):
    """get_latest_snapshot returns None for non-existent match."""
    store = TraditionalOddsStore()
    result = store.get_latest_snapshot(match_id="nonexistent")
    assert result is None


def test_get_snapshots_returns_all_oldest_first(kernel_db):
    """get_snapshots returns all snapshots ordered by captured_at ascending."""
    store = TraditionalOddsStore()
    t1 = _utcnow() - timedelta(minutes=20)
    t2 = _utcnow() - timedelta(minutes=10)
    t3 = _utcnow()
    for t, prob in [(t1, 0.55), (t2, 0.60), (t3, 0.65)]:
        store.append_snapshot(
            match_id="m1", mapped_outcome="home_win", competition="nba",
            implied_prob=prob, decimal_odds=1.0 / prob, captured_at=t,
        )
    snapshots = store.get_snapshots(match_id="m1")
    assert len(snapshots) == 3
    assert snapshots[0]["captured_at"] == t1
    assert snapshots[2]["captured_at"] == t3
    assert snapshots[0]["implied_prob"] == pytest.approx(0.55)
    assert snapshots[2]["implied_prob"] == pytest.approx(0.65)


def test_get_snapshots_filtered_by_outcome(kernel_db):
    """get_snapshots filters by mapped_outcome when provided."""
    store = TraditionalOddsStore()
    now = _utcnow()
    store.append_snapshot(
        match_id="m1", mapped_outcome="home_win", competition="epl",
        implied_prob=0.45, decimal_odds=2.222, captured_at=now,
    )
    store.append_snapshot(
        match_id="m1", mapped_outcome="draw", competition="epl",
        implied_prob=0.28, decimal_odds=3.571, captured_at=now,
    )
    store.append_snapshot(
        match_id="m1", mapped_outcome="away_win", competition="epl",
        implied_prob=0.27, decimal_odds=3.704, captured_at=now,
    )
    home_only = store.get_snapshots(match_id="m1", mapped_outcome="home_win")
    assert len(home_only) == 1
    assert home_only[0]["mapped_outcome"] == "home_win"


def test_get_snapshots_returns_empty_when_no_data(kernel_db):
    """get_snapshots returns empty list for non-existent match."""
    store = TraditionalOddsStore()
    result = store.get_snapshots(match_id="nonexistent")
    assert result == []


def test_append_snapshot_idempotent_via_unique_constraint(kernel_db):
    """Duplicate (match_id, mapped_outcome, captured_at) raises and does not insert."""
    store = TraditionalOddsStore()
    now = _utcnow()
    store.append_snapshot(
        match_id="m1", mapped_outcome="home_win", competition="nba",
        implied_prob=0.65, decimal_odds=1.538, captured_at=now,
    )
    # Second insert with same (match_id, mapped_outcome, captured_at) should raise
    with pytest.raises(Exception):
        store.append_snapshot(
            match_id="m1", mapped_outcome="home_win", competition="nba",
            implied_prob=0.70, decimal_odds=1.429, captured_at=now,
        )
    # Verify only 1 row exists
    snapshots = store.get_snapshots(match_id="m1")
    assert len(snapshots) == 1
    assert snapshots[0]["implied_prob"] == pytest.approx(0.65)
