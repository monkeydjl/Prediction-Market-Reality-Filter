"""Tests for QUALITY_ALERT_* config fields (LATER #3)."""
import unittest

from app.core.config import settings


class TestQualityAlertSettings(unittest.TestCase):
    def test_settings_have_quality_alert_fields(self):
        self.assertTrue(hasattr(settings, "QUALITY_ALERT_MIN_SAMPLES"))
        self.assertTrue(hasattr(settings, "QUALITY_ALERT_DIRECTION_ACCURACY_MEDIUM"))
        self.assertTrue(hasattr(settings, "QUALITY_ALERT_DIRECTION_ACCURACY_HIGH"))
        self.assertTrue(hasattr(settings, "QUALITY_ALERT_BRIER_MEDIUM"))
        self.assertTrue(hasattr(settings, "QUALITY_ALERT_BRIER_HIGH"))
        self.assertTrue(hasattr(settings, "QUALITY_ALERT_MISSING_CALIBRATION_RATE_MEDIUM"))
        self.assertTrue(hasattr(settings, "QUALITY_ALERT_MISSING_CALIBRATION_RATE_HIGH"))
        self.assertTrue(hasattr(settings, "QUALITY_ALERT_REPORT_ERRORS_HIGH"))

    def test_default_values(self):
        self.assertEqual(settings.QUALITY_ALERT_MIN_SAMPLES, 10)
        self.assertEqual(settings.QUALITY_ALERT_DIRECTION_ACCURACY_MEDIUM, 0.60)
        self.assertEqual(settings.QUALITY_ALERT_DIRECTION_ACCURACY_HIGH, 0.50)
        self.assertEqual(settings.QUALITY_ALERT_BRIER_MEDIUM, 0.25)
        self.assertEqual(settings.QUALITY_ALERT_BRIER_HIGH, 0.35)
        self.assertEqual(settings.QUALITY_ALERT_MISSING_CALIBRATION_RATE_MEDIUM, 0.20)
        self.assertEqual(settings.QUALITY_ALERT_MISSING_CALIBRATION_RATE_HIGH, 0.40)
        self.assertEqual(settings.QUALITY_ALERT_REPORT_ERRORS_HIGH, 1)


if __name__ == "__main__":
    unittest.main()
