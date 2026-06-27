import unittest

from app.services.world_cup_confidence_calibration import (
    MIN_SAMPLES_PER_BUCKET,
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
    counts = bucket_counts or [0, 0, 0, 4, 0]
    bucket_accuracies = accuracies or [None, None, None, 0.50, None]
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
    def test_calibration_info_preserves_existing_bucket_formula(self):
        curve = reliability()

        info = build_confidence_calibration_info(0.72, reliability_cache=curve)

        self.assertEqual(calibrate_confidence(0.72, reliability_cache=curve), 0.61)
        self.assertEqual(info["raw"], 0.72)
        self.assertEqual(info["calibrated"], 0.61)
        self.assertTrue(info["is_reliable"])
        self.assertTrue(info["bucket_is_reliable"])
        self.assertFalse(info["is_reference_only"])
        self.assertEqual(info["min_total_samples"], MIN_CALIBRATION_SAMPLES)
        self.assertEqual(info["min_bucket_samples"], MIN_SAMPLES_PER_BUCKET)
        self.assertEqual(info["reason"], "bucket_reliability_curve")
        self.assertEqual(info["bucket"]["label"], "60-80%")
        self.assertEqual(info["applied_bucket"]["count"], 4)

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


if __name__ == "__main__":
    unittest.main()
