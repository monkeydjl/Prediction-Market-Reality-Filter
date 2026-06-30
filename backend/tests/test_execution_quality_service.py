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
    # NOTE (test-side fix beyond plan): spread default corrected from 2.0
    # to 4.0 to match bid=48, ask=52 (bid-ask=4). With spread=4.0:
    # half-spread=2.0, mid=50 → slippage_pct=(2/50)*100=4.0 (matches the
    # corrected test assertion for relative-to-mid slippage).
    spread: float | None = 4.0,
    bid: float | None = 48.0,
    ask: float | None = 52.0,
    # NOTE (test-side fix beyond plan): last_updated default changed from
    # "2026-06-30T12:00:00+00:00" to None. The plan's hardcoded date made
    # tests date-dependent: they passed on 2026-06-30 but failed afterward
    # because the price became stale relative to datetime.now(utc). With
    # None, stale_price_flag=None (unknown) for tests that don't patch
    # _now, matching the "absent last_updated" convention. The single test
    # that exercises staleness (test_stale_price_flag_true_when_last_updated_old)
    # explicitly sets last_updated + patches _now.
    last_updated: str | None = None,
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
        kwargs = _rec()  # spread=4.0, liquidity=10000, volume=5000
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
        # Slippage = (half-spread / mid) * 100, as a true percentage.
        # spread=4, mid=50 → (2/50)*100 = 4.0 (meaning 4%).
        self.assertAlmostEqual(result["estimated_slippage_pct"], 4.0, places=4)
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

    def test_missing_market_quote_defaults_to_executable(self):
        """When market_quote has no bid/ask (Polymarket/Kalshi last_price only),
        the service cannot assess spread/slippage but MUST NOT block execution.
        executable=True (default — unable to assess ≠ not executable),
        slippage/entry_price None (unknown). Rule 4 does not fire on unknown.
        Regression for post-merge bug: previously added "行情数据缺失" constraint
        which forced executable=False → Rule 4 → WAIT on real Polymarket/Kalshi
        sources that don't provide bid_ask.
        """
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
        # No bid/ask → cannot assess spread → no constraint → executable=True
        # (default open: only explicit evidence blocks execution).
        self.assertTrue(result["executable"])
        self.assertIsNone(result["estimated_slippage_pct"])
        self.assertIsNone(result["effective_entry_price"])
        self.assertEqual(result["platform_constraint_reasons"], [])
        self.assertFalse(result["downgraded"])

    def test_kalshi_zero_bid_ask_placeholder_does_not_block(self):
        """Kalshi returns {"bid":0,"ask":0,"spread":0} when only last_price is
        available. mid=None (bid>0 check fails) → spread_pct=None. This MUST
        NOT add "行情数据缺失" constraint or force executable=False. Regression
        for post-merge bug.
        """
        kwargs = _rec()
        kwargs["market_quote"] = {"bid": 0.0, "ask": 0.0, "spread": 0.0}
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
        self.assertIsNone(result["estimated_slippage_pct"])
        self.assertEqual(result["platform_constraint_reasons"], [])

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
