"""Tests for market_quality_service (Phase 2).

Pure-function tests for build_market_quality() and merge_quality_overlays().
Verifies:
- source.type gating (only prediction_market produces a block)
- spread penalty calculation (Kalshi bid/ask)
- liquidity/volume scoring and thin_market_flag
- stale_price_flag always None in Phase 2 (no last_updated)
- missing market_quote -> spread_penalty=None (graceful degradation)
- downgrade YES/NO -> WAIT when score < threshold
- WAIT/AVOID never downgraded by market quality
- merge: most-strict direction wins
- merge: reasons concatenated when both downgrade to same severity
- no-writeback invariant
- adversarial input never raises
"""
import copy
import unittest

from app.services.market_quality_service import (
    build_market_quality,
    merge_quality_overlays,
)


def _recommendation(direction: str = "YES") -> dict:
    return {"direction": direction, "risk_level": "medium"}


def _polymarket_source() -> dict:
    return {"type": "prediction_market", "platform": "Polymarket"}


def _kalshi_source() -> dict:
    return {"type": "prediction_market", "platform": "Kalshi"}


def _metaculus_source() -> dict:
    return {"type": "prediction_question", "platform": "Metaculus"}


def _manual_source() -> dict:
    return {"type": "manual"}


DEFAULT_KWARGS = {
    "max_spread_pct": 12.0,
    "min_liquidity": 1000.0,
    "min_volume": 1000.0,
    "score_threshold": 0.5,
}


