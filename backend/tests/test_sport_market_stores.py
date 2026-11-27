"""Tests for sport market link + snapshot stores (real SQLite via tmp_path)."""
from datetime import datetime, timezone

import pytest

from app.kernel.kernel_db import (
    KernelSportMarketLink,
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
    store.upsert_link(
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
    assert all(link["verified"] is True for link in verified)


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

    A caller that passes ``captured_at`` explicitly makes a tie directly, and
    any path that hoists ``now`` out of its write loop carries the same shape
    in code. What makes a tie *observable*, though, is two rows for the
    **same** link: section 53.45 measured that
    ``futures_market_service.capture_snapshots``' hoisted ``now`` only shares
    an instant *across* links and that its read is a per-link ``MAX``, so the
    tie is harmless there -- the read under test has no such protection.
    Without the ``id`` tiebreaker the relative order inside the tie is
    undefined, and the measured behaviour was to return the *first*-inserted
    row -- i.e. to report the *older* capture as the newest. That is the shape
    of the one-off full-suite red recorded in
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


def test_the_snapshot_store_propagates_a_read_failure(kernel_db):
    """Both raw reads raise instead of inventing an empty value.

    ``[]`` and ``None`` are legal answers here -- every link starts with no
    rows -- so a store that returns them on failure has answered "no prices"
    from a query that never ran. Measured pre-fix through
    ``market_liquidity.compute_match_liquidity_factor``, that re-armed the
    inverted alarm documented in tests/test_market_liquidity_degraded.py.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from app.kernel.market_snapshot_store import MarketSnapshotStore
    from app.kernel.sport_market_link_store import SportMarketLinkStore

    link = SportMarketLinkStore().upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.95, verified=True,
        market_question="q", implied_prob=0.6,
    )
    snap_store = MarketSnapshotStore()
    snap_store.append_snapshot(link_id=link["id"], implied_prob=0.6, price=0.6)
    # Healthy read first, so the assertions below are about the break.
    assert len(snap_store.get_snapshots(link_id=link["id"])) == 1

    session = get_kernel_session()
    try:
        session.execute(text("DROP TABLE kernel_market_snapshots"))
        session.commit()
    finally:
        session.close()

    with pytest.raises(OperationalError, match="no such table"):
        snap_store.get_snapshots(link_id=link["id"])
    with pytest.raises(OperationalError, match="no such table"):
        snap_store.get_latest_snapshot(link_id=link["id"])


def test_audit_summary_names_a_failed_read_instead_of_calling_it_empty(kernel_db):
    """``available: false`` alone could not tell the two zeros apart.

    The audit body already carries a switch, so the third meaning gets a key
    rather than a 500. ``snapshot_count`` stays 0 because nothing was counted,
    not because the count *was* zero -- which is why the reason word is the
    load-bearing part of this body.
    """
    from sqlalchemy import text
    from app.kernel.market_snapshot_store import MarketSnapshotStore

    session = get_kernel_session()
    try:
        session.execute(text("DROP TABLE kernel_market_snapshots"))
        session.commit()
    finally:
        session.close()

    out = MarketSnapshotStore().audit_summary(link_id=1)
    assert out["read_failed"] is True
    assert out["available"] is False
    assert out["snapshot_count"] == 0
    assert out["flags"] == ["read_failed"]


def test_audit_summary_keeps_the_other_zero_apart(kernel_db):
    """A readable, empty table keeps ``read_failed: false``.

    Every branch carries the key explicitly, because an absent key would read
    as "not asked" -- a fourth meaning.
    """
    from app.kernel.market_snapshot_store import MarketSnapshotStore

    out = MarketSnapshotStore().audit_summary(link_id=99999)
    assert out["read_failed"] is False
    assert out["available"] is False
    assert out["flags"] == ["no_snapshots"]


# --- the row->dict converters hand out aware datetimes (audit 53.183) ---
#
# These values never pass a `.isoformat()` call site. A converter puts the
# `datetime` object itself into the dict and pydantic (response_model) or
# FastAPI's jsonable_encoder formats it afterwards, so neither the grep census
# nor the AST rule in `test_row_timestamp_serialization.py` can see them: there
# is no call to match. What can see them is the converter's own return value.
#
# Every one of these columns comes back from SQLite with no tzinfo, so a
# converter that hands the column through emits `2026-06-20T13:00:00` and the
# browser reads it as local time.

AUDIT_183_AT = datetime(2026, 6, 20, 13, 0, tzinfo=timezone.utc)


def _stamps_in(value):
    """Every ``datetime`` reachable in a nested dict/list payload."""
    if isinstance(value, datetime):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _stamps_in(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _stamps_in(v)]
    return []


def _raw_column(model, attr):
    """Read one column straight from the row, bypassing every converter."""
    session = get_kernel_session()
    try:
        row = session.query(model).first()
    finally:
        session.close()
    assert row is not None, "the seed wrote no row to read back"
    return getattr(row, attr)


def _seed_market_snapshot():
    from app.kernel.kernel_db import KernelMarketSnapshot
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    from app.kernel.sport_market_link_store import SportMarketLinkStore

    link = SportMarketLinkStore().upsert_link(
        match_id="m1", contract_id="c1", source="polymarket", outcome_label="YES",
        mapped_outcome="home_win", link_method="rule", link_confidence=0.9,
        verified=True, market_question="q", implied_prob=0.5,
    )
    MarketSnapshotStore().append_snapshot(
        link_id=link["id"], implied_prob=0.5, captured_at=AUDIT_183_AT,
    )
    payload = MarketSnapshotStore().get_snapshots(link_id=link["id"])
    return payload, _raw_column(KernelMarketSnapshot, "captured_at")


def _seed_sport_market_link():
    from app.kernel.kernel_db import KernelSportMarketLink
    from app.kernel.sport_market_link_store import SportMarketLinkStore

    SportMarketLinkStore().upsert_link(
        match_id="m1", contract_id="c1", source="polymarket", outcome_label="YES",
        mapped_outcome="home_win", link_method="rule", link_confidence=0.9,
        verified=True, market_question="q", implied_prob=0.5,
    )
    payload = SportMarketLinkStore().get_links(match_id="m1")
    return payload, _raw_column(KernelSportMarketLink, "created_at")


def _seed_edge():
    from app.kernel.edge_store import EdgeStore
    from app.kernel.kernel_db import KernelSportEdge

    EdgeStore().append_edge(
        match_id="m1", mapped_outcome="home_win", model_prob=0.65, market_prob=0.5,
        raw_edge=0.15, trust=0.7, liquidity_factor=1.0, adjusted_edge=0.07,
        spread=None, sources_count=1, stale=False, captured_at=AUDIT_183_AT,
    )
    payload = EdgeStore().get_latest_edges(match_id="m1")
    return payload, _raw_column(KernelSportEdge, "captured_at")


def _seed_traditional_odds():
    from app.kernel.kernel_db import KernelTraditionalOddsSnapshot
    from app.kernel.traditional_odds_store import TraditionalOddsStore

    TraditionalOddsStore().append_snapshot(
        match_id="m1", mapped_outcome="home_win", competition="epl",
        implied_prob=0.5, decimal_odds=2.0, bookmaker="bk", bookmakers_count=1,
        captured_at=AUDIT_183_AT,
    )
    payload = TraditionalOddsStore().get_snapshots(match_id="m1")
    return payload, _raw_column(KernelTraditionalOddsSnapshot, "captured_at")


def _seed_futures_links():
    from app.kernel.futures_link_store import FuturesLinkStore
    from app.kernel.kernel_db import KernelFuturesLink

    store = FuturesLinkStore()
    store.upsert_link(
        competition="epl", season="2025-26", team="Arsenal", contract_id="c9",
        source="polymarket", market_question="q", implied_prob=0.4, verified=True,
    )
    link_id = store.get_links("epl", "2025-26")[0]["id"]
    store.append_snapshot(
        link_id=link_id, implied_prob=0.4, price=None, liquidity=None, volume=None,
        captured_at=AUDIT_183_AT,
    )
    payload = store.get_links("epl", "2025-26") + store.get_latest_snapshots(
        "epl", "2025-26"
    )
    return payload, _raw_column(KernelFuturesLink, "created_at")


SEEDS = [
    ("market_snapshot", _seed_market_snapshot),
    ("sport_market_link", _seed_sport_market_link),
    ("edge", _seed_edge),
    ("traditional_odds", _seed_traditional_odds),
    ("futures", _seed_futures_links),
]


@pytest.mark.parametrize("label,seed", SEEDS, ids=[name for name, _ in SEEDS])
def test_no_row_dict_converter_hands_out_a_naive_datetime(kernel_db, label, seed):
    """Every timestamp a converter publishes is aware.

    Three assertions, and the first two are what keep the third from passing
    vacuously:

    1. the column really does come back naive -- if SQLite ever round-trips the
       offset, "aware in the dict" would prove nothing about the converter;
    2. the payload really does contain a datetime -- a renamed key would
       otherwise leave nothing to check;
    3. none of them is naive.
    """
    payload, raw = seed()

    assert raw.tzinfo is None, (
        f"{label}: premise broken -- SQLite now round-trips the offset, so this "
        "test no longer measures the converter"
    )

    stamps = _stamps_in(payload)
    assert stamps, f"{label}: the payload carries no datetime at all"

    naive = [s for s in stamps if s.tzinfo is None]
    assert naive == [], (
        f"{label}: these reach a route without an offset, and the browser reads "
        f"them as local time: {naive}"
    )

