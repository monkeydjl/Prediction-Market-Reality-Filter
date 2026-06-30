# Execution Quality & Degraded-Mode Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `execution_quality` overlay (spec §3.5) that models whether a recommendation is actually executable on the market, plus end-to-end degraded-mode scenario tests (spec §4.2) that verify the pipeline produces safe output under partial failure.

**Architecture:** A new pure-function service `execution_quality_service.py` follows the established overlay pattern (like `market_quality_service.py`): only computed for `source.type == "prediction_market"`, best-effort attached in `_build_all_overlays`, feature-flagged with default OFF. It produces an `execution_quality` block with `executable`, `effective_entry_price`, `estimated_slippage_pct`, `max_safe_position_size`, `stale_price_flag`, `platform_constraint_reasons`. It feeds into `guardrail_service` as a new rule 4 (`market_not_executable_blocks_act`). Degraded-mode tests are added to the existing `test_decision_quality_engine_integration.py`.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, pytest, existing `market_quality_service` / `guardrail_service` / `event_intelligence_service` patterns.

## Global Constraints

- `EXECUTION_QUALITY_ENABLED` MUST default to `false`, resulting in byte-identical behavior to pre-Plan-3 when off (no `execution_quality` key attached, no guardrail rule 4 firing).
- `execution_quality_service` MUST be a pure, synchronous, deterministic function with no LLM/IO operations (same convention as `decision_quality_service` / `market_quality_service` / `source_reliability_service`).
- `execution_quality` block MUST only be attached for `source.type == "prediction_market"` events (Polymarket/Kalshi). Other source types (`prediction_question`, `open_web`, `sports_event`, `manual`) MUST omit the key entirely (same gating as `market_quality`).
- `execution_quality_service` MUST NOT mutate `actionable_recommendation.direction`, `ai_probability`, `evidence_profile`, `regression_to_market`, `final_displayed_direction`, or any overlay block (audit-only layer, one-way data flow).
- `execution_quality_service` MUST use try/except best-effort fallback in `_build_all_overlays`: on failure, attach an error block `{"error": "build_failed", ...}` with `executable=False`, never raise.
- `stale_price_flag` MUST be `True` only when `market_quote.last_updated` is older than `EXECUTION_STALE_PRICE_SECONDS` (default 300). When `last_updated` is absent, `stale_price_flag` MUST be `None` (unknown), NOT `False` — matches the existing `market_quality_service.stale_price_flag` convention.
- `platform_constraint_reasons` MUST be a `list[str]` of Chinese-language reasons (e.g. `"手续费过高"`, `"最小交易单位限制"`, `"市场暂停"`). NEVER contain the banned terms long/short/buy/sell/position/kelly/order (vocabulary lock from prior phases).
- `max_safe_position_size` MUST be `None` when `liquidity` is unknown or when `executable=False`.
- Rule 4 (`market_not_executable_blocks_act`) in `guardrail_service` MUST force YES/NO → WAIT when `execution_quality.executable=False`. When the rule fires, the reason MUST be appended to `final_downgrade_reason` with ` | ` separator (matches rules 1-3 convention).
- `EXECUTION_QUALITY_ENABLED` MUST also gate rule 4 in guardrail_service: when OFF, rule 4 is skipped even if `GUARDRAILS_ENABLED=True` (no `execution_quality` key exists to read).
- Degraded-mode tests MUST use the existing `_build_all_overlays(record)` entry point (not `analyze_event`, which makes real LLM calls). Tests construct synthetic records and call `_build_all_overlays` directly.
- All datetime operations MUST use timezone-aware objects with explicit `timezone` imports.
- `downgrade_reason` / `final_downgrade_reason` values MUST be Chinese-language strings and MUST NOT contain the banned terms long/short/buy/sell/position/kelly/order.

---

## File Structure

**New files:**
- `backend/app/services/execution_quality_service.py` — Pure-function overlay service. ~200 lines.
- `backend/tests/test_execution_quality_service.py` — Unit tests for the pure function. ~250 lines.

**Modified files:**
- `backend/app/core/config.py` — Add 6 `EXECUTION_*` config flags.
- `backend/app/services/guardrail_service.py` — Add rule 4 (`market_not_executable_blocks_act`).
- `backend/app/services/event_intelligence_service.py` — Attach `execution_quality` overlay in `_build_all_overlays` (after `market_quality`, before merge); pass `execution_quality` to `evaluate_guardrails`.
- `backend/tests/test_guardrail_service.py` — Add tests for rule 4.
- `backend/tests/test_decision_quality_engine_integration.py` — Add `TestDegradedModeScenarios` class (spec §4.2).

---

## Task 1: Pure `execution_quality_service` + config flags

**Files:**
- Create: `backend/app/services/execution_quality_service.py`
- Create: `backend/tests/test_execution_quality_service.py`
- Modify: `backend/app/core/config.py` (add `EXECUTION_*` flags after the `MARKET_*` block around line 587)

**Interfaces:**
- Consumes: `record.get("actionable_recommendation")`, `record.get("source")`, `record.get("market_quote")`, `record.get("volume")`, `record.get("liquidity")` — same inputs as `market_quality_service`.
- Produces: `build_execution_quality(*, recommendation, source, market_quote, volume, liquidity, max_spread_pct, stale_price_seconds, min_liquidity, target_order_size, fee_rate_pct) -> dict | None`. Returns `None` for non-prediction-market sources. Returns a dict with keys: `executable: bool`, `effective_entry_price: float | None`, `estimated_slippage_pct: float | None`, `max_safe_position_size: float | None`, `stale_price_flag: bool | None`, `platform_constraint_reasons: list[str]`, `raw_direction: str`, `suggested_direction: str`, `downgrade_reason: str | None`, `downgraded: bool`, `applied_to_displayed_direction: bool`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_execution_quality_service.py`:

```python
"""Unit tests for execution_quality_service (Plan 3 §3.5).

Pure-function tests — no I/O, no settings, no store imports. The service
takes the same inputs as market_quality_service and returns an execution
feasibility block. Returns None for non-prediction-market sources.
"""
from __future__ import annotations

