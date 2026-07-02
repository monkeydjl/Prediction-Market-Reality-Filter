"""Unit tests for quality_metrics_report_service shape extensions (LATER #3)."""
import unittest

from app.services.quality_metrics_report_service import (
    build_report,
    slice_metrics,
)


def _item(
    event_id: str = "e1",
    source_type: str = "prediction_market",
    analysis_quality: str = "llm",
    edge_bucket: str = "5-10",
    source_reliability_bucket: str = "high(0.6-0.8)",
    direction_correct: bool | None = True,
    brier_score: float | None = 0.18,
    estimated_probability: float | None = 60.0,
    actual_outcome: float | None = 100.0,
) -> dict:
    return {
        "event_id": event_id,
        "source_type": source_type,
        "analysis_quality": analysis_quality,
        "edge_bucket": edge_bucket,
        "source_reliability_bucket": source_reliability_bucket,
        "direction_correct": direction_correct,
        "brier_score": brier_score,
        "estimated_probability": estimated_probability,
        "actual_outcome": actual_outcome,
    }


class TestSliceMetricsShape(unittest.TestCase):
    def test_slice_includes_missing_calibration_fields(self):
        items = [_item("e1", brier_score=0.18), _item("e2", brier_score=None)]
        s = slice_metrics(items)
        self.assertEqual(s["n"], 2)
        self.assertEqual(s["missing_calibration"], 1)
        self.assertEqual(s["missing_calibration_rate"], 0.5)

    def test_slice_missing_calibration_rate_none_when_empty(self):
        s = slice_metrics([])
        self.assertEqual(s["n"], 0)
        self.assertEqual(s["missing_calibration"], 0)
        self.assertIsNone(s["missing_calibration_rate"])

    def test_slice_missing_calibration_zero_when_all_have_brier(self):
        items = [_item("e1", brier_score=0.18), _item("e2", brier_score=0.22)]
        s = slice_metrics(items)
        self.assertEqual(s["missing_calibration"], 0)
        self.assertEqual(s["missing_calibration_rate"], 0.0)


class TestBuildReportOverviewShape(unittest.TestCase):
    def test_overview_includes_direction_accuracy_and_brier(self):
        items = [
            _item("e1", direction_correct=True, brier_score=0.10),
            _item("e2", direction_correct=False, brier_score=0.20),
        ]
        report = build_report(items, [])
        ov = report["overview"]
        self.assertEqual(ov["total_resolved"], 2)
        self.assertEqual(ov["with_calibration"], 2)
        self.assertEqual(ov["missing_calibration"], 0)
        self.assertEqual(ov["missing_calibration_rate"], 0.0)
        # direction_accuracy = true / (true + false) = 1/2 = 0.5
        self.assertEqual(ov["direction_accuracy"], 0.5)
        # mean brier = (0.10 + 0.20) / 2 = 0.15
        self.assertAlmostEqual(ov["brier_score"], 0.15)
        self.assertEqual(ov["brier_n"], 2)

    def test_overview_direction_accuracy_none_when_no_directional(self):
        items = [_item("e1", direction_correct=None), _item("e2", direction_correct=None)]
        report = build_report(items, [])
        self.assertIsNone(report["overview"]["direction_accuracy"])

    def test_overview_brier_none_when_no_calibration(self):
        items = [_item("e1", brier_score=None), _item("e2", brier_score=None)]
        report = build_report(items, [])
        self.assertIsNone(report["overview"]["brier_score"])
        self.assertEqual(report["overview"]["brier_n"], 0)
        self.assertEqual(report["overview"]["missing_calibration"], 2)
        self.assertEqual(report["overview"]["missing_calibration_rate"], 1.0)

    def test_overview_brier_n_excludes_none(self):
        items = [_item("e1", brier_score=0.10), _item("e2", brier_score=None)]
        report = build_report(items, [])
        self.assertEqual(report["overview"]["brier_n"], 1)
        self.assertAlmostEqual(report["overview"]["brier_score"], 0.10)


if __name__ == "__main__":
    unittest.main()
