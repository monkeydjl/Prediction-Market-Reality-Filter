"""Tests for model_eval_gate_service — 发布门槛 (Q1).

The two rules this file exists to pin, both stated in the module docstring:

  1. **A missing measurement fails.** ``quality_alert_service`` documents that
     ``None`` metrics do not alert, which is right for paging and wrong for
     certification. ``test_the_same_none_alerts_nowhere_and_passes_nothing``
     asserts both halves at once, so making the gate "consistent" with the
     alerter turns it red.
  2. **Each metric is held to min_samples on its own denominator.** The live
     store's ``llm`` slice held 45 events whose ECE came from 6. A gate that
     checked ``overview.n`` and then compared that ECE would have ruled on six
     events believing it had forty-five.
"""
from __future__ import annotations

import unittest

from app.services.model_eval_gate_service import (
    DEFAULT_GATE_THRESHOLDS,
    evaluate_release_gate,
    gate_thresholds_from_settings,
)


def _report(**overrides):
    """A report that passes every check, so a single override isolates one."""
    overview = {
        "n": 40,
        "brier": {"brier_score": 0.16, "n": 40},
        "ece": 8.0,
        "ece_n": 40,
        "direction_accuracy": 0.70,
        "direction_correct_true": 28,
        "direction_correct_false": 12,
        "degraded_rate": 0.05,
    }
    overview.update(overrides.pop("overview", {}))
    report = {
        "report_schema_version": 2,
        "overview": overview,
        "report_errors": [],
    }
    report.update(overrides)
    return report


def _eval_set(**overrides):
    block = {
        "name": "baseline", "revision": "1", "digest": "d" * 64,
        "event_count": 40, "matched": 40,
        "missing_event_ids": [], "drifted_event_ids": [],
        "coverage": 1.0, "complete": True,
    }
    block.update(overrides)
    return block


def _check(gate, name):
    for c in gate["checks"]:
        if c["name"] == name:
            return c
    raise AssertionError(f"no check named {name!r} in {[c['name'] for c in gate['checks']]}")


class TestCleanPass(unittest.TestCase):
    def test_healthy_report_passes(self):
        gate = evaluate_release_gate(_report())
        self.assertTrue(gate["passed"], gate["failed"])
        self.assertEqual(gate["failed"], [])

    def test_every_metric_check_is_always_constructed(self):
        """``passed`` is a conjunction, so an empty check list would pass
        vacuously. These six are built unconditionally."""
        gate = evaluate_release_gate(_report())
        self.assertEqual(
            [c["name"] for c in gate["checks"]],
            [
                "min_samples", "brier_max", "ece_max",
                "direction_accuracy_min", "degraded_rate_max",
                "report_errors_max",
            ],
        )

    def test_thresholds_are_echoed(self):
        gate = evaluate_release_gate(_report())
        self.assertEqual(gate["thresholds"], DEFAULT_GATE_THRESHOLDS)

    def test_defaults_are_not_mutated_by_a_run(self):
        before = dict(DEFAULT_GATE_THRESHOLDS)
        gate = evaluate_release_gate(_report())
        gate["thresholds"]["brier_max"] = 99.0
        self.assertEqual(DEFAULT_GATE_THRESHOLDS, before)

    def test_caller_thresholds_are_not_mutated(self):
        th = dict(DEFAULT_GATE_THRESHOLDS)
        gate = evaluate_release_gate(_report(), th)
        gate["thresholds"]["min_samples"] = 999
        self.assertEqual(th["min_samples"], DEFAULT_GATE_THRESHOLDS["min_samples"])