import unittest

from app.services.execution_quality_service import build_execution_quality


def _rec(
    *,
    direction: str = "YES",
    source_type: str = "prediction_market",
    spread: float | None = 2.0,
    bid: float | None = 48.0,
    ask: float | None = 52.0,
    last_updated: str | None = "2026-06-30T12:00:00+00:00",
    volume: float | None = 5000.0,
    liquidity: float | None = 10000.0,
    target_order_size: float = 100.0,
    fee_rate_pct: float = 1.0,
    stale_price_seconds: int = 300,
    max_spread_pct: float = 12.0,
    min_liquidity: float = 1000.0,
):
    recommendation = {"direction": direction}
    source = {"type": source_type}
    market_quote: dict = {}
    if bid is not None:
        market_quote["bid"] = bid
    if ask is not None:
        market_quote["ask"] = ask
    if spread is not None:
        market_quote["spread"] = spread
    if last_updated is not None:
        market_quote["last_updated"] = last_updated
    return {
        "recommendation": recommendation,
        "source": source,
        "market_quote": market_quote,
        "volume": volume,
        "liquidity": liquidity,
        "target_order_size": target_order_size,
        "fee_rate_pct": fee_rate_pct,
        "stale_price_seconds": stale_price_seconds,
        "max_spread_pct": max_spread_pct,
        "min_liquidity": min_liquidity,
    }


