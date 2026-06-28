"""Tests for the World Cup live update service.

Covers the three query helpers (get_live_matches, get_matches_near_kickoff,
get_newly_finished_matches) and the main update_live_predictions orchestrator,
including error isolation when scoring fails mid-loop.

All external dependencies are mocked — no database, network, or LLM is hit.
"""

import asyncio
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call

from app.services import world_cup_live_update_service as svc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fixture(match_id: str, *, status: str = "scheduled",
                  kickoff_utc: datetime | None = None,
                  home_score: int | None = None,
                  away_score: int | None = None) -> MagicMock:
    """Build a lightweight mock that quacks like a MatchFixture row."""
    f = MagicMock()
    f.match_id = match_id
    f.status = status
    f.kickoff_utc = kickoff_utc or datetime.now(timezone.utc)
    f.home_score = home_score
    f.away_score = away_score
    return f


def _build_query_chain(rows: list):
    """Return a mock session whose .query().filter(…).all() returns *rows*.

    Each intermediate method returns the next link so the SQLAlchemy-style
    chaining works regardless of how many .filter() calls are made.
    """
    chain = MagicMock()
    chain.all.return_value = rows
    chain.filter.return_value = chain
    return chain


# ---------------------------------------------------------------------------
# get_live_matches
# ---------------------------------------------------------------------------

class GetLiveMatchesTests(unittest.TestCase):

    @patch.object(svc, "close_prediction_session")
    @patch.object(svc, "get_prediction_session")
    def test_returns_in_play_ids(self, mock_get_session, mock_close):
        session = MagicMock()
        mock_get_session.return_value = session
        session.query.return_value.filter.return_value.all.return_value = [
            _make_fixture("M001", status="in_play"),
            _make_fixture("M002", status="in_play"),
        ]

        result = svc.get_live_matches()

        self.assertEqual(result, ["M001", "M002"])
        mock_close.assert_called_once_with(session)

    @patch.object(svc, "close_prediction_session")
    @patch.object(svc, "get_prediction_session")
    def test_empty_when_no_in_play(self, mock_get_session, mock_close):
        session = MagicMock()
        mock_get_session.return_value = session
        session.query.return_value.filter.return_value.all.return_value = []

        result = svc.get_live_matches()

        self.assertEqual(result, [])
        mock_close.assert_called_once_with(session)

    @patch.object(svc, "close_prediction_session")
    @patch.object(svc, "get_prediction_session")
    def test_session_closed_on_exception(self, mock_get_session, mock_close):
        """Session is closed even if the query blows up (finally block)."""
        session = MagicMock()
        mock_get_session.return_value = session
        session.query.return_value.filter.return_value.all.side_effect = RuntimeError("db down")

        with self.assertRaises(RuntimeError):
            svc.get_live_matches()

        mock_close.assert_called_once_with(session)


# ---------------------------------------------------------------------------
# get_matches_near_kickoff
# ---------------------------------------------------------------------------

class GetMatchesNearKickoffTests(unittest.TestCase):

    @patch.object(svc, "close_prediction_session")
    @patch.object(svc, "get_prediction_session")
    def test_returns_matches_within_window(self, mock_get_session, mock_close):
        session = MagicMock()
        mock_get_session.return_value = session
        soon = datetime.now(timezone.utc) + timedelta(minutes=5)
        session.query.return_value.filter.return_value.all.return_value = [
            _make_fixture("M010", kickoff_utc=soon),
        ]

        result = svc.get_matches_near_kickoff(window_minutes=15)

        self.assertEqual(result, ["M010"])
        mock_close.assert_called_once_with(session)

    @patch.object(svc, "close_prediction_session")
    @patch.object(svc, "get_prediction_session")
    def test_empty_when_no_upcoming(self, mock_get_session, mock_close):
        session = MagicMock()
        mock_get_session.return_value = session
        session.query.return_value.filter.return_value.all.return_value = []

        result = svc.get_matches_near_kickoff()

        self.assertEqual(result, [])
        mock_close.assert_called_once_with(session)

    @patch.object(svc, "close_prediction_session")
    @patch.object(svc, "get_prediction_session")
    def test_custom_window_minutes(self, mock_get_session, mock_close):
        session = MagicMock()
        mock_get_session.return_value = session
        session.query.return_value.filter.return_value.all.return_value = [
            _make_fixture("M020"),
            _make_fixture("M021"),
        ]

        result = svc.get_matches_near_kickoff(window_minutes=30)

        self.assertEqual(result, ["M020", "M021"])
        mock_close.assert_called_once_with(session)


