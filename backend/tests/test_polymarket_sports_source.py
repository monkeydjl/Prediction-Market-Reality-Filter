"""Tests for the Polymarket sports source (mocked httpx)."""
import pytest
from unittest.mock import AsyncMock, patch

from httpx import Response


def _make_market(contract_id, question, price=0.5, no_price=0.5,
                 liquidity=1000.0, volume=5000.0):
    """Build a minimal Polymarket gamma-API item."""
    return {
        "id": contract_id,
        "question": question,
        "clobTokenIds": f'["{contract_id}-yes","{contract_id}-no"]',
        "outcomePrices": f'["{price}", "{no_price}"]',
        "liquidity": liquidity,
        "volume": volume,
        "closed": "false",
        "archived": "false",
    }


@pytest.mark.asyncio
async def test_fetch_filters_to_sport_markets():
    from app.services.polymarket_sports_source import fetch_polymarket_sport_markets
    api_data = [
        _make_market("poly-sport-1", "Will the Lakers beat the Celtics?"),
        _make_market("poly-non-1", "Will Bitcoin reach $100k?"),
    ]
    response = Response(200, json=api_data)

    with patch("app.services.polymarket_sports_source.httpx.AsyncClient") as mock_client_cls:
        client = mock_client_cls.return_value.__aenter__.return_value
        client.get = AsyncMock(return_value=response)
        results = await fetch_polymarket_sport_markets(limit=10)

    # Only the sport market is returned.
    assert len(results) == 1
    assert results[0]["contract_id"] == "poly-sport-1"
    assert results[0]["detected_competition"] == "nba"


@pytest.mark.asyncio
async def test_fetch_excludes_futures():
    from app.services.polymarket_sports_source import fetch_polymarket_sport_markets
    api_data = [
        _make_market("poly-fut", "Will the Lakers win the NBA Championship?"),
        _make_market("poly-sport-2", "Will the Yankees beat the Red Sox?"),
    ]
    response = Response(200, json=api_data)

    with patch("app.services.polymarket_sports_source.httpx.AsyncClient") as mock_client_cls:
        client = mock_client_cls.return_value.__aenter__.return_value
        client.get = AsyncMock(return_value=response)
        results = await fetch_polymarket_sport_markets(limit=10)

    assert len(results) == 1
    assert results[0]["contract_id"] == "poly-sport-2"


@pytest.mark.asyncio
async def test_fetch_api_error_returns_empty():
    from app.services.polymarket_sports_source import fetch_polymarket_sport_markets
    response = Response(500, text="server error")

    with patch("app.services.polymarket_sports_source.httpx.AsyncClient") as mock_client_cls:
        client = mock_client_cls.return_value.__aenter__.return_value
        client.get = AsyncMock(return_value=response)
        results = await fetch_polymarket_sport_markets(limit=10)

    assert results == []


@pytest.mark.asyncio
async def test_fetch_returns_expected_keys():
    from app.services.polymarket_sports_source import fetch_polymarket_sport_markets
    api_data = [_make_market("poly-1", "Will the Lakers beat the Celtics?", price=0.6)]
    response = Response(200, json=api_data)

    with patch("app.services.polymarket_sports_source.httpx.AsyncClient") as mock_client_cls:
        client = mock_client_cls.return_value.__aenter__.return_value
        client.get = AsyncMock(return_value=response)
        results = await fetch_polymarket_sport_markets(limit=10)

    assert len(results) == 1
    item = results[0]
    for key in ("contract_id", "question", "price", "no_price",
                "liquidity", "volume", "detected_sport",
                "detected_competition", "detected_teams", "detected_date"):
        assert key in item, f"missing key {key}"
    assert item["price"] == 0.6