class TestBuildExecutionQuality(unittest.TestCase):
    def test_returns_none_for_non_prediction_market_source(self):
        kwargs = _rec(source_type="open_web")
        result = build_execution_quality(
            recommendation=kwargs["recommendation"],
            source=kwargs["source"],
            market_quote=kwargs["market_quote"],
            volume=kwargs["volume"],
            liquidity=kwargs["liquidity"],
            max_spread_pct=kwargs["max_spread_pct"],
            stale_price_seconds=kwargs["stale_price_seconds"],
            min_liquidity=kwargs["min_liquidity"],
            target_order_size=kwargs["target_order_size"],
            fee_rate_pct=kwargs["fee_rate_pct"],
        )
        self.assertIsNone(result)

    def test_executable_when_spread_tight_and_liquid(self):
        kwargs = _rec()  # spread=2.0, liquidity=10000, volume=5000
        result = build_execution_quality(
            recommendation=kwargs["recommendation"],
            source=kwargs["source"],
            market_quote=kwargs["market_quote"],
            volume=kwargs["volume"],
            liquidity=kwargs["liquidity"],
            max_spread_pct=kwargs["max_spread_pct"],
            stale_price_seconds=kwargs["stale_price_seconds"],
            min_liquidity=kwargs["min_liquidity"],
            target_order_size=kwargs["target_order_size"],
            fee_rate_pct=kwargs["fee_rate_pct"],
        )
        self.assertIsNotNone(result)
        self.assertTrue(result["executable"])
        self.assertEqual(result["raw_direction"], "YES")
        self.assertEqual(result["suggested_direction"], "YES")
        self.assertFalse(result["downgraded"])
        self.assertIsNone(result["downgrade_reason"])
        # Slippage estimate is half-spread / 100 (2% half-spread → 0.02)
        self.assertAlmostEqual(result["estimated_slippage_pct"], 0.02, places=4)
        # Effective entry = mid + slippage (for YES buyer)
        self.assertIsNotNone(result["effective_entry_price"])
        self.assertGreater(result["max_safe_position_size"], 0)

    def test_not_executable_when_spread_too_wide(self):
        kwargs = _rec(spread=20.0)  # > max_spread_pct=12
        result = build_execution_quality(
            recommendation=kwargs["recommendation"],
            source=kwargs["source"],
            market_quote=kwargs["market_quote"],
            volume=kwargs["volume"],
            liquidity=kwargs["liquidity"],
            max_spread_pct=kwargs["max_spread_pct"],
            stale_price_seconds=kwargs["stale_price_seconds"],
            min_liquidity=kwargs["min_liquidity"],
            target_order_size=kwargs["target_order_size"],
            fee_rate_pct=kwargs["fee_rate_pct"],
        )
        self.assertFalse(result["executable"])
        self.assertEqual(result["suggested_direction"], "WAIT")
        self.assertTrue(result["downgraded"])
        self.assertIsNotNone(result["downgrade_reason"])
        # Reason must not contain banned terms
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        for term in banned:
            self.assertNotIn(term, (result["downgrade_reason"] or "").lower())

    def test_stale_price_flag_true_when_last_updated_old(self):
        # last_updated = 1 hour ago, stale_price_seconds = 300 (5 min)
        kwargs = _rec(last_updated="2026-06-30T11:00:00+00:00")
        # Use a recent "now" for the test — the service uses datetime.now(timezone.utc)
        # so we patch it.
        from datetime import datetime, timezone
        from unittest.mock import patch
        fixed_now = datetime(2026, 6, 30, 12, 5, 0, tzinfo=timezone.utc)
        with patch("app.services.execution_quality_service._now", return_value=fixed_now):
            result = build_execution_quality(
                recommendation=kwargs["recommendation"],
                source=kwargs["source"],
                market_quote=kwargs["market_quote"],
                volume=kwargs["volume"],
                liquidity=kwargs["liquidity"],
                max_spread_pct=kwargs["max_spread_pct"],
                stale_price_seconds=kwargs["stale_price_seconds"],
                min_liquidity=kwargs["min_liquidity"],
                target_order_size=kwargs["target_order_size"],
                fee_rate_pct=kwargs["fee_rate_pct"],
            )
        self.assertTrue(result["stale_price_flag"])
        self.assertFalse(result["executable"])

    def test_stale_price_flag_none_when_last_updated_absent(self):
        kwargs = _rec(last_updated=None)
        result = build_execution_quality(
            recommendation=kwargs["recommendation"],
            source=kwargs["source"],
            market_quote=kwargs["market_quote"],
            volume=kwargs["volume"],
            liquidity=kwargs["liquidity"],
            max_spread_pct=kwargs["max_spread_pct"],
            stale_price_seconds=kwargs["stale_price_seconds"],
            min_liquidity=kwargs["min_liquidity"],
            target_order_size=kwargs["target_order_size"],
            fee_rate_pct=kwargs["fee_rate_pct"],
        )
        self.assertIsNone(result["stale_price_flag"])

    def test_not_executable_when_liquidity_below_min(self):
        kwargs = _rec(liquidity=500.0, min_liquidity=1000.0)
        result = build_execution_quality(
            recommendation=kwargs["recommendation"],
            source=kwargs["source"],
            market_quote=kwargs["market_quote"],
            volume=kwargs["volume"],
            liquidity=kwargs["liquidity"],
            max_spread_pct=kwargs["max_spread_pct"],
            stale_price_seconds=kwargs["stale_price_seconds"],
            min_liquidity=kwargs["min_liquidity"],
            target_order_size=kwargs["target_order_size"],
            fee_rate_pct=kwargs["fee_rate_pct"],
        )
        self.assertFalse(result["executable"])
        self.assertIn("流动性", (result["downgrade_reason"] or ""))

    def test_max_safe_position_size_none_when_not_executable(self):
        kwargs = _rec(spread=20.0)  # not executable
        result = build_execution_quality(
            recommendation=kwargs["recommendation"],
            source=kwargs["source"],
            market_quote=kwargs["market_quote"],
            volume=kwargs["volume"],
            liquidity=kwargs["liquidity"],
            max_spread_pct=kwargs["max_spread_pct"],
            stale_price_seconds=kwargs["stale_price_seconds"],
            min_liquidity=kwargs["min_liquidity"],
            target_order_size=kwargs["target_order_size"],
            fee_rate_pct=kwargs["fee_rate_pct"],
        )
        self.assertFalse(result["executable"])
        self.assertIsNone(result["max_safe_position_size"])

    def test_platform_constraint_reasons_list_is_chinese(self):
        """When multiple constraints fire, reasons is a list of Chinese strings."""
        kwargs = _rec(spread=20.0, liquidity=500.0)  # wide spread + thin liquidity
        result = build_execution_quality(
            recommendation=kwargs["recommendation"],
            source=kwargs["source"],
            market_quote=kwargs["market_quote"],
            volume=kwargs["volume"],
            liquidity=kwargs["liquidity"],
            max_spread_pct=kwargs["max_spread_pct"],
            stale_price_seconds=kwargs["stale_price_seconds"],
            min_liquidity=kwargs["min_liquidity"],
            target_order_size=kwargs["target_order_size"],
            fee_rate_pct=kwargs["fee_rate_pct"],
        )
        self.assertFalse(result["executable"])
        self.assertIsInstance(result["platform_constraint_reasons"], list)
        self.assertGreater(len(result["platform_constraint_reasons"]), 0)
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        for reason in result["platform_constraint_reasons"]:
            for term in banned:
                self.assertNotIn(term, reason.lower())

    def test_raw_direction_wait_stays_wait(self):
        """WAIT/AVOID recommendations are not downgraded further."""
        kwargs = _rec(direction="WAIT")
        result = build_execution_quality(
            recommendation=kwargs["recommendation"],
            source=kwargs["source"],
            market_quote=kwargs["market_quote"],
            volume=kwargs["volume"],
            liquidity=kwargs["liquidity"],
            max_spread_pct=kwargs["max_spread_pct"],
            stale_price_seconds=kwargs["stale_price_seconds"],
            min_liquidity=kwargs["min_liquidity"],
            target_order_size=kwargs["target_order_size"],
            fee_rate_pct=kwargs["fee_rate_pct"],
        )
        self.assertEqual(result["raw_direction"], "WAIT")
        self.assertEqual(result["suggested_direction"], "WAIT")
        self.assertFalse(result["downgraded"])

    def test_missing_market_quote_still_returns_block(self):
        """When market_quote is empty/missing, executable=False, slippage=None."""
        kwargs = _rec()
        kwargs["market_quote"] = {}
        result = build_execution_quality(
            recommendation=kwargs["recommendation"],
            source=kwargs["source"],
            market_quote=kwargs["market_quote"],
            volume=kwargs["volume"],
            liquidity=kwargs["liquidity"],
            max_spread_pct=kwargs["max_spread_pct"],
            stale_price_seconds=kwargs["stale_price_seconds"],
            min_liquidity=kwargs["min_liquidity"],
            target_order_size=kwargs["target_order_size"],
            fee_rate_pct=kwargs["fee_rate_pct"],
        )
        self.assertIsNotNone(result)
        self.assertFalse(result["executable"])
        self.assertIsNone(result["estimated_slippage_pct"])
        self.assertIsNone(result["effective_entry_price"])

    def test_block_shape_matches_spec(self):
        """Verify all spec-mandated keys are present."""
        kwargs = _rec()
        result = build_execution_quality(
            recommendation=kwargs["recommendation"],
            source=kwargs["source"],
            market_quote=kwargs["market_quote"],
            volume=kwargs["volume"],
            liquidity=kwargs["liquidity"],
            max_spread_pct=kwargs["max_spread_pct"],
            stale_price_seconds=kwargs["stale_price_seconds"],
            min_liquidity=kwargs["min_liquidity"],
            target_order_size=kwargs["target_order_size"],
            fee_rate_pct=kwargs["fee_rate_pct"],
        )
        expected_keys = {
            "executable", "effective_entry_price", "estimated_slippage_pct",
            "max_safe_position_size", "stale_price_flag",
            "platform_constraint_reasons", "raw_direction",
            "suggested_direction", "downgrade_reason", "downgraded",
            "applied_to_displayed_direction",
        }
        self.assertEqual(set(result.keys()), expected_keys)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_execution_quality_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.execution_quality_service'`

