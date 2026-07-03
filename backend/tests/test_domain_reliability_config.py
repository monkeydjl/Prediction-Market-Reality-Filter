"""Tests for DOMAIN_RELIABILITY_* config settings (LATER #2)."""
import unittest

from app.core.config import settings


class TestDomainReliabilityConfig(unittest.TestCase):
    def test_settings_have_domain_reliability_fields(self):
        self.assertTrue(hasattr(settings, "DOMAIN_RELIABILITY_TRACKING_ENABLED"))
        self.assertTrue(hasattr(settings, "DOMAIN_RELIABILITY_DB_PATH"))
        self.assertTrue(hasattr(settings, "DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES"))
        self.assertTrue(hasattr(settings, "DOMAIN_RELIABILITY_FEEDBACK_ENABLED"))
        self.assertTrue(hasattr(settings, "DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT"))

    def test_default_values(self):
        self.assertFalse(settings.DOMAIN_RELIABILITY_TRACKING_ENABLED)
        self.assertTrue(settings.DOMAIN_RELIABILITY_DB_PATH.endswith("domain_reliability.db"))
        self.assertEqual(settings.DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES, 5)
        self.assertFalse(settings.DOMAIN_RELIABILITY_FEEDBACK_ENABLED)
        self.assertEqual(settings.DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT, 5)


if __name__ == "__main__":
    unittest.main()
