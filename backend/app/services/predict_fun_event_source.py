"""Predict.fun prediction-market candidate source.

Adapter for Predict.fun beta API market discovery. Missing credentials disable
the source without a network call.
"""

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.utils.failure_policy import fail_closed_empty_list
from app.utils.market_utils import safe_float


logger = logging.getLogger(__name__)

_ACTIVE_TRADING_STATUSES = {"open", "trading"}
_ACTIVE_MARKET_STATUSES = {"registered", "active", "open"}
_QUESTION_FIELDS = ("title", "question", "name")
_VOLUME_FIELDS = ("volume", "volumeUsd", "volume_usd", "totalVolume")
_LIQUIDITY_FIELDS = ("liquidity", "liquidityUsd", "liquidity_usd", "totalLiquidity")
_ID_FIELDS = ("id", "marketId", "market_id", "slug")


async def fetch_candidate_events(limit: int = 10) -> list[dict[str, Any]]:
    if (
        not settings.PREDICT_FUN_SOURCE_ENABLED
        or not settings.PREDICT_FUN_API_URL
        or not settings.PREDICT_FUN_API_KEY
    ):
        return []
    try:
        raw_markets = await _fetch_raw_markets(limit)
    except Exception as exc:
        return fail_closed_empty_list(
            logger,
            "predict_fun_candidates",
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
    params = {"first": str(min(max(limit * 5, limit, 1), 100))}
    headers = {"x-api-key": settings.PREDICT_FUN_API_KEY}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            settings.PREDICT_FUN_API_URL,
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
    value = data.get("data")
    if isinstance(value, list):
        return value
    return []


def _is_eligible(market: Any) -> bool:
    if not isinstance(market, dict):
        return False
    if not _extract_text(market, _QUESTION_FIELDS):
        return False
    if not _extract_text(market, _ID_FIELDS):
        return False
    if market.get("isVisible") is False:
        return False
    if not _has_supported_status(market):
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
        url = f"https://predict.fun/markets/{source_id}"
    status = str(market.get("status", "") or "").strip().lower()
    return {
        "question": question,
        "baseline_probability": round(probability, 2),
        "volume": volume,
        "liquidity": liquidity,
        "source": {
            "type": "prediction_market",
            "platform": settings.PREDICT_FUN_SOURCE_NAME,
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
    trading_status = str(market.get("tradingStatus", "") or "").strip().lower()
    market_status = str(market.get("status", "") or "").strip().lower()
    return (
        trading_status in _ACTIVE_TRADING_STATUSES
        and market_status in _ACTIVE_MARKET_STATUSES
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
    resolution = market.get("resolution")
    if not isinstance(resolution, dict):
        return None
    bid = _extract_price(resolution.get("bestBid"))
    ask = _extract_price(resolution.get("bestAsk"))
    if bid is None or ask is None:
        return None
    return round((bid + ask) / 2, 6)


def _extract_price(raw: Any) -> float | None:
    if isinstance(raw, dict):
        raw = raw.get("price")
    return _normalize_probability(raw)


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