class BuildMarketQualityTests(unittest.TestCase):

    # --- Source Type Gating ---

    def test_metaculus_excluded(self):
        """Metaculus (prediction_question) produces NO market_quality block."""
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_metaculus_source(),
            market_quote=None,
            volume=500,
            liquidity=0.0,
            **DEFAULT_KWARGS,
        )
        self.assertIsNone(result)

    def test_manual_excluded(self):
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_manual_source(),
            market_quote=None,
            volume=None,
            liquidity=None,
            **DEFAULT_KWARGS,
        )
        self.assertIsNone(result)

    def test_none_source_excluded(self):
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=None,
            market_quote=None,
            volume=None,
            liquidity=None,
            **DEFAULT_KWARGS,
        )
        self.assertIsNone(result)

    def test_polymarket_included(self):
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_polymarket_source(),
            market_quote=None,
            volume=5000,
            liquidity=5000,
            **DEFAULT_KWARGS,
        )
        self.assertIsNotNone(result)

    def test_kalshi_included(self):
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_kalshi_source(),
            market_quote={"bid": 48, "ask": 52, "spread": 4},
            volume=5000,
            liquidity=5000,
            **DEFAULT_KWARGS,
        )
        self.assertIsNotNone(result)

    # --- Spread Penalty ---

    def test_spread_penalty_computed_from_market_quote(self):
        """Kalshi with real spread: penalty = spread / 100."""
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_kalshi_source(),
            market_quote={"bid": 45, "ask": 55, "spread": 10},
            volume=5000,
            liquidity=5000,
            **DEFAULT_KWARGS,
        )
        self.assertIsNotNone(result)
        self.assertIsNotNone(result["spread_penalty"])
        self.assertAlmostEqual(result["spread_penalty"], 0.10, places=2)

    def test_spread_penalty_none_when_market_quote_missing(self):
        """Polymarket has no bid_ask -> spread_penalty is None (unknown)."""
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_polymarket_source(),
            market_quote=None,
            volume=5000,
            liquidity=5000,
            **DEFAULT_KWARGS,
        )
        self.assertIsNotNone(result)
        self.assertIsNone(result["spread_penalty"])

    def test_spread_penalty_none_when_spread_zero(self):
        """Kalshi placeholder bid/ask (0,0,0) -> spread_penalty is None."""
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_kalshi_source(),
            market_quote={"bid": 0, "ask": 0, "spread": 0},
            volume=5000,
            liquidity=5000,
            **DEFAULT_KWARGS,
        )
        self.assertIsNotNone(result)
        self.assertIsNone(result["spread_penalty"])

    # --- Liquidity / Volume ---

    def test_thin_market_flag_when_liquidity_low(self):
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_polymarket_source(),
            market_quote=None,
            volume=5000,
            liquidity=500,  # below min
            **DEFAULT_KWARGS,
        )
        self.assertTrue(result["thin_market_flag"])

    def test_thin_market_flag_when_volume_low(self):
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_polymarket_source(),
            market_quote=None,
            volume=500,  # below min
            liquidity=5000,
            **DEFAULT_KWARGS,
        )
        self.assertTrue(result["thin_market_flag"])

    def test_thin_market_flag_false_when_both_above_min(self):
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_polymarket_source(),
            market_quote=None,
            volume=5000,
            liquidity=5000,
            **DEFAULT_KWARGS,
        )
        self.assertFalse(result["thin_market_flag"])

    def test_thin_market_flag_false_when_values_unknown(self):
        """Missing liquidity/volume does NOT trigger thin_market_flag."""
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_polymarket_source(),
            market_quote=None,
            volume=None,
            liquidity=None,
            **DEFAULT_KWARGS,
        )
        self.assertFalse(result["thin_market_flag"])

    def test_liquidity_score_scales_linearly(self):
        """liquidity at min -> 1.0; at half min -> 0.5; below 0 -> None."""
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_polymarket_source(),
            market_quote=None,
            volume=5000,
            liquidity=500,  # half of min (1000)
            **DEFAULT_KWARGS,
        )
        self.assertAlmostEqual(result["liquidity_score"], 0.5, places=2)

    def test_volume_score_none_when_missing(self):
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_polymarket_source(),
            market_quote=None,
            volume=None,
            liquidity=5000,
            **DEFAULT_KWARGS,
        )
        self.assertIsNone(result["volume_score"])

    # --- Stale Price Flag ---

    def test_stale_price_flag_always_none_in_phase2(self):
        """Per audit: no adapter exposes last_updated. Stale check is no-op."""
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_kalshi_source(),
            market_quote={"bid": 48, "ask": 52, "spread": 4},
            volume=5000,
            liquidity=5000,
            **DEFAULT_KWARGS,
        )
        self.assertIsNone(result["stale_price_flag"])

    # --- Score and Downgrade ---

    def test_score_high_when_market_healthy(self):
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_polymarket_source(),
            market_quote=None,
            volume=5000,
            liquidity=5000,
            **DEFAULT_KWARGS,
        )
        self.assertGreaterEqual(result["score"], 0.5)

    def test_score_low_when_thin_market(self):
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_polymarket_source(),
            market_quote=None,
            volume=100,  # well below min
            liquidity=100,
            **DEFAULT_KWARGS,
        )
        self.assertLess(result["score"], 0.5)

    def test_downgrade_yes_to_wait_when_score_low(self):
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_polymarket_source(),
            market_quote=None,
            volume=100,
            liquidity=100,
            **DEFAULT_KWARGS,
        )
        self.assertEqual(result["suggested_direction"], "WAIT")
        self.assertTrue(result["downgraded"])
        self.assertIsNotNone(result["downgrade_reason"])
        self.assertIn("市场质量", result["downgrade_reason"])

    def test_downgrade_no_to_wait_when_score_low(self):
        result = build_market_quality(
            recommendation=_recommendation("NO"),
            source=_polymarket_source(),
            market_quote=None,
            volume=100,
            liquidity=100,
            **DEFAULT_KWARGS,
        )
        self.assertEqual(result["suggested_direction"], "WAIT")
        self.assertTrue(result["downgraded"])

    def test_wait_not_downgraded_by_market_quality(self):
        """WAIT is already cautious — market quality never downgrades it."""
        result = build_market_quality(
            recommendation=_recommendation("WAIT"),
            source=_polymarket_source(),
            market_quote=None,
            volume=0,
            liquidity=0,
            **DEFAULT_KWARGS,
        )
        self.assertEqual(result["suggested_direction"], "WAIT")
        self.assertFalse(result["downgraded"])
        self.assertIsNone(result["downgrade_reason"])

    def test_avoid_not_downgraded_by_market_quality(self):
        result = build_market_quality(
            recommendation=_recommendation("AVOID"),
            source=_polymarket_source(),
            market_quote=None,
            volume=0,
            liquidity=0,
            **DEFAULT_KWARGS,
        )
        self.assertEqual(result["suggested_direction"], "AVOID")
        self.assertFalse(result["downgraded"])

    def test_no_downgrade_when_score_above_threshold(self):
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_polymarket_source(),
            market_quote=None,
            volume=5000,
            liquidity=5000,
            **DEFAULT_KWARGS,
        )
        self.assertEqual(result["suggested_direction"], "YES")
        self.assertFalse(result["downgraded"])
        self.assertIsNone(result["downgrade_reason"])

    def test_applied_to_displayed_direction_defaults_false(self):
        """The service sets this to False; the merge step may override."""
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_polymarket_source(),
            market_quote=None,
            volume=5000,
            liquidity=5000,
            **DEFAULT_KWARGS,
        )
        self.assertFalse(result["applied_to_displayed_direction"])

    # --- raw_direction ---

    def test_raw_direction_mirrors_recommendation(self):
        for direction in ("YES", "NO", "WAIT", "AVOID"):
            result = build_market_quality(
                recommendation=_recommendation(direction),
                source=_polymarket_source(),
                market_quote=None,
                volume=5000,
                liquidity=5000,
                **DEFAULT_KWARGS,
            )
            self.assertEqual(result["raw_direction"], direction)

    def test_raw_direction_wait_when_recommendation_none(self):
        result = build_market_quality(
            recommendation=None,
            source=_polymarket_source(),
            market_quote=None,
            volume=5000,
            liquidity=5000,
            **DEFAULT_KWARGS,
        )
        self.assertEqual(result["raw_direction"], "WAIT")

    # --- No-Writeback ---

    def test_no_writeback_to_inputs(self):
        rec = _recommendation("YES")
        source = _polymarket_source()
        mq = {"bid": 48, "ask": 52, "spread": 4}
        rec_before = copy.deepcopy(rec)
        source_before = copy.deepcopy(source)
        mq_before = copy.deepcopy(mq)
        build_market_quality(
            recommendation=rec, source=source, market_quote=mq,
            volume=5000, liquidity=5000, **DEFAULT_KWARGS,
        )
        self.assertEqual(rec, rec_before)
        self.assertEqual(source, source_before)
        self.assertEqual(mq, mq_before)

    # --- Adversarial Input ---

    def test_adversarial_input_never_raises(self):
        result = build_market_quality(
            recommendation="not a dict",
            source="not a dict",
            market_quote="not a dict",
            volume="not a number",
            liquidity=None,
            **DEFAULT_KWARGS,
        )
        # source is not a dict -> not prediction_market -> None
        self.assertIsNone(result)

    def test_adversarial_market_quote_does_not_crash(self):
        """market_quote with non-numeric spread should not crash."""
        result = build_market_quality(
            recommendation=_recommendation("YES"),
            source=_kalshi_source(),
            market_quote={"bid": "abc", "ask": None, "spread": "xyz"},
            volume=5000,
            liquidity=5000,
            **DEFAULT_KWARGS,
        )
        self.assertIsNotNone(result)
        self.assertIsNone(result["spread_penalty"])


