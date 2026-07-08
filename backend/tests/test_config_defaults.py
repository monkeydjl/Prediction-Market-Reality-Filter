import unittest

from app.core.config import settings


class OnChainSourceConfigDefaultsTests(unittest.TestCase):
    def test_limitless_defaults_to_public_active_endpoint(self):
        self.assertTrue(settings.LIMITLESS_SOURCE_ENABLED)
        self.assertEqual(
            settings.LIMITLESS_API_URL,
            "https://api.limitless.exchange/markets/active",
        )
        self.assertEqual(settings.LIMITLESS_SOURCE_NAME, "Limitless")

    def test_onchain_source_weights_exclude_probable_and_manifold(self):
        self.assertIn("Limitless", settings.SOURCE_WEIGHTS)
        self.assertNotIn("Probable", settings.SOURCE_WEIGHTS)
        self.assertNotIn("Manifold", settings.SOURCE_WEIGHTS)

    def test_opinion_defaults_to_key_gated_endpoint(self):
        self.assertTrue(settings.OPINION_SOURCE_ENABLED)
        self.assertEqual(
            settings.OPINION_API_URL,
            "https://openapi.opinion.trade/openapi/market",
        )
        self.assertEqual(settings.OPINION_API_KEY, "")
        self.assertEqual(settings.OPINION_SOURCE_NAME, "Opinion")
        self.assertIn("Opinion", settings.SOURCE_WEIGHTS)
