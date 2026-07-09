"""Opinion prediction-market candidate source.

Adapter for Opinion's API-key-gated Open API market endpoint. Missing
credentials disable the source without a network call.
"""

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.utils.failure_policy import fail_closed_empty_list
from app.utils.market_utils import safe_float


logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = {"activated", "active", "open", "trading"}
_QUESTION_FIELDS = ("marketTitle", "question", "title", "name")
_PROBABILITY_FIELDS = ("latestPrice", "yesPrice", "yesTokenPrice", "probability")
_VOLUME_FIELDS = ("volume", "volumeUsd", "volume_usd", "totalVolume")
_LIQUIDITY_FIELDS = ("liquidity", "liquidityUsd", "liquidity_usd", "totalLiquidity")
_ID_FIELDS = ("marketId", "id", "market_id", "slug")


async def fetch_candidate_events(limit: int = 10) -> list[dict[str, Any]]:
    if (
        not settings.OPINION_SOURCE_ENABLED
        or not settings.OPINION_API_URL
        or not settings.OPINION_API_KEY
    ):
        return []
    try:
        raw_markets = await _fetch_raw_markets(limit)
    except Exception as exc:
        return fail_closed_empty_list(
            logger,
            "opinion_candidates",
            exc,
            context={"limit": limit},
        )
    candidates = [
        _to_candidate_event(market)
        for market in raw_markets
        if _is_eligible(market)
    ]
    return candidates[:limit]


async def _fetch_raw_markets(limit: int) -> list[dict[str, Any]]:
    params = {
        "limit": str(min(max(limit * 5, limit, 1), 100)),
        "marketType": "0",
        "status": "activated",
    }
    headers = {"apikey": settings.OPINION_API_KEY}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            settings.OPINION_API_URL,
            headers=headers,
            params=params,
        )
        response.raise_for_status()
        data = response.json()
    return _extract_market_list(data)


def _extract_market_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    result = data.get("result")
    if isinstance(result, dict) and isinstance(result.get("list"), list):
        return result["list"]
    return []


def _is_eligible(market: Any) -> bool:
    if not isinstance(market, dict):
        return False
    if not _extract_text(market, _QUESTION_FIELDS):
        return False
    if not _extract_text(market, _ID_FIELDS):
        return False
    if not _has_supported_status(market):
        return False
    if not _has_supported_market_shape(market):
        return False
    return _extract_probability(market) is not None


def _to_candidate_event(market: dict[str, Any]) -> dict[str, Any]:
    question = _extract_text(market, _QUESTION_FIELDS)
    probability = _extract_probability(market) or 0.0
    volume = _extract_number(market, _VOLUME_FIELDS)
    liquidity = _extract_number(market, _LIQUIDITY_FIELDS)
    source_id = _extract_text(market, _ID_FIELDS)
    url = str(market.get("url", "") or "").strip()
    if not url:
        url = f"https://app.opinion.trade/market/{source_id}"
    status = str(market.get("status", "") or "").strip().lower()
    return {
        "question": question,
        "baseline_probability": round(probability, 2),
        "volume": volume,
        "liquidity": liquidity,
        "source": {
            "type": "prediction_market",
            "platform": settings.OPINION_SOURCE_NAME,
            "chain": "BNB Chain",
            "source_id": source_id,
            "question": question,
            "baseline_probability": round(probability, 2),
            "liquidity": liquidity,
            "volume": volume,
            "url": url,
            "status": status,
        },
    }


def _has_supported_status(market: dict[str, Any]) -> bool:
    status = str(market.get("status", "") or "").strip().lower()
    return status in _ACTIVE_STATUSES


def _has_supported_market_shape(market: dict[str, Any]) -> bool:
    market_type = str(market.get("marketType", "0") if market.get("marketType") is not None else "").strip().lower()
    if market_type not in {"0", "binary"}:
        return False
    return bool(str(market.get("yesTokenId", "") or "").strip()) and bool(
        str(market.get("noTokenId", "") or "").strip()
    )


def _extract_text(market: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = str(market.get(field, "") or "").strip()
        if value:
            return value
    return ""


def _extract_number(market: dict[str, Any], fields: tuple[str, ...]) -> float:
    for field in fields:
        if market.get(field) is not None:
            return safe_float(_clean_number(market.get(field)), 0.0)
    return 0.0


def _extract_probability(market: dict[str, Any]) -> float | None:
    for field in _PROBABILITY_FIELDS:
        if market.get(field) is None:
            continue
        return _normalize_probability(market.get(field))
    return None


def _normalize_probability(raw: Any) -> float | None:
    value = safe_float(_clean_number(raw), -1.0)
    if 0.0 <= value <= 1.0:
        return value * 100
    if 0.0 <= value <= 100.0:
        return value
    return None


def _clean_number(raw: Any) -> Any:
    if isinstance(raw, str):
        return raw.replace("$", "").replace(",", "").replace("%", "").strip()
    return raw
