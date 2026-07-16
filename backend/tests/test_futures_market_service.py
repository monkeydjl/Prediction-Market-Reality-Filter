# backend/tests/test_futures_market_service.py
"""Tests for FuturesMarketService — TDD RED phase."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from app.kernel.futures_market_service import FuturesMarketService


@pytest.fixture
def service():
    return FuturesMarketService()


@pytest.fixture
def sample_candidate():
    return {
        "event_ticker": "KXNBACHAMP-25JAN",
        "title": "NBA Championship 2024-25",
        "competition": "nba",
        "championship_type": "championship",
        "contracts": [
            {"ticker": "KXNBACHAMP-LAL", "team": "LAL", "price": 0.18, "liquidity": 50000, "volume": 12000},
            {"ticker": "KXNBACHAMP-BOS", "team": "BOS", "price": 0.32, "liquidity": 80000, "volume": 25000},
        ],
        "source": "kalshi",
    }


def test_parse_season_from_title_extracts_year_range(service):
    assert service._parse_season_from_title("NBA Championship 2024-25") == "2024-25"
    assert service._parse_season_from_title("Stanley Cup 2023-24") == "2023-24"
    assert service._parse_season_from_title("World Series 2024") == "2024"
    assert service._parse_season_from_title("No season here") == ""
    assert service._parse_season_from_title("") == ""


@pytest.mark.asyncio
async def test_link_futures_market_upserts_one_link_per_contract(service, sample_candidate):
    mock_store = MagicMock()
    mock_store.upsert_link = MagicMock(side_effect=[
        {"id": 1, "team": "LAL"},
        {"id": 2, "team": "BOS"},
    ])
    service._store = mock_store

    result = await service.link_futures_market(sample_candidate)

    assert result["links"] == 2
    assert result["errors"] == 0
    assert mock_store.upsert_link.call_count == 2
    # First call: LAL contract
    first_call_kwargs = mock_store.upsert_link.call_args_list[0].kwargs
    assert first_call_kwargs["team"] == "LAL"
    assert first_call_kwargs["contract_id"] == "KXNBACHAMP-LAL"
    assert first_call_kwargs["competition"] == "nba"
    assert first_call_kwargs["season"] == "2024-25"
    assert first_call_kwargs["verified"] is True


@pytest.mark.asyncio
async def test_discover_and_link_returns_counts(service, sample_candidate):
    mock_store = MagicMock()
    mock_store.upsert_link = MagicMock(return_value={"id": 1})
    service._store = mock_store

    with patch(
        "app.kernel.futures_market_service.fetch_kalshi_futures_markets",
        AsyncMock(return_value=[sample_candidate]),
    ):
        result = await service.discover_and_link()

    assert result["discovered"] == 1
    assert result["linked"] == 2  # 2 contracts in the candidate
    assert result["errors"] == 0


@pytest.mark.asyncio
async def test_capture_snapshots_stores_one_per_verified_link(service):
    mock_store = MagicMock()
    mock_store.get_verified_links = MagicMock(return_value=[
        {"id": 1, "contract_id": "KXNBACHAMP-LAL", "competition": "nba", "season": "2024-25", "team": "LAL", "source": "kalshi"},
        {"id": 2, "contract_id": "KXNBACHAMP-BOS", "competition": "nba", "season": "2024-25", "team": "BOS", "source": "kalshi"},
    ])
    mock_store.append_snapshot = MagicMock(return_value={"id": 100})
    service._store = mock_store

    with patch(
        "app.kernel.futures_market_service.fetch_kalshi_futures_markets",
        AsyncMock(return_value=[
            {
                "event_ticker": "KXNBACHAMP-25JAN",
                "title": "NBA Championship 2024-25",
                "competition": "nba",
                "championship_type": "championship",
                "contracts": [
                    {"ticker": "KXNBACHAMP-LAL", "team": "LAL", "price": 0.20, "liquidity": 51000, "volume": 12100},
                    {"ticker": "KXNBACHAMP-BOS", "team": "BOS", "price": 0.30, "liquidity": 79000, "volume": 24900},
                ],
                "source": "kalshi",
            }
        ]),
    ):
        result = await service.capture_snapshots()

    assert result["captured"] == 2
    assert result["errors"] == 0
    assert mock_store.append_snapshot.call_count == 2
    # Verify LAL snapshot was stored with updated price
    lal_call = mock_store.append_snapshot.call_args_list[0].kwargs
    assert lal_call["link_id"] == 1
    assert lal_call["implied_prob"] == 0.20
