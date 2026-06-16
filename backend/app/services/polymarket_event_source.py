"""polymarket_event_source.py
========================
Event-source adapter that wraps Polymarket as a source of candidate events for
discovery. Where the evidence adapters (rss / official / sec / economic) produce
articles, an event source produces candidate events: the questions to analyze,
each with a baseline probability and the source descriptor to attach to the
resulting event record.

Thin by design: it composes the low-level Polymarket client
(`polymarket_service.fetch_markets`) with `market_filter_service.filter_markets`
and normalizes each market into the candidate-event shape. No evidence,
scoring, or analysis logic lives here.

Candidate-event shape consumed by `event_intelligence_service.discover_events`:
    {
        "question": str,
        "baseline_probability": float,    # 0-100, before evidence
        "volume": float,
        "liquidity": float,
        "source": {                        # attached to the event record
            "type": "prediction_market",
            "platform": "Polymarket",
            "source_id": str,
            "question": str,
            "baseline_probability": float,
            "liquidity": float,
            "volume": float,
        },
    }
"""

from typing import Any

from app.utils.market_utils import safe_float


async def fetch_candidate_events(limit: int = 10) -> list[dict[str, Any]]:
    """Fetch and normalize candidate events from Polymarket.

    `limit` is the target number of events; more candidates are fetched and then
    filtered down, mirroring the sizing previously inlined in discover_events.
    """
    from app.services.market_filter_service import filter_markets
    from app.services.polymarket_service import fetch_markets

    candidate_limit = min(max(limit * 5, limit), 100)
    candidate_markets = await fetch_markets(limit=candidate_limit)
    markets = filter_markets(
        candidate_markets,
        max_markets=min(max(limit * 3, limit), 30),
    )
    return [_to_candidate_event(market) for market in markets]


def _to_candidate_event(market) -> dict[str, Any]:
    baseline = safe_float(market.yes_price, 0.5) * 100
    volume = safe_float(market.volume, 0.0)
    liquidity = safe_float(market.liquidity, 0.0)
    url = f"https://polymarket.com/event/{market.slug}" if market.slug else ""
    return {
        "question": market.question,
        "baseline_probability": baseline,
        "volume": volume,
        "liquidity": liquidity,
        "source": {
            "type": "prediction_market",
            "platform": "Polymarket",
            "source_id": market.id,
            "question": market.question,
            "baseline_probability": round(baseline, 2),
            "liquidity": liquidity,
            "volume": volume,
            "url": url,
        },
    }
