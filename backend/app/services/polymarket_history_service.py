"""
polymarket_history_service.py
=============================
从 Polymarket API 获取已解决市场的历史数据。

用于：
  1. 自动校准 agent_memory（填充历史数据）
  2. 回测系统信号准确率
  3. 计算基准利率的实证数据
"""

import logging
import json

import httpx
from typing import Any

from app.utils.failure_policy import fail_closed_empty_list
from app.utils.market_utils import safe_float

logger = logging.getLogger(__name__)


POLYMARKET_API = "https://gamma-api.polymarket.com/markets"


def _parse_json_array(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as exc:
            raise ValueError("invalid JSON array") from exc
        return parsed if isinstance(parsed, list) else []
    return value if isinstance(value, list) else []


def _to_resolved_market(item: dict[str, Any]) -> dict[str, Any] | None:
    question = item.get("question", "")
    if not question:
        return None
    if item.get("closed") is not True:
        return None

    prices = _parse_json_array(item.get("outcomePrices", "[]"))
    if not prices or len(prices) < 2:
        return None

    yes_price = safe_float(prices[0], -1.0)
    if yes_price < 0.0 or yes_price > 1.0:
        return None

    # For Polymarket's binary/2-outcome markets, source_id tracks the first
    # outcome/YES-side price. If that final price is >= 0.5, the tracked side
    # resolved true; otherwise false.
    actual_outcome = 100.0 if yes_price >= 0.5 else 0.0

    return {
        "id": str(item.get("id", "")),
        "question": question,
        "actual_outcome": actual_outcome,
        "final_yes_price": yes_price,
        "volume": safe_float(item.get("volume"), 0.0),
        "liquidity": safe_float(item.get("liquidity"), 0.0),
        "start_date": item.get("startDate", ""),
        "end_date": item.get("endDate", ""),
    }


async def fetch_resolved_markets(
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """
    获取已解决的历史市场。
    返回包含 question、final_probability（实际结果）的列表。
    """
    params = {
        "closed": "true",
        "limit": str(limit),
        "offset": str(offset),
        "order": "volume",
        "ascending": "false",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.get(POLYMARKET_API, params=params)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            return fail_closed_empty_list(
                logger,
                "polymarket_resolved",
                exc,
                context={"limit": limit, "offset": offset},
            )

    markets = []
    for item in data:
        try:
            market = _to_resolved_market(item)
            if market is not None:
                markets.append(market)
        except Exception as exc:
            logger.warning(
                "Skipping malformed Polymarket resolved market [id=%s]: %s",
                item.get("id", ""),
                exc,
            )
            continue

    return markets


async def fetch_markets_by_ids(ids: list[str]) -> list[dict[str, Any]]:
    """Fetch specific Polymarket markets by Gamma market id.

    The bulk resolved endpoint is sorted/capped and can miss low-volume or
    older markets. This direct path is used only for known event->market links
    and returns entries only when the market is actually closed/resolved.
    """
    unique_ids = [str(mid).strip() for mid in dict.fromkeys(ids) if str(mid).strip()]
    if not unique_ids:
        return []

    resolved: list[dict[str, Any]] = []
    headers = {"User-Agent": "Mozilla/5.0"}
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        for market_id in unique_ids:
            try:
                response = await client.get(f"{POLYMARKET_API}/{market_id}")
                response.raise_for_status()
                item = response.json()
                if not isinstance(item, dict):
                    continue
                market = _to_resolved_market(item)
                if market is not None:
                    resolved.append(market)
            except Exception as exc:
                logger.warning(
                    "Skipping Polymarket direct fetch [id=%s]: %s",
                    market_id,
                    exc,
                )
                continue
    return resolved


async def get_backtest_baseline() -> dict[str, Any]:
    """
    获取市场基准：当你始终猜测"市场概率"（不做任何分析）时的 Brier Score。
    这是系统必须超越的基准线。
    """
    resolved = await fetch_resolved_markets(limit=100)
    if not resolved:
        return {"status": "no_data"}

    baseline_scores = []
    for m in resolved:
        # 基准：直接用最终市场价格作为预测
        pred = m["final_yes_price"] * 100
        actual = m["actual_outcome"]
        bs = ((pred / 100.0) - (actual / 100.0)) ** 2
        baseline_scores.append(bs)

    avg_baseline = sum(baseline_scores) / len(baseline_scores)

    return {
        "status": "ok",
        "n": len(resolved),
        "baseline_brier_score": round(avg_baseline, 4),
        "baseline_grade": _grade_brier(avg_baseline),
        "note": (
            "This is the Brier score you get by just trusting market prices. "
            "Your system must beat this to have any edge."
        ),
    }


def _grade_brier(b: float) -> str:
    if b <= 0.05:
        return "EXCELLENT"
    if b <= 0.10:
        return "GOOD"
    if b <= 0.15:
        return "ACCEPTABLE"
    if b <= 0.20:
        return "POOR"
    return "RANDOM_LEVEL"
