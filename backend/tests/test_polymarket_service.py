import asyncio
import math
import unittest
from unittest.mock import AsyncMock, patch

from app.services.polymarket_service import (
    fetch_markets,
    is_crypto_market,
    parse_market,
    parse_outcome_prices,
)


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


def _raw_market(question, mid="m"):
    """Minimal gamma-api item shape that parse_market accepts."""
    return {
        "id": mid,
        "slug": "s",
        "question": question,
        "outcomePrices": '["0.5", "0.5"]',
        "volume": 1000,
        "liquidity": 5000,
    }


class CryptoFetchTests(unittest.TestCase):
    """fetch_markets(crypto_only=True): gamma-api tag_id filter + crypto-keyword
    gate. The tag is best-effort, so the keyword gate must reject a non-crypto
    market even if the (mocked) tag filter returned it."""

    def _patch_response(self, items):
        """Patch httpx.AsyncClient.get to return a fake response carrying `items`."""
        response = AsyncMock()
        response.raise_for_status = lambda: None
        response.json = lambda: items
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=response)
        # Capture the request params so a test can assert the tag_id was sent.
        self._last_client = client
        return patch("httpx.AsyncClient", return_value=client)

    def test_crypto_only_sends_tag_id_param(self):
        with self._patch_response([_raw_market("Will Bitcoin reach $100k?", "m1")]):
            asyncio.run(fetch_markets(limit=5, crypto_only=True))
        params = self._last_client.get.call_args.kwargs.get("params", {})
        self.assertEqual(params.get("tag_id"), "crypto")

    def test_crypto_only_keyword_gate_rejects_non_crypto(self):
        # Tag filter is mocked to return a politics market anyway; the keyword
        # gate must drop it so a wrong/empty tag never floods the crypto pool.
        items = [
            _raw_market("Will Bitcoin reach $100k?", "m1"),       # crypto: keep
            _raw_market("Will the Iran deal pass?", "m2"),        # not crypto: drop
            _raw_market("Will Solana hit $300?", "m3"),           # crypto: keep
        ]
        with self._patch_response(items):
            markets = asyncio.run(fetch_markets(limit=10, crypto_only=True))
        questions = [m.question for m in markets]
        self.assertIn("Will Bitcoin reach $100k?", questions)
        self.assertIn("Will Solana hit $300?", questions)
        self.assertNotIn("Will the Iran deal pass?", questions)

    def test_crypto_only_default_off_unchanged(self):
        # crypto_only defaults False: uses is_allowed_market (broader keyword
        # set), not the crypto gate. A politics market in ALLOWED_KEYWORDS passes.
        items = [
            _raw_market("Will the next Trump tariff take effect?", "m1"),
        ]
        with self._patch_response(items):
            markets = asyncio.run(fetch_markets(limit=5))
        self.assertEqual(len(markets), 1)
        self.assertEqual(markets[0].question, "Will the next Trump tariff take effect?")


class IsCryptoMarketTests(unittest.TestCase):
    def test_crypto_question_passes_gate(self):
        for q in (
            "Will Bitcoin reach $100k?",
            "Will ETH flip BTC?",
            "Will Solana hit $300?",
        ):
            market = parse_market(_raw_market(q))
            self.assertTrue(is_crypto_market(market), q)

    def test_non_crypto_question_rejected(self):
        market = parse_market(_raw_market("Will the Iran nuclear deal pass?"))
        self.assertFalse(is_crypto_market(market))


if __name__ == "__main__":
    unittest.main()
