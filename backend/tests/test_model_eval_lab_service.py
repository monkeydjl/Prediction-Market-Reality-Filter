"""Unit tests for model_eval_lab_service."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.services.model_eval_lab_service import (
    compute_ece,
    extract_model_metrics,
)


def _record(**overrides):
    """Minimal record with calibration + llm_telemetry populated."""
    rec = {
        "event_id": "evt-001",
        "source": {"type": "prediction_market"},
        "llm_telemetry": {
            "model": "gpt-4o-mini",
            "analysis_quality": "llm",
            "degraded_mode": False,
            "estimated_token_cost": 0.0189,
        },
        "actionable_recommendation": {"direction": "YES", "edge": 12.0},
        "outcome": {"status": "resolved", "actual_outcome": 100.0},
        "calibration": {
            "brier_score": 0.16,
            "estimated_probability": 72.0,
        },
        "source_reliability": {"overall_score": 0.65},
        "guardrail_fired": ["wide_spread"],
    }
    rec.update(overrides)
    return rec


class TestExtractModelMetrics(unittest.TestCase):
    def test_appends_model_degraded_cost_guardrail(self):
        item = extract_model_metrics(_record())
        self.assertEqual(item["model"], "gpt-4o-mini")
        self.assertFalse(item["degraded_mode"])
        self.assertEqual(item["degraded_mode_label"], "normal")
        self.assertEqual(item["estimated_token_cost"], 0.0189)
        self.assertEqual(item["guardrail_fired"], ["wide_spread"])

    def test_preserves_extract_metrics_fields(self):
        item = extract_model_metrics(_record())
        # Fields from extract_metrics still present
        self.assertEqual(item["event_id"], "evt-001")
        self.assertEqual(item["source_type"], "prediction_market")
        self.assertEqual(item["analysis_quality"], "llm")
        self.assertEqual(item["brier_score"], 0.16)
        self.assertEqual(item["estimated_probability"], 72.0)
        self.assertEqual(item["actual_outcome"], 100.0)

    def test_model_unknown_when_llm_telemetry_missing(self):
        rec = _record()
        del rec["llm_telemetry"]
        item = extract_model_metrics(rec)
        self.assertEqual(item["model"], "unknown")
        self.assertIsNone(item["estimated_token_cost"])
        self.assertFalse(item["degraded_mode"])
        self.assertEqual(item["degraded_mode_label"], "normal")

    def test_model_unknown_when_llm_telemetry_not_dict(self):
        item = extract_model_metrics(_record(llm_telemetry="broken"))
        self.assertEqual(item["model"], "unknown")

    def test_cost_none_for_bool(self):
        item = extract_model_metrics(
            _record(llm_telemetry={"model": "x", "estimated_token_cost": True})
        )
        self.assertIsNone(item["estimated_token_cost"])

    def test_cost_none_for_nan(self):
        item = extract_model_metrics(
            _record(llm_telemetry={"model": "x", "estimated_token_cost": float("nan")})
        )
        self.assertIsNone(item["estimated_token_cost"])

    def test_cost_none_for_string(self):
        item = extract_model_metrics(
            _record(llm_telemetry={"model": "x", "estimated_token_cost": "0.02"})
        )
        self.assertIsNone(item["estimated_token_cost"])

    def test_degraded_mode_label_degraded(self):
        item = extract_model_metrics(
            _record(llm_telemetry={"model": "x", "degraded_mode": True})
        )
        self.assertTrue(item["degraded_mode"])
        self.assertEqual(item["degraded_mode_label"], "degraded")

    def test_guardrail_fired_non_list_becomes_empty(self):
        item = extract_model_metrics(_record(guardrail_fired="not a list"))
        self.assertEqual(item["guardrail_fired"], [])


class TestComputeEce(unittest.TestCase):
    def test_returns_none_when_no_eligible(self):
        # No estimated_probability
        items = [{"estimated_probability": None, "actual_outcome": 100.0}]
        self.assertIsNone(compute_ece(items))

    def test_returns_none_when_empty(self):
        self.assertIsNone(compute_ece([]))

    def test_returns_zero_when_perfectly_calibrated(self):
        # All in [60,80) bucket, predicted=actual=70
        items = [
            {"estimated_probability": 70.0, "actual_outcome": 70.0},
            {"estimated_probability": 70.0, "actual_outcome": 70.0},
        ]
        self.assertAlmostEqual(compute_ece(items), 0.0)

    def test_computed_value_single_bucket(self):
        # [0,20) bucket: predicted_mean=10, actual_mean=0 -> ECE = 10
        items = [
            {"estimated_probability": 10.0, "actual_outcome": 0.0},
        ]
        self.assertAlmostEqual(compute_ece(items), 10.0)

    def test_excludes_bool_probability(self):
        # bool True is int subclass; must not count as 1.0
        items = [
            {"estimated_probability": True, "actual_outcome": 100.0},
        ]
        self.assertIsNone(compute_ece(items))

    def test_excludes_bool_actual_outcome(self):
        items = [
            {"estimated_probability": 50.0, "actual_outcome": True},
        ]
        self.assertIsNone(compute_ece(items))

    def test_covers_100_boundary(self):
        # estimated_probability == 100 must fall in last bucket [80,101)
        items = [
            {"estimated_probability": 100.0, "actual_outcome": 100.0},
        ]
        # Perfectly calibrated -> ECE 0, but confirms 100 is eligible
        self.assertAlmostEqual(compute_ece(items), 0.0)

    def test_multi_bucket_weighted(self):
        # Bucket [0,20): 2 items, pred_mean=10, act_mean=0 -> |10|
        # Bucket [80,101): 2 items, pred_mean=90, act_mean=100 -> |10|
        # total=4, ECE = (2/4)*10 + (2/4)*10 = 10
        items = [
            {"estimated_probability": 10.0, "actual_outcome": 0.0},
            {"estimated_probability": 10.0, "actual_outcome": 0.0},
            {"estimated_probability": 90.0, "actual_outcome": 100.0},
            {"estimated_probability": 90.0, "actual_outcome": 100.0},
        ]
        self.assertAlmostEqual(compute_ece(items), 10.0)


if __name__ == "__main__":
    unittest.main()
