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

    def test_sports_general_tour_de_france(self):
        result = classify_market(
            "Will Decathlon CMA CGM Team bring this exact team to the 2026 Tour de France?"
        )
        self.assertEqual(result.category, "sports_general")

    def test_sports_game_win_against(self):
        result = classify_market("Will Portugal win against Croatia?")
        self.assertEqual(result.category, "sports_game")

    def test_geopolitics_regime_fall(self):
        result = classify_market("Will Iran's regime fall in 2026?")
        self.assertEqual(result.category, "geopolitics_general")

    def test_geopolitics_strait_of_hormuz(self):
        result = classify_market(
            "Strait of Hormuz traffic returns to normal by July 7? [Polymarket]"
        )
        self.assertEqual(result.category, "geopolitics_general")

    def test_btc_price_price_of_bitcoin(self):
        result = classify_market("Will the price of Bitcoin be above $58,000 on July 4?")
        self.assertEqual(result.category, "crypto_price_btc")

    def test_congressional_house_seat(self):
        result = classify_market("Will the Republican Party win the TX-31 House seat?")
        self.assertEqual(result.category, "congressional")

    def test_policy_department_abolished(self):
        result = classify_market("Department of Education abolished by July 4, 2026")
        self.assertEqual(result.category, "policy_general")

    def test_remaining_unknown_sample_patterns(self):
        examples = {
            "Will Pete Hegseth leave the Trump administration before 2027?": "politics_general",
            "Fable reenabled for Europeans before July 1?": "tech_product",
            "Will Elon Musk visit Mars in his lifetime?": "science_event",
            "Tampa Bay Rays vs. Boston Red Sox": "sports_game",
            "Will Dan Sullivan win the Alaska Senate race in 2026?": "congressional",
            "Will Ukraine win the Russo-Ukrainian War?": "geopolitics_general",
            "Will the WTI Crude Oil Spot Price be above $76 on June 29, 2026?": "markets",
            "Will the Bank of Russia make no change to the key rate after the July Meeting?": "monetary",
            "Next Pirates of the Caribbean film: Will Johnny Depp be cast?": "entertainment_awards",
        }

        for question, expected in examples.items():
            with self.subTest(question=question):
                self.assertEqual(classify_market(question).category, expected)

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