- [ ] **Step 3: Add config flags to `config.py`**

In `backend/app/core/config.py`, after the `MARKET_QUALITY_SCORE_THRESHOLD` block (around line 588), add:

```python
    # ── Execution Quality (Plan 3 §3.5) ────────────────────────────────
    # Defaults to OFF — byte-identical to pre-Plan-3 when off (no
    # execution_quality key attached, no guardrail rule 4 firing).
    EXECUTION_QUALITY_ENABLED: bool = os.getenv(
        "EXECUTION_QUALITY_ENABLED", "false"
    ).lower() in ("1", "true", "yes")
    # Max acceptable bid-ask spread as percentage of mid price (0-100).
    # Reuses MARKET_MAX_SPREAD_PCT by default but can be overridden.
    EXECUTION_MAX_SPREAD_PCT: float = float(
        os.getenv("EXECUTION_MAX_SPREAD_PCT", "12")
    )
    # Price considered stale if last_updated is older than this (seconds).
    EXECUTION_STALE_PRICE_SECONDS: int = int(
        os.getenv("EXECUTION_STALE_PRICE_SECONDS", "300")
    )
    # Minimum liquidity for execution feasibility.
    EXECUTION_MIN_LIQUIDITY: float = float(
        os.getenv("EXECUTION_MIN_LIQUIDITY", "1000")
    )
    # Target order size (shares) for slippage estimation.
    EXECUTION_TARGET_ORDER_SIZE: float = float(
        os.getenv("EXECUTION_TARGET_ORDER_SIZE", "100")
    )
    # Platform fee rate as percentage of notional (0-100).
    EXECUTION_FEE_RATE_PCT: float = float(
        os.getenv("EXECUTION_FEE_RATE_PCT", "1.0")
    )
    # Guardrail rule 4: when True, executable=False forces YES/NO → WAIT.
    GUARDRAIL_MARKET_NOT_EXECUTABLE_BLOCKS_ACT: bool = os.getenv(
        "GUARDRAIL_MARKET_NOT_EXECUTABLE_BLOCKS_ACT", "true"
    ).lower() in ("1", "true", "yes")
```

- [ ] **Step 4: Implement `execution_quality_service.py`**

Create `backend/app/services/execution_quality_service.py`:

```python
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

When a sub-field is missing, the corresponding output is ``None`` (unknown)
and ``executable`` degrades to ``False`` (fail-safe: unknown = not executable).
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
    volume: float | None,
    liquidity: float | None,
    max_spread_pct: float,
    stale_price_seconds: int,
    min_liquidity: float,
    target_order_size: float,
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
    produce ``None`` sub-values and ``executable=False``.
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

    # ── Sub-signal 2: slippage estimate ────────────────────────────────
    # Slippage = half-spread / 100 (as a fraction of mid). For a YES buyer,
    # effective entry = ask; for NO buyer, effective entry = 100 - bid.
    # We use half-spread as the slippage proxy (conservative).
    estimated_slippage_pct: float | None = None
    effective_entry_price: float | None = None
    if spread is not None and spread > 0:
        estimated_slippage_pct = (spread / 2.0) / 100.0
        if ask is not None and raw_direction == "YES":
            effective_entry_price = ask
        elif bid is not None and raw_direction == "NO":
            effective_entry_price = 100.0 - bid
        elif mid is not None:
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_execution_quality_service.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/execution_quality_service.py backend/tests/test_execution_quality_service.py backend/app/core/config.py
git commit -m "feat(execution): add pure execution_quality_service (executable flag + slippage + stale price)"
```

---

## Task 2: Guardrail rule 4 + overlay wiring in `_build_all_overlays`

**Files:**
- Modify: `backend/app/services/guardrail_service.py` (add rule 4 after rule 3, around line 144)
- Modify: `backend/app/services/event_intelligence_service.py` (attach `execution_quality` overlay after `market_quality` around line 370; pass to `evaluate_guardrails` around line 520)
- Modify: `backend/tests/test_guardrail_service.py` (add tests for rule 4)

**Interfaces:**
- Consumes: `build_execution_quality` from Task 1; `evaluate_guardrails` signature extended with `market_not_executable_blocks_act: bool`.
- Produces: `record["execution_quality"]` key attached in `_build_all_overlays` when `EXECUTION_QUALITY_ENABLED=true` and `source.type == "prediction_market"`. Guardrail rule 4 reads `record["execution_quality"]["executable"]`.

- [ ] **Step 1: Write the failing tests for guardrail rule 4**

In `backend/tests/test_guardrail_service.py`, add a new test class `TestRule4MarketNotExecutable`:

