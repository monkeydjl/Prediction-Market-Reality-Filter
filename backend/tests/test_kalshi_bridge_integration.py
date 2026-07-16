"""Tests for Kalshi bridge integration — TDD RED phase.

Adapted to the actual SportMarketBridgeService interface:
- The link store attribute is ``self._links`` (NOT ``_link_store``).
- ``_rule_match`` / ``_llm_match`` return ``MatchResult | None`` dataclasses
  (with ``confidence``, ``mapped_outcome``, ``reasoning``), not dicts.
- ``link_kalshi_market(candidate)`` reads ``match_id`` from the candidate dict
  (parallel to ``link_polymarket_market`` which takes ``match_id`` as a kwarg).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.kernel.sport_market_bridge_service import (
    SportMarketBridgeService,
    MatchResult,
)


@pytest.fixture
def bridge():
    return SportMarketBridgeService()


def _make_candidate(**overrides):
    """Build a Kalshi candidate dict compatible with link_kalshi_market."""
    defaults = dict(
        match_id="nba-20250101-LAL-BOS",
        contract_id="KXNBAGAME-25JAN01-LAL-BOS",
        question="Lakers vs Celtics Jan 1",
        price=0.65,
        no_price=0.35,
        liquidity=5000,
        volume=12000,
        source="kalshi",
        detected_sport="basketball",
        detected_competition="nba",
        detected_teams=["Lakers", "Celtics"],
        detected_date="2025-01-01",
    )
    defaults.update(overrides)
    return defaults


@pytest.mark.asyncio
async def test_link_kalshi_market_stores_with_source_kalshi(bridge):
    """link_kalshi_market stores link with source='kalshi'."""
    candidate = _make_candidate()

    with patch.object(bridge, "_rule_match", return_value=MatchResult(
        confidence=0.95, mapped_outcome="home_win", reasoning="rule high"
    )):
        with patch.object(bridge, "_links") as mock_store:
            mock_store.upsert_link = MagicMock(
                return_value={"id": 1, "verified": True, "source": "kalshi"}
            )

            result = await bridge.link_kalshi_market(candidate)

    assert result["source"] == "kalshi"
    mock_store.upsert_link.assert_called_once()
    call_kwargs = mock_store.upsert_link.call_args
    assert call_kwargs.kwargs.get("source") == "kalshi"


@pytest.mark.asyncio
async def test_link_kalshi_market_auto_verifies_on_rule_match(bridge):
    """Rule match with confidence >= 0.9 auto-verifies."""
    candidate = _make_candidate()

    with patch.object(bridge, "_rule_match", return_value=MatchResult(
        confidence=0.95, mapped_outcome="home_win", reasoning="rule high"
    )):
        with patch.object(bridge, "_links") as mock_store:
            mock_store.upsert_link = MagicMock(
                return_value={"id": 1, "verified": True, "source": "kalshi"}
            )
            result = await bridge.link_kalshi_market(candidate)

    assert result["verified"] is True


@pytest.mark.asyncio
async def test_link_kalshi_market_sends_to_pending_on_medium_confidence(bridge):
    """LLM match with confidence 0.6-0.85 goes to pending (verified=False)."""
    candidate = _make_candidate()

    with patch.object(bridge, "_rule_match", return_value=MatchResult(
        confidence=0.3, mapped_outcome="home_win", reasoning="rule low"
    )):
        with patch.object(bridge, "_llm_match", return_value=MatchResult(
            confidence=0.70, mapped_outcome="home_win", reasoning="llm mid"
        )):
            with patch.object(bridge, "_links") as mock_store:
                mock_store.upsert_link = MagicMock(
                    return_value={"id": 2, "verified": False, "source": "kalshi"}
                )
                result = await bridge.link_kalshi_market(candidate)

    assert result["verified"] is False


@pytest.mark.asyncio
async def test_fetch_kalshi_price_parses_response(bridge):
    """_fetch_kalshi_price correctly parses Kalshi market response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "markets": [{
            "ticker": "KXNBAGAME-25JAN01-LAL-BOS",
            "last_price_dollars": 0.68,
            "yes_bid_dollars": 0.66,
            "yes_ask_dollars": 0.70,
            "liquidity_dollars": 8000,
            "volume_fp": 15000,
        }]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await bridge._fetch_kalshi_price("KXNBAGAME-25JAN01-LAL-BOS")

    assert result["implied_prob"] == pytest.approx(0.68)
    assert result["price"] == pytest.approx(0.68)
    assert result["liquidity"] == 8000
    assert result["volume"] == 15000
