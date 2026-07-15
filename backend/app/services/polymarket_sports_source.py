"""Polymarket sports market collection source.

Exists in PARALLEL with polymarket_event_source (which is NOT modified).
Fetches from the Polymarket gamma API, then filters to single-match sport
markets via the sport_market_detector. Returns a list of candidate dicts
consumed by the SportMarketBridgeService.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.services.sport_market_detector import detect_sport_market

logger = logging.getLogger(__name__)

POLYMARKET_API = "https://gamma-api.polymarket.com/markets"


def _parse_price(prices_field: str | None, index: int) -> float | None:
    """Parse outcomePrices JSON string -> float at index."""
    if not prices_field:
        return None
    try:
        prices = json.loads(prices_field)
        if isinstance(prices, list) and len(prices) > index:
            return float(prices[index])
    except (ValueError, TypeError):
        pass
    return None


async def fetch_polymarket_sport_markets(limit: int = 100) -> list[dict[str, Any]]:
    """Fetch Polymarket markets and filter to single-match sport markets.

    Returns list of dicts with keys: contract_id, question, price, no_price,
    liquidity, volume, detected_sport, detected_competition, detected_teams,
    detected_date.
    """
    results: list[dict[str, Any]] = []
    target = max(limit, 1)
    page_size = 100
    offset = 0

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            while len(results) < target:
                params = {
                    "limit": str(page_size),
                    "offset": str(offset),
                    "closed": "false",
                    "archived": "false",
                    "order": "volume",
                    "ascending": "false",
                }
                response = await client.get(POLYMARKET_API, params=params)
                if response.status_code != 200:
                    logger.warning(
                        "Polymarket sports source got %d: %s",
                        response.status_code, response.text[:200],
                    )
                    return results
                data = response.json()
                if not data:
                    break

                for item in data:
                    if len(results) >= target:
                        break
                    question = item.get("question", "")
                    contract_id = str(item.get("id", ""))
                    if not question or not contract_id:
                        continue
                    info = detect_sport_market(
                        contract_id=contract_id,
                        question=question,
                        source="polymarket",
                    )
                    if info is None:
                        continue
                    results.append({
                        "contract_id": contract_id,
                        "question": question,
                        "price": _parse_price(item.get("outcomePrices"), 0),
                        "no_price": _parse_price(item.get("outcomePrices"), 1),
                        "liquidity": item.get("liquidity"),
                        "volume": item.get("volume"),
                        "detected_sport": info.detected_sport,
                        "detected_competition": info.detected_competition,
                        "detected_teams": info.detected_teams,
                        "detected_date": info.detected_date.isoformat() if info.detected_date else None,
                    })
                # Stop when the API returns a partial page (no more data).
                if len(data) < page_size:
                    break
                offset += page_size
    except Exception as e:
        logger.debug("Polymarket sports source fetch error: %s", e)
        return results

    return results
