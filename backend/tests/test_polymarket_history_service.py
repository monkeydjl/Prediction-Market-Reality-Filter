import asyncio
import math
import unittest
from unittest.mock import patch

from app.services import polymarket_history_service as source


class _Response:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _Client:
    def __init__(self, data):
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, params=None):
        return _Response(self._data)


class _FailingClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, params=None):
        raise RuntimeError("network down")


def _market(**overrides):
    market = {
        "id": "m1",
        "question": "Will Bitcoin close above $100k?",
        "outcomePrices": "[0.75, 0.25]",
        "volume": 1000.0,
        "liquidity": 500.0,
    }
    market.update(overrides)
    return market


class PolymarketHistoryServiceTests(unittest.TestCase):
    def test_fetch_resolved_markets_falls_back_for_non_finite_volume_fields(self):
        data = [_market(volume="Infinity", liquidity="NaN")]

        with patch.object(source.httpx, "AsyncClient", return_value=_Client(data)):
            markets = asyncio.run(source.fetch_resolved_markets(limit=1))

        self.assertEqual(len(markets), 1)
        self.assertEqual(markets[0]["volume"], 0.0)
        self.assertEqual(markets[0]["liquidity"], 0.0)

    def test_fetch_resolved_markets_skips_non_finite_final_price(self):
        data = [_market(outcomePrices='["NaN", 0.25]')]

        with patch.object(source.httpx, "AsyncClient", return_value=_Client(data)):
            markets = asyncio.run(source.fetch_resolved_markets(limit=1))

        self.assertEqual(markets, [])
        self.assertTrue(all(math.isfinite(m["final_yes_price"]) for m in markets))

    def test_fetch_resolved_markets_logs_malformed_market_rows(self):
        data = [_market(outcomePrices="[bad json")]

        with patch.object(source.httpx, "AsyncClient", return_value=_Client(data)), \
                self.assertLogs("app.services.polymarket_history_service", level="WARNING") as logs:
            markets = asyncio.run(source.fetch_resolved_markets(limit=1))

        self.assertEqual(markets, [])
        self.assertIn("Skipping malformed Polymarket resolved market", "\n".join(logs.output))

    def test_fetch_resolved_markets_logs_source_failure_policy(self):
        with patch.object(source.httpx, "AsyncClient", return_value=_FailingClient()), \
                self.assertLogs("app.services.polymarket_history_service", level="WARNING") as logs:
            markets = asyncio.run(source.fetch_resolved_markets(limit=1))

        self.assertEqual(markets, [])
        text = "\n".join(logs.output)
        self.assertIn("source=polymarket_resolved", text)
        self.assertIn("policy=fail_closed_empty_list", text)


if __name__ == "__main__":
    unittest.main()
