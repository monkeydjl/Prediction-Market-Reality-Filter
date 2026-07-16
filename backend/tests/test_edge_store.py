"""Tests for EdgeStore persistence (kernel_sport_edges table)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.kernel.kernel_db import init_kernel_db, close_kernel_db
from app.kernel.edge_store import EdgeStore


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "edge_store_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def _utcnow():
    return datetime.now(timezone.utc)


def test_append_edge_and_get_latest(kernel_db):
    """Append 2 edges for same outcome at different times -> get_latest returns only newest."""
    store = EdgeStore()
    old_ts = _utcnow() - timedelta(hours=2)
    new_ts = _utcnow()
    store.append_edge(
        match_id="m1", mapped_outcome="home_win", model_prob=0.6, market_prob=0.55,
        raw_edge=0.05, trust=0.7, liquidity_factor=0.8, adjusted_edge=0.028,
        spread=None, sources_count=1, stale=False, captured_at=old_ts,
    )
    store.append_edge(
        match_id="m1", mapped_outcome="home_win", model_prob=0.65, market_prob=0.58,
        raw_edge=0.07, trust=0.72, liquidity_factor=0.85, adjusted_edge=0.0428,
        spread=None, sources_count=2, stale=False, captured_at=new_ts,
    )
    latest = store.get_latest_edges(match_id="m1")
    assert len(latest) == 1
    assert latest[0]["model_prob"] == pytest.approx(0.65)
    assert latest[0]["raw_edge"] == pytest.approx(0.07)


def test_get_latest_edges_multiple_outcomes(kernel_db):
    """3 outcomes -> returns 3 latest edges."""
    store = EdgeStore()
    ts = _utcnow()
    for outcome in ("home_win", "draw", "away_win"):
        store.append_edge(
            match_id="m1", mapped_outcome=outcome, model_prob=0.4, market_prob=0.35,
            raw_edge=0.05, trust=0.7, liquidity_factor=0.8, adjusted_edge=0.028,
            spread=None, sources_count=1, stale=False, captured_at=ts,
        )
    latest = store.get_latest_edges(match_id="m1")
    assert len(latest) == 3
    outcomes = {e["mapped_outcome"] for e in latest}
    assert outcomes == {"home_win", "draw", "away_win"}


def test_get_edge_history_filtered_by_outcome(kernel_db):
    """History with mapped_outcome filter returns only that outcome's series."""
    store = EdgeStore()
    ts = _utcnow()
    store.append_edge(
        match_id="m1", mapped_outcome="home_win", model_prob=0.6, market_prob=0.55,
        raw_edge=0.05, trust=0.7, liquidity_factor=0.8, adjusted_edge=0.028,
        spread=None, sources_count=1, stale=False, captured_at=ts,
    )
    store.append_edge(
        match_id="m1", mapped_outcome="away_win", model_prob=0.4, market_prob=0.45,
        raw_edge=-0.05, trust=0.7, liquidity_factor=0.8, adjusted_edge=-0.028,
        spread=None, sources_count=1, stale=False, captured_at=ts,
    )
    history = store.get_edge_history(match_id="m1", mapped_outcome="home_win")
    assert len(history) == 1
    assert history[0]["mapped_outcome"] == "home_win"
    # Unfiltered history returns all
    all_history = store.get_edge_history(match_id="m1")
    assert len(all_history) == 2


def test_get_top_discrepancies_min_abs_edge_filter(kernel_db):
    """min_abs_edge filters out small edges; orders by |adjusted_edge| DESC."""
    store = EdgeStore()
    ts = _utcnow()
    # small edge
    store.append_edge(
        match_id="m1", mapped_outcome="home_win", model_prob=0.55, market_prob=0.54,
        raw_edge=0.01, trust=0.7, liquidity_factor=0.8, adjusted_edge=0.0056,
        spread=None, sources_count=1, stale=False, captured_at=ts,
    )
    # large edge
    store.append_edge(
        match_id="m2", mapped_outcome="home_win", model_prob=0.70, market_prob=0.50,
        raw_edge=0.20, trust=0.8, liquidity_factor=0.9, adjusted_edge=0.144,
        spread=None, sources_count=1, stale=False, captured_at=ts,
    )
    # min_abs_edge=0.05 filters out m1
    top = store.get_top_discrepancies(limit=20, min_abs_edge=0.05)
    assert len(top) == 1
    assert top[0]["match_id"] == "m2"
    assert top[0]["adjusted_edge"] == pytest.approx(0.144)
