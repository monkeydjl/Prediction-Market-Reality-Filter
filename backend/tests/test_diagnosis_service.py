"""Tests for diagnosis_service (M2 Disagreement Diagnosis).

Pure-function tests: trust from segment calibration (with dormancy default),
liquidity factor, the act/watch/skip decision gate, and the key invariant that an
unproven (dormant) segment caps at "watch" - never "act". Thresholds are pinned
via patched settings so the tests don't drift with config defaults.
"""

import unittest
from unittest.mock import patch

from app.services import diagnosis_service as diag


class CalibrationTrustTests(unittest.TestCase):
    def test_dormant_segment_returns_default(self):
        stats = {"n": 3, "mean_brier": 0.02}  # below min_samples
        self.assertEqual(
            diag.calibration_trust(stats, min_samples=8, dormant_trust=0.5), 0.5
        )

    def test_no_samples_returns_default(self):
        stats = {"n": 0, "mean_brier": None}
        self.assertEqual(
            diag.calibration_trust(stats, min_samples=8, dormant_trust=0.5), 0.5
        )

    def test_qualified_excellent_segment_high_trust(self):
        # brier 0.04 -> skill = 1 - 0.04/0.25 = 0.84
        stats = {"n": 10, "mean_brier": 0.04}
        self.assertAlmostEqual(
            diag.calibration_trust(stats, min_samples=8, dormant_trust=0.5), 0.84
        )

    def test_qualified_random_segment_zero_trust(self):
        # brier 0.25 -> skill 0 -> trust clamped to 0
        stats = {"n": 12, "mean_brier": 0.25}
        self.assertEqual(
            diag.calibration_trust(stats, min_samples=8, dormant_trust=0.5), 0.0
        )

    def test_worse_than_random_clamps_to_zero(self):
        stats = {"n": 9, "mean_brier": 0.40}  # skill negative
        self.assertEqual(
            diag.calibration_trust(stats, min_samples=8, dormant_trust=0.5), 0.0
        )


class LiquidityFactorTests(unittest.TestCase):
    def test_unknown_liquidity_is_not_penalized(self):
        self.assertEqual(diag.liquidity_factor(0, floor=5000.0), 1.0)
        self.assertEqual(diag.liquidity_factor(None, floor=5000.0), 1.0)

    def test_below_floor_ramps(self):
        self.assertAlmostEqual(diag.liquidity_factor(1000.0, floor=5000.0), 0.2)

    def test_at_or_above_floor_is_full(self):
        self.assertEqual(diag.liquidity_factor(5000.0, floor=5000.0), 1.0)
        self.assertEqual(diag.liquidity_factor(20000.0, floor=5000.0), 1.0)


class DecideTests(unittest.TestCase):
    def test_qualified_large_edge_acts(self):
        self.assertEqual(
            diag.decide(12.0, qualified=True, act_edge=10.0, watch_edge=3.0), "act"
        )

    def test_mid_edge_watches(self):
        self.assertEqual(
            diag.decide(5.0, qualified=True, act_edge=10.0, watch_edge=3.0), "watch"
        )

    def test_small_edge_skips(self):
        self.assertEqual(
            diag.decide(1.0, qualified=True, act_edge=10.0, watch_edge=3.0), "skip"
        )

    def test_dormant_segment_caps_at_watch(self):
        # Huge edge, but the segment is not qualified -> never "act".
        self.assertEqual(
            diag.decide(50.0, qualified=False, act_edge=10.0, watch_edge=3.0), "watch"
        )

    def test_negative_edge_uses_magnitude(self):
        self.assertEqual(
            diag.decide(-12.0, qualified=True, act_edge=10.0, watch_edge=3.0), "act"
        )


class DiagnoseTests(unittest.TestCase):
    def _settings(self):
        # Pin thresholds so the test is independent of config drift.
        return patch.multiple(
            diag.settings,
            CALIBRATION_FEEDBACK_MIN_SAMPLES=8,
            DIAGNOSIS_DORMANT_TRUST=0.5,
            DIAGNOSIS_LIQUIDITY_FLOOR=5000.0,
            DIAGNOSIS_TRUST_FLOOR=0.1,
            DECISION_ACT_EDGE=10.0,
            DECISION_WATCH_EDGE=3.0,
        )

    def test_dormant_segment_damps_and_caps_watch(self):
        with self._settings():
            out = diag.diagnose(40.0, {"n": 0, "mean_brier": None}, liquidity=20000.0)
        # trust 0.5, liq 1.0 -> adjusted 20.0, but dormant -> capped at watch
        self.assertEqual(out["trust"], 0.5)
        self.assertEqual(out["adjusted_edge"], 20.0)
        self.assertEqual(out["decision"], "watch")
        self.assertEqual(out["segment_n"], 0)
        self.assertEqual(out["segment_min_samples"], 8)

    def test_qualified_strong_segment_acts(self):
        with self._settings():
            out = diag.diagnose(40.0, {"n": 10, "mean_brier": 0.04}, liquidity=20000.0)
        # trust 0.84, liq 1.0 -> adjusted 33.6 -> act (qualified)
        self.assertEqual(out["trust"], 0.84)
        self.assertAlmostEqual(out["adjusted_edge"], 33.6)
        self.assertEqual(out["decision"], "act")

    def test_random_segment_floored_not_collapsed(self):
        # A worse-than-or-equal-to-random segment (mean_brier 0.25 -> skill 0)
        # is NOT clamped to trust 0 (which would be an absorbing state: always
        # skip -> excluded from segment_skill -> Brier never improves). It is
        # floored at DIAGNOSIS_TRUST_FLOOR so it still penalizes hard but a large
        # enough edge can keep the segment sampling.
        with self._settings():
            out = diag.diagnose(40.0, {"n": 10, "mean_brier": 0.25}, liquidity=20000.0)
        # trust floored 0.1, liq 1.0 -> adjusted 4.0 -> watch (>=3, below act 10)
        self.assertEqual(out["trust"], 0.1)
        self.assertAlmostEqual(out["adjusted_edge"], 4.0)
        self.assertEqual(out["decision"], "watch")

    def test_floored_segment_small_edge_still_skips(self):
        # The floor is small enough that an ordinary edge in a poor segment still
        # skips - the penalty is severe, only a large divergence survives it.
        with self._settings():
            out = diag.diagnose(20.0, {"n": 10, "mean_brier": 0.30}, liquidity=20000.0)
        # skill negative -> floored 0.1; 20 * 0.1 * 1.0 = 2.0 -> below watch 3
        self.assertEqual(out["trust"], 0.1)
        self.assertAlmostEqual(out["adjusted_edge"], 2.0)
        self.assertEqual(out["decision"], "skip")

    def test_low_liquidity_shrinks_edge(self):
        with self._settings():
            out = diag.diagnose(40.0, {"n": 10, "mean_brier": 0.04}, liquidity=1000.0)
        # trust 0.84, liq 0.2 -> adjusted 6.72 -> watch (below act 10)
        self.assertAlmostEqual(out["adjusted_edge"], 6.72)
        self.assertEqual(out["decision"], "watch")


if __name__ == "__main__":
    unittest.main()
