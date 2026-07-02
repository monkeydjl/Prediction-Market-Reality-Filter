"""Unit tests for quality_alert_service (LATER #3)."""
import unittest
from unittest.mock import MagicMock

from app.services.quality_alert_service import (
    DEFAULT_THRESHOLDS,
    collect_insufficient_samples,
    evaluate_quality_alerts,
    thresholds_from_settings,
)


def _overview(
    total_resolved: int = 50,
    with_calibration: int = 45,
    missing_calibration: int = 5,
    missing_calibration_rate: float | None = 0.10,
    direction_accuracy: float | None = 0.70,
    brier_score: float | None = 0.15,
    brier_n: int = 45,
) -> dict:
    return {
        "total_resolved": total_resolved,
        "with_calibration": with_calibration,
        "missing_calibration": missing_calibration,
        "missing_calibration_rate": missing_calibration_rate,
        "direction_accuracy": direction_accuracy,
        "brier_score": brier_score,
        "brier_n": brier_n,
    }


def _slice(
    n: int = 20,
    direction_accuracy: float | None = 0.70,
    brier_score: float | None = 0.15,
    missing_calibration_rate: float | None = 0.10,
) -> dict:
    return {
        "n": n,
        "missing_calibration": int(n * (missing_calibration_rate or 0)),
        "missing_calibration_rate": missing_calibration_rate,
        "direction_correct_true": 14,
        "direction_correct_false": 6,
        "direction_correct_none": 0,
        "direction_accuracy": direction_accuracy,
        "brier": {"brier_score": brier_score, "skill_score": None, "grade": "GOOD", "n": n},
    }


def _report(
    overview: dict | None = None,
    by_source_type: dict | None = None,
    by_edge_bucket: dict | None = None,
    report_errors: list | None = None,
) -> dict:
    return {
        "overview": overview or _overview(),
        "by_source_type": by_source_type or {},
        "by_analysis_quality": {},
        "by_edge_bucket": by_edge_bucket or {},
        "by_source_reliability_bucket": {},
        "calibration_deviation": [],
        "report_errors": report_errors or [],
    }


class TestEvaluateQualityAlerts(unittest.TestCase):
    def test_empty_report_no_alerts(self):
        result = evaluate_quality_alerts(_report(overview=_overview(
            total_resolved=0, with_calibration=0, missing_calibration=0,
            missing_calibration_rate=None, direction_accuracy=None,
            brier_score=None, brier_n=0,
        )))
        self.assertEqual(result, [])

    def test_overview_direction_accuracy_high(self):
        report = _report(overview=_overview(direction_accuracy=0.45))
        alerts = evaluate_quality_alerts(report)
        high_acc = [a for a in alerts if a["code"] == "direction_accuracy_low" and a["severity"] == "high"]
        self.assertEqual(len(high_acc), 1)
        self.assertEqual(high_acc[0]["scope"], "overview")
        self.assertEqual(high_acc[0]["value"], 0.45)
        self.assertEqual(high_acc[0]["threshold"], 0.50)

    def test_overview_direction_accuracy_medium(self):
        report = _report(overview=_overview(direction_accuracy=0.55))
        alerts = evaluate_quality_alerts(report)
        med = [a for a in alerts if a["code"] == "direction_accuracy_low" and a["severity"] == "medium"]
        high = [a for a in alerts if a["code"] == "direction_accuracy_low" and a["severity"] == "high"]
        self.assertEqual(len(med), 1)
        self.assertEqual(len(high), 0)

    def test_overview_direction_accuracy_ok(self):
        report = _report(overview=_overview(direction_accuracy=0.75))
        alerts = evaluate_quality_alerts(report)
        acc_alerts = [a for a in alerts if a["code"] == "direction_accuracy_low"]
        self.assertEqual(len(acc_alerts), 0)

    def test_overview_brier_high(self):
        report = _report(overview=_overview(brier_score=0.38))
        alerts = evaluate_quality_alerts(report)
        high = [a for a in alerts if a["code"] == "brier_score_high" and a["severity"] == "high"]
        self.assertEqual(len(high), 1)
        self.assertEqual(high[0]["threshold"], 0.35)

    def test_overview_brier_medium(self):
        report = _report(overview=_overview(brier_score=0.28))
        alerts = evaluate_quality_alerts(report)
        med = [a for a in alerts if a["code"] == "brier_score_high" and a["severity"] == "medium"]
        high = [a for a in alerts if a["code"] == "brier_score_high" and a["severity"] == "high"]
        self.assertEqual(len(med), 1)
        self.assertEqual(len(high), 0)

    def test_overview_missing_calibration_high(self):
        report = _report(overview=_overview(missing_calibration_rate=0.45))
        alerts = evaluate_quality_alerts(report)
        high = [a for a in alerts if a["code"] == "missing_calibration_rate_high" and a["severity"] == "high"]
        self.assertEqual(len(high), 1)

    def test_overview_missing_calibration_medium(self):
        report = _report(overview=_overview(missing_calibration_rate=0.25))
        alerts = evaluate_quality_alerts(report)
        med = [a for a in alerts if a["code"] == "missing_calibration_rate_high" and a["severity"] == "medium"]
        self.assertEqual(len(med), 1)

    def test_overview_report_errors(self):
        report = _report(report_errors=[{"event_id": "e1", "error": "x"},
                                         {"event_id": "e2", "error": "y"}])
        alerts = evaluate_quality_alerts(report)
        err_alerts = [a for a in alerts if a["code"] == "report_errors_high"]
        self.assertEqual(len(err_alerts), 1)
        self.assertEqual(err_alerts[0]["severity"], "high")
        self.assertEqual(err_alerts[0]["value"], 2)
        self.assertEqual(err_alerts[0]["threshold"], 1)

    def test_high_and_medium_dedup(self):
        """direction_accuracy=0.45 breaches both high(0.50) and medium(0.60)
        → only one high alert, no duplicate medium."""
        report = _report(overview=_overview(direction_accuracy=0.45))
        alerts = evaluate_quality_alerts(report)
        acc_alerts = [a for a in alerts if a["code"] == "direction_accuracy_low"]
        self.assertEqual(len(acc_alerts), 1)
        self.assertEqual(acc_alerts[0]["severity"], "high")

    def test_slice_below_min_samples_skipped(self):
        sl = _slice(n=2, direction_accuracy=0.10)  # n < min_samples=10
        report = _report(by_source_type={"sports_event": sl})
        alerts = evaluate_quality_alerts(report)
        slice_alerts = [a for a in alerts if a["scope"] == "slice"]
        self.assertEqual(len(slice_alerts), 0)

    def test_slice_direction_accuracy_alert(self):
        sl = _slice(n=15, direction_accuracy=0.40)  # < 0.50 high
        report = _report(by_source_type={"sports_event": sl})
        alerts = evaluate_quality_alerts(report)
        slice_alerts = [a for a in alerts if a["scope"] == "slice"
                        and a["code"] == "direction_accuracy_low"
                        and a["severity"] == "high"]
        self.assertEqual(len(slice_alerts), 1)
        self.assertEqual(slice_alerts[0]["dimension"], "by_source_type")
        self.assertEqual(slice_alerts[0]["slice"], "sports_event")

    def test_slice_brier_alert(self):
        sl = _slice(n=20, brier_score=0.30)  # > 0.25 medium, < 0.35 high
        report = _report(by_source_type={"sports_event": sl})
        alerts = evaluate_quality_alerts(report)
        slice_alerts = [a for a in alerts if a["scope"] == "slice"
                        and a["code"] == "brier_score_high"
                        and a["severity"] == "medium"]
        self.assertEqual(len(slice_alerts), 1)

    def test_none_metric_does_not_alert(self):
        """direction_accuracy=None → no direction_accuracy_low alert."""
        report = _report(overview=_overview(direction_accuracy=None))
        alerts = evaluate_quality_alerts(report)
        acc_alerts = [a for a in alerts if a["code"] == "direction_accuracy_low"]
        self.assertEqual(len(acc_alerts), 0)


