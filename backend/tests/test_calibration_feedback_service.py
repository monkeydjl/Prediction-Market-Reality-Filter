"""Tests for the calibration feedback loop (calibration_feedback_service).

Pure-math unit tests over synthetic resolved-event records: the per-component
and per-category Brier history, the dormancy below min_samples, weighted fusion,
base-rate shrinkage, and the combined adjust_probability. The store seam
(_load_resolved_records) is bypassed by passing resolved_records explicitly, so
these are I/O-free.
"""

import unittest

from app.services.calibration_feedback_service import (
    adjust_probability,
    briers_by_component,
    category_briers,
    category_shrinkage,
    component_weights,
    fuse_components,
    shrink_to_prior,
)


def _record(market, llm, actual, *, cross=None, category="legal", brier=None):
    """Synthesize one resolved-event record dict.

    Components are scored on the fly from (market, llm, cross) vs actual, mirroring
    what analyze_event records and resolve attaches. `brier` sets the main
    published-estimate calibration used by category shrinkage; defaults to the
    LLM component's own Brier so a category's history tracks the LLM by default.
    """
    components = {"market": market, "llm": llm}
    if cross is not None:
        components["cross_validation"] = cross
    if brier is None:
        brier = ((llm - actual) / 100.0) ** 2
    return {
        "calibration_components": components,
        "outcome": {"actual_outcome": actual},
        "legacy_analysis": {"base_rate_category": category},
        "calibration": {"brier_score": brier},
    }


class BriersByComponentTests(unittest.TestCase):
    def test_scores_each_component_against_outcome(self):
        recs = [_record(market=20, llm=80, actual=100)]
        briers = briers_by_component(recs)
        # market: ((20-100)/100)^2 = 0.64 ; llm: ((80-100)/100)^2 = 0.04
        self.assertAlmostEqual(briers["market"][0], 0.64)
        self.assertAlmostEqual(briers["llm"][0], 0.04)

    def test_skips_records_without_components_or_outcome(self):
        recs = [
            {"outcome": {"actual_outcome": 100}},  # no components
            {"calibration_components": {"llm": 50}},  # no outcome
            {"calibration_components": {"llm": 50},
             "outcome": {"actual_outcome": "nan"}},  # non-finite outcome
            _record(market=40, llm=60, actual=100),  # valid
        ]
        briers = briers_by_component(recs)
        self.assertEqual(len(briers["llm"]), 1)

    def test_skips_non_finite_component_prediction(self):
        recs = [{
            "calibration_components": {"llm": 60, "market": float("inf")},
            "outcome": {"actual_outcome": 100},
        }]
        briers = briers_by_component(recs)
        self.assertIn("llm", briers)
        self.assertNotIn("market", briers)


class CategoryBriersTests(unittest.TestCase):
    def test_filters_by_category(self):
        recs = [
            _record(50, 60, 100, category="legal", brier=0.1),
            _record(50, 60, 100, category="crypto_price_btc", brier=0.2),
            _record(50, 60, 100, category="legal", brier=0.3),
        ]
        self.assertEqual(sorted(category_briers(recs, "legal")), [0.1, 0.3])
        self.assertEqual(category_briers(recs, "crypto_price_btc"), [0.2])

    def test_missing_category_is_unknown(self):
        recs = [{"calibration": {"brier_score": 0.1}}]  # no legacy_analysis
        self.assertEqual(category_briers(recs, "unknown"), [0.1])


class ComponentWeightsTests(unittest.TestCase):
    def test_empty_below_min_samples(self):
        recs = [_record(20, 80, 100) for _ in range(3)]
        self.assertEqual(component_weights(recs, min_samples=8), {})

    def test_accurate_component_gets_more_weight(self):
        # llm always near outcome (low brier), market always far (high brier).
        recs = [_record(market=10, llm=95, actual=100) for _ in range(8)]
        weights = component_weights(recs, min_samples=8)
        self.assertEqual(set(weights), {"market", "llm"})
        self.assertGreater(weights["llm"], weights["market"])
        self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_empty_when_only_one_component_qualifies(self):
        # market present in all, cross only in a few -> only market qualifies ->
        # fewer than two qualifying components -> no weighting.
        recs = [_record(20, 80, 100) for _ in range(8)]
        weights = component_weights(recs, min_samples=8)
        # market and llm both have 8 -> two qualify, so this returns weights.
        self.assertEqual(set(weights), {"market", "llm"})