```python
class TestRule4MarketNotExecutable(unittest.TestCase):
    """Rule 4: execution_quality.executable=False blocks YES/NO → WAIT."""

    def _base_record(self, executable: bool) -> dict:
        return {
            "execution_quality": {
                "executable": executable,
                "platform_constraint_reasons": [] if executable else ["价差过宽无法成交"],
            },
            "llm_telemetry": {"degraded_mode": False},
            "decision_quality": {"conflict_score": 0.1},
            "category": "politics",
        }

    def test_executable_false_forces_yes_to_wait(self):
        from app.services.guardrail_service import evaluate_guardrails
        record = self._base_record(executable=False)
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.7,
            market_not_executable_blocks_act=True,
            qualified_categories=None,
        )
        self.assertEqual(direction, "WAIT")
        self.assertIn("market_not_executable_blocks_act", fired)
        self.assertIn("不可执行", (reason or ""))

    def test_executable_true_does_not_fire(self):
        from app.services.guardrail_service import evaluate_guardrails
        record = self._base_record(executable=True)
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.7,
            market_not_executable_blocks_act=True,
            qualified_categories=None,
        )
        self.assertEqual(direction, "YES")
        self.assertEqual(fired, [])

    def test_rule4_skipped_when_execution_quality_absent(self):
        """When execution_quality key is absent (feature off), rule 4 is a no-op."""
        from app.services.guardrail_service import evaluate_guardrails
        record = {
            "llm_telemetry": {"degraded_mode": False},
            "decision_quality": {"conflict_score": 0.1},
        }
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.7,
            market_not_executable_blocks_act=True,
            qualified_categories=None,
        )
        self.assertEqual(direction, "YES")
        self.assertEqual(fired, [])

    def test_rule4_does_not_fire_on_wait(self):
        """WAIT is already conservative — rule 4 does not escalate."""
        from app.services.guardrail_service import evaluate_guardrails
        record = self._base_record(executable=False)
        direction, reason, fired = evaluate_guardrails(
            final_direction="WAIT",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.7,
            market_not_executable_blocks_act=True,
            qualified_categories=None,
        )
        self.assertEqual(direction, "WAIT")
        self.assertEqual(fired, [])

    def test_rule4_reason_excludes_banned_terms(self):
        from app.services.guardrail_service import evaluate_guardrails
        record = self._base_record(executable=False)
        _, reason, _ = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.7,
            market_not_executable_blocks_act=True,
            qualified_categories=None,
        )
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        for term in banned:
            self.assertNotIn(term, (reason or "").lower())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_guardrail_service.py::TestRule4MarketNotExecutable -v`
Expected: FAIL with `TypeError: evaluate_guardrails() got an unexpected keyword argument 'market_not_executable_blocks_act'`

- [ ] **Step 3: Add rule 4 to `guardrail_service.py`**

In `backend/app/services/guardrail_service.py`:

1. Update the `evaluate_guardrails` signature to add `market_not_executable_blocks_act: bool` parameter (after `high_conflict_threshold`):

```python
def evaluate_guardrails(
    *,
    final_direction: str | None,
    final_downgrade_reason: str | None,
    record: dict[str, Any],
    enabled: bool,
    llm_degraded_blocks_act: bool,
    uncalibrated_category_blocks_act: bool,
    high_conflict_blocks_act: bool,
    high_conflict_threshold: float,
    market_not_executable_blocks_act: bool = False,
    qualified_categories: set[str] | None = None,
) -> tuple[str | None, str | None, list[str]]:
```

2. Add rule 4 after rule 3 (after line 143, before the `if not fired:` check):

```python
    # Rule 4: Market not executable blocks strong actions.
    if market_not_executable_blocks_act and _is_market_not_executable(record):
        fired.append("market_not_executable_blocks_act")
        new_reasons.append("市场不可执行触发护栏，强方向降级为 WAIT。")
```

3. Add the helper function (after `_has_high_conflict`, around line 197):

```python
def _is_market_not_executable(record: dict[str, Any]) -> bool:
    """Rule 4 helper: True when ``execution_quality.executable`` is False.

    Returns False when ``execution_quality`` key is absent (feature off)
    or when ``executable`` is True. Only triggers on explicitly False —
    ``None`` (unknown) does not fire (fail-open for unknown state, since
    the service only sets True/False, never None).
    """
    eq = record.get("execution_quality")
    if not isinstance(eq, dict):
        return False
    return eq.get("executable") is False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_guardrail_service.py::TestRule4MarketNotExecutable -v`
Expected: All tests PASS

- [ ] **Step 5: Wire `execution_quality` overlay into `_build_all_overlays`**

In `backend/app/services/event_intelligence_service.py`, in the `_build_all_overlays` function:

1. After the `market_quality` block (around line 370, after the `except Exception` block for market_quality), add the `execution_quality` overlay:

```python
    # Phase 2b: Execution Quality overlay (Plan 3 §3.5). Best-effort audit
    # layer — only computed for ``source.type == "prediction_market"``.
    # ``build_execution_quality`` returns None for other source types, so
    # the record stays byte-identical to pre-Plan-3 for those sources.
    # When the feature flag is off, no ``execution_quality`` key is attached.
    try:
        if settings.EXECUTION_QUALITY_ENABLED:
            from app.services.execution_quality_service import build_execution_quality
            _overlay_t0 = time.perf_counter()
            eq = build_execution_quality(
                recommendation=record.get("actionable_recommendation"),
                source=record.get("source"),
                market_quote=record.get("market_quote"),
                volume=volume,
                liquidity=liquidity,
                max_spread_pct=settings.EXECUTION_MAX_SPREAD_PCT,
                stale_price_seconds=settings.EXECUTION_STALE_PRICE_SECONDS,
                min_liquidity=settings.EXECUTION_MIN_LIQUIDITY,
                target_order_size=settings.EXECUTION_TARGET_ORDER_SIZE,
                fee_rate_pct=settings.EXECUTION_FEE_RATE_PCT,
            )
            if eq is not None:
                record["execution_quality"] = eq
                from app.utils.metrics import record_overlay_latency
                record_overlay_latency("execution_quality", time.perf_counter() - _overlay_t0)
                if isinstance(eq, dict) and eq.get("downgraded"):
                    from app.utils.metrics import RULE_FIRE
                    RULE_FIRE.labels(rule="execution_quality_downgrade").inc()
    except Exception as exc:
        logger.warning("execution_quality build failed: %s", exc)
        from app.utils.metrics import record_overlay_build_failure
        record_overlay_build_failure("execution_quality")
        src = record.get("source")
        if isinstance(src, dict) and src.get("type") == "prediction_market":
            fallback_direction = (record.get("actionable_recommendation") or {}).get("direction", "WAIT")
            record["execution_quality"] = {
                "error": "build_failed",
                "executable": False,
                "effective_entry_price": None,
                "estimated_slippage_pct": None,
                "max_safe_position_size": None,
                "stale_price_flag": None,
                "platform_constraint_reasons": ["构建失败"],
                "raw_direction": fallback_direction,
                "suggested_direction": fallback_direction,
                "downgraded": False,
                "applied_to_displayed_direction": False,
            }
```