class TestMissingMeasurementFails(unittest.TestCase):
    def test_an_empty_report_cannot_pass(self):
        """Measuring nothing must not certify anything."""
        gate = evaluate_release_gate({})
        self.assertFalse(gate["passed"])
        self.assertIn("min_samples", gate["failed"])
        self.assertIn("brier_max", gate["failed"])
        self.assertIn("ece_max", gate["failed"])
        self.assertIn("direction_accuracy_min", gate["failed"])

    def test_each_none_metric_fails_with_a_stated_reason(self):
        for metric, check_name in (
            ("n", "min_samples"),
            ("ece", "ece_max"),
            ("direction_accuracy", "direction_accuracy_min"),
            ("degraded_rate", "degraded_rate_max"),
        ):
            with self.subTest(metric=metric):
                gate = evaluate_release_gate(_report(overview={metric: None}))
                check = _check(gate, check_name)
                self.assertFalse(check["passed"])
                self.assertIsNone(check["value"])
                self.assertTrue(check["reason"])

    def test_none_brier_fails(self):
        gate = evaluate_release_gate(
            _report(overview={"brier": {"brier_score": None, "n": 0}}),
        )
        check = _check(gate, "brier_max")
        self.assertFalse(check["passed"])
        self.assertIn("calibration", check["reason"])

    def test_the_same_none_alerts_nowhere_and_passes_nothing(self):
        """The distinction this module exists for, asserted side by side.

        ``quality_alert_service`` is right not to page on a None; the gate is
        right not to certify on one. Reverting either half turns this red.
        """
        from app.services.quality_alert_service import evaluate_quality_alerts

        # Nothing measured: the alerter stays quiet, which is correct for paging.
        self.assertEqual(evaluate_quality_alerts({
            "overview": {
                "total_resolved": 0, "direction_accuracy": None,
                "brier_score": None, "missing_calibration_rate": None,
            },
            "report_errors": [],
        }), [])

        # The same absence must not certify a release.
        gate = evaluate_release_gate(_report(overview={
            "direction_accuracy": None, "ece": None,
            "brier": {"brier_score": None, "n": 0},
        }))
        self.assertFalse(gate["passed"])
        self.assertEqual(
            set(gate["failed"]),
            {"brier_max", "ece_max", "direction_accuracy_min"},
        )

    def test_no_short_circuit_when_min_samples_fails(self):
        """Every check is reported so an operator can see whether to widen the
        set or fix the model, not just that the set was small."""
        gate = evaluate_release_gate(_report(overview={"n": 1}))
        self.assertFalse(_check(gate, "min_samples")["passed"])
        self.assertEqual(len(gate["checks"]), 6)
        self.assertEqual(_check(gate, "brier_max")["value"], 0.16)

    def test_non_dict_overview_is_survivable(self):
        for bad in (None, "overview", [1, 2]):
            with self.subTest(overview=bad):
                gate = evaluate_release_gate({"overview": bad, "report_errors": []})
                self.assertFalse(gate["passed"])

    def test_non_dict_brier_block_is_survivable(self):
        gate = evaluate_release_gate(_report(overview={"brier": "nope"}))
        self.assertFalse(_check(gate, "brier_max")["passed"])


class TestPerMetricDenominator(unittest.TestCase):
    def test_the_live_defect_a_thin_ece_under_a_fat_slice(self):
        """n=45 with ece_n=6 was the real shape on the store.

        The pre-fix gate checked overview.n, saw 45, and compared an ECE
        computed from 6 events to a threshold.
        """
        gate = evaluate_release_gate(
            _report(overview={"n": 45, "ece": 8.0, "ece_n": 6}),
        )
        self.assertTrue(_check(gate, "min_samples")["passed"])
        ece = _check(gate, "ece_max")
        self.assertFalse(ece["passed"])
        self.assertEqual(ece["sample_count"], 6)
        self.assertIn("min_samples=20", ece["reason"])
        # The value itself was inside the threshold -- only the count failed.
        self.assertLessEqual(ece["value"], DEFAULT_GATE_THRESHOLDS["ece_max"])

    def test_each_metric_uses_its_own_count(self):
        for override, check_name, expected_n in (
            ({"brier": {"brier_score": 0.1, "n": 3}}, "brier_max", 3),
            ({"ece_n": 4}, "ece_max", 4),
            (
                {"direction_correct_true": 3, "direction_correct_false": 2},
                "direction_accuracy_min", 5,
            ),
            ({"n": 7}, "degraded_rate_max", 7),
        ):
            with self.subTest(check=check_name):
                gate = evaluate_release_gate(_report(overview=override))
                check = _check(gate, check_name)
                self.assertEqual(check["sample_count"], expected_n)
                self.assertFalse(check["passed"])
                self.assertIn("carry this metric", check["reason"])

    def test_direction_accuracy_counts_both_outcomes(self):
        """The denominator is right+wrong, not just right."""
        gate = evaluate_release_gate(_report(overview={
            "direction_correct_true": 12, "direction_correct_false": 10,
        }))
        check = _check(gate, "direction_accuracy_min")
        self.assertEqual(check["sample_count"], 22)
        self.assertTrue(check["passed"])

    def test_report_errors_has_no_denominator_gate(self):
        """It is a count of failures, not a measurement over a sample."""
        gate = evaluate_release_gate(_report())
        self.assertIsNone(_check(gate, "report_errors_max")["sample_count"])

    def test_a_sufficient_count_does_not_rescue_a_bad_value(self):
        gate = evaluate_release_gate(_report(overview={"ece": 40.0}))
        check = _check(gate, "ece_max")
        self.assertFalse(check["passed"])
        self.assertIsNone(check["reason"])  # a real comparison, not a count veto


