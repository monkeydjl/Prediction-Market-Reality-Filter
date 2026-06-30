"""Tests for drift_alert_dispatcher (Plan 2 §1.7 alert dispatch).

The dispatcher is best-effort: webhook/Sentry failures never raise. It is
gated by ``DRIFT_ALERTS_ENABLED`` (default false) — when off, dispatch is a
no-op. Tests patch settings + HTTP + Sentry to verify both branches.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from app.services import drift_alert_dispatcher


class TestDispatchDriftAlerts(unittest.TestCase):
    def _alert(self):
        return {
            "code": "brier_relative_drift",
            "severity": "high",
            "detail": {"drift_score": 0.5, "note": "recent worse"},
        }

    def test_disabled_flag_is_noop(self):
        with patch("app.services.drift_alert_dispatcher.settings") as s:
            s.DRIFT_ALERTS_ENABLED = False
            s.DRIFT_ALERT_WEBHOOK_URL = "http://example.com/hook"
            with patch("app.services.drift_alert_dispatcher._post_webhook") as wh, \
                 patch("app.services.drift_alert_dispatcher.capture_message") as cap:
                drift_alert_dispatcher.dispatch_drift_alerts([self._alert()])
                wh.assert_not_called()
                cap.assert_not_called()

    def test_enabled_dispatches_webhook_and_sentry(self):
        with patch("app.services.drift_alert_dispatcher.settings") as s:
            s.DRIFT_ALERTS_ENABLED = True
            s.DRIFT_ALERT_WEBHOOK_URL = "http://example.com/hook"
            s.DRIFT_ALERT_COOLDOWN_SECONDS = 0  # disable cooldown for test
            with patch("app.services.drift_alert_dispatcher._post_webhook") as wh, \
                 patch("app.services.drift_alert_dispatcher.capture_message") as cap:
                drift_alert_dispatcher.dispatch_drift_alerts([self._alert()])
                wh.assert_called_once()
                cap.assert_called_once()

    def test_no_webhook_url_skips_webhook_not_sentry(self):
        with patch("app.services.drift_alert_dispatcher.settings") as s:
            s.DRIFT_ALERTS_ENABLED = True
            s.DRIFT_ALERT_WEBHOOK_URL = ""
            s.DRIFT_ALERT_COOLDOWN_SECONDS = 0
            with patch("app.services.drift_alert_dispatcher._post_webhook") as wh, \
                 patch("app.services.drift_alert_dispatcher.capture_message") as cap:
                drift_alert_dispatcher.dispatch_drift_alerts([self._alert()])
                wh.assert_not_called()
                cap.assert_called_once()

    def test_webhook_failure_does_not_raise(self):
        with patch("app.services.drift_alert_dispatcher.settings") as s:
            s.DRIFT_ALERTS_ENABLED = True
            s.DRIFT_ALERT_WEBHOOK_URL = "http://example.com/hook"
            s.DRIFT_ALERT_COOLDOWN_SECONDS = 0
            with patch("app.services.drift_alert_dispatcher._post_webhook",
                       side_effect=Exception("network down")), \
                 patch("app.services.drift_alert_dispatcher.capture_message"):
                # Must not raise
                drift_alert_dispatcher.dispatch_drift_alerts([self._alert()])

    def test_cooldown_deduplicates_repeated_alerts(self):
        with patch("app.services.drift_alert_dispatcher.settings") as s:
            s.DRIFT_ALERTS_ENABLED = True
            s.DRIFT_ALERT_WEBHOOK_URL = ""
            s.DRIFT_ALERT_COOLDOWN_SECONDS = 3600
            with patch("app.services.drift_alert_dispatcher.capture_message") as cap:
                drift_alert_dispatcher.dispatch_drift_alerts([self._alert()])
                drift_alert_dispatcher.dispatch_drift_alerts([self._alert()])  # same code, deduped
                # First dispatch fires; second is within cooldown → skipped
                self.assertEqual(cap.call_count, 1)
            # Reset cooldown state for other tests
            drift_alert_dispatcher._reset_cooldown_state()

    def test_rule4_scheduler_zero_resolved_alert(self):
        """Rule 4: scheduler succeeded N times but 0 new resolved predictions.

        Detection reads each run's ``result.resolved_count`` (stored by
        ``event_auto_resolve`` in ``loop_runs.result_json``). When the
        sum across the N recent successful runs is 0, the pipeline is
        stuck and the alert fires.
        """
        runs = [
            {"job_name": "event_auto_resolve", "status": "success",
             "result": {"resolved_count": 0}},
            {"job_name": "event_auto_resolve", "status": "success",
             "result": {"resolved_count": 0}},
            {"job_name": "event_auto_resolve", "status": "success",
             "result": {"resolved_count": 0}},
        ]
        with patch("app.services.drift_alert_dispatcher.settings") as s:
            s.DRIFT_ALERTS_ENABLED = True
            s.DRIFT_ALERT_WEBHOOK_URL = ""
            s.DRIFT_ALERT_COOLDOWN_SECONDS = 0
            s.DRIFT_SCHEDULER_ZERO_RESOLVED_RUNS = 3
            # loop_run_store is imported locally inside
            # evaluate_scheduler_alerts, so patch the SOURCE module —
            # patching ``app.services.drift_alert_dispatcher.loop_run_store``
            # raises AttributeError.
            with patch("app.memory.loop_run_store.recent_runs", return_value=runs), \
                 patch("app.services.drift_alert_dispatcher.capture_message") as cap:
                alerts = drift_alert_dispatcher.evaluate_scheduler_alerts()
                codes = [a["code"] for a in alerts]
                self.assertIn("scheduler_zero_resolved", codes)

    def test_rule4_no_alert_when_runs_have_resolved_count_positive(self):
        """Rule 4 does NOT fire when the recent runs resolved >0 events.

        Previously the detection read the drift recent-window sample
        count (always >0 once any prediction is scored), so the alert
        never fired. Now it reads run.result.resolved_count — a positive
        sum means the pipeline is making progress.
        """
        runs = [
            {"job_name": "event_auto_resolve", "status": "success",
             "result": {"resolved_count": 5}},
            {"job_name": "event_auto_resolve", "status": "success",
             "result": {"resolved_count": 3}},
            {"job_name": "event_auto_resolve", "status": "success",
             "result": {"resolved_count": 0}},
        ]
        with patch("app.services.drift_alert_dispatcher.settings") as s:
            s.DRIFT_ALERTS_ENABLED = True
            s.DRIFT_SCHEDULER_ZERO_RESOLVED_RUNS = 3
            with patch("app.memory.loop_run_store.recent_runs", return_value=runs):
                alerts = drift_alert_dispatcher.evaluate_scheduler_alerts()
                codes = [a["code"] for a in alerts]
                self.assertNotIn("scheduler_zero_resolved", codes)

    def test_rule4_detection_runs_when_dispatch_disabled(self):
        """Rule 4 detection is NOT gated by DRIFT_ALERTS_ENABLED.

        Only ``dispatch_drift_alerts()`` is gated — detection always
        runs so the ``alerts`` list returned by the drift route stays
        consistent with rules 1-3 (which are pure functions and never
        gated by the flag).
        """
        runs = [
            {"job_name": "event_auto_resolve", "status": "success",
             "result": {"resolved_count": 0}},
        ] * 3
        with patch("app.services.drift_alert_dispatcher.settings") as s:
            s.DRIFT_ALERTS_ENABLED = False  # dispatch off, detection still runs
            s.DRIFT_SCHEDULER_ZERO_RESOLVED_RUNS = 3
            with patch("app.memory.loop_run_store.recent_runs", return_value=runs):
                alerts = drift_alert_dispatcher.evaluate_scheduler_alerts()
                codes = [a["code"] for a in alerts]
                self.assertIn("scheduler_zero_resolved", codes)


if __name__ == "__main__":
    unittest.main()
