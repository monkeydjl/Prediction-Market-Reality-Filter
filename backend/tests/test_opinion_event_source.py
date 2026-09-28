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

    def test_every_documented_probability_field_is_read(self):
        # Driven off _PROBABILITY_FIELDS itself, so a new member is covered too.
        # All four read as 0-1 fractions -> x100 (audit section 18.2 / section 22.2.1: only
        # latestPrice was exercised before the section 21 fix, and that fix spot-checked
        # three of the four fallbacks rather than the constant).
        for field in source._PROBABILITY_FIELDS:
            with self.subTest(field=field):
                market = _market()
                for other in source._PROBABILITY_FIELDS:
                    market.pop(other, None)
                market[field] = 0.83
                with patch.object(
                    source.settings, "OPINION_API_KEY", "secret"
                ), patch.object(
                    source, "_fetch_raw_markets", new=AsyncMock(return_value=[market])
                ):
                    events = asyncio.run(source.fetch_candidate_events(limit=5))
                self.assertEqual(events[0]["baseline_probability"], 83.0)

    def test_latest_price_is_preferred_over_fallbacks(self):
        market = _market(latestPrice=0.41, yesPrice=0.83)
        with patch.object(source.settings, "OPINION_API_KEY", "secret"), patch.object(
            source, "_fetch_raw_markets", new=AsyncMock(return_value=[market])
        ):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events[0]["baseline_probability"], 41.0)

    def test_fetch_error_degrades_to_empty(self):
        with patch.object(source.settings, "OPINION_API_KEY", "secret"), patch.object(
            source,
            "_fetch_raw_markets",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ), self.assertLogs("app.services.opinion_event_source", level="WARNING") as logs:
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events, [])
        self.assertIn("source=opinion_candidates", "\n".join(logs.output))

    def test_every_listed_field_and_status_is_honoured(self):
        # Driven off the module's own tuples / status set, so adding an entry is
        # covered automatically (audit section 22.2.1).
        for const in ("_QUESTION_FIELDS", "_ID_FIELDS"):
            for field in getattr(source, const):
                with self.subTest(const=const, field=field):
                    self.assertEqual(
                        source._extract_text({field: "VALUE"}, getattr(source, const)),
                        "VALUE",
                    )
        for const in ("_VOLUME_FIELDS", "_LIQUIDITY_FIELDS"):
            for field in getattr(source, const):
                with self.subTest(const=const, field=field):
                    self.assertEqual(
                        source._extract_number({field: "12.5"}, getattr(source, const)),
                        12.5,
                    )
        for status in source._ACTIVE_STATUSES:
            with self.subTest(status=status):
                # upper-cased to also pin the .lower() normalisation the code relies on
                self.assertTrue(source._has_supported_status({"status": status.upper()}))
