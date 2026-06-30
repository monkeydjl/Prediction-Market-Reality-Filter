import unittest
from unittest.mock import patch

from app.services.world_cup_confidence_calibration import (
    CALIBRATION_BLEND_RATIO,
    MIN_SAMPLES_PER_BUCKET,
    apply_confidence_calibration,
    build_confidence_calibration_info,
    calibrate_confidence,
)
from app.services.world_cup_quality_service import MIN_CALIBRATION_SAMPLES


def reliability(
    *,
    is_reliable: bool = True,
    total_samples: int = 10,
    bucket_counts: list[int] | None = None,
    accuracies: list[float | None] | None = None,
) -> dict:
    labels = ["0-20%", "20-40%", "40-60%", "60-80%", "80-100%"]
    counts = bucket_counts or [0, 0, 0, 4, 5]
    bucket_accuracies = accuracies or [None, None, None, 0.50, 0.70]
    buckets = [
        {
            "label": label,
            "count": counts[index],
            "actual_accuracy": bucket_accuracies[index],
            "avg_confidence": None,
        }
        for index, label in enumerate(labels)
    ]
    return {
        "buckets": buckets,
        "total_samples": total_samples,
        "is_reliable": is_reliable,
        "engine_filter": "hybrid",
    }


class WorldCupConfidenceCalibrationTests(unittest.TestCase):
    def test_piecewise_linear_calibration_with_two_reliable_buckets(self):
        """Two reliable buckets (60-80% & 80-100%) enable piecewise-linear
        interpolation.  For raw=0.72 (between centres 0.7 and 0.9):
          interpolated = 0.50 + (0.72-0.7)/(0.9-0.7) * (0.70-0.50) = 0.52
          calibrated   = 0.70*0.52 + 0.30*0.72 = 0.58
        """
        curve = reliability()

        info = build_confidence_calibration_info(0.72, reliability_cache=curve)

        expected = round(
            CALIBRATION_BLEND_RATIO * 0.52 + (1 - CALIBRATION_BLEND_RATIO) * 0.72, 3
        )
        self.assertEqual(calibrate_confidence(0.72, reliability_cache=curve), expected)
        self.assertEqual(info["raw"], 0.72)
        self.assertEqual(info["calibrated"], expected)
        self.assertTrue(info["is_reliable"])
        self.assertTrue(info["bucket_is_reliable"])
        self.assertFalse(info["is_reference_only"])
        self.assertEqual(info["min_total_samples"], MIN_CALIBRATION_SAMPLES)
        self.assertEqual(info["min_bucket_samples"], MIN_SAMPLES_PER_BUCKET)
        self.assertEqual(info["reason"], "piecewise_linear_calibration")
        self.assertEqual(info["bucket"]["label"], "60-80%")

    def test_single_reliable_bucket_falls_back_to_raw(self):
        """Only one reliable bucket — not enough for interpolation."""
        curve = reliability(bucket_counts=[0, 0, 0, 4, 0], accuracies=[None, None, None, 0.50, None])

        info = build_confidence_calibration_info(0.72, reliability_cache=curve)

        self.assertEqual(info["raw"], 0.72)
        self.assertEqual(info["calibrated"], 0.72)
        self.assertTrue(info["is_reliable"])
        self.assertTrue(info["is_reference_only"])
        self.assertEqual(info["reason"], "insufficient_bucket_samples")

    def test_calibration_info_keeps_raw_when_samples_are_insufficient(self):
        curve = reliability(is_reliable=False, total_samples=2, bucket_counts=[0, 0, 0, 2, 0])

        info = build_confidence_calibration_info(0.72, reliability_cache=curve)

        self.assertEqual(info["raw"], 0.72)
        self.assertEqual(info["calibrated"], 0.72)
        self.assertFalse(info["is_reliable"])
        self.assertFalse(info["bucket_is_reliable"])
        self.assertTrue(info["is_reference_only"])
        self.assertEqual(info["min_total_samples"], MIN_CALIBRATION_SAMPLES)
        self.assertEqual(info["min_bucket_samples"], MIN_SAMPLES_PER_BUCKET)
        self.assertEqual(info["reason"], "insufficient_total_samples")
        self.assertEqual(info["total_samples"], 2)


    def test_apply_falls_back_to_overall_when_engine_lacks_data(self):
        """apply_confidence_calibration should use overall calibration when
        engine-specific data is insufficient (is_reference_only=True)."""
        engine_result = {
            "raw": 0.80,
            "calibrated": 0.80,
            "method": "piecewise_linear_reliability",
            "engine_filter": "integrated",
            "total_samples": 5,
            "is_reliable": False,
            "is_reference_only": True,
            "reason": "insufficient_bucket_samples",
            "bucket": None,
            "applied_bucket": None,
        }
        overall_result = {
            "raw": 0.80,
            "calibrated": 0.55,
            "method": "piecewise_linear_reliability",
            "engine_filter": None,
            "total_samples": 52,
            "is_reliable": True,
            "is_reference_only": False,
            "reason": "piecewise_linear_calibration",
            "bucket": None,
            "applied_bucket": None,
        }

        def fake_build(raw, engine_name=None, reliability_cache=None):
            if engine_name == "integrated":
                return dict(engine_result)
            return dict(overall_result)

        prediction = {"confidence": 0.80}
        with patch(
            "app.services.world_cup_confidence_calibration.build_confidence_calibration_info",
            side_effect=fake_build,
        ):
            apply_confidence_calibration(prediction, engine_name="integrated")

        self.assertEqual(prediction["confidence"], 0.55)
        self.assertEqual(prediction["raw_confidence"], 0.80)
        self.assertEqual(
            prediction["calibration_info"]["reason"],
            "piecewise_linear_calibration_via_overall",
        )
        self.assertEqual(prediction["calibration_info"]["engine_filter"], "integrated")

    def test_apply_does_not_fallback_when_overall_also_insufficient(self):
        """When both engine-specific and overall are reference-only, keep engine result."""
        ref_result = {
            "raw": 0.80,
            "calibrated": 0.80,
            "method": "piecewise_linear_reliability",
            "engine_filter": "gbm",
            "total_samples": 2,
            "is_reliable": False,
            "is_reference_only": True,
            "reason": "insufficient_total_samples",
            "bucket": None,
            "applied_bucket": None,
        }

        prediction = {"confidence": 0.80}
        with patch(
            "app.services.world_cup_confidence_calibration.build_confidence_calibration_info",
            return_value=dict(ref_result),
        ):
            apply_confidence_calibration(prediction, engine_name="gbm")

        # No overall fallback — both were reference-only
        self.assertEqual(prediction["confidence"], 0.80)
        self.assertEqual(
            prediction["calibration_info"]["reason"],
            "insufficient_total_samples",
        )


if __name__ == "__main__":
    unittest.main()
