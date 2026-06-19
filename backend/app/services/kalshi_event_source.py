"""kalshi_event_source.py
======================
Event-source adapter that wraps Kalshi as a third source of candidate events for
discovery, alongside polymarket_event_source and manifold_event_source.

Kalshi organizes markets under multi-value "events"; the clean, human-readable
question lives on the event (e.g. "Will the world pass 2 degrees Celsius..."),
not on the individual market legs (whose titles are concatenated). So this
adapter reads the `/events` endpoint with nested markets and keeps only
single-leg events - those are clean binary YES/NO questions with one probability.
Multi-leg events are multi-outcome and do not fit the binary baseline model; they
are skipped (left for future multi-outcome handling).

Kalshi's elections host serves all categories and needs no API key for read-only
market data. Prices are in dollars (0.00-1.00 = 0-100% implied probability).

Thin and defensive: a missing URL or an unreachable / malformed API yields an
empty list rather than raising, so a down source never breaks discovery.

Candidate-event shape (identical to the other event sources) consumed by
event_intelligence_service.discover_events:
    {
        "question": str,
        "baseline_probability": float,    # 0-100, before evidence
        "volume": float,
        "liquidity": float,
        "source": {
            "type": "prediction_market",
            "platform": "Kalshi",
            "source_id": str,
            "question": str,
            "baseline_probability": float,
            "liquidity": float,
            "volume": float,
        },
    }
"""

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.utils.market_utils import safe_float


logger = logging.getLogger(__name__)

_SETTLED_STATUSES = {"settled", "finalized", "closed", "determined"}


async def fetch_candidate_events(limit: int = 10) -> list[dict[str, Any]]:
    """Fetch and normalize candidate events from Kalshi.

    Returns at most `limit` candidates (single-leg binary events). Returns an
    empty list when Kalshi is not configured or unreachable, so a failing source
    degrades gracefully instead of breaking discovery.
    """
    try:
        raw_events = await _fetch_raw_events(limit)
    except Exception as exc:
        logger.warning("Kalshi event source failed: %s", exc)
        return []
    candidates = [
        _to_candidate_event(event)
        for event in raw_events
        if _is_eligible(event)
    ]
    return candidates[:limit]


async def _fetch_raw_events(limit: int) -> list[dict[str, Any]]:
    url = settings.KALSHI_API_URL
    if not url:
        return []
    # Only ~1/3 of open events are single-leg, so over-fetch to find `limit`.
    params = {
        "status": "open",
        "with_nested_markets": "true",
        "limit": str(min(max(limit * 5, limit), 200)),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    events = data.get("events") if isinstance(data, dict) else None
    return events if isinstance(events, list) else []


def _is_eligible(event: Any) -> bool:
    if not isinstance(event, dict):
        return False
    if not str(event.get("title", "") or "").strip():
        return False
    markets = event.get("markets")
    if not isinstance(markets, list) or len(markets) != 1:
        return False
    market = markets[0]
    if not isinstance(market, dict):
        return False
    if str(market.get("status", "")).lower() in _SETTLED_STATUSES:
        return False
    if market.get("result"):
        return False
    return True


def _to_candidate_event(event: dict[str, Any]) -> dict[str, Any]:
    question = str(event.get("title", "") or "").strip()
    market = event["markets"][0]
    baseline = _baseline_pct(market)
    volume = safe_float(market.get("volume_fp"), 0.0)
    liquidity = safe_float(market.get("liquidity_dollars"), 0.0)
    ticker = str(event.get("event_ticker", "") or "")
    url = f"https://kalshi.com/markets/{ticker.lower()}" if ticker else ""
    return {
        "question": question,
        "baseline_probability": baseline,
        "volume": volume,
        "liquidity": liquidity,
        "source": {
            "type": "prediction_market",
            "platform": settings.KALSHI_SOURCE_NAME,
            "source_id": ticker,
            "question": question,
            "baseline_probability": round(baseline, 2),
            "liquidity": liquidity,
            "volume": volume,
            "url": url,
        },
    }


def _baseline_pct(market: dict[str, Any]) -> float:
    """Implied YES probability (0-100) from Kalshi dollar prices.

    Prefer the last trade price; fall back to the yes bid/ask midpoint; default
    to 50 when the market has no price signal yet.
    """
    last = safe_float(market.get("last_price_dollars"), 0.0)
    if last > 0:
        return last * 100
    bid = safe_float(market.get("yes_bid_dollars"), 0.0)
    ask = safe_float(market.get("yes_ask_dollars"), 0.0)
    if bid > 0 or ask > 0:
        return (bid + ask) / 2 * 100
    return 50.0


async def fetch_resolved_markets(limit: int = 200) -> list[dict[str, Any]]:
    """Fetch settled Kalshi events for event auto-resolution.

    Returns [{id, question, actual_outcome}] from single-leg settled events: the
    market result yes->100, no->0; anything else is skipped. `id` is the
    event_ticker (same scheme as the candidate source's source_id) so the
    event->market identity holds through to resolve. Empty list when not
    configured or unreachable (graceful), so a down source never breaks
    auto-resolve.
    """
    try:
        raw = await _fetch_raw_resolved(limit)
    except Exception as exc:
        logger.warning("Kalshi resolved fetch failed: %s", exc)
        return []
    resolved: list[dict[str, Any]] = []
    for event in raw:
        if not isinstance(event, dict):
            continue
        question = str(event.get("title", "") or "").strip()
        ticker = str(event.get("event_ticker", "") or "")
        markets = event.get("markets")
        if not question or not isinstance(markets, list) or len(markets) != 1:
            continue
        result = str((markets[0] or {}).get("result", "") or "").lower()
        if result == "yes":
            resolved.append({"id": ticker, "question": question, "actual_outcome": 100.0})
        elif result == "no":
            resolved.append({"id": ticker, "question": question, "actual_outcome": 0.0})
    return resolved


async def _fetch_raw_resolved(limit: int) -> list[dict[str, Any]]:
    url = settings.KALSHI_API_URL
    if not url:
        return []
    params = {
        "status": "settled",
        "with_nested_markets": "true",
        "limit": str(min(max(limit, 1), 200)),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    events = data.get("events") if isinstance(data, dict) else None
    return events if isinstance(events, list) else []
