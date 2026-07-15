"""Tests for SportMarketBridgeService — three-layer matching engine.

Covers: rule-layer auto-verify, LLM fallback (auto-verify / pending / no-link),
traditional odds linking, fail-closed verified filter, snapshot capture.
Uses real SQLite via tmp_path (no DB mocks); LLM/rule methods are injected
via AsyncMock/MagicMock to control the three-layer routing deterministically.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.kernel.kernel_db import init_kernel_db, close_kernel_db
from app.kernel.sport_market_link_store import SportMarketLinkStore
from app.kernel.market_snapshot_store import MarketSnapshotStore


@pytest.fixture
def kernel_db(tmp_path):
    """Fresh kernel DB in tmp_path for each test."""
    db_path = tmp_path / "kernel_bridge_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


@pytest.fixture
def stores(kernel_db):
    return SportMarketLinkStore(), MarketSnapshotStore()


def _make_market_info(**overrides):
    from app.services.sport_market_detector import SportMarketInfo
    defaults = dict(
        contract_id="poly-1",
        source="polymarket",
        market_question="Will the Lakers beat the Celtics?",
        market_type="single_match_binary",
        detected_sport="basketball",
        detected_competition="nba",
        detected_teams=["los_angeles_lakers", "boston_celtics"],
        detected_date=None,
        outcome_label="YES",
    )
    defaults.update(overrides)
    return SportMarketInfo(**defaults)


# --- Test 1: rule-layer high-confidence auto-verified ---

@pytest.mark.asyncio
async def test_rule_match_high_confidence_auto_verified(stores):
    from app.kernel.sport_market_bridge_service import SportMarketBridgeService
    link_store, snapshot_store = stores
    svc = SportMarketBridgeService(link_store=link_store, snapshot_store=snapshot_store)
    info = _make_market_info()
    # Real _rule_match runs: match_id teams LAL/BOS resolve to the two
    # detected canonical teams -> matched=2 -> confidence 0.95 -> auto-verified.
    result = await svc.link_polymarket_market(
        match_id="nba-20250101-LAL-BOS",
        market_info=info,
        yes_price=0.60,
        no_price=0.45,
    )
    assert result is not None
    assert result["verified"] is True
    assert result["link_method"] == "rule"
    assert result["link_confidence"] >= 0.9
    assert result["mapped_outcome"] == "home_win"
    assert result["implied_prob"] == pytest.approx(0.60)


# --- Test 2: LLM fallback auto-verified ---

@pytest.mark.asyncio
async def test_llm_fallback_auto_verified(stores):
    from app.kernel.sport_market_bridge_service import (
        SportMarketBridgeService,
        MatchResult,
    )
    link_store, snapshot_store = stores
    svc = SportMarketBridgeService(link_store=link_store, snapshot_store=snapshot_store)
    info = _make_market_info(detected_teams=["some_other_team"])
    # Rule layer returns low confidence -> escalate to LLM.
    svc._rule_match = MagicMock(
        return_value=MatchResult(confidence=0.3, mapped_outcome="home_win", reasoning="low")
    )
    svc._llm_match = AsyncMock(
        return_value=MatchResult(confidence=0.9, mapped_outcome="home_win", reasoning="llm high")
    )
    result = await svc.link_polymarket_market(
        match_id="nba-20250101-LAL-BOS",
        market_info=info,
        yes_price=0.55,
        no_price=0.50,
    )
    assert result is not None
    assert result["verified"] is True
    assert result["link_method"] == "llm"
    assert result["link_confidence"] >= 0.85


# --- Test 3: LLM pending manual verification ---

@pytest.mark.asyncio
async def test_llm_pending_manual_verification(stores):
    from app.kernel.sport_market_bridge_service import (
        SportMarketBridgeService,
        MatchResult,
    )
    link_store, snapshot_store = stores
    svc = SportMarketBridgeService(link_store=link_store, snapshot_store=snapshot_store)
    info = _make_market_info(detected_teams=["unknown_team"])
    svc._rule_match = MagicMock(
        return_value=MatchResult(confidence=0.3, mapped_outcome="home_win", reasoning="low")
    )
    svc._llm_match = AsyncMock(
        return_value=MatchResult(confidence=0.7, mapped_outcome="home_win", reasoning="llm mid")
    )
    result = await svc.link_polymarket_market(
        match_id="nba-20250101-LAL-BOS",
        market_info=info,
        yes_price=0.55,
        no_price=0.50,
    )
    assert result is not None
    assert result["verified"] is False
    assert result["link_method"] == "llm"
    assert 0.6 <= result["link_confidence"] < 0.85


# --- Test 4: LLM low confidence -> no link ---

@pytest.mark.asyncio
async def test_llm_low_confidence_no_link(stores):
    from app.kernel.sport_market_bridge_service import (
        SportMarketBridgeService,
        MatchResult,
    )
    link_store, snapshot_store = stores
    svc = SportMarketBridgeService(link_store=link_store, snapshot_store=snapshot_store)
    info = _make_market_info(detected_teams=["unknown_team"])
    svc._rule_match = MagicMock(
        return_value=MatchResult(confidence=0.3, mapped_outcome="home_win", reasoning="low")
    )
    svc._llm_match = AsyncMock(
        return_value=MatchResult(confidence=0.4, mapped_outcome="none", reasoning="llm low")
    )
    result = await svc.link_polymarket_market(
        match_id="nba-20250101-LAL-BOS",
        market_info=info,
        yes_price=0.55,
        no_price=0.50,
    )
    assert result is None


# --- Test 5: traditional odds linking ---

@pytest.mark.asyncio
async def test_link_traditional_odds(stores):
    from app.kernel.sport_market_bridge_service import SportMarketBridgeService
    link_store, snapshot_store = stores
    svc = SportMarketBridgeService(link_store=link_store, snapshot_store=snapshot_store)
    fake_odds = {
        "home": 1.5,
        "draw": 6.0,
        "away": 2.5,
        "source": "the_odds_api",
        "last_update": "2025-08-16T00:00:00Z",
    }
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "app.kernel.sport_market_bridge_service.fetch_match_odds",
            AsyncMock(return_value=fake_odds),
        )
        results = await svc.link_traditional_odds(
            match_id="epl-20250816-LIV-MCI",
            home_team="Liverpool",
            away_team="Man City",
            competition="epl",
        )
    assert len(results) == 3
    outcomes = {r["mapped_outcome"] for r in results}
    assert outcomes == {"home_win", "draw", "away_win"}
    for r in results:
        assert r["verified"] is True
        assert r["link_method"] == "rule"
        assert r["link_confidence"] == 1.0
        assert r["source"] == "the_odds_api"


# --- Test 6: fail-closed verified filter ---

def test_get_verified_links_fail_closed(stores):
    from app.kernel.sport_market_bridge_service import SportMarketBridgeService
    link_store, snapshot_store = stores
    link_store.upsert_link(
        match_id="nba-20250101-LAL-BOS", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=True, market_question="q", implied_prob=0.6,
    )
    link_store.upsert_link(
        match_id="nba-20250101-LAL-BOS", contract_id="c2", source="polymarket",
        outcome_label="NO", mapped_outcome="away_win", link_method="llm",
        link_confidence=0.7, verified=False, market_question="q2", implied_prob=0.4,
    )
    svc = SportMarketBridgeService(link_store=link_store, snapshot_store=snapshot_store)
    verified = svc.get_verified_links(match_id="nba-20250101-LAL-BOS")
    assert len(verified) == 1
    assert verified[0]["contract_id"] == "c1"
    assert all(v["verified"] is True for v in verified)


# --- Test 7: capture snapshots ---

@pytest.mark.asyncio
async def test_capture_snapshots(stores):
    from app.kernel.sport_market_bridge_service import SportMarketBridgeService
    link_store, snapshot_store = stores
    link_store.upsert_link(
        match_id="epl-20250816-LIV-MCI", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=True, market_question="q", implied_prob=0.6,
    )
    svc = SportMarketBridgeService(link_store=link_store, snapshot_store=snapshot_store)
    svc._fetch_latest_price = AsyncMock(return_value=0.62)
    count = await svc.capture_snapshots(match_id="epl-20250816-LIV-MCI")
    assert count == 1
    links = link_store.get_verified_links(match_id="epl-20250816-LIV-MCI")
    snaps = snapshot_store.get_snapshots(link_id=links[0]["id"])
    assert len(snaps) == 1
    assert snaps[0]["implied_prob"] == pytest.approx(0.62)
