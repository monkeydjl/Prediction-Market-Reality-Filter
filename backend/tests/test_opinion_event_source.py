import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.services import opinion_event_source as source


def _market(**overrides):
    market = {
        "marketId": "op-1",
        "marketTitle": "Will BNB close above $1,000 in 2026?",
        "yesTokenId": "yes-token-1",
        "noTokenId": "no-token-1",
        "latestPrice": 0.41,
        "volume": "777.0",
        "liquidity": "333.0",
        "status": "activated",
        "marketType": 0,
    }
    market.update(overrides)
    return market


class _FakeAsyncClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.get_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.responses.pop(0)


class OpinionEventSourceTests(unittest.TestCase):
    def test_missing_api_key_returns_empty_without_fetching(self):
        with patch.object(source.settings, "OPINION_API_KEY", ""), patch.object(
            source, "_fetch_raw_markets", new=AsyncMock(return_value=[_market()])
        ) as fetch:
            self.assertEqual(asyncio.run(source.fetch_candidate_events(limit=5)), [])
            fetch.assert_not_called()

    def test_fetch_raw_markets_reads_documented_result_list(self):
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {"result": {"list": [_market()], "total": 1}}
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
                        "params": {"limit": "20", "marketType": "0", "status": "activated"},
                    },
                )
            ],
        )

    def test_fetch_candidate_events_normalizes_documented_market(self):
        with patch.object(source.settings, "OPINION_API_KEY", "secret"), patch.object(
            source, "_fetch_raw_markets", new=AsyncMock(return_value=[_market()])
        ):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events[0]["question"], "Will BNB close above $1,000 in 2026?")
        self.assertEqual(events[0]["baseline_probability"], 41.0)
        self.assertEqual(events[0]["source"]["platform"], "Opinion")
        self.assertEqual(events[0]["source"]["chain"], "BNB Chain")
        self.assertEqual(events[0]["source"]["source_id"], "op-1")
        self.assertEqual(events[0]["source"]["url"], "https://app.opinion.trade/market/op-1")

    def test_filters_unsupported_and_malformed_markets(self):
        raw = [
            _market(marketId="ok"),
            _market(marketId="closed", status="closed"),
            _market(marketId="non-binary", marketType=1),
            _market(marketId=""),
            _market(marketId="blank", marketTitle="   "),
            _market(marketId="missing-probability", latestPrice=None),
            _market(marketId="too-high", latestPrice=101),
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
