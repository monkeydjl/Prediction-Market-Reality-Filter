import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.services import predict_fun_event_source as source


def _market(**overrides):
    market = {
        "id": "pf-1",
        "title": "Will BTC close above $150,000 in 2026?",
        "resolution": {
            "bestBid": {"price": 0.57},
            "bestAsk": {"price": 0.59},
        },
        "volume": "900.0",
        "liquidity": "150.0",
        "tradingStatus": "OPEN",
        "status": "REGISTERED",
        "isVisible": True,
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


class PredictFunEventSourceTests(unittest.TestCase):
    def test_missing_api_key_returns_empty_without_fetching(self):
        with patch.object(source.settings, "PREDICT_FUN_API_KEY", ""), patch.object(
            source, "_fetch_raw_markets", new=AsyncMock(return_value=[_market()])
        ) as fetch:
            self.assertEqual(asyncio.run(source.fetch_candidate_events(limit=5)), [])
            fetch.assert_not_called()

    def test_fetch_raw_markets_sends_api_key_header_and_first_param(self):
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {"data": [_market()]}
        client = _FakeAsyncClient(response)

        with patch.object(
            source.httpx, "AsyncClient", Mock(return_value=client)
        ), patch.object(
            source.settings,
            "PREDICT_FUN_API_URL",
            "https://api.predict.fun/v1/markets",
        ), patch.object(source.settings, "PREDICT_FUN_API_KEY", "secret"):
            markets = asyncio.run(source._fetch_raw_markets(limit=6))

        self.assertEqual(markets, [_market()])
        self.assertEqual(
            client.get_calls,
            [
                (
                    "https://api.predict.fun/v1/markets",
                    {
                        "headers": {"x-api-key": "secret"},
                        "params": {"first": "30"},
                    },
                )
            ],
        )

    def test_fetch_candidate_events_normalizes_documented_market(self):
        with patch.object(source.settings, "PREDICT_FUN_API_KEY", "secret"), patch.object(
            source, "_fetch_raw_markets", new=AsyncMock(return_value=[_market()])
        ):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events[0]["question"], "Will BTC close above $150,000 in 2026?")
        self.assertEqual(events[0]["baseline_probability"], 58.0)
        self.assertEqual(events[0]["source"]["platform"], "Predict.fun")
        self.assertEqual(events[0]["source"]["chain"], "BNB Chain")
        self.assertEqual(events[0]["source"]["source_id"], "pf-1")

    def test_filters_unsupported_and_malformed_markets(self):
        raw = [
            _market(id="ok"),
            _market(id="halted", tradingStatus="HALTED"),
            _market(id="not-registered", status="DRAFT"),
            _market(id="hidden", isVisible=False),
            _market(id=""),
            _market(id="blank", title="   "),
            _market(id="missing-resolution", resolution=None),
            _market(id="negative", resolution={"bestBid": {"price": -1}, "bestAsk": {"price": 0.5}}),
        ]
        with patch.object(source.settings, "PREDICT_FUN_API_KEY", "secret"), patch.object(
            source, "_fetch_raw_markets", new=AsyncMock(return_value=raw)
        ):
            events = asyncio.run(source.fetch_candidate_events(limit=10))
        self.assertEqual([e["source"]["source_id"] for e in events], ["ok"])

    def test_fetch_error_degrades_to_empty(self):
        with patch.object(source.settings, "PREDICT_FUN_API_KEY", "secret"), patch.object(
            source,
            "_fetch_raw_markets",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ), self.assertLogs(
            "app.services.predict_fun_event_source", level="WARNING"
        ) as logs:
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events, [])
        self.assertIn("source=predict_fun_candidates", "\n".join(logs.output))
