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


from app.services.model_eval_lab_service import (
    build_model_eval_report,
    group_model_slices,
    slice_model_metrics,
)


def _item(**overrides):
    """Minimal extracted item for slice tests."""
    base = {
        "event_id": "evt-x",
        "source_type": "prediction_market",
        "analysis_quality": "llm",
        "edge_bucket": "10-20",
        "source_reliability_bucket": "high(0.6-0.8)",
        "direction_correct": True,
        "brier_score": 0.16,
        "estimated_probability": 72.0,
        "actual_outcome": 100.0,
        "model": "gpt-4o-mini",
        "degraded_mode": False,
        "degraded_mode_label": "normal",
        "estimated_token_cost": 0.02,
        "guardrail_fired": ["wide_spread"],
    }
    base.update(overrides)
    return base


class TestSliceModelMetrics(unittest.TestCase):
    def test_inherits_slice_metrics_fields(self):
        items = [_item(), _item(direction_correct=False)]
        s = slice_model_metrics(items)
        self.assertEqual(s["n"], 2)
        self.assertEqual(s["direction_correct_true"], 1)
        self.assertEqual(s["direction_correct_false"], 1)
        self.assertEqual(s["direction_accuracy"], 0.5)
        self.assertIn("brier", s)
        self.assertIn("missing_calibration_rate", s)

    def test_ece_computed(self):
        items = [_item(), _item()]
        s = slice_model_metrics(items)
        self.assertAlmostEqual(s["ece"], 28.0)  # pred=72, act=100 -> ECE = |72-100| = 28.0

    def test_cost_aggregation(self):
        items = [_item(estimated_token_cost=0.02), _item(estimated_token_cost=0.04)]
        s = slice_model_metrics(items)
        self.assertEqual(s["cost_n"], 2)
        self.assertAlmostEqual(s["cost_total"], 0.06)
        self.assertAlmostEqual(s["cost_avg"], 0.03)

    def test_cost_avg_none_when_all_missing(self):
        items = [_item(estimated_token_cost=None), _item(estimated_token_cost=None)]
        s = slice_model_metrics(items)
        self.assertEqual(s["cost_n"], 0)
        self.assertIsNone(s["cost_avg"])
        self.assertEqual(s["cost_total"], 0.0)

    def test_cost_partial(self):
        items = [
            _item(estimated_token_cost=0.02),
            _item(estimated_token_cost=None),
        ]
        s = slice_model_metrics(items)
        self.assertEqual(s["cost_n"], 1)
        self.assertAlmostEqual(s["cost_avg"], 0.02)

    def test_guardrail_rate(self):
        items = [
            _item(guardrail_fired=["x"]),
            _item(guardrail_fired=[]),
        ]
        s = slice_model_metrics(items)
        self.assertEqual(s["guardrail_count"], 1)
        self.assertAlmostEqual(s["guardrail_rate"], 0.5)

    def test_degraded_rate(self):
        items = [
            _item(degraded_mode=True, degraded_mode_label="degraded"),
            _item(degraded_mode=False, degraded_mode_label="normal"),
        ]
        s = slice_model_metrics(items)
        self.assertEqual(s["degraded_count"], 1)
        self.assertAlmostEqual(s["degraded_rate"], 0.5)

    def test_empty_items(self):
        s = slice_model_metrics([])
        self.assertEqual(s["n"], 0)
        self.assertIsNone(s["cost_avg"])
        self.assertEqual(s["guardrail_rate"], 0.0)


class TestGroupModelSlices(unittest.TestCase):
    def test_groups_by_model(self):
        items = [
            _item(model="gpt-4o-mini"),
            _item(model="gpt-4o-mini"),
            _item(model="unknown"),
        ]
        result = group_model_slices(items, "model")
        self.assertEqual(set(result.keys()), {"gpt-4o-mini", "unknown"})
        self.assertEqual(result["gpt-4o-mini"]["n"], 2)
        self.assertEqual(result["unknown"]["n"], 1)

    def test_min_samples_flags_insufficient(self):
        items = [_item(model="rare")]
        result = group_model_slices(items, "model", min_samples=5)
        self.assertTrue(result["rare"]["insufficient_samples"])

    def test_min_samples_does_not_drop(self):
        items = [_item(model="rare"), _item(model="common"), _item(model="common")]
        result = group_model_slices(items, "model", min_samples=2)
        self.assertIn("rare", result)  # not dropped
        self.assertTrue(result["rare"]["insufficient_samples"])
        self.assertFalse(result["common"]["insufficient_samples"])

    def test_min_samples_zero_never_flags(self):
        items = [_item(model="rare")]
        result = group_model_slices(items, "model", min_samples=0)
        self.assertFalse(result["rare"]["insufficient_samples"])

    def test_groups_by_degraded_mode_label(self):
        items = [
            _item(degraded_mode_label="normal"),
            _item(degraded_mode_label="degraded"),
        ]
        result = group_model_slices(items, "degraded_mode_label")
        self.assertEqual(set(result.keys()), {"normal", "degraded"})


class TestBuildModelEvalReport(unittest.TestCase):
    def test_overview_from_all_items(self):
        items = [
            _item(model="a"),
            _item(model="b"),
        ]
        report = build_model_eval_report(items, [], min_samples=5)
        self.assertEqual(report["overview"]["n"], 2)

    def test_min_samples_does_not_filter_overview(self):
        items = [_item(model="a")]
        report = build_model_eval_report(items, [], min_samples=10)
        # Overview still shows all items
        self.assertEqual(report["overview"]["n"], 1)
        # But by_model group is flagged insufficient
        self.assertTrue(report["by_model"]["a"]["insufficient_samples"])

    def test_report_has_all_sections(self):
        report = build_model_eval_report([_item()], [])
        for key in ("overview", "by_model", "by_analysis_quality",
                    "by_degraded_mode", "calibration_deviation",
                    "report_errors", "min_samples"):
            self.assertIn(key, report)

    def test_by_degraded_mode_uses_label_keys(self):
        items = [
            _item(degraded_mode_label="normal"),
            _item(degraded_mode_label="degraded"),
        ]
        report = build_model_eval_report(items, [])
        self.assertEqual(set(report["by_degraded_mode"].keys()), {"normal", "degraded"})

    def test_report_errors_passed_through(self):
        errors = [{"event_id": "x", "error": "boom"}]
        report = build_model_eval_report([], errors)
        self.assertEqual(report["report_errors"], errors)

    def test_empty_items_overview_n_zero(self):
        report = build_model_eval_report([], [])
        self.assertEqual(report["overview"]["n"], 0)


if __name__ == "__main__":
    unittest.main()