class TestThresholdComparisons(unittest.TestCase):
    def test_boundary_values_pass(self):
        """<= and >= are inclusive; a value exactly at the line ships."""
        gate = evaluate_release_gate(_report(overview={
            "n": DEFAULT_GATE_THRESHOLDS["min_samples"],
            "brier": {"brier_score": DEFAULT_GATE_THRESHOLDS["brier_max"], "n": 20},
            "ece": DEFAULT_GATE_THRESHOLDS["ece_max"],
            "ece_n": 20,
            "direction_accuracy": DEFAULT_GATE_THRESHOLDS["direction_accuracy_min"],
            "direction_correct_true": 11, "direction_correct_false": 9,
            "degraded_rate": DEFAULT_GATE_THRESHOLDS["degraded_rate_max"],
        }))
        self.assertTrue(gate["passed"], gate["failed"])

    def test_each_metric_can_fail_on_its_own(self):
        for override, check_name in (
            ({"brier": {"brier_score": 0.40, "n": 40}}, "brier_max"),
            ({"ece": 30.0}, "ece_max"),
            ({"direction_accuracy": 0.40}, "direction_accuracy_min"),
            ({"degraded_rate": 0.90}, "degraded_rate_max"),
        ):
            with self.subTest(check=check_name):
                gate = evaluate_release_gate(_report(overview=override))
                self.assertEqual(gate["failed"], [check_name])
                self.assertFalse(gate["passed"])

    def test_report_errors_fail_the_gate(self):
        gate = evaluate_release_gate(
            _report(report_errors=[{"event_id": "x", "error": "boom"}]),
        )
        self.assertEqual(gate["failed"], ["report_errors_max"])
        self.assertEqual(_check(gate, "report_errors_max")["value"], 1)

    def test_non_list_report_errors_counts_zero(self):
        gate = evaluate_release_gate(_report(report_errors=None))
        self.assertEqual(_check(gate, "report_errors_max")["value"], 0)

    def test_custom_thresholds_are_honoured(self):
        loose = {**DEFAULT_GATE_THRESHOLDS, "min_samples": 1, "brier_max": 0.9}
        gate = evaluate_release_gate(
            _report(overview={"n": 2, "brier": {"brier_score": 0.8, "n": 2},
                              "ece_n": 2, "direction_correct_true": 2,
                              "direction_correct_false": 0}),
            loose,
        )
        self.assertTrue(gate["passed"], gate["failed"])


