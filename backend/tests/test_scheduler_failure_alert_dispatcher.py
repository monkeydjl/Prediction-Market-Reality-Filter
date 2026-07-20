"""Tests for scheduler_failure_alert_dispatcher (E8).

The dispatcher is best-effort: webhook/Sentry failures never raise. It
is gated by ``SCHEDULER_FAILURE_ALERT_ENABLED`` (default false) — when
off, dispatch is a no-op. Tests patch settings + HTTP + Sentry to
verify both branches.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from app.services import scheduler_failure_alert_dispatcher


class TestDispatchSchedulerFailureAlert(unittest.TestCase):
    def _exc(self) -> ValueError:
        try:
            raise ValueError("boom")
        except ValueError as exc:
            return exc

    def test_disabled_flag_is_noop(self):
        with patch("app.services.scheduler_failure_alert_dispatcher.settings") as s:
            s.SCHEDULER_FAILURE_ALERT_ENABLED = False
            s.SCHEDULER_FAILURE_ALERT_WEBHOOK_URL = "http://example.com/hook"
            s.SCHEDULER_FAILURE_ALERT_COOLDOWN_SECONDS = 0
            with patch("app.services.scheduler_failure_alert_dispatcher._post_webhook") as wh, \
                 patch("app.services.scheduler_failure_alert_dispatcher.capture_message",
                       create=True) as cap:
                scheduler_failure_alert_dispatcher.dispatch_scheduler_failure_alert(
                    job_name="event_discover",
                    run_id="r1",
                    error="boom",
                    exc=self._exc(),
                )
                wh.assert_not_called()
                cap.assert_not_called()

    def test_enabled_dispatches_webhook_and_sentry(self):
        scheduler_failure_alert_dispatcher._reset_cooldown_state()
        with patch("app.services.scheduler_failure_alert_dispatcher.settings") as s:
            s.SCHEDULER_FAILURE_ALERT_ENABLED = True
            s.SCHEDULER_FAILURE_ALERT_WEBHOOK_URL = "http://example.com/hook"
            s.SCHEDULER_FAILURE_ALERT_COOLDOWN_SECONDS = 0
            # capture_message is imported lazily inside the dispatcher;
            # patch it at the sentry_utils module so the lazy import picks
            # up the mock.
            with patch("app.services.scheduler_failure_alert_dispatcher._post_webhook") as wh, \
                 patch("app.utils.sentry.capture_message") as cap:
                scheduler_failure_alert_dispatcher.dispatch_scheduler_failure_alert(
                    job_name="event_discover",
                    run_id="r1",
                    error="boom",
                    exc=self._exc(),
                )
                wh.assert_called_once()
                cap.assert_called_once()
        scheduler_failure_alert_dispatcher._reset_cooldown_state()

    def test_no_webhook_url_skips_webhook_not_sentry(self):
        scheduler_failure_alert_dispatcher._reset_cooldown_state()
        with patch("app.services.scheduler_failure_alert_dispatcher.settings") as s:
            s.SCHEDULER_FAILURE_ALERT_ENABLED = True
            s.SCHEDULER_FAILURE_ALERT_WEBHOOK_URL = ""
            s.SCHEDULER_FAILURE_ALERT_COOLDOWN_SECONDS = 0
            with patch("app.services.scheduler_failure_alert_dispatcher._post_webhook") as wh, \
                 patch("app.utils.sentry.capture_message") as cap:
                scheduler_failure_alert_dispatcher.dispatch_scheduler_failure_alert(
                    job_name="event_discover",
                    run_id="r1",
                    error="boom",
                    exc=None,
                )
                wh.assert_not_called()
                cap.assert_called_once()
        scheduler_failure_alert_dispatcher._reset_cooldown_state()

    def test_webhook_failure_does_not_raise(self):
        scheduler_failure_alert_dispatcher._reset_cooldown_state()
        with patch("app.services.scheduler_failure_alert_dispatcher.settings") as s:
            s.SCHEDULER_FAILURE_ALERT_ENABLED = True
            s.SCHEDULER_FAILURE_ALERT_WEBHOOK_URL = "http://example.com/hook"
            s.SCHEDULER_FAILURE_ALERT_COOLDOWN_SECONDS = 0
            with patch("app.services.scheduler_failure_alert_dispatcher._post_webhook",
                       side_effect=Exception("network down")), \
                 patch("app.utils.sentry.capture_message"):
                # Must not raise
                scheduler_failure_alert_dispatcher.dispatch_scheduler_failure_alert(
                    job_name="event_discover",
                    run_id="r1",
                    error="boom",
                    exc=None,
                )
        scheduler_failure_alert_dispatcher._reset_cooldown_state()

    def test_cooldown_deduplicates_per_job_name(self):
        scheduler_failure_alert_dispatcher._reset_cooldown_state()
        with patch("app.services.scheduler_failure_alert_dispatcher.settings") as s:
            s.SCHEDULER_FAILURE_ALERT_ENABLED = True
            s.SCHEDULER_FAILURE_ALERT_WEBHOOK_URL = ""
            s.SCHEDULER_FAILURE_ALERT_COOLDOWN_SECONDS = 3600
            with patch("app.utils.sentry.capture_message") as cap:
                scheduler_failure_alert_dispatcher.dispatch_scheduler_failure_alert(
                    job_name="event_discover", run_id="r1", error="boom", exc=None,
                )
                # Same job_name → within cooldown → skipped
                scheduler_failure_alert_dispatcher.dispatch_scheduler_failure_alert(
                    job_name="event_discover", run_id="r2", error="boom", exc=None,
                )
                # Different job_name → outside cooldown window → fires
                scheduler_failure_alert_dispatcher.dispatch_scheduler_failure_alert(
                    job_name="event_auto_resolve", run_id="r3", error="boom", exc=None,
                )
                # First + third fire; second is deduped
                self.assertEqual(cap.call_count, 2)
        scheduler_failure_alert_dispatcher._reset_cooldown_state()

    def test_force_bypasses_cooldown(self):
        scheduler_failure_alert_dispatcher._reset_cooldown_state()
        with patch("app.services.scheduler_failure_alert_dispatcher.settings") as s:
            s.SCHEDULER_FAILURE_ALERT_ENABLED = True
            s.SCHEDULER_FAILURE_ALERT_WEBHOOK_URL = ""
            s.SCHEDULER_FAILURE_ALERT_COOLDOWN_SECONDS = 3600
            with patch("app.utils.sentry.capture_message") as cap:
                scheduler_failure_alert_dispatcher.dispatch_scheduler_failure_alert(
                    job_name="event_discover", run_id="r1", error="boom", exc=None,
                )
                scheduler_failure_alert_dispatcher.dispatch_scheduler_failure_alert(
                    job_name="event_discover", run_id="r2", error="boom", exc=None,
                    force=True,
                )
                self.assertEqual(cap.call_count, 2)
        scheduler_failure_alert_dispatcher._reset_cooldown_state()

    def test_unknown_job_name_normalized(self):
        scheduler_failure_alert_dispatcher._reset_cooldown_state()
        with patch("app.services.scheduler_failure_alert_dispatcher.settings") as s:
            s.SCHEDULER_FAILURE_ALERT_ENABLED = True
            s.SCHEDULER_FAILURE_ALERT_WEBHOOK_URL = ""
            s.SCHEDULER_FAILURE_ALERT_COOLDOWN_SECONDS = 0
            with patch("app.utils.sentry.capture_message") as cap:
                # Empty job_name should be normalized to "unknown"
                scheduler_failure_alert_dispatcher.dispatch_scheduler_failure_alert(
                    job_name="", run_id="r1", error="boom", exc=None,
                )
                cap.assert_called_once()
                # The job_name kwarg in the capture_message call should be "unknown"
                _, kwargs = cap.call_args
                self.assertEqual(kwargs.get("job_name"), "unknown")
        scheduler_failure_alert_dispatcher._reset_cooldown_state()


if __name__ == "__main__":
    unittest.main()
