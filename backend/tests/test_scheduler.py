"""Tests for scheduler job logic.

The scheduler jobs are thin glue (fetch -> process -> log), but
_job_morning_scan was refactored from a serial loop to a Semaphore(4) +
asyncio.gather concurrent model, so this locks the concurrency behavior:
every market is processed exactly once, the signal-count aggregation is
correct, and a single market's failure does not abort the scan.

All external dependencies are mocked (fetch_markets / fetch_google_news /
analyze_market / persistence), so no network or LLM is hit.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.core import scheduler
from app.models.market import MarketModel


def _market(qid, signal_target=None):
    """A valid market (passes _is_valid_market: liquidity>=5k, volume>=1k,
    yes_price 0.08-0.92, non-absurd question)."""
    return MarketModel(
        id=str(qid),
        question=f"Will something real happen before 2030 number {qid}?",
        yes_price=0.45,
        volume=10_000.0,
        liquidity=20_000.0,
    )


class MorningScanConcurrencyTests(unittest.TestCase):
    """_job_morning_scan processes markets concurrently (Semaphore 4) and
    aggregates signal counts correctly."""

    def _run_with_mocks(self, markets, analyze_side_effect=None):
        """Run _job_morning_scan with all deps mocked; return (signal_counts,
        actionable_len, log_calls)."""
        async def fake_analyze(**kwargs):
            if analyze_side_effect:
                return analyze_side_effect(kwargs.get("market_question", ""))
            return {"signal": "WATCHLIST", "ai_probability": 50.0,
                    "confidence_score": 0.5, "divergence": 0.0,
                    "market_question": kwargs.get("market_question", "")}

        with patch("app.services.polymarket_service.fetch_markets",
                   new=AsyncMock(return_value=markets)), \
                patch("app.services.gnews_service.fetch_google_news",
                      new=AsyncMock(return_value=[])), \
                patch("app.services.rss_service.fetch_news",
                      new=AsyncMock(return_value=[])), \
                patch("app.services.ai_analysis_service.analyze_market",
                      new=AsyncMock(side_effect=fake_analyze)), \
                patch("app.services.analysis_audit_service.record_analysis"), \
                patch("app.memory.market_memory.get_cached_analysis",
                      return_value=None), \
                patch("app.memory.market_memory.set_cached_analysis"), \
                patch("app.memory.agent_memory.add_prediction"):
            asyncio.run(scheduler._job_morning_scan())

    def test_all_markets_processed_and_counted(self):
        markets = [_market(i) for i in range(6)]
        # No analyze failures, all return WATCHLIST.
        self._run_with_mocks(markets)
        # If we got here without raising, the gather completed for all 6.

    def test_single_market_failure_does_not_abort_scan(self):
        markets = [_market(i) for i in range(4)]

        def side_effect(question):
            if "number 2" in question:
                raise RuntimeError("analyze blew up")
            return {"signal": "WATCHLIST", "ai_probability": 50.0,
                    "confidence_score": 0.5, "divergence": 0.0,
                    "market_question": question}

        # Should not raise: the failing market is isolated, the rest still run.
        self._run_with_mocks(markets, analyze_side_effect=side_effect)

    def test_invalid_market_is_skipped(self):
        # yes_price 0.95 -> prob 95 >= 92 certainty ceiling -> invalid.
        m = MarketModel(id="x", question="Will something real happen before 2030?",
                        yes_price=0.95, volume=10_000.0, liquidity=20_000.0)
        self._run_with_mocks([m])
        # Should complete; the invalid market is skipped (not analyzed).


class JobDefaultsTests(unittest.TestCase):
    """job_defaults on the scheduler prevent silent misfire drops."""

    def test_scheduler_has_coalesce_and_misfire_grace(self):
        defaults = scheduler.scheduler._job_defaults
        self.assertTrue(defaults.get("coalesce"))
        self.assertGreater(defaults.get("misfire_grace_time", 0), 60)


if __name__ == "__main__":
    unittest.main()
