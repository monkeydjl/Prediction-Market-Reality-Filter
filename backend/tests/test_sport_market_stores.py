"""Tests for sport market link + snapshot stores (real SQLite via tmp_path)."""
from datetime import datetime, timezone

import pytest

from app.kernel.kernel_db import (
    KernelSportMarketLink,
    KernelMarketSnapshot,
    init_kernel_db,
    close_kernel_db,
    get_kernel_session,
)


@pytest.fixture
def kernel_db(tmp_path, monkeypatch):
    """Initialize a fresh kernel DB in tmp_path for each test."""
    db_path = tmp_path / "kernel_test.db"
    # Reset module-level engine so init_kernel_db creates a new one.
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def test_table_classes_exist(kernel_db):
    # Tables are created by init_kernel_db -> create_all
    session = get_kernel_session()
    try:
        # Smoke: insert a link row
        link = KernelSportMarketLink(
            match_id="nba-20250101-LAL-BOS",
            contract_id="poly-123",
            source="polymarket",
            outcome_label="YES",
            mapped_outcome="home_win",
            link_method="rule",
            link_confidence=0.95,
            verified=True,
            market_question="Will Lakers beat Celtics?",
            implied_prob=0.6,
        )
        session.add(link)
        session.commit()
        assert link.id is not None
    finally:
        session.close()


def test_upsert_link_inserts(kernel_db):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    result = store.upsert_link(
        match_id="nba-20250101-LAL-BOS",
        contract_id="poly-123",
        source="polymarket",
        outcome_label="YES",
        mapped_outcome="home_win",
        link_method="rule",
        link_confidence=0.95,
        verified=True,
        market_question="Will Lakers beat Celtics?",
        implied_prob=0.6,
    )
    assert result["match_id"] == "nba-20250101-LAL-BOS"
    assert result["verified"] is True
    assert result["id"] is not None


def test_upsert_link_updates_existing(kernel_db):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.5, verified=False,
        market_question="q", implied_prob=0.5,
    )
    # Upsert same key with new confidence/verified
    updated = store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.95, verified=True,
        market_question="q", implied_prob=0.6,
    )
    links = store.get_links(match_id="m1")
    assert len(links) == 1  # no duplicate
    assert links[0]["verified"] is True
    assert links[0]["link_confidence"] == 0.95


def test_get_verified_links_fail_closed(kernel_db):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="llm", link_confidence=0.7, verified=False,
        market_question="q", implied_prob=0.5,
    )
    store.upsert_link(
        match_id="m1", contract_id="c2", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.95, verified=True,
        market_question="q2", implied_prob=0.6,
    )
    verified = store.get_verified_links(match_id="m1")
    assert len(verified) == 1
    assert verified[0]["contract_id"] == "c2"
    # Unverified link must NOT leak
    assert all(l["verified"] is True for l in verified)


def test_get_pending_links(kernel_db):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="llm", link_confidence=0.7, verified=False,
        market_question="q", implied_prob=0.5,
    )
    store.upsert_link(
        match_id="m2", contract_id="c2", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.95, verified=True,
        market_question="q2", implied_prob=0.6,
    )
    pending = store.get_pending_links()
    assert len(pending) == 1
    assert pending[0]["verified"] is False


def test_get_all_verified_links(kernel_db):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.95, verified=True,
        market_question="q", implied_prob=0.6,
    )
    store.upsert_link(
        match_id="m2", contract_id="c2", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.95, verified=True,
        market_question="q2", implied_prob=0.55,
    )
    all_verified = store.get_all_verified_links()
    assert len(all_verified) == 2


def test_set_verified(kernel_db):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    link = store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="llm", link_confidence=0.7, verified=False,
        market_question="q", implied_prob=0.5,
    )
    ok = store.set_verified(link_id=link["id"], verified=True)
    assert ok is True
    verified = store.get_verified_links(match_id="m1")
    assert len(verified) == 1


def test_set_verified_missing_returns_false(kernel_db):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    ok = store.set_verified(link_id=99999, verified=True)
    assert ok is False