class MergeQualityOverlaysTests(unittest.TestCase):
    """Tests for merge_quality_overlays() — parallel + most-strict semantics."""

    def test_both_none_returns_none(self):
        direction, reason, applied, source_applied = merge_quality_overlays(None, None)
        self.assertIsNone(direction)
        self.assertIsNone(reason)
        self.assertFalse(applied)
        self.assertFalse(source_applied)

    def test_only_decision_quality(self):
        dq = {"displayed_direction": "WAIT", "downgrade_reason": "证据冲突"}
        direction, reason, applied, source_applied = merge_quality_overlays(dq, None)
        self.assertEqual(direction, "WAIT")
        self.assertEqual(reason, "证据冲突")
        self.assertFalse(applied)
        self.assertFalse(source_applied)

    def test_only_market_quality(self):
        mq = {"suggested_direction": "WAIT", "downgrade_reason": "市场质量不足"}
        direction, reason, applied, source_applied = merge_quality_overlays(None, mq)
        self.assertEqual(direction, "WAIT")
        self.assertEqual(reason, "市场质量不足")
        self.assertFalse(applied)
        self.assertFalse(source_applied)

    def test_market_stricter_than_decision(self):
        """dq=YES, mq=WAIT -> market wins, applied=True."""
        dq = {"displayed_direction": "YES", "downgrade_reason": None}
        mq = {"suggested_direction": "WAIT", "downgrade_reason": "市场质量不足"}
        direction, reason, applied, source_applied = merge_quality_overlays(dq, mq)
        self.assertEqual(direction, "WAIT")
        self.assertEqual(reason, "市场质量不足")
        self.assertTrue(applied)
        self.assertFalse(source_applied)

    def test_decision_stricter_than_market(self):
        """dq=AVOID, mq=WAIT -> decision wins, applied=False."""
        dq = {"displayed_direction": "AVOID", "downgrade_reason": "高风险"}
        mq = {"suggested_direction": "WAIT", "downgrade_reason": "市场质量不足"}
        direction, reason, applied, source_applied = merge_quality_overlays(dq, mq)
        self.assertEqual(direction, "AVOID")
        self.assertEqual(reason, "高风险")
        self.assertFalse(applied)
        self.assertFalse(source_applied)

    def test_both_wait_reasons_concatenated(self):
        """Both downgraded to WAIT -> reasons concatenated with ' | '."""
        dq = {"displayed_direction": "WAIT", "downgrade_reason": "证据冲突较高"}
        mq = {"suggested_direction": "WAIT", "downgrade_reason": "市场质量不足"}
        direction, reason, applied, source_applied = merge_quality_overlays(dq, mq)
        self.assertEqual(direction, "WAIT")
        self.assertIn("证据冲突较高", reason)
        self.assertIn("市场质量不足", reason)
        self.assertIn(" | ", reason)
        self.assertTrue(applied)
        self.assertFalse(source_applied)

    def test_both_avoid_reasons_concatenated(self):
        dq = {"displayed_direction": "AVOID", "downgrade_reason": "高风险"}
        mq = {"suggested_direction": "AVOID", "downgrade_reason": "市场极端"}
        direction, reason, applied, source_applied = merge_quality_overlays(dq, mq)
        self.assertEqual(direction, "AVOID")
        self.assertIn(" | ", reason)
        self.assertTrue(applied)
        self.assertFalse(source_applied)

    def test_market_not_applied_when_same_severity_no_market_reason(self):
        """mq has no downgrade_reason but same direction -> applied=False."""
        dq = {"displayed_direction": "WAIT", "downgrade_reason": "证据冲突"}
        mq = {"suggested_direction": "WAIT", "downgrade_reason": None}
        direction, reason, applied, source_applied = merge_quality_overlays(dq, mq)
        self.assertEqual(direction, "WAIT")
        self.assertEqual(reason, "证据冲突")
        self.assertFalse(applied)
        self.assertFalse(source_applied)

    def test_neither_downgraded_returns_raw(self):
        """dq=YES (not downgraded), mq=YES (not downgraded) -> YES, no reason."""
        dq = {"displayed_direction": "YES", "downgrade_reason": None}
        mq = {"suggested_direction": "YES", "downgrade_reason": None}
        direction, reason, applied, source_applied = merge_quality_overlays(dq, mq)
        self.assertEqual(direction, "YES")
        self.assertIsNone(reason)
        self.assertFalse(applied)
        self.assertFalse(source_applied)


if __name__ == "__main__":
    unittest.main()
