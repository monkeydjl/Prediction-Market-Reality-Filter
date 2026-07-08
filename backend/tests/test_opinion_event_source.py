import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.services import opinion_event_source as source


def _market(**overrides):
    market = {
        "id": "op-1",
        "question": "Will BNB close above $1,000 in 2026?",
        "probability": 0.41,
        "volume": 777.0,
        "liquidity": 333.0,
        "url": "https://app.opinion.trade/market/op-1",
        "status": "open",
    }
    market.update(overrides)
    return market


class _FakeAsyncClient:
    def __init__(self, response):
        self.response = response
        self.get_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.response


class OpinionEventSourceTests(unittest.TestCase):
    def test_missing_api_key_returns_empty_without_fetching(self):
        with patch.object(source.settings, "OPINION_API_KEY", ""), patch.object(
            source, "_fetch_raw_markets", new=AsyncMock(return_value=[_market()])
        ) as fetch:
            self.assertEqual(asyncio.run(source.fetch_candidate_events(limit=5)), [])
            fetch.assert_not_called()

    def test_fetch_raw_markets_sends_api_key_header(self):
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {"data": [_market()]}
        client = _FakeAsyncClient(response)

        with patch.object(
            source.httpx, "AsyncClient", Mock(return_value=client)
        ), patch.object(
            source.settings,
            "OPINION_API_URL",
            "https://openapi.opinion.trade/openapi/market",
        ), patch.object(source.settings, "OPINION_API_KEY", "secret"):
            markets = asyncio.run(source._fetch_raw_markets(limit=4))

        self.assertEqual(markets, [_market()])
        self.assertEqual(
            client.get_calls,
            [
                (
                    "https://openapi.opinion.trade/openapi/market",
                    {
                        "headers": {"apikey": "secret"},
                        "params": {"limit": "20"},
                    },
                )
            ],
        )

    def test_fetch_candidate_events_normalizes_market(self):
        with patch.object(source.settings, "OPINION_API_KEY", "secret"), patch.object(
            source, "_fetch_raw_markets", new=AsyncMock(return_value=[_market()])
        ):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events[0]["question"], "Will BNB close above $1,000 in 2026?")
        self.assertEqual(events[0]["baseline_probability"], 41.0)
        self.assertEqual(events[0]["source"]["platform"], "Opinion")
        self.assertEqual(events[0]["source"]["chain"], "BNB Chain")

    def test_filters_closed_blank_missing_and_impossible_probability(self):
        raw = [
            _market(id="ok"),
            _market(id="closed", status="closed"),
            _market(id="blank", question="   "),
            _market(id="missing-probability", probability=None),
            _market(id="too-high", probability=101),
        ]
        with patch.object(source.settings, "OPINION_API_KEY", "secret"), patch.object(
            source, "_fetch_raw_markets", new=AsyncMock(return_value=raw)
        ):
            events = asyncio.run(source.fetch_candidate_events(limit=10))
        self.assertEqual([e["source"]["source_id"] for e in events], ["ok"])

    def test_fetch_error_degrades_to_empty(self):
        with patch.object(source.settings, "OPINION_API_KEY", "secret"), patch.object(
            source,
            "_fetch_raw_markets",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ), self.assertLogs("app.services.opinion_event_source", level="WARNING") as logs:
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events, [])
        self.assertIn("source=opinion_candidates", "\n".join(logs.output))