class ShrinkageTests(unittest.TestCase):
    def test_zero_below_min_samples(self):
        recs = [_record(50, 60, 100, brier=0.25) for _ in range(3)]
        self.assertEqual(category_shrinkage(recs, "legal", min_samples=8), 0.0)

    def test_perfect_history_no_shrink(self):
        recs = [_record(50, 100, 100, brier=0.0) for _ in range(8)]
        self.assertEqual(category_shrinkage(recs, "legal", min_samples=8), 0.0)

    def test_random_history_caps_at_max(self):
        recs = [_record(50, 0, 100, brier=0.25) for _ in range(8)]
        # mean brier 0.25 -> ratio 1.0 -> _MAX_SHRINK (0.5)
        self.assertAlmostEqual(category_shrinkage(recs, "legal", min_samples=8), 0.5)

    def test_overconfident_history_scales_linearly(self):
        recs = [_record(50, 60, 100, brier=0.125) for _ in range(8)]
        # ratio 0.5 -> 0.5 * _MAX_SHRINK = 0.25
        self.assertAlmostEqual(category_shrinkage(recs, "legal", min_samples=8), 0.25)


class FuseComponentsTests(unittest.TestCase):
    def test_falls_back_to_llm_when_no_weights(self):
        self.assertEqual(fuse_components({"market": 40, "llm": 70}, {}), 70)

    def test_weighted_average_over_overlap(self):
        fused = fuse_components(
            {"market": 40.0, "llm": 80.0},
            {"market": 0.25, "llm": 0.75},
        )
        self.assertAlmostEqual(fused, 40 * 0.25 + 80 * 0.75)

    def test_ignores_components_without_weight(self):
        # cross_validation has no weight -> excluded; market+llm fused.
        fused = fuse_components(
            {"market": 40.0, "llm": 80.0, "cross_validation": 10.0},
            {"market": 0.5, "llm": 0.5},
        )
        self.assertAlmostEqual(fused, 60.0)


class ShrinkToPriorTests(unittest.TestCase):
    def test_endpoints(self):
        self.assertEqual(shrink_to_prior(80, 50, 0.0), 80)
        self.assertEqual(shrink_to_prior(80, 50, 1.0), 50)
        self.assertEqual(shrink_to_prior(80, 50, 0.5), 65)


class AdjustProbabilityTests(unittest.TestCase):
    def test_dormant_returns_llm_estimate(self):
        """With no history the feedback is a no-op: adjusted == llm."""
        adjusted, info = adjust_probability(
            {"market": 40.0, "llm": 75.0, "cross_validation": 60.0},
            "legal", 50.0,
            resolved_records=[], min_samples=8,
        )
        self.assertEqual(adjusted, 75.0)
        self.assertEqual(info["weights"], {})
        self.assertEqual(info["shrinkage"], 0.0)

    def test_full_history_fuses_and_shrinks(self):
        # llm accurate, market poor, and a moderately overconfident category.
        recs = [_record(market=10, llm=90, actual=100, brier=0.125)
                for _ in range(8)]
        adjusted, info = adjust_probability(
            {"market": 10.0, "llm": 90.0}, "legal", 50.0,
            resolved_records=recs, min_samples=8,
        )
        # llm dominates fusion (fused near 90), then shrink 0.25 toward prior 50.
        self.assertGreater(info["weights"]["llm"], info["weights"]["market"])
        self.assertAlmostEqual(info["shrinkage"], 0.25)
        self.assertEqual(adjusted, round(info["fused"] * 0.75 + 50 * 0.25, 2))

    def test_result_is_clamped(self):
        recs = [_record(market=200, llm=200, actual=100, brier=0.0)
                for _ in range(8)]
        adjusted, _ = adjust_probability(
            {"market": 200.0, "llm": 200.0}, "legal", 50.0,
            resolved_records=recs, min_samples=8,
        )
        self.assertLessEqual(adjusted, 100.0)


if __name__ == "__main__":
    unittest.main()