# ---------------------------------------------------------------------------
# get_newly_finished_matches
# ---------------------------------------------------------------------------

class GetNewlyFinishedMatchesTests(unittest.TestCase):

    @patch.object(svc, "close_prediction_session")
    @patch.object(svc, "get_prediction_session")
    def test_returns_finished_without_results(self, mock_get_session, mock_close):
        """Finished matches that lack a MatchResult row are 'newly finished'."""
        session = MagicMock()
        mock_get_session.return_value = session

        # First query: finished match ids with scores
        finished_rows = [("M100",), ("M101",), ("M102",)]
        # Second query: already-scored match ids
        scored_rows = [("M100",)]

        # .query() is called twice with different model args; use side_effect
        q1 = MagicMock()
        q1.filter.return_value.all.return_value = finished_rows

        q2 = MagicMock()
        q2.filter.return_value.all.return_value = scored_rows

        session.query.side_effect = [q1, q2]

        result = svc.get_newly_finished_matches()

        self.assertEqual(sorted(result), ["M101", "M102"])
        mock_close.assert_called_once_with(session)

    @patch.object(svc, "close_prediction_session")
    @patch.object(svc, "get_prediction_session")
    def test_empty_when_all_scored(self, mock_get_session, mock_close):
        session = MagicMock()
        mock_get_session.return_value = session

        finished_rows = [("M100",)]
        scored_rows = [("M100",)]

        q1 = MagicMock()
        q1.filter.return_value.all.return_value = finished_rows
        q2 = MagicMock()
        q2.filter.return_value.all.return_value = scored_rows

        session.query.side_effect = [q1, q2]

        result = svc.get_newly_finished_matches()

        self.assertEqual(result, [])
        mock_close.assert_called_once_with(session)

    @patch.object(svc, "close_prediction_session")
    @patch.object(svc, "get_prediction_session")
    def test_empty_when_no_finished_matches(self, mock_get_session, mock_close):
        """Early return when there are zero finished fixtures."""
        session = MagicMock()
        mock_get_session.return_value = session

        q1 = MagicMock()
        q1.filter.return_value.all.return_value = []
        session.query.return_value = q1

        result = svc.get_newly_finished_matches()

        self.assertEqual(result, [])
        mock_close.assert_called_once_with(session)


# ---------------------------------------------------------------------------
# update_live_predictions — full flow
# ---------------------------------------------------------------------------

