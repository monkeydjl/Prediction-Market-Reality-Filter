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
        """Rule 4: scheduler succeeded N times but 0 new resolved predictions."""
        runs = [
            {"job_name": "event_auto_resolve", "status": "success"},
            {"job_name": "event_auto_resolve", "status": "success"},
            {"job_name": "event_auto_resolve", "status": "success"},
        ]
        with patch("app.services.drift_alert_dispatcher.settings") as s:
            s.DRIFT_ALERTS_ENABLED = True
            s.DRIFT_ALERT_WEBHOOK_URL = ""
            s.DRIFT_ALERT_COOLDOWN_SECONDS = 0
            s.DRIFT_SCHEDULER_ZERO_RESOLVED_RUNS = 3
            # The dispatcher imports loop_run_store + prediction_store
            # locally inside evaluate_scheduler_alerts (per the brief's
            # Step 5 verbatim), so they are NOT module-level attributes
            # on drift_alert_dispatcher — patching
            # ``app.services.drift_alert_dispatcher.loop_run_store`` raises
            # AttributeError. Patch the SOURCE modules instead, mirroring
            # the pattern in test_drift_gauge_wired_uses_store_not_placeholder
            # (which patches app.memory.prediction_store.<fn>).
            with patch("app.memory.loop_run_store.recent_runs", return_value=runs), \
                 patch("app.memory.prediction_store.list_scored_samples_for_drift",
                       return_value={"recent": [], "baseline": []}), \
                 patch("app.services.drift_alert_dispatcher.capture_message") as cap:
                alerts = drift_alert_dispatcher.evaluate_scheduler_alerts()
                codes = [a["code"] for a in alerts]
                self.assertIn("scheduler_zero_resolved", codes)


if __name__ == "__main__":
    unittest.main()
