# backend/app/services/futures_market_source.py
"""Futures/championship market source — fetches multi-leg events from Kalshi.

Parallel to kalshi_sports_source.py (single-leg). Filters Kalshi events to
multi-leg championship series (KXNBACHAMP, KXMLBCHAMP, etc.) and extracts
one contract per team.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Kalshi futures series ticker prefixes — multi-leg championship events.
# Maps series prefix -> (competition, championship_type).
_KALSHI_FUTURES_SERIES_PREFIXES: dict[str, tuple[str, str]] = {
    "KXNBACHAMP": ("nba", "championship"),
    "KXMLBCHAMP": ("mlb", "world_series"),
    "KXNHLCHAMP": ("nhl", "stanley_cup"),
    "KXSOCCERWCS": ("wc", "world_cup"),
    "KXSOCCERUCL": ("ucl", "champions_league"),
}


def _extract_team_from_ticker(ticker: str) -> str:
    """Extract team code from a futures contract ticker.

    `KXNBACHAMP-LAL` -> "LAL". Returns "" if no dash present. If multiple
    dashes, takes the last segment (e.g., `KXSOCCERWCS-BRA` -> "BRA").
    """
    if not ticker or "-" not in ticker:
        return ""
    return ticker.rsplit("-", 1)[-1].strip()


def _parse_kalshi_price(last_price: float, yes_bid: float, yes_ask: float) -> float:
    """Parse Kalshi market price: last_price > midpoint > 0.5 fallback."""
    if last_price and last_price > 0:
        return float(last_price)
    if yes_bid > 0 and yes_ask > 0:
        return float((yes_bid + yes_ask) / 2)
    return 0.5


async def fetch_kalshi_futures_markets(limit: int = 200) -> list[dict[str, Any]]:
    """Fetch multi-leg championship events from Kalshi.

    Returns list of dicts with keys: event_ticker, title, competition,
    championship_type, contracts (list of {ticker, team, price, liquidity,
    volume}), source. Fail-closed: returns empty list on any error.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                settings.KALSHI_API_URL,
                params={
                    "status": "open",
                    "with_nested_markets": "true",
                    "limit": limit,
                },
            )
            response.raise_for_status()
            data = response.json()

        events = data.get("events", [])
        candidates: list[dict[str, Any]] = []

        for event in events:
            markets = event.get("markets", [])
            # Filter to multi-leg only (>=2 contracts = futures championship)
            if len(markets) < 2:
                continue

            series = (event.get("series_ticker") or "").upper()
            mapping = _KALSHI_FUTURES_SERIES_PREFIXES.get(series)
            if mapping is None:
                continue

            competition, championship_type = mapping
            event_ticker = event.get("event_ticker", "")
            title = event.get("title", "") or event_ticker

            contracts: list[dict[str, Any]] = []
            for market in markets:
                ticker = market.get("ticker", "")
                if not ticker:
                    continue
                team = _extract_team_from_ticker(ticker)
                if not team:
                    continue
                price = _parse_kalshi_price(
                    market.get("last_price_dollars", 0) or 0,
                    market.get("yes_bid_dollars", 0) or 0,
                    market.get("yes_ask_dollars", 0) or 0,
                )
                contracts.append({
                    "ticker": ticker,
                    "team": team,
                    "price": price,
                    "liquidity": float(market.get("liquidity_dollars", 0) or 0),
                    "volume": float(market.get("volume_fp", 0) or 0),
                })

            if not contracts:
                continue

            candidates.append({
                "event_ticker": event_ticker,
                "title": title,
                "competition": competition,
                "championship_type": championship_type,
                "contracts": contracts,
                "source": "kalshi",
            })

            # Polite rate limit
            await asyncio.sleep(settings.KALSHI_SPORTS_REQUEST_INTERVAL_SECONDS)

        return candidates

    except Exception:
        logger.warning("Failed to fetch Kalshi futures markets", exc_info=True)
        return []