class TestEvalSetChecks(unittest.TestCase):
    def test_unpinned_report_skips_the_completeness_check(self):
        """There is nothing to be complete about -- and a vacuous pass is the
        failure mode this module exists to avoid."""
        gate = evaluate_release_gate(_report())
        names = [c["name"] for c in gate["checks"]]
        self.assertNotIn("eval_set_complete", names)
        self.assertNotIn("eval_set_required", names)
        self.assertTrue(gate["passed"])

    def test_require_eval_set_fails_an_unpinned_report(self):
        th = {**DEFAULT_GATE_THRESHOLDS, "require_eval_set": True}
        gate = evaluate_release_gate(_report(), th)
        self.assertEqual(gate["failed"], ["eval_set_required"])
        self.assertIn("pinned", _check(gate, "eval_set_required")["reason"])

    def test_require_eval_set_is_off_by_default(self):
        self.assertFalse(DEFAULT_GATE_THRESHOLDS["require_eval_set"])

    def test_pinned_and_whole_passes(self):
        gate = evaluate_release_gate(_report(eval_set=_eval_set()))
        self.assertTrue(gate["passed"], gate["failed"])
        self.assertTrue(_check(gate, "eval_set_complete")["passed"])

    def test_missing_pinned_event_fails_and_is_named(self):
        gate = evaluate_release_gate(_report(eval_set=_eval_set(
            matched=39, missing_event_ids=["evt-007"], coverage=0.975,
            complete=False,
        )))
        check = _check(gate, "eval_set_complete")
        self.assertFalse(check["passed"])
        self.assertIn("1 pinned event(s) missing", check["reason"])

    def test_drift_fails_even_at_full_coverage(self):
        """Coverage is 100%: every pinned id was found. They are not the same
        data any more, so the score is not comparable to the last run."""
        gate = evaluate_release_gate(_report(eval_set=_eval_set(
            drifted_event_ids=["evt-003"], complete=False,
        )))
        check = _check(gate, "eval_set_complete")
        self.assertEqual(check["value"], check["threshold"])  # matched == count
        self.assertFalse(check["passed"])
        self.assertIn("re-graded", check["reason"])

    def test_missing_and_drift_are_both_reported(self):
        gate = evaluate_release_gate(_report(eval_set=_eval_set(
            matched=39, missing_event_ids=["a"], drifted_event_ids=["b"],
        )))
        reason = _check(gate, "eval_set_complete")["reason"]
        self.assertIn("missing", reason)
        self.assertIn("re-graded", reason)

    def test_empty_pinned_set_fails(self):
        """Zero matched of zero pinned is arithmetically complete and means
        nothing was graded."""
        gate = evaluate_release_gate(_report(eval_set=_eval_set(
            event_count=0, matched=0, coverage=None, complete=False,
        )))
        check = _check(gate, "eval_set_complete")
        self.assertFalse(check["passed"])
        self.assertIn("empty", check["reason"])

    def test_non_dict_eval_set_is_treated_as_unpinned(self):
        gate = evaluate_release_gate(_report(eval_set="baseline"))
        self.assertNotIn(
            "eval_set_complete", [c["name"] for c in gate["checks"]],
        )


class TestThresholdsFromSettings(unittest.TestCase):
    def test_adapter_reads_every_default(self):
        from app.core.config import settings

        th = gate_thresholds_from_settings(settings)
        self.assertEqual(set(th), set(DEFAULT_GATE_THRESHOLDS))
        self.assertEqual(th, DEFAULT_GATE_THRESHOLDS)

    def test_require_eval_set_ships_off(self):
        from app.core.config import settings

        self.assertFalse(settings.MODEL_EVAL_GATE_REQUIRE_EVAL_SET)

    def test_adapter_output_drives_the_gate(self):
        class _Stub:
            MODEL_EVAL_GATE_MIN_SAMPLES = 1
            MODEL_EVAL_GATE_BRIER_MAX = 0.01
            MODEL_EVAL_GATE_ECE_MAX = 1.0
            MODEL_EVAL_GATE_DIRECTION_ACCURACY_MIN = 0.99
            MODEL_EVAL_GATE_DEGRADED_RATE_MAX = 0.0
            MODEL_EVAL_GATE_REPORT_ERRORS_MAX = 0
            MODEL_EVAL_GATE_REQUIRE_EVAL_SET = True

        gate = evaluate_release_gate(_report(), gate_thresholds_from_settings(_Stub()))
        self.assertFalse(gate["passed"])
        self.assertEqual(
            set(gate["failed"]),
            {"brier_max", "ece_max", "direction_accuracy_min",
             "degraded_rate_max", "eval_set_required"},
        )


if __name__ == "__main__":
    unittest.main()
