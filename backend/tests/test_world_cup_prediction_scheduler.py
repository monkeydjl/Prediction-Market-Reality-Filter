"""Tests for the World Cup daily prediction update scheduler function.

run_daily_prediction_update is a thin async orchestrator that:
1. Syncs fixtures from API-Football (sync call via asyncio.to_thread)
2. Runs post-match backfill scoring (sync call via asyncio.to_thread)
3. Runs batch predictions for all remaining matches (async call)

All external dependencies are mocked so no network, DB, or LLM is hit.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services.world_cup_prediction_scheduler import run_daily_prediction_update

MODULE = "app.services.world_cup_prediction_scheduler"


class RunDailyPredictionUpdateTests(unittest.TestCase):
    """run_daily_prediction_update orchestrates fixture sync, backfill, and prediction."""

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------
    def test_success_path(self):
        """All 3 steps succeed; final result carries every sub-result."""
        sync_result = {"status": "ok", "fixtures_parsed": 5}
        backfill_result = {"candidate_count": 2, "scoring": {"scored": 1}}
        predict_result = {"total": 10, "succeeded": 9}

        with patch(
            f"{MODULE}.sync_world_cup_fixtures",
            return_value=sync_result,
        ), patch(
            f"{MODULE}.run_post_match_backfill",
            return_value=backfill_result,
        ), patch(
            f"{MODULE}.batch_predict_matches",
            new=AsyncMock(return_value=predict_result),
        ):
            result = asyncio.run(run_daily_prediction_update())

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["fixture_sync"], sync_result)
        self.assertEqual(result["post_match_backfill"], backfill_result)
        self.assertEqual(result["predictions"], predict_result)
        self.assertIn("timestamp", result)

    # ------------------------------------------------------------------
    # Step 1 - fixture sync failures
    # ------------------------------------------------------------------
    def test_fixture_sync_failure_early_return(self):
        """sync_world_cup_fixtures returns an error dict; pipeline stops."""
        sync_result = {"status": "error", "error": "API down"}

        with patch(
            f"{MODULE}.sync_world_cup_fixtures",
            return_value=sync_result,
        ), patch(
            f"{MODULE}.run_post_match_backfill",
        ) as mock_backfill, patch(
            f"{MODULE}.batch_predict_matches",
            new=AsyncMock(),
        ) as mock_predict:
            result = asyncio.run(run_daily_prediction_update())

        mock_backfill.assert_not_called()
        mock_predict.assert_not_called()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["step"], "fixture_sync")
        self.assertEqual(result["error"], "API down")

    def test_fixture_sync_exception(self):
        """sync_world_cup_fixtures raises; caught by outer try/except."""
        with patch(
            f"{MODULE}.sync_world_cup_fixtures",
            side_effect=RuntimeError("connection refused"),
        ):
            result = asyncio.run(run_daily_prediction_update())

        self.assertEqual(result["status"], "error")
        self.assertIn("connection refused", result["error"])

    # ------------------------------------------------------------------
    # Step 2 - backfill failures
    # ------------------------------------------------------------------
    def test_backfill_failure_error_return(self):
        """run_post_match_backfill raises; caught by outer try/except."""
        sync_result = {"status": "ok", "fixtures_parsed": 3}

        with patch(
            f"{MODULE}.sync_world_cup_fixtures",
            return_value=sync_result,
        ), patch(
            f"{MODULE}.run_post_match_backfill",
            side_effect=Exception("backfill DB timeout"),
        ), patch(
            f"{MODULE}.batch_predict_matches",
            new=AsyncMock(),
        ) as mock_predict:
            result = asyncio.run(run_daily_prediction_update())

        mock_predict.assert_not_called()
        self.assertEqual(result["status"], "error")
        self.assertIn("backfill DB timeout", result["error"])

    # ------------------------------------------------------------------
    # Step 3 - prediction failures
    # ------------------------------------------------------------------
    def test_batch_predict_failure_included(self):
        """batch_predict_matches raises; caught by outer try/except."""
        sync_result = {"status": "ok", "fixtures_parsed": 2}
        backfill_result = {"candidate_count": 0, "scoring": {"scored": 0}}

        with patch(
            f"{MODULE}.sync_world_cup_fixtures",
            return_value=sync_result,
        ), patch(
            f"{MODULE}.run_post_match_backfill",
            return_value=backfill_result,
        ), patch(
            f"{MODULE}.batch_predict_matches",
            new=AsyncMock(side_effect=RuntimeError("LLM quota exceeded")),
        ):
            result = asyncio.run(run_daily_prediction_update())

        self.assertEqual(result["status"], "error")
        self.assertIn("LLM quota exceeded", result["error"])


if __name__ == "__main__":
    unittest.main()
