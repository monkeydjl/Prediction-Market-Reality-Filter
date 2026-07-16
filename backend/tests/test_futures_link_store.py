# backend/tests/test_futures_link_store.py
"""Tests for FuturesLinkStore — TDD RED phase."""
from datetime import datetime, timezone

import pytest

from app.kernel.futures_link_store import FuturesLinkStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Create a store with an isolated SQLite DB."""
    db_path = str(tmp_path / "test_futures.db")
    monkeypatch.setenv("KERNEL_DB_PATH", db_path)
    from app.kernel import kernel_db
    kernel_db.KernelBase.metadata.create_all(kernel_db._get_engine(db_path))

    # Patch get_kernel_session to use this isolated engine
    engine = kernel_db._get_engine(db_path)
    from sqlalchemy.orm import sessionmaker
    SessionFactory = sessionmaker(bind=engine)

    monkeypatch.setattr(
        "app.kernel.futures_link_store.get_kernel_session",
        lambda: SessionFactory(),
    )
    return FuturesLinkStore()


def test_upsert_link_inserts_and_returns_record(store):
    result = store.upsert_link(
        competition="nba", season="2024-25",
        team="LAL", contract_id="KXNBACHAMP-LAL",
        source="kalshi", market_question="Lakers win NBA Championship",
        implied_prob=0.18, verified=True,
    )
    assert result["id"] is not None
    assert result["competition"] == "nba"
    assert result["team"] == "LAL"
    assert result["verified"] is True


def test_upsert_link_updates_existing(store):
    store.upsert_link(
        competition="nba", season="2024-25",
        team="LAL", contract_id="KXNBACHAMP-LAL",
        source="kalshi", market_question="old question",
        implied_prob=0.18, verified=False,
    )
    updated = store.upsert_link(
        competition="nba", season="2024-25",
        team="LAL", contract_id="KXNBACHAMP-LAL",
        source="kalshi", market_question="new question",
        implied_prob=0.22, verified=True,
    )
    # Same row, updated values
    links = store.get_links("nba", "2024-25")
    assert len(links) == 1
    assert links[0]["implied_prob"] == 0.22
    assert links[0]["market_question"] == "new question"
    assert links[0]["verified"] is True


def test_get_links_filters_by_competition_and_season(store):
    store.upsert_link(
        competition="nba", season="2024-25",
        team="LAL", contract_id="KXNBACHAMP-LAL",
        source="kalshi", market_question="",
        implied_prob=0.18, verified=True,
    )
    store.upsert_link(
        competition="mlb", season="2024",
        team="NYY", contract_id="KXMLBCHAMP-NYY",
        source="kalshi", market_question="",
        implied_prob=0.10, verified=True,
    )
    nba_links = store.get_links("nba", "2024-25")
    assert len(nba_links) == 1
    assert nba_links[0]["team"] == "LAL"
    mlb_links = store.get_links("mlb", "2024")
    assert len(mlb_links) == 1
    assert mlb_links[0]["team"] == "NYY"


def test_get_verified_links_returns_only_verified(store):
    store.upsert_link(
        competition="nba", season="2024-25",
        team="LAL", contract_id="KXNBACHAMP-LAL",
        source="kalshi", market_question="",
        implied_prob=0.18, verified=True,
    )
    store.upsert_link(
        competition="nba", season="2024-25",
        team="BOS", contract_id="KXNBACHAMP-BOS",
        source="kalshi", market_question="",
        implied_prob=0.32, verified=False,
    )
    verified = store.get_verified_links()
    assert len(verified) == 1
    assert verified[0]["team"] == "LAL"


def test_append_snapshot_and_get_latest(store):
    link = store.upsert_link(
        competition="nba", season="2024-25",
        team="LAL", contract_id="KXNBACHAMP-LAL",
        source="kalshi", market_question="",
        implied_prob=0.18, verified=True,
    )
    t1 = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 7, 16, 11, 0, tzinfo=timezone.utc)
    store.append_snapshot(
        link_id=link["id"], implied_prob=0.18, price=0.18,
        liquidity=50000.0, volume=12000.0, captured_at=t1,
    )
    store.append_snapshot(
        link_id=link["id"], implied_prob=0.22, price=0.22,
        liquidity=55000.0, volume=13000.0, captured_at=t2,
    )
    latest = store.get_latest_snapshots("nba", "2024-25")
    assert len(latest) == 1
    # Latest snapshot is the most recent one (t2)
    assert latest[0]["implied_prob"] == 0.22
    assert latest[0]["team"] == "LAL"
