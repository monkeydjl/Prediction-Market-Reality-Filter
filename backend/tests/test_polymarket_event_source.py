import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import polymarket_event_source as source


def _market(question, id, yes_price, volume, liquidity, slug="x-slug"):
    return SimpleNamespace(
        question=question, id=id, yes_price=yes_price,
        volume=volume, liquidity=liquidity, slug=slug,
    )


class PolymarketEventSourceTests(unittest.TestCase):
    def test_fetch_candidate_events_normalizes(self):
        market = _market("Will X happen?", "m1", 0.25, 1000.0, 500.0)
        with patch("app.services.polymarket_service.fetch_markets",
                   AsyncMock(return_value=[market])), \
             patch("app.services.market_filter_service.filter_markets",
                   return_value=[market]):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0], {
            "question": "Will X happen?",
            "baseline_probability": 25.0,
            "volume": 1000.0,
            "liquidity": 500.0,
            "source": {
                "type": "prediction_market",
                "platform": "Polymarket",
                "source_id": "m1",
                "question": "Will X happen?",
                "baseline_probability": 25.0,
                "liquidity": 500.0,
                "volume": 1000.0,
                "url": "https://polymarket.com/event/x-slug",
            },
        })

    def test_fetch_candidate_events_defaults_missing_fields(self):
        market = _market("Q", "m2", None, None, None)
        with patch("app.services.polymarket_service.fetch_markets",
                   AsyncMock(return_value=[market])), \
             patch("app.services.market_filter_service.filter_markets",
                   return_value=[market]):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        ev = events[0]
        # Missing yes_price defaults to 0.5 -> 50.0%; missing volume/liquidity -> 0.0.
        self.assertEqual(ev["baseline_probability"], 50.0)
        self.assertEqual(ev["volume"], 0.0)
        self.assertEqual(ev["liquidity"], 0.0)
        self.assertEqual(ev["source"]["baseline_probability"], 50.0)

    def test_fetch_crypto_candidate_events_calls_crypto_only_fetch(self):
        # The crypto source must fetch with crypto_only=True so the gamma-api tag
        # filter + crypto-keyword gate apply. Capture the kwarg on the mock.
        market = _market("Will Bitcoin reach $100k?", "m1", 0.6, 1000.0, 500.0)
        fetch_mock = AsyncMock(return_value=[market])
        with patch("app.services.polymarket_service.fetch_markets", fetch_mock), \
             patch("app.services.market_filter_service.filter_markets",
                   return_value=[market]):
            events = asyncio.run(source.fetch_crypto_candidate_events(limit=5))
        fetch_mock.assert_awaited_once()
        self.assertTrue(fetch_mock.call_args.kwargs.get("crypto_only"))
        # Shape identical to fetch_candidate_events.
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["question"], "Will Bitcoin reach $100k?")
        self.assertEqual(events[0]["source"]["platform"], "Polymarket")


if __name__ == "__main__":
    unittest.main()
