"""Limitless prediction-market candidate source.

Thin public adapter for the Limitless active markets endpoint. It emits the
shared candidate-event shape and fails closed so source outages do not break
discovery.
"""

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.utils.failure_policy import fail_closed_empty_list
from app.utils.market_utils import safe_float


logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = {"active", "funded", "open", "trading"}
_QUESTION_FIELDS = ("title", "question", "name")
_VOLUME_FIELDS = ("volumeFormatted", "volume", "volumeUsd", "volume_usd", "totalVolume")
_LIQUIDITY_FIELDS = (
    "liquidityFormatted",
    "liquidity",
    "liquidityUsd",
    "liquidity_usd",
    "totalLiquidity",
)
_ID_FIELDS = ("slug", "id", "marketId", "market_id", "address")


async def fetch_candidate_events(limit: int = 10) -> list[dict[str, Any]]:
    if not settings.LIMITLESS_SOURCE_ENABLED or not settings.LIMITLESS_API_URL:
        return []
    try:
        raw_markets = await _fetch_raw_markets(limit)
    except Exception as exc:
        return fail_closed_empty_list(
            logger,
            "limitless_candidates",
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
    params = {"limit": str(min(max(limit * 5, limit, 1), 100))}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(settings.LIMITLESS_API_URL, params=params)
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
    if not _has_supported_status(market):
        return False
    if not _has_supported_market_shape(market):
        return False
    if market.get("expired") is True or market.get("resolved") is True or market.get("closed") is True:
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
        url = f"https://limitless.exchange/markets/{source_id}"
    status = str(market.get("status", "") or "").strip().lower()
    return {
        "question": question,
        "baseline_probability": round(probability, 2),
        "volume": volume,
        "liquidity": liquidity,
        "source": {
            "type": "prediction_market",
            "platform": settings.LIMITLESS_SOURCE_NAME,
            "chain": "Base",
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
    market_type = str(market.get("marketType", "single") or "").strip().lower()
    if market_type not in {"single", "binary", "0"}:
        return False
    outcomes = market.get("outcomes") or market.get("outcomeNames")
    if outcomes is not None and not (isinstance(outcomes, list) and len(outcomes) == 2):
        return False
    prices = market.get("prices")
    return isinstance(prices, (list, dict))


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
    prices = market.get("prices")
    if isinstance(prices, dict):
        for key in ("yes", "YES", "Yes"):
            if key in prices:
                return _normalize_probability(prices.get(key))
        return None
    if not isinstance(prices, list):
        return None
    if len(prices) != 2:
        return None
    if all(not isinstance(price, dict) for price in prices):
        return _normalize_probability(prices[1])
    for price in prices:
        if not isinstance(price, dict):
            continue
        label = str(
            price.get("outcome") or price.get("name") or price.get("label") or ""
        ).strip().lower()
        if label == "yes":
            return _normalize_probability(
                price.get("price") or price.get("value") or price.get("probability")
            )
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
