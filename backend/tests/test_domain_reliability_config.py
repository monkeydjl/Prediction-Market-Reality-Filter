"""Tests for DOMAIN_RELIABILITY_* config settings (LATER #2)."""
import unittest

from app.core.config import settings


class TestDomainReliabilityConfig(unittest.TestCase):
    def test_settings_have_domain_reliability_fields(self):
        self.assertTrue(hasattr(settings, "DOMAIN_RELIABILITY_TRACKING_ENABLED"))
        self.assertTrue(hasattr(settings, "DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES"))

    def test_default_values(self):
        self.assertFalse(settings.DOMAIN_RELIABILITY_TRACKING_ENABLED)
        self.assertEqual(settings.DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES, 5)


if __name__ == "__main__":
    unittest.main()
