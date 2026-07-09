"""metacus_event_source.py
========================
Event-source adapter that wraps Metaculus as an optional source of candidate
events for discovery, alongside polymarket / kalshi / open-web sources.

Metaculus is a community-forecasting platform (long-horizon science / tech /
AI / policy questions). Unlike the prediction-market sources it has no prices
or liquidity: the baseline probability comes from the community forecast, and
the "volume" proxy is the number of forecasters (a participation signal).
Binary questions map cleanly to the candidate-event shape; numeric, date, and
multiple-choice questions are skipped (they do not fit the binary baseline
model, mirroring how kalshi_event_source skips multi-leg events).

Authenticated: Metaculus requires an API token. The source auto-disables
when ``settings.METACULUS_API_TOKEN`` is empty, so a fresh checkout never
makes authenticated network calls — only operators who have explicitly set
the token in ``.env`` get the source.

Thin and defensive: a missing URL / token, an unreachable API, or a malformed
response yields an empty list rather than raising, so a down source never
breaks discovery (the multi-source composition in event_intelligence_service
also isolates failures).

Candidate-event shape (identical to the other event sources) consumed by
``event_intelligence_service.discover_events``::

    {
        "question": str,
        "baseline_probability": float,    # 0-100, before evidence
        "volume": float,                  # forecasters count proxy
        "liquidity": float,               # 0.0 (no market liquidity)
        "source": {
            "type": "prediction_question",   # distinct from prediction_market
            "platform": "Metaculus",
            "source_id": str,
            "question": str,
            "baseline_probability": float,
            "liquidity": float,
            "volume": float,
            "url": str,
        },
    }
"""

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.utils.failure_policy import fail_closed_empty_list
from app.utils.market_utils import safe_float, safe_int


logger = logging.getLogger(__name__)


async def fetch_candidate_events(limit: int = 10) -> list[dict[str, Any]]:
    """Fetch and normalize candidate events from Metaculus.

    Returns at most ``limit`` candidates (binary open questions). Returns an
    empty list when Metaculus is not configured (no token / no URL) or
    unreachable, so a failing source degrades gracefully instead of breaking
    discovery.
    """
    if not settings.METACULUS_API_TOKEN:
        # No token = source intentionally disabled. Not an error.
        return []
    try:
        raw_posts = await _fetch_raw_posts(limit)
    except Exception as exc:
        return fail_closed_empty_list(
            logger,
            "metaculus_candidates",
            exc,
            context={"limit": limit},
        )
    candidates = [
        _to_candidate_event(post)
        for post in raw_posts
        if _is_eligible(post)
    ]
    return candidates[:limit]


async def _fetch_raw_posts(limit: int) -> list[dict[str, Any]]:
    url = settings.METACULUS_API_URL
    if not url:
        return []
    # Over-fetch (max 5x) because numeric / date / multiple-choice questions
    # are filtered out client-side, mirroring kalshi's single-leg filter.
    fetch_limit = min(max(limit * 5, limit), 100)
    params = {
        "limit": str(fetch_limit),
        "type": "forecast",
        "status": "open",
        "has_group": "false",
        "order_by": "-activity",
    }
    headers = {"Authorization": f"Token {settings.METACULUS_API_TOKEN}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
    # Metaculus returns {"results": [...], "count": N, "next": "..."}.
    if isinstance(data, dict):
        results = data.get("results")
        return results if isinstance(results, list) else []
    # Some endpoints return a bare list — tolerate it.
    return data if isinstance(data, list) else []


def _is_eligible(post: Any) -> bool:
    if not isinstance(post, dict):
        return False
    question = str(post.get("title", "") or "").strip()
    if not question:
        return False
    inner = post.get("question")
    if not isinstance(inner, dict):
        return False
    if str(inner.get("question_type", "") or "").lower() != "binary":
        return False
    # Skip already-resolved questions even if the API returns status=open.
    if inner.get("resolution"):
        return False
    return _community_probability(post) is not None


def _to_candidate_event(post: dict[str, Any]) -> dict[str, Any]:
    question = str(post.get("title", "") or "").strip()
    baseline = _community_probability(post) or 50.0
    # Metaculus has no trading volume; use forecaster count as participation.
    forecasters = _forecaster_count(post)
    post_id = post.get("id")
    source_id = str(post_id) if post_id is not None else ""
    url = _post_url(post)
    return {
        "question": question,
        "baseline_probability": baseline,
        "volume": float(forecasters),
        "liquidity": 0.0,
        "source": {
            "type": "prediction_question",
            "platform": settings.METACULUS_SOURCE_NAME,
            "source_id": source_id,
            "question": question,
            "baseline_probability": round(baseline, 2),
            "liquidity": 0.0,
            "volume": float(forecasters),
            "url": url,
        },
    }


def _community_probability(post: dict[str, Any]) -> float | None:
    """Extract the binary YES probability (0-100) from a Metaculus post.

    Returns ``None`` when no probability is available (used by ``_is_eligible``
    to skip questions without forecasts). Metaculus exposes the community
    forecast in a few shapes depending on API version and question state, so
    we try the documented primary field first and fall back to the most recent
    forecast record.
    """
    cp = post.get("community_prediction")
    if isinstance(cp, dict):
        prob = cp.get("probability_yes")
        if prob is not None:
            return _clamp_pct(safe_float(prob, 0.0) * 100.0)
        # Nested under "question" in some responses.
        cp_q = cp.get("question")
        if isinstance(cp_q, dict):
            prob = cp_q.get("probability_yes")
            if prob is not None:
                return _clamp_pct(safe_float(prob, 0.0) * 100.0)
    # Fall back to the latest forecast record (forecasts are time-ordered,
    # most recent last in the v2 schema).
    forecasts = post.get("forecasts")
    if isinstance(forecasts, list) and forecasts:
        latest = forecasts[-1]
        if isinstance(latest, dict):
            prob = latest.get("probability_yes")
            if prob is not None:
                return _clamp_pct(safe_float(prob, 0.0) * 100.0)
    return None


def _forecaster_count(post: dict[str, Any]) -> int:
    counts = post.get("user_counts")
    if isinstance(counts, dict):
        n = counts.get("forecasters")
        if n is not None:
            return max(0, safe_int(n, 0))
    # Some payloads nest under "question".
    inner = post.get("question")
    if isinstance(inner, dict):
        counts = inner.get("user_counts")
        if isinstance(counts, dict):
            n = counts.get("forecasters")
            if n is not None:
                return max(0, safe_int(n, 0))
    return 0


def _post_url(post: dict[str, Any]) -> str:
    url = str(post.get("url", "") or "").strip()
    if url:
        return url
    post_id = post.get("id")
    if post_id is None:
        return ""
    return f"https://www.metaculus.com/questions/{post_id}/"


def _clamp_pct(value: float) -> float:
    return max(0.0, min(100.0, value))