2. In the guardrail evaluation block (around line 520), add the new parameter to `evaluate_guardrails`:

```python
            fired_dir, fired_reason, fired_rules = evaluate_guardrails(
                final_direction=record.get("final_displayed_direction"),
                final_downgrade_reason=record.get("final_downgrade_reason"),
                record=record,
                enabled=True,
                llm_degraded_blocks_act=settings.GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT,
                uncalibrated_category_blocks_act=settings.GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT,
                high_conflict_blocks_act=settings.GUARDRAIL_HIGH_CONFLICT_BLOCKS_ACT,
                high_conflict_threshold=settings.GUARDRAIL_HIGH_CONFLICT_THRESHOLD,
                market_not_executable_blocks_act=settings.GUARDRAIL_MARKET_NOT_EXECUTABLE_BLOCKS_ACT,
                qualified_categories=qualified_cats,
            )
```

- [ ] **Step 6: Run the full guardrail + integration test suite**

Run: `cd backend && python -m pytest tests/test_guardrail_service.py tests/test_decision_quality_engine_integration.py -v`
Expected: All existing tests still pass + new rule 4 tests pass

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/guardrail_service.py backend/app/services/event_intelligence_service.py backend/tests/test_guardrail_service.py
git commit -m "feat(guardrail): add rule 4 market_not_executable_blocks_act + wire execution_quality overlay"
```

---

## Task 3: Degraded-mode scenario tests (spec §4.2)

**Files:**
- Modify: `backend/tests/test_decision_quality_engine_integration.py` (add `TestDegradedModeScenarios` class)

**Interfaces:**
- Consumes: `_build_all_overlays` from `event_intelligence_service`. Existing overlay services + guardrail service.
- Produces: `TestDegradedModeScenarios` class with 5 test methods verifying partial-failure safety.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_decision_quality_engine_integration.py`, add a new test class at the end:

