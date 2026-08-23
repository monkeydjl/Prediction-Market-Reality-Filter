"""Tests for SportMarketBridgeService — three-layer matching engine.

Covers: rule-layer auto-verify, LLM fallback (auto-verify / pending / no-link),
traditional odds linking, fail-closed verified filter, price dispatch by source.
Uses real SQLite via tmp_path (no DB mocks); LLM/rule methods are injected
via AsyncMock/MagicMock to control the three-layer routing deterministically.
"""
import inspect

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


# --- Test 7: the capture loop lives in the scheduler, not on the service ---

@pytest.mark.asyncio
async def test_service_exposes_no_capture_helper_that_bypasses_source_dispatch():
    """No service-level capture helper may price links outside fetch_link_price.

    `capture_snapshots` used to live here as a stale copy of the scheduler's
    loop, from before dispatch-by-source existed: it sent Kalshi links through
    `fetch_link_price` and everything else through a `_fetch_latest_price` stub
    that returned None forever. Nothing in app/ ever called it, and its test
    passed only because it assigned `_fetch_latest_price` an AsyncMock - the
    implementation production never had. Wiring it up on the strength of that
    green test would have priced Kalshi links and silently written nothing for
    Polymarket ones, while also dropping the liquidity/volume the real loop
    captures.

    The live path is `_job_capture_sport_market_snapshots`, which routes EVERY
    verified link through `fetch_link_price` (covered by the dispatch tests
    below). This asserts the trap is not reintroduced: a helper here is only
    safe if it delegates to that dispatcher.
    """
    from app.kernel import sport_market_bridge_service as module
    from app.kernel.sport_market_bridge_service import SportMarketBridgeService

    assert not hasattr(SportMarketBridgeService, "_fetch_latest_price")

    source = inspect.getsource(module)
    for name, member in inspect.getmembers(
        SportMarketBridgeService, predicate=inspect.isfunction
    ):
        if "capture" not in name:
            continue
        body = inspect.getsource(member)
        assert "fetch_link_price" in body, (
            f"{name} prices links without going through fetch_link_price, "
            "which is how the source-dispatch bug returns"
        )
    # The stub's docstring invited exactly the override that hid the gap.
    assert "tests replace this method with an AsyncMock" not in source


# --- Test 8: price fetch dispatches by link source ---

@pytest.mark.asyncio
async def test_fetch_link_price_routes_kalshi_to_the_kalshi_endpoint(stores):
    """A Kalshi link must not be priced against the Polymarket gamma API.

    gamma is queried by contract ``id``; a Kalshi link stores a Kalshi ticker
    there, so routing it to gamma matches nothing and the link silently never
    gets a snapshot. The scheduler used to call fetch_current_price for EVERY
    verified link, which is exactly that bug.
    """
    from app.kernel.sport_market_bridge_service import SportMarketBridgeService
    link_store, snapshot_store = stores
    svc = SportMarketBridgeService(link_store=link_store, snapshot_store=snapshot_store)
    svc._fetch_kalshi_price = AsyncMock(
        return_value={"implied_prob": 0.71, "price": 0.71, "liquidity": 5.0, "volume": 9.0}
    )
    svc.fetch_current_price = AsyncMock(return_value={"implied_prob": 0.1, "price": 0.1})

    price = await svc.fetch_link_price(
        {"id": 1, "contract_id": "KXNBA-25JAN01-LAL", "source": "kalshi"}
    )

    svc._fetch_kalshi_price.assert_awaited_once_with("KXNBA-25JAN01-LAL")
    svc.fetch_current_price.assert_not_awaited()
    assert price["implied_prob"] == pytest.approx(0.71)


@pytest.mark.asyncio
async def test_fetch_link_price_routes_polymarket_and_unknown_source_to_gamma(stores):
    """Polymarket links - and pre-Kalshi links with no source - use gamma."""
    from app.kernel.sport_market_bridge_service import SportMarketBridgeService
    link_store, snapshot_store = stores
    svc = SportMarketBridgeService(link_store=link_store, snapshot_store=snapshot_store)
    svc._fetch_kalshi_price = AsyncMock()
    svc.fetch_current_price = AsyncMock(return_value={"implied_prob": 0.42, "price": 0.42})

    for link in (
        {"id": 1, "contract_id": "poly-1", "source": "polymarket"},
        {"id": 2, "contract_id": "poly-2"},
    ):
        price = await svc.fetch_link_price(link)
        assert price["implied_prob"] == pytest.approx(0.42)

    svc._fetch_kalshi_price.assert_not_awaited()
    assert svc.fetch_current_price.await_count == 2


@pytest.mark.asyncio
async def test_fetch_link_price_swallows_kalshi_failure(stores):
    """A Kalshi fetch error skips that link instead of aborting the match."""
    from app.kernel.sport_market_bridge_service import SportMarketBridgeService
    link_store, snapshot_store = stores
    svc = SportMarketBridgeService(link_store=link_store, snapshot_store=snapshot_store)
    svc._fetch_kalshi_price = AsyncMock(side_effect=RuntimeError("kalshi 500"))

    price = await svc.fetch_link_price(
        {"id": 1, "contract_id": "KXNBA-1", "source": "kalshi"}
    )

    assert price is None


@pytest.mark.asyncio
async def test_fetch_link_price_does_not_send_traditional_odds_to_gamma(stores):
    """A the_odds_api link must not be priced against the Polymarket gamma API.

    Same failure mode the Kalshi dispatch above was added to fix, left in place
    for the other source this class creates itself: ``link_traditional_odds``
    stores a synthetic ``odds_api::<match_id>::<outcome_label>`` in
    ``contract_id``, which gamma cannot match, so every poll spent an outbound
    request to learn nothing and the link never got a snapshot.

    Returning None does not create the snapshot gap — it already existed — it
    stops querying the wrong venue. The consequence downstream is what matters:
    a traditional-odds link permanently contributes its creation-time
    ``implied_prob`` with no measured liquidity, which is why the mixed
    measured/unmeasured case in EdgeDetectorService is the normal case rather
    than an edge case.
    """
    from app.kernel.sport_market_bridge_service import SportMarketBridgeService
    link_store, snapshot_store = stores
    svc = SportMarketBridgeService(link_store=link_store, snapshot_store=snapshot_store)
    svc._fetch_kalshi_price = AsyncMock()
    svc.fetch_current_price = AsyncMock(return_value={"implied_prob": 0.42, "price": 0.42})

    price = await svc.fetch_link_price({
        "id": 1,
        "contract_id": "odds_api::soccer_epl::2026-08-21::ARS::CHE::home",
        "source": "the_odds_api",
    })

    assert price is None
    svc.fetch_current_price.assert_not_awaited()
    svc._fetch_kalshi_price.assert_not_awaited()
