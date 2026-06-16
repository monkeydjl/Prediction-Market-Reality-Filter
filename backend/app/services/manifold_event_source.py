"""manifold_event_source.py
========================
Event-source adapter that wraps Manifold Markets as a second source of candidate
events for discovery, alongside polymarket_event_source. Where the evidence
adapters (rss / official / sec / economic) produce articles, an event source
produces candidate events: the questions to analyze, each with a baseline
probability and the source descriptor to attach to the resulting event record.

Thin by design: fetch open binary markets from the Manifold public API
(no key required), filter to eligible markets, and normalize each into the
candidate-event shape. No evidence, scoring, or analysis logic lives here.

Graceful by design: a missing URL or an unreachable / malformed API yields an
empty list rather than raising, so a down source never breaks discovery (the
multi-source composition in event_intelligence_service also isolates failures).

Candidate-event shape (identical to polymarket_event_source) consumed by
event_intelligence_service.discover_events:
    {
        "question": str,
        "baseline_probability": float,    # 0-100, before evidence
        "volume": float,
        "liquidity": float,
        "source": {
            "type": "prediction_market",
            "platform": "Manifold",
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


async def fetch_candidate_events(limit: int = 10) -> list[dict[str, Any]]:
    """Fetch and normalize candidate events from Manifold.

    Returns at most `limit` candidates. Returns an empty list when Manifold is
    not configured or unreachable, so a failing source degrades gracefully
    instead of breaking discovery.
    """
    try:
        raw_markets = await _fetch_raw_markets(limit)
    except Exception as exc:
        logger.warning("Manifold event source failed: %s", exc)
        return []
    return [
        _to_candidate_event(market)
        for market in raw_markets
        if _is_eligible(market)
    ]


async def _fetch_raw_markets(limit: int) -> list[dict[str, Any]]:
    url = settings.MANIFOLD_API_URL
    if not url:
        return []
    params = {
        "term": "",
        "sort": "score",
        "filter": "open",
        "contractType": "BINARY",
        "limit": str(min(max(limit, 1), 100)),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    return data if isinstance(data, list) else []


def _is_eligible(market: Any) -> bool:
    if not isinstance(market, dict):
        return False
    if str(market.get("outcomeType", "")).upper() != "BINARY":
        return False
    if market.get("isResolved"):
        return False
    if not str(market.get("question", "") or "").strip():
        return False
    return market.get("probability") is not None


def _to_candidate_event(market: dict[str, Any]) -> dict[str, Any]:
    question = str(market.get("question", "") or "").strip()
    baseline = safe_float(market.get("probability"), 0.5) * 100
    volume = safe_float(market.get("volume"), 0.0)
    liquidity = safe_float(market.get("totalLiquidity"), 0.0)
    return {
        "question": question,
        "baseline_probability": baseline,
        "volume": volume,
        "liquidity": liquidity,
        "source": {
            "type": "prediction_market",
            "platform": settings.MANIFOLD_SOURCE_NAME,
            "source_id": str(market.get("id", "") or ""),
            "question": question,
            "baseline_probability": round(baseline, 2),
            "liquidity": liquidity,
            "volume": volume,
            "url": str(market.get("url", "") or ""),
        },
    }


async def fetch_resolved_markets(limit: int = 200) -> list[dict[str, Any]]:
    """Fetch settled Manifold markets for event auto-resolution.

    Returns [{question, actual_outcome}] (0-100): YES->100, NO->0, MKT (a
    probabilistic resolution) -> resolutionProbability*100. CANCEL / unresolved
    are skipped. Empty list when not configured or unreachable (graceful), so a
    down source never breaks auto-resolve.
    """
    try:
        raw = await _fetch_raw_resolved(limit)
    except Exception as exc:
        logger.warning("Manifold resolved fetch failed: %s", exc)
        return []
    resolved: list[dict[str, Any]] = []
    for market in raw:
        if not isinstance(market, dict):
            continue
        question = str(market.get("question", "") or "").strip()
        if not question:
            continue
        outcome = _resolved_outcome(market)
        if outcome is not None:
            resolved.append({"question": question, "actual_outcome": outcome})
    return resolved


async def _fetch_raw_resolved(limit: int) -> list[dict[str, Any]]:
    url = settings.MANIFOLD_API_URL
    if not url:
        return []
    params = {
        "term": "",
        "filter": "resolved",
        "contractType": "BINARY",
        "limit": str(min(max(limit, 1), 1000)),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    return data if isinstance(data, list) else []


def _resolved_outcome(market: dict[str, Any]) -> float | None:
    """Map a resolved Manifold market to a 0-100 outcome, or None to skip."""
    if not market.get("isResolved"):
        return None
    resolution = str(market.get("resolution", "") or "").upper()
    if resolution == "YES":
        return 100.0
    if resolution == "NO":
        return 0.0
    if resolution == "MKT":
        prob = market.get("resolutionProbability")
        if prob is None:
            return None
        return max(0.0, min(100.0, safe_float(prob, 0.0) * 100))
    return None  # CANCEL / unknown
