"""Execution quality service (Plan 3 §3.5: Market Microstructure & Executability).

Pure-function layer that models whether a recommendation is actually
executable on the market — separating "predictive edge" from "tradeable
edge". Produces an ``execution_quality`` overlay block with slippage
estimates, stale-price detection, platform constraints, and an
``executable`` boolean.

Only computed for events whose ``source.type == "prediction_market"``
(Polymarket, Kalshi). For other source types, returns ``None`` — same
gating convention as ``market_quality_service``.

This is an audit/overlay layer. It MUST NOT feed back into
``ai_probability``, ``evidence_profile``, ``regression_to_market``,
``actionable_recommendation``, or any overlay block. The data flow is
one-way:

    actionable_recommendation + market fields
      -> build_execution_quality
      -> execution_quality (overlay only, no writeback)

The function is synchronous and deterministic — no LLM calls, no I/O.
``settings`` is intentionally not passed; the orchestrator extracts
concrete scalar config values and passes them explicitly.

Field availability varies per adapter (see market-quality-field-audit.md):
- ``market_quote.last_updated``: unavailable across most adapters.
  ``stale_price_flag`` is ``None`` (unknown) when ``last_updated`` is absent.
- ``market_quote.bid`` / ``market_quote.ask``: only Kalshi. Polymarket
  typically has only ``last_price``. Slippage estimate is ``None`` when
  bid/ask are missing.
- ``volume`` / ``liquidity``: Polymarket/Kalshi have real values.

When a sub-field is missing, the corresponding output is ``None`` (unknown).
``executable`` defaults to ``True`` and is set to ``False`` only by explicit
evidence (wide spread, thin liquidity, stale price, high fee). Unknown
microstructure data (e.g. Polymarket/Kalshi not exposing bid/ask) does NOT
force ``executable=False`` — the market is still tradeable, we simply lack
the data to model slippage. Rule 4 fires only on explicit evidence.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_STRONG_DIRECTIONS = ("YES", "NO")


def _now() -> datetime:
    """Indirection for test patching — returns timezone-aware UTC now."""
    return datetime.now(timezone.utc)


def build_execution_quality(
    *,
    recommendation: dict[str, Any] | None,
    source: dict[str, Any] | None,
    market_quote: dict[str, Any] | None,
    volume: float | None,  # RESERVED: not yet consumed (liquidity is used for position sizing)
    liquidity: float | None,
    max_spread_pct: float,
    stale_price_seconds: int,
    min_liquidity: float,
    target_order_size: float,  # RESERVED: not yet consumed (see config.py)
    fee_rate_pct: float,
) -> dict[str, Any] | None:
    """Build the execution_quality overlay block.

    Returns ``None`` when the event is not a ``prediction_market`` source.
    Returns a dict with ``executable``, ``effective_entry_price``,
    ``estimated_slippage_pct``, ``max_safe_position_size``,
    ``stale_price_flag``, ``platform_constraint_reasons``, ``raw_direction``,
    ``suggested_direction``, ``downgrade_reason``, ``downgraded``,
    ``applied_to_displayed_direction``.

    Pure function: does not mutate inputs. Never raises — missing fields
    produce ``None`` sub-values. ``executable`` defaults to ``True`` and
    is set to ``False`` only by explicit evidence (wide spread, thin
    liquidity, stale price, high fee); unknown microstructure data does
    NOT block execution.
    """
    if not _is_prediction_market(source):
        return None

    raw_direction = _extract_direction(recommendation)
    constraints: list[str] = []

    # ── Sub-signal 1: spread ──────────────────────────────────────────
    spread = _get_float(market_quote, "spread")
    bid = _get_float(market_quote, "bid")
    ask = _get_float(market_quote, "ask")
    mid = _compute_mid(bid, ask)

    spread_pct = None
    if spread is not None and mid is not None and mid > 0:
        spread_pct = (spread / mid) * 100.0
    elif bid is not None and ask is not None and bid > 0:
        spread_pct = ((ask - bid) / ((ask + bid) / 2.0)) * 100.0

    if spread_pct is not None and spread_pct > max_spread_pct:
        constraints.append("价差过宽无法成交")
    # When spread_pct is None (no bid/ask data — e.g. Polymarket provides
    # only last_price, Kalshi returns {bid:0,ask:0,spread:0} when only
    # last_price_dollars is set), we do NOT add a constraint. Unable to
    # assess spread ≠ not executable — the market is still tradeable, we
    # simply lack the microstructure data to model slippage. Rule 4 fires
    # only on explicit evidence (wide spread, thin liquidity, stale price,
    # high fee), not on unknown state. This prevents forcing WAIT on real
    # Polymarket/Kalshi sources that don't expose bid/ask.

    # ── Sub-signal 2: slippage estimate ────────────────────────────────
    # Slippage = (half-spread / mid) * 100, as a true percentage of mid.
    # For a YES buyer, effective entry = ask; for NO buyer, effective
    # entry = 100 - bid. We use half-spread as the slippage proxy
    # (conservative). None when mid is unknown (no bid/ask).
    estimated_slippage_pct: float | None = None
    effective_entry_price: float | None = None
    if spread is not None and spread > 0 and mid is not None and mid > 0:
        estimated_slippage_pct = ((spread / 2.0) / mid) * 100.0
        if ask is not None and raw_direction == "YES":
            effective_entry_price = ask
        elif bid is not None and raw_direction == "NO":
            effective_entry_price = 100.0 - bid
        else:
            effective_entry_price = mid

    # ── Sub-signal 3: stale price ─────────────────────────────────────
    stale_price_flag = _check_stale_price(market_quote, stale_price_seconds)
    if stale_price_flag is True:
        constraints.append("价格陈旧")

    # ── Sub-signal 4: liquidity ───────────────────────────────────────
    if liquidity is not None and liquidity < min_liquidity:
        constraints.append("流动性不足")
    elif liquidity is None:
        constraints.append("流动性未知")

    # ── Sub-signal 5: platform fee ────────────────────────────────────
    if fee_rate_pct > 5.0:
        constraints.append("手续费过高")

    # ── Aggregate: executable ──────────────────────────────────────────
    executable = len(constraints) == 0

    # ── Max safe position size ─────────────────────────────────────────
    # Cap at 10% of liquidity (avoid moving the market). None when not
    # executable or liquidity unknown.
    max_safe_position_size: float | None = None
    if executable and liquidity is not None and liquidity > 0:
        max_safe_position_size = liquidity * 0.10

    # ── Direction suggestion ──────────────────────────────────────────
    suggested_direction = raw_direction
    downgrade_reason: str | None = None
    downgraded = False
    if not executable and raw_direction in _STRONG_DIRECTIONS:
        suggested_direction = "WAIT"
        downgrade_reason = " | ".join(constraints) if constraints else "不可执行"
        downgraded = True

    return {
        "executable": executable,
        "effective_entry_price": _round_or_none(effective_entry_price),
        "estimated_slippage_pct": _round_or_none(estimated_slippage_pct),
        "max_safe_position_size": _round_or_none(max_safe_position_size),
        "stale_price_flag": stale_price_flag,
        "platform_constraint_reasons": constraints,
        "raw_direction": raw_direction,
        "suggested_direction": suggested_direction,
        "downgrade_reason": downgrade_reason,
        "downgraded": downgraded,
        "applied_to_displayed_direction": False,  # set by merge step
    }


def _is_prediction_market(source: dict[str, Any] | None) -> bool:
    if not isinstance(source, dict):
        return False
    return source.get("type") == "prediction_market"


def _extract_direction(recommendation: dict[str, Any] | None) -> str:
    if not isinstance(recommendation, dict):
        return "WAIT"
    direction = recommendation.get("direction")
    if direction in ("YES", "NO", "WAIT", "AVOID"):
        return direction
    return "WAIT"


def _get_float(d: dict[str, Any] | None, key: str) -> float | None:
    if not isinstance(d, dict):
        return None
    val = d.get(key)
    if not isinstance(val, (int, float)):
        return None
    return float(val)


def _compute_mid(bid: float | None, ask: float | None) -> float | None:
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return None


def _check_stale_price(
    market_quote: dict[str, Any] | None,
    stale_price_seconds: int,
) -> bool | None:
    """Returns True if price is stale, False if fresh, None if unknown.

    ``last_updated`` is expected as an ISO 8601 string with timezone.
    When absent or unparseable, returns None (unknown) — NOT False.
    """
    if not isinstance(market_quote, dict):
        return None
    last_updated_str = market_quote.get("last_updated")
    if not isinstance(last_updated_str, str) or not last_updated_str:
        return None
    try:
        # Handle both 'Z' suffix and '+00:00'
        ts = last_updated_str.replace("Z", "+00:00")
        last_updated = datetime.fromisoformat(ts)
        if last_updated.tzinfo is None:
            return None  # naive datetime — can't determine freshness
    except (ValueError, TypeError):
        return None
    now = _now()
    age = (now - last_updated).total_seconds()
    return age > stale_price_seconds


def _round_or_none(val: float | None, places: int = 4) -> float | None:
    if val is None:
        return None
    return round(float(val), places)
