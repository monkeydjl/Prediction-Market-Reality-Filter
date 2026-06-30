"""
Unit tests for the Manifold event-source adapter.

Network-free: `_fetch_raw_markets` (the httpx seam) is mocked, mirroring how
test_polymarket_event_source mocks `fetch_markets`. Covers normalization to the
shared candidate-event shape, the eligibility filter, the no-URL off switch, and
graceful failure (fetch errors -> empty list).
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services import manifold_event_source as source


def _market(**overrides):
    market = {
        "id": "m1",
        "question": "Will X happen?",
        "probability": 0.25,
        "outcomeType": "BINARY",
        "volume": 1000.0,
        "totalLiquidity": 500.0,
        "isResolved": False,
        "url": "https://manifold.markets/u/x",
    }
    market.update(overrides)
    return market


class ManifoldEventSourceTests(unittest.TestCase):
    def test_fetch_candidate_events_normalizes(self):
        with patch.object(source, "_fetch_raw_markets",
                          new=AsyncMock(return_value=[_market()])):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0], {
            "question": "Will X happen?",
            "baseline_probability": 25.0,
            "volume": 1000.0,
            "liquidity": 500.0,
            "source": {
                "type": "prediction_market",
                "platform": "Manifold",
                "source_id": "m1",
                "question": "Will X happen?",
                "baseline_probability": 25.0,
                "liquidity": 500.0,
                "volume": 1000.0,
                "url": "https://manifold.markets/u/x",
            },
        })

    def test_ineligible_markets_are_filtered_out(self):
        raw = [
            _market(id="ok", probability=0.6),
            _market(id="multi", outcomeType="MULTIPLE_CHOICE"),
            _market(id="resolved", isResolved=True),
            _market(id="blank-question", question="   "),
            _market(id="no-probability", probability=None),
        ]
        with patch.object(source, "_fetch_raw_markets", new=AsyncMock(return_value=raw)):
            events = asyncio.run(source.fetch_candidate_events(limit=10))
        self.assertEqual([e["source"]["source_id"] for e in events], ["ok"])

    def test_missing_optional_fields_default_safely(self):
        with patch.object(source, "_fetch_raw_markets", new=AsyncMock(return_value=[{
            "id": "m2", "question": "Q", "probability": 0.5,
            "outcomeType": "BINARY", "isResolved": False,
        }])):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        ev = events[0]
        self.assertEqual(ev["baseline_probability"], 50.0)
        self.assertEqual(ev["volume"], 0.0)
        self.assertEqual(ev["liquidity"], 0.0)

    def test_no_url_configured_returns_empty(self):
        # Empty URL is the off switch; _fetch_raw_markets short-circuits, no network.
        with patch.object(source.settings, "MANIFOLD_API_URL", ""):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events, [])

    def test_fetch_error_degrades_to_empty(self):
        with patch.object(source, "_fetch_raw_markets",
                          new=AsyncMock(side_effect=RuntimeError("network down"))), \
             self.assertLogs("app.services.manifold_event_source", level="WARNING") as logs:
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events, [])
        text = "\n".join(logs.output)
        self.assertIn("source=manifold_candidates", text)
        self.assertIn("policy=fail_closed_empty_list", text)

    def test_fetch_resolved_markets_maps_outcomes(self):
        raw = [
            {"id": "m-yes", "question": "Q yes", "isResolved": True, "resolution": "YES"},
            {"id": "m-no", "question": "Q no", "isResolved": True, "resolution": "NO"},
            {"id": "m-mkt", "question": "Q mkt", "isResolved": True, "resolution": "MKT",
             "resolutionProbability": 0.7},
            {"question": "Q cancel", "isResolved": True, "resolution": "CANCEL"},
            {"question": "Q open", "isResolved": False, "resolution": ""},
        ]
        with patch.object(source, "_fetch_raw_resolved",
                          new=AsyncMock(return_value=raw)):
            out = asyncio.run(source.fetch_resolved_markets(limit=10))
        self.assertEqual(out, [
            {"id": "m-yes", "question": "Q yes", "actual_outcome": 100.0},
            {"id": "m-no", "question": "Q no", "actual_outcome": 0.0},
            {"id": "m-mkt", "question": "Q mkt", "actual_outcome": 70.0},
        ])

    def test_fetch_resolved_error_degrades_to_empty(self):
        with patch.object(source, "_fetch_raw_resolved",
                          new=AsyncMock(side_effect=RuntimeError("boom"))), \
             self.assertLogs("app.services.manifold_event_source", level="WARNING") as logs:
            self.assertEqual(asyncio.run(source.fetch_resolved_markets()), [])
        text = "\n".join(logs.output)
        self.assertIn("source=manifold_resolved", text)
        self.assertIn("policy=fail_closed_empty_list", text)


if __name__ == "__main__":
    unittest.main()
