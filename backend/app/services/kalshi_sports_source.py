"""Kalshi sports market source — fetches sports markets from Kalshi API.

Parallel to polymarket_sports_source.py. Uses Kalshi's public read-only API
(no auth needed). Filters to single-leg binary events only.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from app.core.config import settings
from app.services.sport_market_detector import detect_sport_market

logger = logging.getLogger(__name__)

# Kalshi sports series ticker prefixes
_KALSHI_SPORTS_SERIES_PREFIXES = (
    "KXNBAGAME", "KXMLBGAME", "KXNHLGAME",
    "KXSOCCEREPL", "KXSOCCERUCL", "KXSOCCERWCS",
    "KXNFL", "KXNBAGAME",
)


async def fetch_kalshi_sport_markets(limit: int = 100) -> list[dict]:
    """Fetch sports markets from Kalshi, filtered to single-leg binary events.

    Returns list of dicts compatible with SportMarketBridgeService.link_kalshi_market.
    Fail-closed: returns empty list on any error.
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
        candidates: list[dict] = []

        for event in events:
            markets = event.get("markets", [])
            # Filter to single-leg binary events only
            if len(markets) != 1:
                continue

            market = markets[0]
            ticker = market.get("ticker", "")
            series = event.get("series_ticker", "")

            # Filter to sports series
            if not series.upper().startswith(_KALSHI_SPORTS_SERIES_PREFIXES):
                continue

            # Parse price
            last_price = market.get("last_price_dollars", 0) or 0
            yes_bid = market.get("yes_bid_dollars", 0) or 0
            yes_ask = market.get("yes_ask_dollars", 0) or 0

            if last_price > 0:
                price = last_price
            elif yes_bid > 0 and yes_ask > 0:
                price = (yes_bid + yes_ask) / 2
            else:
                price = 0.5

            no_price = 1.0 - price

            # Use sport_market_detector to extract sport/competition/teams.
            # It returns a SportMarketInfo dataclass (or None for
            # futures/non-sport markets) — read attributes, not dict keys, and
            # match polymarket_sports_source's candidate shape.
            detected = detect_sport_market(
                contract_id=ticker,
                question=market.get("title", "") or event.get("title", ""),
                source="kalshi",
            )

            if detected is None:
                continue

            candidates.append({
                "contract_id": ticker,
                "question": market.get("title", "") or event.get("title", ""),
                "price": price,
                "no_price": no_price,
                "liquidity": float(market.get("liquidity_dollars", 0) or 0),
                "volume": float(market.get("volume_fp", 0) or 0),
                "source": "kalshi",
                "detected_sport": detected.detected_sport,
                "detected_competition": detected.detected_competition,
                "detected_teams": detected.detected_teams,
                # _resolve_match_id compares this against
                # kickoff_utc.strftime("%Y-%m-%d"), so emit an ISO date string.
                "detected_date": (
                    detected.detected_date.isoformat()
                    if detected.detected_date
                    else None
                ),
            })

            # Polite rate limit
            await asyncio.sleep(settings.KALSHI_SPORTS_REQUEST_INTERVAL_SECONDS)

        return candidates

    except Exception:
        logger.warning("Failed to fetch Kalshi sport markets", exc_info=True)
        return []