class TestCollectInsufficientSamples(unittest.TestCase):
    def test_collect_insufficient_samples(self):
        report = _report(
            by_source_type={"good": _slice(n=20), "bad": _slice(n=2)},
            by_edge_bucket={"20+": _slice(n=1)},
        )
        result = collect_insufficient_samples(report)
        self.assertEqual(len(result), 2)  # "bad" and "20+"
        dims = {r["dimension"] for r in result}
        self.assertIn("by_source_type", dims)
        self.assertIn("by_edge_bucket", dims)

    def test_collect_insufficient_empty_report(self):
        result = collect_insufficient_samples(_report())
        self.assertEqual(result, [])


class TestThresholdsFromSettings(unittest.TestCase):
    def test_thresholds_from_settings(self):
        mock_settings = MagicMock()
        mock_settings.QUALITY_ALERT_MIN_SAMPLES = 10
        mock_settings.QUALITY_ALERT_DIRECTION_ACCURACY_MEDIUM = 0.60
        mock_settings.QUALITY_ALERT_DIRECTION_ACCURACY_HIGH = 0.50
        mock_settings.QUALITY_ALERT_BRIER_MEDIUM = 0.25
        mock_settings.QUALITY_ALERT_BRIER_HIGH = 0.35
        mock_settings.QUALITY_ALERT_MISSING_CALIBRATION_RATE_MEDIUM = 0.20
        mock_settings.QUALITY_ALERT_MISSING_CALIBRATION_RATE_HIGH = 0.40
        mock_settings.QUALITY_ALERT_REPORT_ERRORS_HIGH = 1
        th = thresholds_from_settings(mock_settings)
        self.assertEqual(th["min_samples"], 10)
        self.assertEqual(th["direction_accuracy_medium"], 0.60)
        self.assertEqual(th["direction_accuracy_high"], 0.50)
        self.assertEqual(th["brier_medium"], 0.25)
        self.assertEqual(th["brier_high"], 0.35)
        self.assertEqual(th["missing_calibration_rate_medium"], 0.20)
        self.assertEqual(th["missing_calibration_rate_high"], 0.40)
        self.assertEqual(th["report_errors_high"], 1)


class TestDefaultThresholds(unittest.TestCase):
    def test_default_thresholds_when_none(self):
        """thresholds=None → uses DEFAULT_THRESHOLDS."""
        # direction_accuracy=0.45 would breach DEFAULT high=0.50
        report = _report(overview=_overview(direction_accuracy=0.45))
        alerts = evaluate_quality_alerts(report, thresholds=None)
        self.assertTrue(any(a["code"] == "direction_accuracy_low" for a in alerts))

    def test_default_thresholds_constant(self):
        self.assertEqual(DEFAULT_THRESHOLDS["min_samples"], 10)
        self.assertEqual(DEFAULT_THRESHOLDS["direction_accuracy_high"], 0.50)


if __name__ == "__main__":
    unittest.main()
