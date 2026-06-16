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
        self.assertEqual(events[0], {
            "question": "Will it rain?",
            "baseline_probability": 64.0,
            "volume": 1000.0,
            "liquidity": 250.0,
            "source": {
                "type": "prediction_market",
                "platform": "Kalshi",
                "source_id": "EVT",
                "question": "Will it rain?",
                "baseline_probability": 64.0,
                "liquidity": 250.0,
                "volume": 1000.0,
                "url": "https://kalshi.com/markets/evt",
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
                          new=AsyncMock(side_effect=RuntimeError("boom"))):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