```python
class TestDegradedModeScenarios(unittest.TestCase):
    """Spec §4.2: Degraded-mode scenario coverage.

    Verifies the pipeline produces safe output when individual overlays
    or LLM calls fail. Uses ``_build_all_overlays`` directly (no live LLM).
    """

    def _base_record(self, **overrides) -> dict:
        """Minimal record that exercises all 5 overlays + merge + guardrail."""
        record = {
            "event_id": "test-001",
            "question": "Will X happen?",
            "source": {"type": "prediction_market", "platform": "polymarket"},
            "actionable_recommendation": {
                "direction": "YES",
                "confidence": "high",
                "ai_probability": 0.72,
            },
            "evidence_breakdown": [
                {"direction": "support", "source": "reuters.com",
                 "url": "https://reuters.com/1", "summary": "Evidence 1"},
                {"direction": "oppose", "source": "bloomberg.com",
                 "url": "https://bloomberg.com/1", "summary": "Evidence 2"},
            ],
            "market_quote": {"bid": 48.0, "ask": 52.0, "spread": 4.0,
                              "last_updated": "2026-06-30T12:00:00+00:00"},
            "volume": 5000.0,
            "liquidity": 10000.0,
            "category": "politics",
            "llm_telemetry": {"degraded_mode": False},
        }
        record.update(overrides)
        return record

    @patch("app.core.config.settings")
    def test_all_overlays_enabled_merge_correctly(self, mock_settings):
        """All 5 overlays + execution_quality + guardrail enabled together."""
        from app.services.event_intelligence_service import _build_all_overlays
        mock_settings.DECISION_QUALITY_ENABLED = True
        mock_settings.MARKET_QUALITY_ENABLED = True
        mock_settings.SOURCE_RELIABILITY_ENABLED = True
        mock_settings.LLM_TELEMETRY_ENABLED = True
        mock_settings.GUARDRAILS_ENABLED = True
        mock_settings.EXECUTION_QUALITY_ENABLED = True
        mock_settings.DECISION_QUALITY_MAX_EVIDENCE_ITEMS = 10
        mock_settings.DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD = 0.7
        mock_settings.DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD = 0.4
        mock_settings.MARKET_MAX_SPREAD_PCT = 12.0
        mock_settings.MARKET_MIN_LIQUIDITY = 1000.0
        mock_settings.MARKET_MIN_VOLUME = 1000.0
        mock_settings.MARKET_QUALITY_SCORE_THRESHOLD = 0.5
        mock_settings.SOURCE_RELIABILITY_SCORE_THRESHOLD = 0.5
        mock_settings.SOURCE_RELIABILITY_MIN_TRUSTED_RATIO = 0.3
        mock_settings.SOURCE_RELIABILITY_MIN_DOMAIN_DIVERSITY = 2
        mock_settings.SOURCE_RELIABILITY_MIN_SOURCES = 2
        mock_settings.OPENAI_MODEL = "gpt-4"
        mock_settings.GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT = True
        mock_settings.GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT = True
        mock_settings.GUARDRAIL_HIGH_CONFLICT_BLOCKS_ACT = True
        mock_settings.GUARDRAIL_HIGH_CONFLICT_THRESHOLD = 0.7
        mock_settings.GUARDRAIL_MARKET_NOT_EXECUTABLE_BLOCKS_ACT = True
        mock_settings.EXECUTION_MAX_SPREAD_PCT = 12.0
        mock_settings.EXECUTION_STALE_PRICE_SECONDS = 300
        mock_settings.EXECUTION_MIN_LIQUIDITY = 1000.0
        mock_settings.EXECUTION_TARGET_ORDER_SIZE = 100.0
        mock_settings.EXECUTION_FEE_RATE_PCT = 1.0

        record = self._base_record()
        _build_all_overlays(record)
        # All overlays should be present
        self.assertIn("decision_quality", record)
        self.assertIn("market_quality", record)
        self.assertIn("source_reliability", record)
        self.assertIn("llm_telemetry", record)
        self.assertIn("execution_quality", record)
        self.assertIn("final_displayed_direction", record)

    @patch("app.core.config.settings")
    def test_all_phases_disabled_byte_identical(self, mock_settings):
        """All flags off → record has no overlay keys (pre-Phase-1 compatible)."""
        from app.services.event_intelligence_service import _build_all_overlays
        mock_settings.DECISION_QUALITY_ENABLED = False
        mock_settings.MARKET_QUALITY_ENABLED = False
        mock_settings.SOURCE_RELIABILITY_ENABLED = False
        mock_settings.LLM_TELEMETRY_ENABLED = False
        mock_settings.GUARDRAILS_ENABLED = False
        mock_settings.EXECUTION_QUALITY_ENABLED = False

        record = self._base_record()
        original_keys = set(record.keys())
        _build_all_overlays(record)
        # No new overlay keys added
        for key in ("decision_quality", "market_quality", "source_reliability",
                     "llm_telemetry", "execution_quality", "final_displayed_direction",
                     "final_downgrade_reason", "guardrail_fired"):
            self.assertNotIn(key, record, f"{key} should be absent when all flags off")

    @patch("app.core.config.settings")
    def test_llm_degraded_still_produces_recommendation(self, mock_settings):
        """When llm_telemetry.degraded_mode=True, guardrail forces YES → WAIT.

        The pipeline still produces a recommendation (WAIT), not an error.
        """
        from app.services.event_intelligence_service import _build_all_overlays
        mock_settings.DECISION_QUALITY_ENABLED = True
        mock_settings.MARKET_QUALITY_ENABLED = True
        mock_settings.SOURCE_RELIABILITY_ENABLED = True
        mock_settings.LLM_TELEMETRY_ENABLED = True
        mock_settings.GUARDRAILS_ENABLED = True
        mock_settings.EXECUTION_QUALITY_ENABLED = True
        mock_settings.DECISION_QUALITY_MAX_EVIDENCE_ITEMS = 10
        mock_settings.DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD = 0.7
        mock_settings.DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD = 0.4
        mock_settings.MARKET_MAX_SPREAD_PCT = 12.0
        mock_settings.MARKET_MIN_LIQUIDITY = 1000.0
        mock_settings.MARKET_MIN_VOLUME = 1000.0
        mock_settings.MARKET_QUALITY_SCORE_THRESHOLD = 0.5
        mock_settings.SOURCE_RELIABILITY_SCORE_THRESHOLD = 0.5
        mock_settings.SOURCE_RELIABILITY_MIN_TRUSTED_RATIO = 0.3
        mock_settings.SOURCE_RELIABILITY_MIN_DOMAIN_DIVERSITY = 2
        mock_settings.SOURCE_RELIABILITY_MIN_SOURCES = 2
        mock_settings.OPENAI_MODEL = "gpt-4"
        mock_settings.GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT = True
        mock_settings.GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT = False
        mock_settings.GUARDRAIL_HIGH_CONFLICT_BLOCKS_ACT = False
        mock_settings.GUARDRAIL_HIGH_CONFLICT_THRESHOLD = 0.7
        mock_settings.GUARDRAIL_MARKET_NOT_EXECUTABLE_BLOCKS_ACT = True
        mock_settings.EXECUTION_MAX_SPREAD_PCT = 12.0
        mock_settings.EXECUTION_STALE_PRICE_SECONDS = 300
        mock_settings.EXECUTION_MIN_LIQUIDITY = 1000.0
        mock_settings.EXECUTION_TARGET_ORDER_SIZE = 100.0
        mock_settings.EXECUTION_FEE_RATE_PCT = 1.0

        record = self._base_record()
        record["llm_telemetry"] = {"degraded_mode": True}
        _build_all_overlays(record)
        # Guardrail should have forced YES → WAIT
        self.assertEqual(record.get("final_displayed_direction"), "WAIT")
        self.assertIn("guardrail_fired", record)
        self.assertIn("llm_degraded_blocks_act", record["guardrail_fired"])

    @patch("app.core.config.settings")
    def test_non_prediction_market_has_no_market_or_execution_quality(self, mock_settings):
        """open_web / sports_event sources omit market_quality AND execution_quality."""
        from app.services.event_intelligence_service import _build_all_overlays
        mock_settings.DECISION_QUALITY_ENABLED = True
        mock_settings.MARKET_QUALITY_ENABLED = True
        mock_settings.SOURCE_RELIABILITY_ENABLED = True
        mock_settings.LLM_TELEMETRY_ENABLED = True
        mock_settings.GUARDRAILS_ENABLED = False  # no qualified_categories needed
        mock_settings.EXECUTION_QUALITY_ENABLED = True
        mock_settings.DECISION_QUALITY_MAX_EVIDENCE_ITEMS = 10
        mock_settings.DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD = 0.7
        mock_settings.DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD = 0.4
        mock_settings.MARKET_MAX_SPREAD_PCT = 12.0
        mock_settings.MARKET_MIN_LIQUIDITY = 1000.0
        mock_settings.MARKET_MIN_VOLUME = 1000.0
        mock_settings.MARKET_QUALITY_SCORE_THRESHOLD = 0.5
        mock_settings.SOURCE_RELIABILITY_SCORE_THRESHOLD = 0.5
        mock_settings.SOURCE_RELIABILITY_MIN_TRUSTED_RATIO = 0.3
        mock_settings.SOURCE_RELIABILITY_MIN_DOMAIN_DIVERSITY = 2
        mock_settings.SOURCE_RELIABILITY_MIN_SOURCES = 2
        mock_settings.OPENAI_MODEL = "gpt-4"
        mock_settings.EXECUTION_MAX_SPREAD_PCT = 12.0
        mock_settings.EXECUTION_STALE_PRICE_SECONDS = 300
        mock_settings.EXECUTION_MIN_LIQUIDITY = 1000.0
        mock_settings.EXECUTION_TARGET_ORDER_SIZE = 100.0
        mock_settings.EXECUTION_FEE_RATE_PCT = 1.0

        record = self._base_record()
        record["source"] = {"type": "open_web"}
        _build_all_overlays(record)
        self.assertNotIn("market_quality", record)
        self.assertNotIn("execution_quality", record)

    @patch("app.core.config.settings")
    def test_market_not_executable_forces_wait(self, mock_settings):
        """execution_quality.executable=False → guardrail rule 4 → WAIT."""
        from app.services.event_intelligence_service import _build_all_overlays
        mock_settings.DECISION_QUALITY_ENABLED = True
        mock_settings.MARKET_QUALITY_ENABLED = True
        mock_settings.SOURCE_RELIABILITY_ENABLED = True
        mock_settings.LLM_TELEMETRY_ENABLED = True
        mock_settings.GUARDRAILS_ENABLED = True
        mock_settings.EXECUTION_QUALITY_ENABLED = True
        mock_settings.DECISION_QUALITY_MAX_EVIDENCE_ITEMS = 10
        mock_settings.DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD = 0.7
        mock_settings.DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD = 0.4
        # Wide spread → execution_quality.executable=False
        mock_settings.MARKET_MAX_SPREAD_PCT = 12.0
        mock_settings.MARKET_MIN_LIQUIDITY = 1000.0
        mock_settings.MARKET_MIN_VOLUME = 1000.0
        mock_settings.MARKET_QUALITY_SCORE_THRESHOLD = 0.5
        mock_settings.SOURCE_RELIABILITY_SCORE_THRESHOLD = 0.5
        mock_settings.SOURCE_RELIABILITY_MIN_TRUSTED_RATIO = 0.3
        mock_settings.SOURCE_RELIABILITY_MIN_DOMAIN_DIVERSITY = 2
        mock_settings.SOURCE_RELIABILITY_MIN_SOURCES = 2
        mock_settings.OPENAI_MODEL = "gpt-4"
        mock_settings.GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT = False
        mock_settings.GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT = False
        mock_settings.GUARDRAIL_HIGH_CONFLICT_BLOCKS_ACT = False
        mock_settings.GUARDRAIL_HIGH_CONFLICT_THRESHOLD = 0.7
        mock_settings.GUARDRAIL_MARKET_NOT_EXECUTABLE_BLOCKS_ACT = True
        mock_settings.EXECUTION_MAX_SPREAD_PCT = 12.0
        mock_settings.EXECUTION_STALE_PRICE_SECONDS = 300
        mock_settings.EXECUTION_MIN_LIQUIDITY = 1000.0
        mock_settings.EXECUTION_TARGET_ORDER_SIZE = 100.0
        mock_settings.EXECUTION_FEE_RATE_PCT = 1.0

        record = self._base_record()
        record["market_quote"] = {"bid": 40.0, "ask": 60.0, "spread": 20.0}  # wide spread
        _build_all_overlays(record)
        self.assertIn("execution_quality", record)
        self.assertFalse(record["execution_quality"]["executable"])
        self.assertEqual(record.get("final_displayed_direction"), "WAIT")
        self.assertIn("guardrail_fired", record)
        self.assertIn("market_not_executable_blocks_act", record["guardrail_fired"])
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_decision_quality_engine_integration.py::TestDegradedModeScenarios -v`
Expected: All 5 tests PASS (implementation already done in Tasks 1-2)

