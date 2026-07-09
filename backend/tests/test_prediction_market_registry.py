import unittest

from app.services.prediction_market_registry import (
    active_discovery_platform_names,
    list_prediction_market_platforms,
)


class PredictionMarketRegistryTests(unittest.TestCase):
    def test_registry_contains_requested_onchain_platforms(self):
        platforms = {p.key: p for p in list_prediction_market_platforms()}

        self.assertEqual(platforms["opinion"].name, "Opinion")
        self.assertEqual(platforms["opinion"].chain, "BNB Chain")
        self.assertEqual(platforms["opinion"].homepage_url, "https://app.opinion.trade/trending")
        self.assertFalse(platforms["opinion"].active_discovery)
        self.assertIn("API key", platforms["opinion"].status_note)

        self.assertEqual(platforms["limitless"].name, "Limitless")
        self.assertEqual(platforms["limitless"].chain, "Base")
        self.assertEqual(platforms["limitless"].homepage_url, "https://limitless.exchange/")
        self.assertTrue(platforms["limitless"].active_discovery)

        self.assertEqual(platforms["predict_fun"].name, "Predict.fun")
        self.assertEqual(platforms["predict_fun"].chain, "BNB Chain")
        self.assertEqual(platforms["predict_fun"].homepage_url, "https://predict.fun/")
        self.assertFalse(platforms["predict_fun"].active_discovery)
        self.assertIn("API key", platforms["predict_fun"].status_note)

        self.assertEqual(platforms["probable"].name, "Probable")
        self.assertEqual(platforms["probable"].chain, "BNB Chain")
        self.assertEqual(platforms["probable"].homepage_url, "https://probable.finance/")
        self.assertFalse(platforms["probable"].active_discovery)
        self.assertIn("requires verification", platforms["probable"].status_note)

    def test_active_discovery_platforms_include_default_live_sources_only(self):
        active = active_discovery_platform_names()

        self.assertEqual(active, ["Polymarket", "Kalshi", "Limitless"])
        self.assertNotIn("Opinion", active)
        self.assertNotIn("Predict.fun", active)
        self.assertNotIn("Probable", active)
        self.assertNotIn("Manifold", active)


if __name__ == "__main__":
    unittest.main()
