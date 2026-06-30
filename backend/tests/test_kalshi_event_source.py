"""
Unit tests for the Kalshi event-source adapter.

Network-free: `_fetch_raw_events` (the httpx seam) is mocked. Covers
normalization of a single-leg event to the shared candidate-event shape, the
single-leg-binary eligibility filter (multi-leg / settled / resolved / untitled
excluded), the baseline price fallback (last price -> bid/ask midpoint -> 50),
the `limit` cap, the no-URL off switch, and graceful failure.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services import kalshi_event_source as source


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
        self.params = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, params=None):
        self.params = params
        return _Response(self._data)


def _event(title="Will it rain?", event_ticker="EVT", market=None, markets=None):
    if markets is None:
        base = {
            "status": "active",
            "result": "",
            "last_price_dollars": 0.64,
            "yes_bid_dollars": 0.63,
            "yes_ask_dollars": 0.65,
            "volume_fp": 1000.0,
            "liquidity_dollars": 250.0,
        }
        if market:
            base.update(market)
        markets = [base]
    return {"title": title, "event_ticker": event_ticker, "markets": markets}


class KalshiEventSourceTests(unittest.TestCase):
    def test_normalizes_single_leg_event(self):
        with patch.object(source, "_fetch_raw_events",
                          new=AsyncMock(return_value=[_event()])):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(len(events), 1)
        # last_price_dollars=0.64 wins, so bid/ask are 0.0 and spread is 0.0.
        self.assertEqual(events[0], {
            "question": "Will it rain?",
            "baseline_probability": 64.0,
            "volume": 1000.0,
            "liquidity": 250.0,
            "bid_ask": {"bid": 0.0, "ask": 0.0, "spread": 0.0},
            "source": {
                "type": "prediction_market",
                "platform": "Kalshi",
                "source_id": "EVT",
                "question": "Will it rain?",
                "baseline_probability": 64.0,
                "liquidity": 250.0,
                "volume": 1000.0,
                "url": "https://kalshi.com/markets/evt",
                "status": "active",
                "close_time": "",
            },
        })

    def test_multi_leg_settled_resolved_and_untitled_excluded(self):
        multi = _event(title="Who wins?", event_ticker="MULTI",
                       markets=[{"status": "active"}, {"status": "active"}])
        settled = _event(title="Settled?", event_ticker="SET", market={"status": "settled"})
        resolved = _event(title="Resolved?", event_ticker="RES", market={"result": "yes"})
        untitled = _event(title="   ", event_ticker="NOPE")
        ok = _event(title="Clean?", event_ticker="OK")
        with patch.object(source, "_fetch_raw_events",
                          new=AsyncMock(return_value=[multi, settled, resolved, untitled, ok])):
            events = asyncio.run(source.fetch_candidate_events(limit=10))
        self.assertEqual([e["source"]["source_id"] for e in events], ["OK"])

    def test_baseline_falls_back_to_midpoint_then_fifty(self):
        mid = _event(event_ticker="MID", market={
            "last_price_dollars": 0.0, "yes_bid_dollars": 0.40, "yes_ask_dollars": 0.50})
        none = _event(event_ticker="NONE", market={
            "last_price_dollars": 0.0, "yes_bid_dollars": 0.0, "yes_ask_dollars": 0.0})
        with patch.object(source, "_fetch_raw_events", new=AsyncMock(return_value=[mid, none])):
            events = asyncio.run(source.fetch_candidate_events(limit=10))
        by_id = {e["source"]["source_id"]: e for e in events}
        self.assertEqual(by_id["MID"]["baseline_probability"], 45.0)
        self.assertEqual(by_id["NONE"]["baseline_probability"], 50.0)

    def test_bid_ask_transparent_when_last_price_missing(self):
        """When last_price is 0, bid/ask midpoint is used and bid_ask is populated."""
        ev = _event(
            event_ticker="TEST-EVENT",
            title="Test event",
            market={
                "last_price_dollars": 0.0,
                "yes_bid_dollars": 0.42,
                "yes_ask_dollars": 0.46,
                "volume_fp": 1000.0,
                "liquidity_dollars": 5000.0,
            },
        )
        with patch.object(source, "_fetch_raw_events",
                          new=AsyncMock(return_value=[ev])):
            candidates = asyncio.run(source.fetch_candidate_events(limit=1))
        self.assertEqual(len(candidates), 1)
        bid_ask = candidates[0]["bid_ask"]
        self.assertEqual(bid_ask["bid"], 42.0)
        self.assertEqual(bid_ask["ask"], 46.0)
        self.assertEqual(bid_ask["spread"], 4.0)
        # midpoint baseline must agree with bid/ask transparency.
        self.assertEqual(candidates[0]["baseline_probability"], 44.0)

    def test_bid_ask_zero_when_last_price_present(self):
        """When last_price is present, bid/ask pass-through is 0/0 and spread is 0."""
        ev = _event(event_ticker="LAST", market={
            "last_price_dollars": 0.70,
            "yes_bid_dollars": 0.69,
            "yes_ask_dollars": 0.71,
        })
        with patch.object(source, "_fetch_raw_events",
                          new=AsyncMock(return_value=[ev])):
            candidates = asyncio.run(source.fetch_candidate_events(limit=1))
        self.assertEqual(candidates[0]["bid_ask"], {"bid": 0.0, "ask": 0.0, "spread": 0.0})

    def test_bid_ask_spread_zero_when_one_side_missing(self):
        """spread is 0.0 unless BOTH bid>0 and ask>0."""
        bid_only = _event(event_ticker="BID", market={
            "last_price_dollars": 0.0, "yes_bid_dollars": 0.42, "yes_ask_dollars": 0.0})
        ask_only = _event(event_ticker="ASK", market={
            "last_price_dollars": 0.0, "yes_bid_dollars": 0.0, "yes_ask_dollars": 0.46})
        with patch.object(source, "_fetch_raw_events",
                          new=AsyncMock(return_value=[bid_only, ask_only])):
            events = asyncio.run(source.fetch_candidate_events(limit=10))
        by_id = {e["source"]["source_id"]: e for e in events}
        self.assertEqual(by_id["BID"]["bid_ask"], {"bid": 42.0, "ask": 0.0, "spread": 0.0})
        self.assertEqual(by_id["ASK"]["bid_ask"], {"bid": 0.0, "ask": 46.0, "spread": 0.0})

    def test_respects_limit(self):
        evs = [_event(event_ticker=f"E{i}") for i in range(10)]
        with patch.object(source, "_fetch_raw_events", new=AsyncMock(return_value=evs)):
            events = asyncio.run(source.fetch_candidate_events(limit=3))
        self.assertEqual(len(events), 3)

    def test_no_url_configured_returns_empty(self):
        with patch.object(source.settings, "KALSHI_API_URL", ""):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events, [])

    def test_fetch_error_degrades_to_empty(self):
        with patch.object(source, "_fetch_raw_events",
                          new=AsyncMock(side_effect=RuntimeError("boom"))), \
             self.assertLogs("app.services.kalshi_event_source", level="WARNING") as logs:
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events, [])
        text = "\n".join(logs.output)
        self.assertIn("source=kalshi_candidates", text)
        self.assertIn("policy=fail_closed_empty_list", text)

    def test_fetch_resolved_markets_maps_results(self):
        raw = [
            {"title": "Q yes", "event_ticker": "K-YES", "markets": [{"result": "yes"}]},
            {"title": "Q no", "event_ticker": "K-NO", "markets": [{"result": "no"}]},
            {"title": "Q multi", "event_ticker": "K-M", "markets": [{"result": "yes"}, {"result": "no"}]},
            {"title": "Q none", "event_ticker": "K-X", "markets": [{"result": ""}]},
            {"title": "", "event_ticker": "K-E", "markets": [{"result": "yes"}]},
        ]
        with patch.object(source, "_fetch_raw_resolved",
                          new=AsyncMock(return_value=raw)):
            out = asyncio.run(source.fetch_resolved_markets(limit=10))
        self.assertEqual(out, [
            {"id": "K-YES", "question": "Q yes", "actual_outcome": 100.0},
            {"id": "K-NO", "question": "Q no", "actual_outcome": 0.0},
        ])

    def test_fetch_resolved_error_degrades_to_empty(self):
        with patch.object(source, "_fetch_raw_resolved",
                          new=AsyncMock(side_effect=RuntimeError("boom"))), \
             self.assertLogs("app.services.kalshi_event_source", level="WARNING") as logs:
            self.assertEqual(asyncio.run(source.fetch_resolved_markets()), [])
        text = "\n".join(logs.output)
        self.assertIn("source=kalshi_resolved", text)
        self.assertIn("policy=fail_closed_empty_list", text)

    def test_fetch_raw_resolved_overfetches_for_single_leg_results(self):
        client = _Client({"events": []})
        with patch.object(source.settings, "KALSHI_API_URL", "https://kalshi.test/events"), \
                patch.object(source.httpx, "AsyncClient", return_value=client):
            events = asyncio.run(source._fetch_raw_resolved(limit=10))

        self.assertEqual(events, [])
        self.assertEqual(client.params["status"], "settled")
        self.assertEqual(client.params["with_nested_markets"], "true")
        self.assertEqual(client.params["limit"], "50")


if __name__ == "__main__":
    unittest.main()
