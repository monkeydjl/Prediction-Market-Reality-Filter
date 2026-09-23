"""The scheduler must feed the drift alert dispatcher, not just the route.

`dispatch_drift_alerts` had exactly one caller: the `/quality-metrics/drift`
handler, and only on its authenticated branch (`can_dispatch` — a valid
X-API-Key). Nothing scheduled it. Detection on a poll is fine for the
dashboard; **dispatch** behind an authenticated manual request is not an alarm
channel at all — an operator has to already be looking, and to have exported
the write key into whatever does the looking.

The route's own docstring said "Operators can use an authenticated request
(X-API-Key) as the alert heartbeat" — a heartbeat you operate by hand. This is
the defect class the repo's own audit named first: a capability whose only
door is one nobody opens.

The job below runs the whole evaluation server-side, on the scheduler, daily:
rules 1-3 from the route body (samples -> build_drift_report ->
evaluate_drift_alerts) plus rule 4 (`evaluate_scheduler_alerts`), then
`dispatch_drift_alerts`. It writes a normal loop_runs ledger row so a failure
is visible through `/api/health` like every other job.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from app.core import scheduler
from app.services import drift_alert_dispatcher


class DriftAlertJobTests(unittest.TestCase):
    """_job_drift_alert_check: registration + behaviour."""

    def test_the_job_is_registered_daily(self):
        fake = MagicMock()
        with patch.object(scheduler, "scheduler", fake), \
                patch.object(scheduler.settings, "SCHEDULER_LOCK_ENABLED", False), \
                patch.object(scheduler.settings, "DRIFT_ALERTS_ENABLED", True):
            scheduler.start_scheduler()
        calls = [
            call for call in fake.add_job.call_args_list
            if call.kwargs.get("id") == "drift_alert_check"
        ]
        self.assertEqual(len(calls), 1, "drift_alert_check not registered exactly once")
        # add_job(func, trigger, ...): the registered callable must be the
        # job itself, not a lambda that no-ops.
        self.assertEqual(calls[0].args[0], scheduler._job_drift_alert_check)

    def test_the_job_is_not_registered_when_alerts_are_disabled(self):
        """The default. DRIFT_ALERTS_ENABLED gates the whole dispatch path
        (dispatch_drift_alerts no-ops without it), so a scheduled evaluation
        under it would compute and dispatch nothing, forever."""
        fake = MagicMock()
        with patch.object(scheduler, "scheduler", fake), \
                patch.object(scheduler.settings, "SCHEDULER_LOCK_ENABLED", False), \
                patch.object(scheduler.settings, "DRIFT_ALERTS_ENABLED", False):
            scheduler.start_scheduler()
        ids = {call.kwargs.get("id") for call in fake.add_job.call_args_list}
        self.assertNotIn("drift_alert_check", ids)

    def _run_job(self, **patched_dispatch):
        with patch.object(
            drift_alert_dispatcher, "dispatch_drift_alerts",
            **({"return_value": None, **patched_dispatch}),
        ) as dispatch_mock:
            asyncio.run(scheduler._job_drift_alert_check())
            return dispatch_mock

    def test_the_job_evaluates_rules_and_dispatches(self):
        """One dispatch call carrying rule 1-3 alerts plus rule 4 alerts —
        the same list the route would have dispatched."""
        with patch.object(
            drift_alert_dispatcher, "evaluate_scheduler_alerts",
            return_value=[{"code": "scheduler_zero_resolved"}],
        ), patch(
            "app.services.calibration_drift_service.evaluate_drift_alerts",
            return_value=[{"code": "brier_relative_drift"}],
        ), patch(
            "app.services.calibration_drift_service.build_drift_report",
            return_value={"drift": 0.5},
        ), patch(
            "app.memory.prediction_store.list_scored_samples_for_drift",
            return_value={"recent": [], "baseline": []},
        ), patch("app.memory.event_store.list_all_events", return_value=[]):
            dispatch_mock = self._run_job()
        codes = [
            alert.get("code")
            for call in dispatch_mock.call_args_list
            for alert in call.args[0]
        ]
        self.assertEqual(
            sorted(codes),
            ["brier_relative_drift", "scheduler_zero_resolved"],
        )

    def test_the_job_survives_a_dispatcher_failure_and_records_it(self):
        """Best-effort dispatch means the *job* still completes; the ledger
        row is where a failure shows. A raise would take the scheduler's
        error path and mark the whole job failed over an alert channel."""
        with patch.object(
            drift_alert_dispatcher, "evaluate_scheduler_alerts", return_value=[],
        ), patch(
            "app.services.calibration_drift_service.evaluate_drift_alerts",
            return_value=[],
        ), patch(
            "app.services.calibration_drift_service.build_drift_report",
            return_value={},
        ), patch(
            "app.memory.prediction_store.list_scored_samples_for_drift",
            return_value={"recent": [], "baseline": []},
        ), patch("app.memory.event_store.list_all_events", return_value=[]):
            dispatch_mock = self._run_job(side_effect=RuntimeError("webhook down"))
        dispatch_mock.assert_called_once()

    def test_the_job_writes_a_ledger_row(self):
        """A job whose failure nothing can see is the alarm-gate defect: the
        row is what /api/health and the timeline read."""
        import tempfile
        from pathlib import Path

        from app.memory import loop_run_store
        from app.utils import sqlite_db

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                sqlite_db, "loop_db_path",
                return_value=str(Path(tmp) / "v2_loop.db"),
            ), patch.object(
                drift_alert_dispatcher, "evaluate_scheduler_alerts",
                return_value=[],
            ), patch(
                "app.services.calibration_drift_service.evaluate_drift_alerts",
                return_value=[],
            ), patch(
                "app.services.calibration_drift_service.build_drift_report",
                return_value={},
            ), patch(
                "app.memory.prediction_store.list_scored_samples_for_drift",
                return_value={"recent": [], "baseline": []},
            ), patch("app.memory.event_store.list_all_events", return_value=[]):
                asyncio.run(scheduler._job_drift_alert_check())
                run = loop_run_store.last_run("drift_alert_check")
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["result"]["alerts_detected"], 0)

    def test_a_store_failure_fails_the_ledger_row(self):
        import tempfile
        from pathlib import Path

        from app.memory import loop_run_store
        from app.utils import sqlite_db

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                sqlite_db, "loop_db_path",
                return_value=str(Path(tmp) / "v2_loop.db"),
            ), patch(
                "app.services.calibration_drift_service.build_drift_report",
                side_effect=RuntimeError("samples unreadable"),
            ):
                asyncio.run(scheduler._job_drift_alert_check())
                run = loop_run_store.last_run("drift_alert_check")
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error"], "Scheduler job failed: RuntimeError")

    def test_the_evaluation_reads_real_samples(self):
        """The samples read must feed the evaluation, not be stubbed away.

        The other behaviour tests patch `list_scored_samples_for_drift`, so a
        job that never calls it — evaluating a hardcoded empty window, with
        rule 4 alone deciding everything — passes them all. This one supplies
        real sample dicts and requires the corresponding rule-1 alert to come
        out the other side.
        """
        baseline = [
            {"predicted_prob": 0.5, "actual_outcome": 1,
             "brier_score": 0.25, "event_id": "b1"},
            {"predicted_prob": 0.5, "actual_outcome": 0,
             "brier_score": 0.25, "event_id": "b2"},
        ]
        recent = [
            {"predicted_prob": 0.9, "actual_outcome": 0,
             "brier_score": 0.81, "event_id": "r1"},
            {"predicted_prob": 0.9, "actual_outcome": 0,
             "brier_score": 0.81, "event_id": "r2"},
            {"predicted_prob": 0.9, "actual_outcome": 0,
             "brier_score": 0.81, "event_id": "r3"},
        ]
        with patch.object(
            drift_alert_dispatcher, "evaluate_scheduler_alerts", return_value=[],
        ), patch(
            "app.memory.prediction_store.list_scored_samples_for_drift",
            return_value={"recent": recent, "baseline": baseline},
        ), patch("app.memory.event_store.list_all_events", return_value=[]):
            dispatch_mock = self._run_job()
        codes = [
            alert.get("code")
            for call in dispatch_mock.call_args_list
            for alert in call.args[0]
        ]
        self.assertIn("brier_relative_drift", codes)


if __name__ == "__main__":
    unittest.main()
