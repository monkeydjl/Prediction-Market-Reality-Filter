import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.services import limitless_event_source as source


def _market(**overrides):
    market = {
        "id": "lim-1",
        "title": "Will ETH close above $5,000 in 2026?",
        "yesProbability": 0.62,
        "volume": 1200.5,
        "liquidity": 450.25,
        "url": "https://limitless.exchange/markets/lim-1",
        "status": "active",
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


class LimitlessEventSourceTests(unittest.TestCase):
    def test_fetch_candidate_events_normalizes_market(self):
        with patch.object(
            source, "_fetch_raw_markets", new=AsyncMock(return_value=[_market()])
        ):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events[0]["question"], "Will ETH close above $5,000 in 2026?")
        self.assertEqual(events[0]["baseline_probability"], 62.0)
        self.assertEqual(events[0]["source"]["platform"], "Limitless")
        self.assertEqual(events[0]["source"]["chain"], "Base")

    def test_filters_malformed_closed_and_ambiguous_markets(self):
        raw = [
            _market(id="ok"),
            _market(id="closed", status="closed"),
            _market(id="blank", title="   "),
            _market(id="missing-probability", yesProbability=None),
            "not a dict",
        ]
        with patch.object(source, "_fetch_raw_markets", new=AsyncMock(return_value=raw)):
            events = asyncio.run(source.fetch_candidate_events(limit=10))
        self.assertEqual([e["source"]["source_id"] for e in events], ["ok"])

    def test_disabled_or_empty_url_returns_empty_without_fetching(self):
        with patch.object(source.settings, "LIMITLESS_SOURCE_ENABLED", False), patch.object(
            source, "_fetch_raw_markets", new=AsyncMock(return_value=[_market()])
        ) as fetch:
            self.assertEqual(asyncio.run(source.fetch_candidate_events(limit=5)), [])
            fetch.assert_not_called()

    def test_fetch_error_degrades_to_empty(self):
        with patch.object(
            source,
            "_fetch_raw_markets",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ), self.assertLogs("app.services.limitless_event_source", level="WARNING") as logs:
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events, [])
        self.assertIn("source=limitless_candidates", "\n".join(logs.output))

    def test_fetch_raw_markets_uses_public_endpoint_without_headers(self):
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {"markets": [_market()]}
        client = _FakeAsyncClient(response)

        with patch.object(
            source.httpx, "AsyncClient", Mock(return_value=client)
        ), patch.object(
            source.settings,
            "LIMITLESS_API_URL",
            "https://api.limitless.exchange/markets/active",
        ):
            markets = asyncio.run(source._fetch_raw_markets(limit=7))

        self.assertEqual(markets, [_market()])
        self.assertEqual(
            client.get_calls,
            [
                (
                    "https://api.limitless.exchange/markets/active",
                    {"params": {"limit": "35"}},
                )
            ],
        )
