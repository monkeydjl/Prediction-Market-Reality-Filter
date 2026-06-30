"""Unit tests for calibration_drift_service (Plan 2 §1.7 drift algorithm).

Pure-function tests — no I/O, no settings, no store imports. The drift
service takes plain sample lists and returns computations; the caller
(Task 2 route + dispatcher) is responsible for fetching samples.
"""
from __future__ import annotations

import unittest

from app.services.calibration_drift_service import (
    compute_ece,
    compute_drift_score,
    build_drift_report,
    evaluate_drift_alerts,
)


class TestComputeECE(unittest.TestCase):
    def test_perfect_calibration_returns_zero(self):
        # predicted 1.0 → outcome YES(1) for all → bin avg (1.0) matches obs freq (1.0)
        samples = [{"predicted_prob": 1.0, "actual_outcome": 1}] * 10
        self.assertAlmostEqual(compute_ece(samples), 0.0, places=6)

    def test_empty_returns_none(self):
        self.assertIsNone(compute_ece([]))

    def test_miscalibrated_returns_positive(self):
        # predicted 0.9 but outcome always NO(0) → big gap in the 0.8-0.9 bin
        samples = [{"predicted_prob": 0.9, "actual_outcome": 0}] * 10
        ece = compute_ece(samples)
        self.assertIsNotNone(ece)
        self.assertGreater(ece, 0.5)  # 0.9 predicted, 0.0 observed

    def test_accepts_percentage_probs(self):
        # predicted_prob may arrive as 0-100 (ai_probability scale); the
        # function must normalize to 0-1 internally. 100.0 → 1.0 with all
        # YES → perfect calibration → ECE 0.
        samples = [{"predicted_prob": 100.0, "actual_outcome": 1}] * 5
        self.assertAlmostEqual(compute_ece(samples), 0.0, places=6)


class TestComputeDriftScore(unittest.TestCase):
    def test_recent_worse_than_baseline_positive_drift(self):
        recent = [0.30, 0.28, 0.32]  # mean 0.30
        baseline = [0.15, 0.20, 0.25]  # mean 0.20
        result = compute_drift_score(recent, baseline)
        self.assertAlmostEqual(result["drift_score"], 0.5, places=4)  # (0.30-0.20)/0.20
        self.assertAlmostEqual(result["recent_mean"], 0.3, places=4)
        self.assertAlmostEqual(result["baseline_mean"], 0.2, places=4)
        self.assertEqual(result["recent_n"], 3)
        self.assertEqual(result["baseline_n"], 3)

    def test_recent_better_than_baseline_negative_drift(self):
        recent = [0.10]
        baseline = [0.20]
        result = compute_drift_score(recent, baseline)
        self.assertAlmostEqual(result["drift_score"], -0.5, places=4)

    def test_empty_baseline_returns_none_drift(self):
        result = compute_drift_score([0.2], [])
        self.assertIsNone(result["drift_score"])
        self.assertEqual(result["baseline_n"], 0)

    def test_empty_recent_returns_none_drift(self):
        result = compute_drift_score([], [0.2])
        self.assertIsNone(result["drift_score"])
        self.assertEqual(result["recent_n"], 0)

    def test_zero_baseline_mean_returns_none(self):
        # baseline mean 0 would divide by zero; guard returns None
        result = compute_drift_score([0.1], [0.0, 0.0])
        self.assertIsNone(result["drift_score"])


class TestBuildDriftReport(unittest.TestCase):
    def test_report_includes_ece_drift_and_buckets(self):
        recent = [
            {"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.09,
             "edge_bucket": "5-10", "confidence_bucket": "high",
             "direction_correct": 1, "degraded": False},
            {"predicted_prob": 0.6, "actual_outcome": 0, "brier_score": 0.36,
             "edge_bucket": "5-10", "confidence_bucket": "medium",
             "direction_correct": 0, "degraded": False},
        ]
        baseline = [
            {"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.09,
             "edge_bucket": "5-10", "confidence_bucket": "high",
             "direction_correct": 1, "degraded": False},
        ]
        report = build_drift_report(recent, baseline)
        self.assertIn("drift", report)
        self.assertIn("ece", report)
        self.assertIn("degraded_mixing", report)
        self.assertEqual(report["degraded_mixing"]["recent_degraded_count"], 0)
        self.assertEqual(report["drift"]["recent_n"], 2)
        self.assertEqual(report["drift"]["baseline_n"], 1)

    def test_report_flags_degraded_mixing(self):
        recent = [
            {"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.09,
             "edge_bucket": "5-10", "confidence_bucket": "high",
             "direction_correct": 1, "degraded": True},
        ]
        report = build_drift_report(recent, [])
        self.assertEqual(report["degraded_mixing"]["recent_degraded_count"], 1)
        self.assertTrue(report["degraded_mixing"]["contaminated"])


class TestEvaluateDriftAlerts(unittest.TestCase):
    def _thresholds(self):
        return {
            "brier_relative_threshold": 0.30,
            "bucket_deviation_pp": 20.0,
            "bucket_min_samples": 2,
        }

    def test_no_alerts_when_drift_within_threshold(self):
        report = build_drift_report(
            [{"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.09,
              "edge_bucket": "5-10", "confidence_bucket": "high",
              "direction_correct": 1, "degraded": False}] * 3,
            [{"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.09,
              "edge_bucket": "5-10", "confidence_bucket": "high",
              "direction_correct": 1, "degraded": False}] * 3,
        )
        alerts = evaluate_drift_alerts(report, self._thresholds())
        self.assertEqual(alerts, [])

    def test_brier_relative_alert_when_recent_30pct_worse(self):
        report = build_drift_report(
            [{"predicted_prob": 0.7, "actual_outcome": 0, "brier_score": 0.30,
              "edge_bucket": "5-10", "confidence_bucket": "high",
              "direction_correct": 0, "degraded": False}] * 5,
            [{"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.15,
              "edge_bucket": "5-10", "confidence_bucket": "high",
              "direction_correct": 1, "degraded": False}] * 5,
        )
        alerts = evaluate_drift_alerts(report, self._thresholds())
        codes = [a["code"] for a in alerts]
        self.assertIn("brier_relative_drift", codes)

    def test_degraded_mixing_alert(self):
        report = build_drift_report(
            [{"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.09,
              "edge_bucket": "5-10", "confidence_bucket": "high",
              "direction_correct": 1, "degraded": True}] * 2,
            [],
        )
        alerts = evaluate_drift_alerts(report, self._thresholds())
        codes = [a["code"] for a in alerts]
        self.assertIn("degraded_mixing", codes)

    def test_alert_detail_excludes_banned_terms(self):
        report = build_drift_report(
            [{"predicted_prob": 0.7, "actual_outcome": 0, "brier_score": 0.30,
              "edge_bucket": "5-10", "confidence_bucket": "high",
              "direction_correct": 0, "degraded": True}] * 5,
            [{"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.15,
              "edge_bucket": "5-10", "confidence_bucket": "high",
              "direction_correct": 1, "degraded": False}] * 5,
        )
        alerts = evaluate_drift_alerts(report, self._thresholds())
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        for alert in alerts:
            blob = str(alert).lower()
            for term in banned:
                self.assertNotIn(term, blob, f"alert leaked banned term '{term}': {alert}")


if __name__ == "__main__":
    unittest.main()
