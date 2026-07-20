# backend/tests/test_futures_market_source.py
"""Tests for futures_market_source — TDD RED phase."""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.futures_market_source import (
    fetch_kalshi_futures_markets,
    _KALSHI_FUTURES_SERIES_PREFIXES,
    _extract_team_from_ticker,
    _parse_kalshi_price,
)


@pytest.fixture
def sample_kalshi_response():
    """Kalshi event with multiple markets (multi-leg futures event)."""
    return {
        "events": [
            {
                "series_ticker": "KXNBACHAMP",
                "event_ticker": "KXNBACHAMP-25JAN",
                "title": "NBA Championship 2024-25",
                "markets": [
                    {
                        "ticker": "KXNBACHAMP-LAL",
                        "title": "Lakers win NBA Championship",
                        "last_price_dollars": 0.18,
                        "yes_bid_dollars": 0.17,
                        "yes_ask_dollars": 0.19,
                        "liquidity_dollars": 50000,
                        "volume_fp": 12000,
                    },
                    {
                        "ticker": "KXNBACHAMP-BOS",
                        "title": "Celtics win NBA Championship",
                        "last_price_dollars": 0.32,
                        "yes_bid_dollars": 0.30,
                        "yes_ask_dollars": 0.34,
                        "liquidity_dollars": 80000,
                        "volume_fp": 25000,
                    },
                ],
            },
            {
                # Single-leg event — must be filtered out (len(markets) == 1)
                "series_ticker": "KXNBAGAME",
                "event_ticker": "KXNBAGAME-25JAN01-LAL-BOS",
                "title": "Lakers vs Celtics Jan 1",
                "markets": [
                    {"ticker": "KXNBAGAME-LAL", "last_price_dollars": 0.55},
                ],
            },
            {
                # Non-sports series — must be filtered out
                "series_ticker": "KXPRES",
                "event_ticker": "KXPRES-2024",
                "title": "Presidential Election",
                "markets": [
                    {"ticker": "KXPRES-DEM", "last_price_dollars": 0.50},
                    {"ticker": "KXPRES-REP", "last_price_dollars": 0.50},
                ],
            },
        ]
    }


@pytest.mark.asyncio
async def test_fetch_kalshi_futures_markets_filters_to_multi_leg_sports(sample_kalshi_response):
    with patch("app.services.futures_market_source.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock()
        instance.get.return_value.raise_for_status = lambda: None
        instance.get.return_value.json = lambda: sample_kalshi_response
        result = await fetch_kalshi_futures_markets(limit=10)
    # Only the NBA Championship futures event qualifies
    assert len(result) == 1
    event = result[0]
    assert event["event_ticker"] == "KXNBACHAMP-25JAN"
    assert event["competition"] == "nba"
    assert event["championship_type"] == "championship"
    assert event["source"] == "kalshi"
    assert len(event["contracts"]) == 2


@pytest.mark.asyncio
async def test_fetch_kalshi_futures_markets_returns_empty_on_error():
    with patch("app.services.futures_market_source.httpx.AsyncClient") as MockClient:
        MockClient.side_effect = RuntimeError("network down")
        result = await fetch_kalshi_futures_markets(limit=10)
    assert result == []


def test_extract_team_from_ticker():
    assert _extract_team_from_ticker("KXNBACHAMP-LAL") == "LAL"
    assert _extract_team_from_ticker("KXMLBCHAMP-NYY") == "NYY"
    assert _extract_team_from_ticker("KXNHLCHAMP-EDM") == "EDM"
    # No dash — return empty string
    assert _extract_team_from_ticker("KXNBACHAMP") == ""
    # Multiple dashes — take last segment
    assert _extract_team_from_ticker("KXSOCCERWCS-BRA") == "BRA"


def test_parse_kalshi_price_prefers_last_price():
    # last_price > 0 wins
    assert _parse_kalshi_price(0.18, 0.17, 0.19) == 0.18
    # Fall back to midpoint when last_price is 0/None
    assert _parse_kalshi_price(0, 0.30, 0.34) == 0.32
    # Return None when all missing (callers should skip, not inject 0.5)
    assert _parse_kalshi_price(0, 0, 0) is None


def test_kalshi_futures_series_prefixes_covers_sports():
    # Baseline + expanded series (P2-SB5)
    assert "KXNBACHAMP" in _KALSHI_FUTURES_SERIES_PREFIXES
    assert "KXMLBCHAMP" in _KALSHI_FUTURES_SERIES_PREFIXES
    assert "KXNHLCHAMP" in _KALSHI_FUTURES_SERIES_PREFIXES
    assert "KXSOCCERWCS" in _KALSHI_FUTURES_SERIES_PREFIXES
    assert "KXSOCCERUCL" in _KALSHI_FUTURES_SERIES_PREFIXES
    assert "KXNFLCHAMP" in _KALSHI_FUTURES_SERIES_PREFIXES
    assert "KXEPLCHAMP" in _KALSHI_FUTURES_SERIES_PREFIXES
    assert "KXNCAAMBCHAMP" in _KALSHI_FUTURES_SERIES_PREFIXES
    assert _KALSHI_FUTURES_SERIES_PREFIXES["KXNBACHAMP"] == ("nba", "championship")
    assert _KALSHI_FUTURES_SERIES_PREFIXES["KXMLBCHAMP"] == ("mlb", "world_series")
    assert _KALSHI_FUTURES_SERIES_PREFIXES["KXNHLCHAMP"] == ("nhl", "stanley_cup")
    assert _KALSHI_FUTURES_SERIES_PREFIXES["KXSOCCERWCS"] == ("wc", "world_cup")
    assert _KALSHI_FUTURES_SERIES_PREFIXES["KXSOCCERUCL"] == ("ucl", "champions_league")
    assert _KALSHI_FUTURES_SERIES_PREFIXES["KXNFLCHAMP"] == ("nfl", "super_bowl")


def test_match_futures_series_longest_prefix():
    assert match_futures_series("KXNBACHAMP") == ("nba", "championship")
    assert match_futures_series("KXNBACHAMP-2025") == ("nba", "championship")
    assert match_futures_series("KXSUPERBOWL") == ("nfl", "super_bowl")
    assert match_futures_series("KXPRES") is None


def test_list_known_futures_series_nonempty():
    series = list_known_futures_series()
    assert len(series) >= 8
    prefixes = {s["series_prefix"] for s in series}
    assert "KXNBACHAMP" in prefixes
    assert "KXNFLCHAMP" in prefixes


def test_multi_leg_integrity_ok_book():
    contracts = [
        {"team": "LAL", "price": 0.22},
        {"team": "BOS", "price": 0.20},
        {"team": "DEN", "price": 0.18},
        {"team": "OKC", "price": 0.45},
    ]
    result = multi_leg_integrity(contracts)
    assert result["status"] == "ok"
    assert result["leg_count"] == 4
    assert result["sum_implied_prob"] == pytest.approx(1.05, abs=0.01)
    assert result["issues"] == []


def test_multi_leg_integrity_flags_incomplete_and_dupes():
    thin = multi_leg_integrity([{"team": "LAL", "price": 0.2}])
    assert thin["status"] == "incomplete"
    assert "too_few_legs" in thin["issues"]

    dupes = multi_leg_integrity(
        [
            {"team": "LAL", "price": 0.3},
            {"team": "LAL", "price": 0.25},
            {"team": "BOS", "price": 0.4},
        ]
    )
    assert "duplicate_teams" in dupes["issues"]
    assert "LAL" in dupes["duplicate_teams"]
