"""Tests for SportMarketLinkStore.get_matches_with_verified_links (additive).

Also covers what every read and the one write do when the links table cannot be
read or written: each used to swallow the failure into its own cold-start value
(``[]`` / ``None`` / ``False``), which is the answer every caller acts on.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.kernel.kernel_db import (
    init_kernel_db, close_kernel_db, get_kernel_session,
)
from app.kernel.sport_market_link_store import SportMarketLinkStore

TABLE = "kernel_sport_market_links"


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "link_matches_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def _sql(stmt: str) -> None:
    """Real DDL through the live ORM session, visible to later ORM reads."""
    session = get_kernel_session()
    try:
        session.execute(text(stmt))
        session.commit()
    finally:
        session.close()


def _seed(store, match_id, contract_id, verified):
    return store.upsert_link(
        match_id=match_id, contract_id=contract_id, source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=verified, market_question="q", implied_prob=0.6,
    )


def test_get_matches_with_verified_links_returns_distinct_match_ids(kernel_db):
    """Returns distinct match_ids that have at least one verified=True link."""
    store = SportMarketLinkStore()
    _seed(store, "m1", "c1", verified=True)
    _seed(store, "m1", "c2", verified=True)  # second verified link for m1
    _seed(store, "m2", "c3", verified=False)  # m2 has only unverified
    _seed(store, "m3", "c4", verified=True)
    matches = store.get_matches_with_verified_links()
    assert sorted(matches) == ["m1", "m3"]


def test_get_matches_with_verified_links_empty_when_none_verified(kernel_db):
    """Returns empty list when no verified links exist."""
    store = SportMarketLinkStore()
    _seed(store, "m1", "c1", verified=False)
    matches = store.get_matches_with_verified_links()
    assert matches == []


# --- A degraded links table must not read as an unlinked one ---

READS = (
    ("get_links", {"match_id": "m1"}),
    ("get_link", {"link_id": 1}),
    ("get_verified_links", {"match_id": "m1"}),
    ("get_pending_links", {}),
    ("get_all_verified_links", {}),
    ("list_links", {}),
    ("get_matches_with_verified_links", {}),
)


def test_a_readable_empty_table_still_answers_empty(kernel_db):
    """Cold start keeps its answer: the fix must not turn "no rows" into an error.

    Without this, "raises on a dropped table" would also pass for a read that
    raised on an empty one, and every caller's first run would break.
    """
    store = SportMarketLinkStore()
    for name, kwargs in READS:
        result = getattr(store, name)(**kwargs)
        if name == "get_link":
            assert result is None, name
        else:
            assert result == [], name
    assert store.set_verified(link_id=1, verified=True) is False
    assert store.auto_verify_high_confidence(dry_run=True) == {
        "pending_total": 0, "candidates": 0, "auto_verified": 0, "would_verify": 0,
        "threshold": 0.95, "dry_run": True, "link_ids": [],
    }


def test_every_read_raises_when_the_table_is_gone(kernel_db, subtests):
    """A dropped table is not "this match has no links".

    Each read returned its own cold-start value, so the operator's whole links
    board, the reviewer's pending queue and the edge detector's
    ``skip_reason="no_verified_links"`` were all indistinguishable from a match
    nobody had linked yet.
    """
    store = SportMarketLinkStore()
    _seed(store, "m1", "c1", verified=True)
    _sql(f"DROP TABLE {TABLE}")
    for name, kwargs in READS:
        with subtests.test(read=name):
            with pytest.raises(OperationalError, match="no such table"):
                getattr(store, name)(**kwargs)


def test_partial_drift_raises_on_every_read_that_builds_a_row(kernel_db, subtests):
    """One dropped column, and the two halves of this store disagree.

    ``get_matches_with_verified_links`` selects only ``match_id``, so it survives
    a rename of ``implied_prob`` and keeps reporting the match — while every read
    that builds a full row dict fails. Measured pre-fix, that combination made
    the snapshot job report ``success matches_total=1 captured=0 errors=0`` and
    the edge job ``success matches_total=1 matches_processed=0``: a green run
    over a match whose links were all unreadable, which is strictly worse than
    the dropped-table case, where the enumeration itself comes back empty.
    """
    store = SportMarketLinkStore()
    _seed(store, "m1", "c1", verified=True)
    _sql(f"ALTER TABLE {TABLE} RENAME COLUMN implied_prob TO implied_prob_old")

    assert store.get_matches_with_verified_links() == ["m1"]
    for name, kwargs in READS:
        if name == "get_matches_with_verified_links":
            continue
        with subtests.test(read=name):
            with pytest.raises(OperationalError, match="no such column"):
                getattr(store, name)(**kwargs)


def test_a_failed_update_raises_instead_of_reporting_no_such_row(kernel_db):
    """``set_verified`` returning False must mean "no such row", nothing else.

    A BEFORE UPDATE trigger raising ABORT stands in for any failing UPDATE while
    leaving every read healthy — the only shape that isolates the write. Pre-fix
    the ``rollback(); return False`` was indistinguishable from a missing row, so
    ``auto_verify_high_confidence`` answered ``candidates: 1 auto_verified: 0``
    with no error for a candidate whose write had failed.
    """
    store = SportMarketLinkStore()
    link = _seed(store, "m1", "c1", verified=False)
    _sql(
        f"CREATE TRIGGER block_update BEFORE UPDATE ON {TABLE} "
        "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
    )

    # Reads are untouched, so this isolates the write path.
    assert len(store.get_pending_links()) == 1

    with pytest.raises(IntegrityError):
        store.set_verified(link_id=int(link["id"]), verified=True)
    with pytest.raises(IntegrityError):
        store.auto_verify_high_confidence(min_confidence=0.95, dry_run=False)

    # The rival configuration: a genuinely absent row still answers False, and
    # no UPDATE is attempted for it, so the trigger never fires.
    assert store.set_verified(link_id=999999, verified=True) is False
