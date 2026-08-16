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

from app.models.market import MarketModel
from app.utils.market_utils import safe_float


async def fetch_candidate_events(limit: int = 10) -> list[dict[str, Any]]:
    """Fetch and normalize candidate events from Polymarket.

    `limit` is the target number of events; more candidates are fetched and then
    filtered down, mirroring the sizing previously inlined in discover_events.
    """
    from app.services.market_filter_service import filter_markets
    from app.services.polymarket_service import fetch_markets

    candidate_limit = min(max(limit * 5, limit), 500)
    candidate_markets = await fetch_markets(limit=candidate_limit)
    markets = filter_markets(
        candidate_markets,
        max_markets=min(max(limit * 3, limit), 200),
    )
    return [_to_candidate_event(market) for market in markets]


async def fetch_crypto_candidate_events(limit: int = 10) -> list[dict[str, Any]]:
    """Fetch and normalize crypto-only candidate events from Polymarket.

    The default `fetch_candidate_events` ranks by volume, so geopolitics crowds
    crypto out of the top-N. This runs a crypto-only fetch (gamma-api tag filter
    + crypto-keyword gate) so crypto markets reach the candidate pool. Shape is
    identical to `fetch_candidate_events` and goes through the same filter +
    normalization, so discovery treats it as just another candidate source
    (dedupe keeps cross-source duplicates out).

    Guarded by settings.POLYMARKET_CRYPTO_FETCH_ENABLED at the call site
    (`_collect_candidate_events`); this function itself does not check the flag
    so it stays a pure fetch+normalize and is trivially testable.
    """
    from app.services.market_filter_service import filter_markets
    from app.services.polymarket_service import fetch_markets

    candidate_limit = min(max(limit * 5, limit), 500)
    candidate_markets = await fetch_markets(limit=candidate_limit, crypto_only=True)
    markets = filter_markets(
        candidate_markets,
        max_markets=min(max(limit * 3, limit), 200),
    )
    return [_to_candidate_event(market) for market in markets]


def _to_candidate_event(market: MarketModel) -> dict[str, Any]:
    baseline = safe_float(market.yes_price, 0.5) * 100
    volume = safe_float(market.volume, 0.0)
    liquidity = safe_float(market.liquidity, 0.0)
    # Prefer event_slug over market slug for the URL: gamma-api /markets returns
    # per-market slugs that do not match https://polymarket.com/event/{slug}.
    ref_slug = market.event_slug or market.slug
    url = f"https://polymarket.com/event/{ref_slug}" if ref_slug else ""
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
            "closed": market.closed,
            "end_date": market.end_date or "",
        },
    }