- [ ] **Step 3: Run full backend suite for regression**

Run: `cd backend && python -m pytest tests/ -q --ignore=tests/test_gbm_engine.py`
Expected: All existing tests pass + new tests pass

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_decision_quality_engine_integration.py
git commit -m "test(degraded-mode): add spec §4.2 degraded-mode scenario coverage (5 integration tests)"
```

---

## Self-Review Checklist

**1. Spec coverage:**
- §3.5 `execution_quality` field with `executable`, `effective_entry_price`, `estimated_slippage_pct`, `max_safe_position_size`, `stale_price_flag`, `platform_constraint_reasons` → Task 1 ✓
- §3.5 stale price detection (`last_updated` > threshold → downgrade) → Task 1 `test_stale_price_flag_true_when_last_updated_old` ✓
- §3.5 slippage estimation → Task 1 `test_executable_when_spread_tight_and_liquid` ✓
- §3.5 platform constraints (fees, min order size) → Task 1 `test_platform_constraint_reasons_list_is_chinese` + `EXECUTION_FEE_RATE_PCT` ✓
- §4.2 `test_all_phases_degraded_still_produces_recommendation` → Task 3 `test_llm_degraded_still_produces_recommendation` ✓
- §4.2 `test_market_quality_disabled_when_source_not_prediction_market` → Task 3 `test_non_prediction_market_has_no_market_or_execution_quality` ✓
- §4.2 `test_all_phases_enabled_merge_correctly` → Task 3 `test_all_overlays_enabled_merge_correctly` ✓
- §4.2 `test_all_phases_disabled_backward_compatible` → Task 3 `test_all_phases_disabled_byte_identical` ✓

**2. Placeholder scan:** No TBD/TODO/handle-edge-cases. All code blocks are complete.

**3. Type consistency:** `build_execution_quality` signature matches between Task 1 (definition) and Task 2 (wiring). `evaluate_guardrails` signature extension is backward-compatible (new param has default `False`). `execution_quality` block keys match between Task 1 (return) and Task 2 (guardrail helper `_is_market_not_executable` reads `executable`).
