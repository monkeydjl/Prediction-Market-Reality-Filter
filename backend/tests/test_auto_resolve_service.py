"""Tests for the market-layer auto_resolve_service.

Regression protection after the refactor that extracted matching into
app.utils.text_match. These lock the end-to-end run_auto_resolve workflow
(network + memory mocked) so the refactor stays behavior-preserving.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.memory import agent_memory as mem
from app.services import auto_resolve_service as ars


class RunAutoResolveTests(unittest.TestCase):
    def _prediction(self, question):
        return {
            "timestamp": "2026-06-14T00:00:00+00:00",
            "market_question": question,
            "market_probability": 50.0,
            "final_probability": 60.0,
            "divergence": 10.0,
            "resolved": False,
            "actual_outcome": None,
            "agents": [],
        }

    def test_matches_and_resolves_prediction(self):
        resolved_market = {
            "question": "Will Bitcoin reach $100,000 by end of 2026?",
            "actual_outcome": 100.0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            mem_path = str(Path(tmp) / "agent_memory.json")
            with patch.object(mem, "_memory_path", return_value=mem_path), \
                    patch.object(ars, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[resolved_market])):
                mem.save_memory([self._prediction(
                    "Will Bitcoin reach $100,000 by end of 2026?"
                )])
                result = asyncio.run(ars.run_auto_resolve(resolved_limit=50))
                after = mem.load_memory()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["resolved_count"], 1)
        self.assertTrue(after[0]["resolved"])
        self.assertEqual(after[0]["actual_outcome"], 100.0)

    def test_no_resolved_markets_returns_no_data(self):
        with patch.object(ars, "fetch_resolved_markets",
                          new=AsyncMock(return_value=[])):
            result = asyncio.run(ars.run_auto_resolve(resolved_limit=50))
        self.assertEqual(result["status"], "no_resolved_markets")
        self.assertEqual(result["resolved_count"], 0)

    def test_no_unresolved_predictions_short_circuits(self):
        resolved_market = {"question": "anything", "actual_outcome": 100.0}
        with tempfile.TemporaryDirectory() as tmp:
            mem_path = str(Path(tmp) / "agent_memory.json")
            with patch.object(mem, "_memory_path", return_value=mem_path), \
                    patch.object(ars, "fetch_resolved_markets",
                                 new=AsyncMock(return_value=[resolved_market])):
                # No predictions at all.
                mem.save_memory([])
                result = asyncio.run(ars.run_auto_resolve(resolved_limit=50))
        self.assertEqual(result["status"], "no_unresolved_predictions")
        self.assertEqual(result["resolved_count"], 0)


if __name__ == "__main__":
    unittest.main()
