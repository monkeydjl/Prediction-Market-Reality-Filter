"""Tests for sport_market_bridge_cli."""
import pytest

from app.kernel.kernel_db import init_kernel_db, close_kernel_db


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "kernel_cli_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def test_cli_list_empty(kernel_db, capsys):
    from scripts.sport_market_bridge_cli import main
    rc = main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no items" in out


def test_cli_list_pending(kernel_db, capsys):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="llm",
        link_confidence=0.7, verified=False, market_question="q", implied_prob=0.5,
    )
    from scripts.sport_market_bridge_cli import main
    rc = main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PENDING" in out
    assert "m1" in out


def test_cli_verify(kernel_db, capsys):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="llm",
        link_confidence=0.7, verified=False, market_question="q", implied_prob=0.5,
    )
    from scripts.sport_market_bridge_cli import main
    rc = main(["verify", "--match-id", "m1", "--contract-id", "c1"])
    assert rc == 0
    links = store.get_links(match_id="m1")
    assert links[0]["verified"] is True


def test_cli_verify_missing_returns_1(kernel_db, capsys):
    from scripts.sport_market_bridge_cli import main
    rc = main(["verify", "--match-id", "nope", "--contract-id", "nope"])
    assert rc == 1