def test_append_and_get_snapshots(kernel_db):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    link_store = SportMarketLinkStore()
    snap_store = MarketSnapshotStore()
    link = link_store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.95, verified=True,
        market_question="q", implied_prob=0.6,
    )
    s1 = snap_store.append_snapshot(link_id=link["id"], implied_prob=0.6, price=0.6)
    s2 = snap_store.append_snapshot(link_id=link["id"], implied_prob=0.65, price=0.65)
    assert s1["id"] is not None
    assert s2["id"] is not None
    snaps = snap_store.get_snapshots(link_id=link["id"])
    assert len(snaps) == 2


def test_get_latest_snapshot(kernel_db):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    link_store = SportMarketLinkStore()
    snap_store = MarketSnapshotStore()
    link = link_store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.95, verified=True,
        market_question="q", implied_prob=0.6,
    )
    snap_store.append_snapshot(link_id=link["id"], implied_prob=0.6, price=0.6)
    snap_store.append_snapshot(link_id=link["id"], implied_prob=0.65, price=0.65)
    latest = snap_store.get_latest_snapshot(link_id=link["id"])
    assert latest is not None
    assert latest["implied_prob"] == 0.65


def test_get_latest_snapshot_tie_returns_the_later_row(kernel_db):
    """A tie in ``captured_at`` must still answer "the one written last".

    Two captures can carry the same instant -- an app path that computes
    ``now`` once outside a loop does it by construction (see
    ``futures_market_service.capture_snapshots``), and so does a caller that
    passes ``captured_at`` explicitly. Without the ``id`` tiebreaker the
    relative order inside the tie is undefined, and the measured behaviour was
    to return the *first*-inserted row -- i.e. to report the *older* capture as
    the newest. That is the shape of the one-off full-suite red recorded in
    docs/reviews/system-health-audit-2026-09-26.md sections 53.39 / 53.43.
    """
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    link_store = SportMarketLinkStore()
    snap_store = MarketSnapshotStore()
    link = link_store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.95, verified=True,
        market_question="q", implied_prob=0.6,
    )
    same = datetime(2026, 10, 7, 12, 0, 0, 654321, tzinfo=timezone.utc)
    first = snap_store.append_snapshot(
        link_id=link["id"], implied_prob=0.6, price=0.6, captured_at=same,
    )
    second = snap_store.append_snapshot(
        link_id=link["id"], implied_prob=0.65, price=0.65, captured_at=same,
    )
    # The test is *about* the tie, so assert the tie really is one, and that
    # the two rows are distinguishable only by insertion order.
    assert first["captured_at"] == second["captured_at"]
    assert second["id"] > first["id"]

    latest = snap_store.get_latest_snapshot(link_id=link["id"])
    assert latest is not None
    assert latest["id"] == second["id"]
    assert latest["implied_prob"] == 0.65


def test_get_snapshots_tie_keeps_insertion_order(kernel_db):
    """The series reader needs the same tiebreaker, in the other direction.

    ``get_snapshots`` feeds a line chart, so a plan-dependent pair inside a tie
    is a visible artefact rather than a wrong scalar.
    """
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    link_store = SportMarketLinkStore()
    snap_store = MarketSnapshotStore()
    link = link_store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.95, verified=True,
        market_question="q", implied_prob=0.6,
    )
    same = datetime(2026, 10, 7, 12, 0, 0, 654321, tzinfo=timezone.utc)
    written = [
        snap_store.append_snapshot(
            link_id=link["id"], implied_prob=prob, price=prob, captured_at=same,
        )
        for prob in (0.6, 0.62, 0.65)
    ]
    assert len({w["captured_at"] for w in written}) == 1, "not a tie"
    rows = snap_store.get_snapshots(link_id=link["id"])
    assert [r["id"] for r in rows] == [w["id"] for w in written]
    assert [r["implied_prob"] for r in rows] == [0.6, 0.62, 0.65]


def test_get_latest_snapshot_empty(kernel_db):
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    snap_store = MarketSnapshotStore()
    assert snap_store.get_latest_snapshot(link_id=99999) is None
