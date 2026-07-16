"""Tests for Kalshi sports market source — TDD RED phase."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.kalshi_sports_source import fetch_kalshi_sport_markets


def _make_kalshi_event(ticker="KXNBAGAME-25JAN01-LAL-BOS", title="Lakers vs Celtics Jan 1",
                       last_price=0.65, yes_bid=0.63, yes_ask=0.67,
                       liquidity=5000.0, volume=12000.0, status="open"):
    """Build a Kalshi event dict matching the API response shape."""
    return {
        "event_ticker": ticker,
        "series_ticker": ticker.split("-")[0],
        "title": title,
        "markets": [{
            "ticker": ticker,
            "title": title,
            "last_price_dollars": last_price,
            "yes_bid_dollars": yes_bid,
            "yes_ask_dollars": yes_ask,
            "liquidity_dollars": liquidity,
            "volume_fp": volume,
            "status": status,
            "close_time": "2025-01-01T23:59:59Z",
        }],
    }


@pytest.mark.asyncio
async def test_returns_empty_list_on_api_failure():
    """Fail-closed: API errors return empty list."""
    with patch("app.services.kalshi_sports_source.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=RuntimeError("network error"))
        mock_client_cls.return_value = mock_client

        result = await fetch_kalshi_sport_markets(limit=10)
        assert result == []


@pytest.mark.asyncio
async def test_filters_to_single_leg_events():
    """Multi-leg events (championships) are excluded."""
    multi_leg_event = _make_kalshi_event()
    multi_leg_event["markets"] = [
        {"ticker": "KXNBAGAME-25JAN01-LAL-BOS", "last_price_dollars": 0.65, "yes_bid_dollars": 0.63, "yes_ask_dollars": 0.67, "liquidity_dollars": 5000, "volume_fp": 12000, "status": "open"},
        {"ticker": "KXNBAGAME-25JAN01-LAL-BOS-NO", "last_price_dollars": 0.35, "yes_bid_dollars": 0.33, "yes_ask_dollars": 0.37, "liquidity_dollars": 5000, "volume_fp": 12000, "status": "open"},
    ]
    single_leg_event = _make_kalshi_event()

    with patch("app.services.kalshi_sports_source.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"events": [multi_leg_event, single_leg_event]}
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with patch("app.services.kalshi_sports_source.detect_sport_market", return_value={
            "is_sport": True, "sport": "basketball", "competition": "nba",
            "teams": ["Lakers", "Celtics"], "date": "2025-01-01",
        }):
            result = await fetch_kalshi_sport_markets(limit=10)

    assert len(result) == 1  # Only single-leg event


@pytest.mark.asyncio
async def test_parses_last_price_as_implied_prob():
    """last_price_dollars is used as the YES implied_prob."""
    event = _make_kalshi_event(last_price=0.72)

    with patch("app.services.kalshi_sports_source.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"events": [event]}
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with patch("app.services.kalshi_sports_source.detect_sport_market", return_value={
            "is_sport": True, "sport": "basketball", "competition": "nba",
            "teams": ["Lakers", "Celtics"], "date": "2025-01-01",
        }):
            result = await fetch_kalshi_sport_markets(limit=10)

    assert len(result) == 1
    assert result[0]["price"] == pytest.approx(0.72)
    assert result[0]["no_price"] == pytest.approx(0.28)


@pytest.mark.asyncio
async def test_falls_back_to_bid_ask_midpoint():
    """When last_price is 0 or missing, use (yes_bid + yes_ask) / 2."""
    event = _make_kalshi_event(last_price=0.0, yes_bid=0.60, yes_ask=0.64)

    with patch("app.services.kalshi_sports_source.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"events": [event]}
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with patch("app.services.kalshi_sports_source.detect_sport_market", return_value={
            "is_sport": True, "sport": "basketball", "competition": "nba",
            "teams": ["Lakers", "Celtics"], "date": "2025-01-01",
        }):
            result = await fetch_kalshi_sport_markets(limit=10)

    assert len(result) == 1
    assert result[0]["price"] == pytest.approx(0.62)  # (0.60 + 0.64) / 2


@pytest.mark.asyncio
async def test_output_includes_source_kalshi():
    """Output dicts include source='kalshi'."""
    event = _make_kalshi_event()

    with patch("app.services.kalshi_sports_source.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"events": [event]}
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with patch("app.services.kalshi_sports_source.detect_sport_market", return_value={
            "is_sport": True, "sport": "basketball", "competition": "nba",
            "teams": ["Lakers", "Celtics"], "date": "2025-01-01",
        }):
            result = await fetch_kalshi_sport_markets(limit=10)

    assert len(result) == 1
    assert result[0]["source"] == "kalshi"
    assert result[0]["contract_id"] == "KXNBAGAME-25JAN01-LAL-BOS"