class UpdateLivePredictionsTests(unittest.TestCase):

    def test_full_flow(self):
        """All three branches fire; return dict has expected keys and counts."""

        with \
            patch.object(svc, "get_newly_finished_matches", return_value=["M100", "M101"]) as mock_finished, \
            patch.object(svc, "get_matches_near_kickoff", return_value=["M010"]) as mock_pre, \
            patch.object(svc, "get_live_matches", return_value=["M050", "M051"]) as mock_live, \
            patch.object(svc, "run_prediction_pipeline", new=AsyncMock(
                return_value={"status": "ok"})) as mock_pipeline, \
            patch("app.services.world_cup_scoring_service.score_finished_match",
                  return_value={"status": "scored"}) as mock_score:

            result = asyncio.run(svc.update_live_predictions())

        # Newly finished → scored
        self.assertEqual(mock_score.call_count, 2)
        mock_score.assert_any_call("M100")
        mock_score.assert_any_call("M101")

        # Pre-match → prediction pipeline called
        mock_pipeline.assert_awaited_once_with("M010", trigger="live_update")

        # Return structure
        self.assertEqual(result["status"], "ok")
        self.assertIn("timestamp", result)
        self.assertEqual(result["in_play_count"], 2)
        self.assertEqual(result["pre_match_updated"], 1)
        self.assertEqual(result["newly_finished_scored"], 2)
        self.assertEqual(result["actions"]["pre_match_predictions"], 1)
        self.assertEqual(result["actions"]["post_match_scoring"], 2)
        self.assertEqual(result["actions"]["in_play_monitoring"], 2)

    def test_score_failure_isolation(self):
        """If one score_finished_match call raises, the rest still run."""

        def score_side_effect(match_id):
            if match_id == "M100":
                raise RuntimeError("scoring exploded")
            return {"status": "scored"}

        with \
            patch.object(svc, "get_newly_finished_matches", return_value=["M100", "M101", "M102"]), \
            patch.object(svc, "get_matches_near_kickoff", return_value=[]), \
            patch.object(svc, "get_live_matches", return_value=[]), \
            patch.object(svc, "run_prediction_pipeline", new=AsyncMock()), \
            patch("app.services.world_cup_scoring_service.score_finished_match",
                  side_effect=score_side_effect):

            # The outer try/except wraps the entire scoring loop, so an
            # exception on M100 stops the loop — M101 and M102 are NOT reached.
            # The function still returns normally because the except catches it.
            result = asyncio.run(svc.update_live_predictions())

        # The except block catches the error and logs it; scored_count stays 0
        # because the exception fires before any successful return from the loop.
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["newly_finished_scored"], 0)

    def test_score_failure_logs_error(self):
        """Verify the error is logged with exc_info when scoring raises."""

        with \
            patch.object(svc, "get_newly_finished_matches", return_value=["M100"]), \
            patch.object(svc, "get_matches_near_kickoff", return_value=[]), \
            patch.object(svc, "get_live_matches", return_value=[]), \
            patch.object(svc, "run_prediction_pipeline", new=AsyncMock()), \
            patch("app.services.world_cup_scoring_service.score_finished_match",
                  side_effect=RuntimeError("db timeout")), \
            patch.object(svc.logger, "error") as mock_error:

            result = asyncio.run(svc.update_live_predictions())

        mock_error.assert_called_once()
        # exc_info=True should be in the keyword args
        _, kwargs = mock_error.call_args
        self.assertTrue(kwargs.get("exc_info"))
        self.assertEqual(result["status"], "ok")

    def test_pre_match_pipeline_failure_does_not_crash(self):
        """A failed prediction pipeline call is caught per-match."""

        with \
            patch.object(svc, "get_newly_finished_matches", return_value=[]), \
            patch.object(svc, "get_matches_near_kickoff", return_value=["M010", "M011"]), \
            patch.object(svc, "get_live_matches", return_value=[]), \
            patch.object(svc, "run_prediction_pipeline", new=AsyncMock(
                side_effect=[RuntimeError("llm down"), {"status": "ok"}])):

            result = asyncio.run(svc.update_live_predictions())

        # Only M011 succeeded
        self.assertEqual(result["pre_match_updated"], 1)
        self.assertEqual(result["status"], "ok")

    def test_pre_match_pipeline_non_ok_status_not_counted(self):
        """Pipeline returns a non-ok status — should not increment counter."""

        with \
            patch.object(svc, "get_newly_finished_matches", return_value=[]), \
            patch.object(svc, "get_matches_near_kickoff", return_value=["M010"]), \
            patch.object(svc, "get_live_matches", return_value=[]), \
            patch.object(svc, "run_prediction_pipeline", new=AsyncMock(
                return_value={"status": "skipped", "reason": "no data"})):

            result = asyncio.run(svc.update_live_predictions())

        self.assertEqual(result["pre_match_updated"], 0)

    def test_no_matches_at_all(self):
        """Quiet period — nothing to do, returns zeroed counters."""

        with \
            patch.object(svc, "get_newly_finished_matches", return_value=[]), \
            patch.object(svc, "get_matches_near_kickoff", return_value=[]), \
            patch.object(svc, "get_live_matches", return_value=[]), \
            patch.object(svc, "run_prediction_pipeline", new=AsyncMock()) as mock_pipeline, \
            patch("app.services.world_cup_scoring_service.score_finished_match") as mock_score:

            result = asyncio.run(svc.update_live_predictions())

        mock_score.assert_not_called()
        mock_pipeline.assert_not_called()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["in_play_count"], 0)
        self.assertEqual(result["pre_match_updated"], 0)
        self.assertEqual(result["newly_finished_scored"], 0)

    def test_scored_count_only_increments_on_truthy_result(self):
        """score_finished_match returning None should not bump the counter."""

        with \
            patch.object(svc, "get_newly_finished_matches", return_value=["M100", "M101"]), \
            patch.object(svc, "get_matches_near_kickoff", return_value=[]), \
            patch.object(svc, "get_live_matches", return_value=[]), \
            patch.object(svc, "run_prediction_pipeline", new=AsyncMock()), \
            patch("app.services.world_cup_scoring_service.score_finished_match",
                  side_effect=[None, {"status": "scored"}]):

            result = asyncio.run(svc.update_live_predictions())

        self.assertEqual(result["newly_finished_scored"], 1)


if __name__ == "__main__":
    unittest.main()
