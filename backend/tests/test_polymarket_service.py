import math
import unittest

from app.services.polymarket_service import parse_market, parse_outcome_prices


class PolymarketServiceTests(unittest.TestCase):
    def test_parse_outcome_prices_falls_back_for_non_finite_values(self):
        yes_price, no_price = parse_outcome_prices('["NaN", "Infinity"]')

        self.assertEqual(yes_price, 0.5)
        self.assertEqual(no_price, 0.5)

    def test_parse_market_falls_back_for_non_finite_numeric_fields(self):
        market = parse_market({
            "id": "poly-1",
            "question": "Will Bitcoin reach a new high in 2026?",
            "outcomePrices": '["NaN", "-Infinity"]',
            "volume": "Infinity",
            "liquidity": "NaN",
        })

        self.assertIsNotNone(market)
        self.assertTrue(math.isfinite(market.yes_price))
        self.assertTrue(math.isfinite(market.no_price))
        self.assertEqual(market.yes_price, 0.5)
        self.assertEqual(market.no_price, 0.5)
        self.assertEqual(market.volume, 0.0)
        self.assertEqual(market.liquidity, 0.0)


if __name__ == "__main__":
    unittest.main()
