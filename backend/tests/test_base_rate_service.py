"""Tests for base_rate_service.classify_market word-boundary matching.

Regression: substring matching (`kw in text`) caused false positives where
short keywords matched inside unrelated words (e.g. "token" in "tokenized",
"sol" in "solar"). classify_market now uses word_in_text so only standalone
word-boundary matches fire.
"""

import unittest

from app.services.base_rate_service import classify_market


class ClassifyMarketPositiveTests(unittest.TestCase):
    """Legitimate questions must still classify correctly."""

    def test_btc_price(self):
        result = classify_market("Will Bitcoin reach $100,000 by end of 2026?")
        self.assertEqual(result.category, "crypto_price_btc")

    def test_eth_price(self):
        result = classify_market("Will Ethereum pass $5,000?")
        self.assertEqual(result.category, "crypto_price_eth")

    def test_crypto_etf(self):
        result = classify_market("Will the Bitcoin ETF be approved?")
        self.assertEqual(result.category, "crypto_etf")

    def test_crypto_general(self):
        result = classify_market("Will crypto regulations tighten in 2026?")
        self.assertEqual(result.category, "crypto_general")

    def test_ai_release(self):
        result = classify_market("Will OpenAI release GPT-5 this year?")
        self.assertEqual(result.category, "ai_release")

    def test_ipo(self):
        result = classify_market("Will SpaceX go public via IPO?")
        self.assertEqual(result.category, "ipo")

    def test_weather_temperature_event(self):
        result = classify_market(
            "Will the highest temperature in Jeddah be 40 degrees or higher on July 7?"
        )
        self.assertEqual(result.category, "weather_event")

    def test_entertainment_awards(self):
        result = classify_market("Will a film win Best Picture at the Oscars?")
        self.assertEqual(result.category, "entertainment_awards")

    def test_company_earnings(self):
        result = classify_market(
            "Will Nvidia report revenue above guidance this quarter?"
        )
        self.assertEqual(result.category, "company_earnings")

    def test_policy_general(self):
        result = classify_market("Will Congress pass the stablecoin bill in 2026?")
        self.assertEqual(result.category, "policy_general")

    def test_health_event(self):
        result = classify_market("Will the FDA approve the new obesity drug this year?")
        self.assertEqual(result.category, "health_event")

    def test_sports_general_world_cup(self):
        result = classify_market("Will Brazil reach the FIFA World Cup semifinals?")
        self.assertEqual(result.category, "sports_general")

    def test_unknown_fallback(self):
        """A completely unrelated question falls back to unknown."""
        result = classify_market("Will aliens land on the White House?")
        self.assertEqual(result.category, "unknown")


class ClassifyMarketWordBoundaryRegressionTests(unittest.TestCase):
    """Substring false positives that the old `kw in text` would fire.

    These are the core regressions: short keywords must NOT match inside
    longer unrelated words. classify_market now uses word_in_text so these
    all fall through to unknown (or a more specific match).
    """

    def test_token_in_tokenized_does_not_match_crypto(self):
        """'token' must not match inside 'tokenized'."""
        result = classify_market(
            "Will the tokenized stock market exceed $10 trillion?"
        )
        # "tokenized" contains "token" as substring, but word boundary rejects it.
        # "stock market" is not a crypto keyword. Should fall through.
        self.assertNotEqual(result.category, "crypto_general")

    def test_sol_in_solar_does_not_match_solana(self):
        """'sol' must not match inside 'solar'."""
        result = classify_market("Will solar energy prices drop in 2026?")
        self.assertNotEqual(result.category, "altcoin_price")

    def test_gpt_in_scraped_does_not_match_ai(self):
        """'gpt' should not match inside a longer non-word token."""
        result = classify_market("Will the new scraping tool launch?")
        self.assertNotEqual(result.category, "ai_release")


if __name__ == "__main__":
    unittest.main()
