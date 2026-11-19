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
        """sync_world_cup_fixtures returns an error dict; pipeline stops.

        The row keeps two different things: a sentence this module owns
        (`World Cup fixture sync failed`) and a **code** for why
        (`configuration_missing`), taken from the producer and validated here.
        The sync layer's own text is *not* copied -- §53.106 did copy it and
        `test_scheduler_ledger_alarm_gate` went red because that text can carry
        a URL, a path or a token (§53.107).
        """
        sync_result = {
            "status": "error",
            "source": "api_football",
            "error": "API-Football configuration missing",
            "reason": "configuration_missing",
        }

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
        self.assertEqual(result["error"], "World Cup fixture sync failed")
        self.assertEqual(result["reason"], "configuration_missing")
        self.assertEqual(result["source"], "api_football")
        self.assertNotIn("API-Football configuration missing", repr(result))

    def test_fixture_sync_failure_without_a_message_still_says_something(self):
        """No `error` key upstream: the sentence stands and the code is unknown.

        A source that fails without a message must not produce `error: None` in
        the ledger -- three separate failures would read identically.
        """
        with patch(
            f"{MODULE}.sync_world_cup_fixtures",
            return_value={"status": "error"},
        ):
            result = asyncio.run(run_daily_prediction_update())

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "World Cup fixture sync failed")
        self.assertEqual(result["reason"], "sync_failed")
        self.assertNotIn("source", result)

    def test_a_credential_bearing_reason_is_rejected_not_copied(self):
        """Only codes from the declared vocabulary are accepted.

        The producer here is misbehaving on purpose -- it puts the upstream text
        in `reason` as well as in `error`. The scheduler must not pass it on: it
        is the last gate before a durable column, and "the producer said so" is
        how §53.106 got a URL and a bearer token into `loop_runs`.
        """
        leaky = (
            "HTTP 401 for https://v3.football.api-sports.io/fixtures?api_key=abc123 "
            "Authorization: Bearer sk-live-999 ticket=TCK-42"
        )
        with patch(
            f"{MODULE}.sync_world_cup_fixtures",
            return_value={"status": "error", "error": leaky, "reason": leaky},
        ):
            result = asyncio.run(run_daily_prediction_update())

        self.assertEqual(result["reason"], "sync_failed")
        exported = repr(result)
        for fragment in ("abc123", "sk-live-999", "TCK-42", "api-sports.io"):
            self.assertNotIn(fragment, exported)

    def test_fixture_sync_exception(self):
        """sync_world_cup_fixtures raises; the type survives, the message does not."""
        with patch(
            f"{MODULE}.sync_world_cup_fixtures",
            side_effect=RuntimeError("connection refused to 10.0.0.7:5432"),
        ):
            result = asyncio.run(run_daily_prediction_update())

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "World Cup daily update failed: RuntimeError")
        self.assertNotIn("10.0.0.7", repr(result))

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
        self.assertEqual(result["error"], "World Cup daily update failed: Exception")
        self.assertNotIn("backfill DB timeout", repr(result))

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
        self.assertEqual(result["error"], "World Cup daily update failed: RuntimeError")
        self.assertNotIn("LLM quota exceeded", repr(result))


class SummarizePredictionUpdateTests(unittest.TestCase):
    """The ledger summary is what an operator reads after the fact.

    `_summarize_prediction_update` is the only bridge between the orchestrator's
    return value and the `loop_runs.result_json` column, so a key dropped here is
    dropped from the record -- which is how §53.106 ended up reading the source
    code to answer "why did the daily update fail".

    Only closed-vocabulary keys are carried (`reason`, `source`), and only when
    the producer supplied them: a harness run has no upstream source and should
    not log `source: null`. Upstream error text is not carried at all.
    """

    def _summarize(self, result):
        from app.core.scheduler import _summarize_prediction_update

        return _summarize_prediction_update(result)

    def test_an_error_keeps_the_sentence_the_step_and_the_code(self):
        summary = self._summarize(
            {
                "status": "error",
                "step": "fixture_sync",
                "error": "World Cup fixture sync failed",
                "reason": "configuration_missing",
            }
        )
        self.assertEqual(summary["status"], "error")
        self.assertEqual(summary["error"], "World Cup fixture sync failed")
        self.assertEqual(summary["step"], "fixture_sync")
        self.assertEqual(summary["reason"], "configuration_missing")
        self.assertNotIn("source", summary)

    def test_an_error_names_the_source_when_one_was_reported(self):
        summary = self._summarize(
            {
                "status": "error",
                "step": "fixture_sync",
                "error": "World Cup fixture sync failed",
                "reason": "request_failed",
                "source": "football_data_org",
            }
        )
        self.assertEqual(summary["source"], "football_data_org")
        self.assertEqual(summary["reason"], "request_failed")

    def test_absent_codes_are_omitted_rather_than_nulled(self):
        summary = self._summarize({"status": "error", "step": None})
        self.assertNotIn("reason", summary)
        self.assertNotIn("source", summary)


class FixtureSyncReasonValidationTests(unittest.TestCase):
    """The scheduler validates the producer's code; it never derives one.

    Classification lives in `world_cup_match_service.fixture_sync_reason`, next to
    the sentences it reads (§53.108). This side accepts a value only if it is in
    the declared vocabulary -- so the vocabulary is the whole contract, and a
    producer that starts emitting a new code has to say so here, not by shipping
    whatever string it likes into a durable column.
    """

    def _validate(self, result):
        from app.services.world_cup_prediction_scheduler import _fixture_sync_reason

        return _fixture_sync_reason(result)

    def test_every_declared_code_is_accepted(self):
        from app.services.world_cup_prediction_scheduler import FIXTURE_SYNC_REASON_CODES

        self.assertGreater(len(FIXTURE_SYNC_REASON_CODES), 1)
        for code in FIXTURE_SYNC_REASON_CODES:
            self.assertEqual(self._validate({"reason": code}), code)

    def test_anything_else_collapses_to_the_unknown_code(self):
        from app.services.world_cup_prediction_scheduler import FIXTURE_SYNC_UNKNOWN

        for value in (
            None,
            "",
            "Configuration_Missing",
            " configuration_missing",
            123,
            ["configuration_missing"],
            "https://upstream.example/?api_key=abc",
        ):
            self.assertEqual(
                self._validate({"reason": value}), FIXTURE_SYNC_UNKNOWN, value
            )

    def test_a_missing_reason_is_not_an_error(self):
        from app.services.world_cup_prediction_scheduler import FIXTURE_SYNC_UNKNOWN

        self.assertEqual(self._validate({}), FIXTURE_SYNC_UNKNOWN)
        self.assertEqual(self._validate({"error": "whatever"}), FIXTURE_SYNC_UNKNOWN)


if __name__ == "__main__":
    unittest.main()
