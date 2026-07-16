"""Tests for SportMarketLinkStore.get_matches_with_verified_links (additive)."""
import pytest

from app.kernel.kernel_db import init_kernel_db, close_kernel_db
from app.kernel.sport_market_link_store import SportMarketLinkStore


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "link_matches_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


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
