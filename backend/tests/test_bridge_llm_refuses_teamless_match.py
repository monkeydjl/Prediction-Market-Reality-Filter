# backend/tests/test_bridge_llm_refuses_teamless_match.py
"""Layer 2 must refuse a match it cannot identify, instead of asking anyway.

``_rule_match`` returns None when the match_id yields no team tokens, so layer 2 is
the branch every live match reaches. Production ids are
``{sport}-{provider_game_id}`` (``nba-21716138``) and carry no team name, so
``team_tokens`` is ``[]`` for all 18,717 rows in ``kernel_match_fixtures``. The prompt
then read ``- Match teams (tokens): []`` and asked whether a market question was about
"the above match", identified only by sport and competition -- true of every fixture
in that competition. A confident answer at ``LLM_CONFIDENCE_THRESHOLD`` is stored as a
**verified** link, and ``get_matches_with_verified_links()`` feeds odds capture.

The existing tests in ``test_sport_market_bridge.py`` all use the dated format
``nba-20250101-LAL-BOS`` -- which no production writer produces -- and inject
``svc._llm_match = AsyncMock(...)``, so the real method had no test.
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.kernel.kernel_db import close_kernel_db, init_kernel_db
from app.kernel.sport_market_link_store import SportMarketLinkStore
from app.kernel.market_snapshot_store import MarketSnapshotStore


@pytest.fixture
def svc(tmp_path):
    from app.kernel.sport_market_bridge_service import SportMarketBridgeService

    close_kernel_db()
    init_kernel_db(str(tmp_path / "kernel_bridge_llm.db"))
    yield SportMarketBridgeService(
        link_store=SportMarketLinkStore(), snapshot_store=MarketSnapshotStore(),
    )
    close_kernel_db()


def _ok(payload):
    """A gateway result object shaped like llm_gateway_service returns."""
    class _R:
        ok = True
        json_data = payload
    return _R()


@pytest.mark.parametrize("match_id", [
    "nba-21716138",
    "mlb-824514",
    "nhl-2026010012",
    "bundesliga-540406",
])
@pytest.mark.asyncio
async def test_a_teamless_match_id_never_reaches_the_model(svc, match_id):
    complete = AsyncMock(return_value=_ok({
        "is_match": True, "confidence": 0.97,
        "mapped_outcome": "home_win", "reasoning": "same competition",
    }))
    with patch("app.services.llm_gateway_service.complete_json", complete):
        result = await svc._llm_match(
            match_id=match_id,
            market_question="Will the Lakers beat the Celtics?",
            detected_competition="nba",
            detected_teams=["los_angeles_lakers", "boston_celtics"],
        )

    assert result is None
    # The cost assertion: refusing after paying is not refusing.
    complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_parseable_match_id_still_reaches_the_model(svc):
    """Rival configuration: the guard must not refuse everything.

    Without this, `return None` at the top of _llm_match would pass the test above.
    """
    complete = AsyncMock(return_value=_ok({
        "is_match": True, "confidence": 0.91,
        "mapped_outcome": "home_win", "reasoning": "teams align",
    }))
    with patch("app.services.llm_gateway_service.complete_json", complete):
        result = await svc._llm_match(
            match_id="nba-20250101-LAL-BOS",
            market_question="Will the Lakers beat the Celtics?",
            detected_competition="nba",
            detected_teams=["los_angeles_lakers", "boston_celtics"],
        )

    complete.assert_awaited_once()
    assert result is not None
    assert result.confidence == pytest.approx(0.91)


@pytest.mark.asyncio
async def test_the_prompt_names_both_sides_of_the_comparison(svc):
    """``detected_teams``/``detected_competition`` were accepted and dropped.

    Both call sites pass them and the prompt used neither, so the model was given
    one side of a two-sided comparison.
    """
    complete = AsyncMock(return_value=_ok({
        "is_match": False, "confidence": 0.1,
        "mapped_outcome": "none", "reasoning": "no",
    }))
    with patch("app.services.llm_gateway_service.complete_json", complete):
        await svc._llm_match(
            match_id="nba-20250101-LAL-BOS",
            market_question="Will the Lakers beat the Celtics?",
            detected_competition="nba",
            detected_teams=["los_angeles_lakers", "boston_celtics"],
        )

    prompt = complete.await_args.kwargs["messages"][0]["content"]
    # The match side, parsed from the id.
    assert "LAL" in prompt and "BOS" in prompt
    # The market side, which used to be discarded.
    assert "los_angeles_lakers" in prompt
    assert "boston_celtics" in prompt
    assert "Detected competition" in prompt
    # And it must never advertise an empty team list as if it were data.
    assert "(tokens): []" not in prompt


@pytest.mark.asyncio
async def test_a_live_format_id_produces_no_link_at_all(svc):
    """End to end: the invented verified link is what this prevents."""
    from app.services.sport_market_detector import SportMarketInfo

    info = SportMarketInfo(
        contract_id="poly-live-1",
        source="polymarket",
        market_question="Will the Lakers beat the Celtics?",
        market_type="single_match_binary",
        detected_sport="basketball",
        detected_competition="nba",
        detected_teams=["los_angeles_lakers", "boston_celtics"],
        detected_date=None,
        outcome_label="YES",
    )
    complete = AsyncMock(return_value=_ok({
        "is_match": True, "confidence": 0.99,
        "mapped_outcome": "home_win", "reasoning": "nba market, nba match",
    }))
    with patch("app.services.llm_gateway_service.complete_json", complete):
        result = await svc.link_polymarket_market(
            match_id="nba-21716138",
            market_info=info,
            yes_price=0.60,
            no_price=0.45,
        )

    assert result is None
    complete.assert_not_awaited()
    # Nothing verified was written, so the odds job's input stays empty.
    assert svc._links.get_matches_with_verified_links() == []
