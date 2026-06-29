"""Tests for the Sentry integration (P0-7 §1.2).

Covers:
- ``init_sentry`` is a no-op when DSN is empty.
- ``init_sentry`` initializes the SDK when DSN is set + sentry_sdk is installed.
- ``capture_exception`` and ``capture_message`` are safe no-ops when disabled.
- ``is_enabled`` reflects the initialized state.
- ``scheduler._finish_run`` forwards failures to ``capture_exception``.
- ``Settings.SENTRY_*`` config fields have expected defaults.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.core.config import settings
from app.utils import sentry


class TestSentryConfigDefaults(unittest.TestCase):
    """Spec §1.2 — Sentry config fields exist with safe defaults."""

    def test_defaults_are_safe(self):
        # SENTRY_DSN must default to empty so the app boots without Sentry
        self.assertEqual(settings.SENTRY_DSN, "")
        self.assertEqual(settings.SENTRY_ENVIRONMENT, "production")
        self.assertEqual(settings.SENTRY_RELEASE, "")
        # Performance tracing is off by default
        self.assertEqual(settings.SENTRY_TRACES_SAMPLE_RATE, 0.0)
        self.assertTrue(settings.SENTRY_ATTACH_STACKTRACES)


class TestSentryNoOpWhenDsnEmpty(unittest.TestCase):
    """When SENTRY_DSN is empty, every call must be a no-op (no exception)."""

    def setUp(self):
        # is_enabled() reads sentry_sdk.is_initialized(), which reflects
        # global SDK state. Prior tests in the same discovery run may have
        # initialized the SDK (e.g. via sentry_sdk.init(dsn=None) in another
        # test's setUp), so we mock is_initialized to return False here to
        # isolate the "DSN empty -> no-op" contract from cross-test state.
        if sentry._SENTRY_AVAILABLE:
            import sentry_sdk
            self._patcher = patch.object(
                sentry_sdk, "is_initialized", return_value=False
            )
            self._patcher.start()
        else:
            self._patcher = None

    def tearDown(self):
        if self._patcher is not None:
            self._patcher.stop()

    def test_init_sentry_returns_false_for_empty_dsn(self):
        result = sentry.init_sentry(dsn="")
        self.assertFalse(result)
        self.assertFalse(sentry.is_enabled())

    def test_capture_exception_does_not_raise_without_init(self):
        # Without init, capture_exception should be a silent no-op
        sentry.capture_exception(ValueError("test"))
        sentry.capture_exception()  # no active exception
        sentry.capture_exception(ValueError("test"), job_id="abc")

    def test_capture_message_does_not_raise_without_init(self):
        sentry.capture_message("test message")
        sentry.capture_message("test message", level="error", extra="ctx")


class TestSentryInitWithDsn(unittest.TestCase):
    """When SENTRY_DSN is set + sentry_sdk available, init initializes the SDK."""

    def setUp(self):
        # Reset any prior init state
        if sentry._SENTRY_AVAILABLE:
            try:
                import sentry_sdk
                sentry_sdk.init(dsn=None)  # reset to uninitialized
            except Exception:
                pass

    def tearDown(self):
        if sentry._SENTRY_AVAILABLE:
            try:
                import sentry_sdk
                sentry_sdk.init(dsn=None)
            except Exception:
                pass

    def test_init_sentry_returns_true_when_dsn_set_and_sdk_available(self):
        if not sentry._SENTRY_AVAILABLE:
            self.skipTest("sentry_sdk not installed")
        with patch("sentry_sdk.init") as mock_init, \
             patch("sentry_sdk.integrations.fastapi.FastApiIntegration") as mock_fastapi:
            mock_fastapi.return_value = MagicMock()
            result = sentry.init_sentry(
                dsn="https://abc@example.ingest.sentry.io/123",
                environment="test",
                release="pmrf@test",
            )
            self.assertTrue(result)
            self.assertTrue(mock_init.called)
            call_kwargs = mock_init.call_args.kwargs
            self.assertEqual(call_kwargs["dsn"], "https://abc@example.ingest.sentry.io/123")
            self.assertEqual(call_kwargs["environment"], "test")
            self.assertEqual(call_kwargs["release"], "pmrf@test")
            self.assertFalse(call_kwargs["send_default_pii"])

    def test_init_sentry_returns_false_when_dsn_set_but_sdk_missing(self):
        # Simulate sentry_sdk missing
        with patch.object(sentry, "_SENTRY_AVAILABLE", False):
            result = sentry.init_sentry(dsn="https://abc@example.ingest.sentry.io/123")
            self.assertFalse(result)

    def test_capture_exception_forwards_to_sentry_sdk_when_enabled(self):
        if not sentry._SENTRY_AVAILABLE:
            self.skipTest("sentry_sdk not installed")
        with patch("sentry_sdk.capture_exception") as mock_capture, \
             patch("sentry_sdk.set_context") as mock_set_context, \
             patch.object(sentry, "_SENTRY_AVAILABLE", True):
            exc = ValueError("boom")
            sentry.capture_exception(exc, job_id="abc")
            mock_set_context.assert_called_once()
            mock_capture.assert_called_once_with(exc)

    def test_capture_message_forwards_to_sentry_sdk_when_enabled(self):
        if not sentry._SENTRY_AVAILABLE:
            self.skipTest("sentry_sdk not installed")
        with patch("sentry_sdk.capture_message") as mock_capture, \
             patch("sentry_sdk.set_context") as mock_set_context, \
             patch.object(sentry, "_SENTRY_AVAILABLE", True):
            sentry.capture_message("hello", level="warning", job_id="abc")
            mock_set_context.assert_called_once()
            mock_capture.assert_called_once_with("hello", level="warning")

    def test_is_enabled_returns_false_when_sdk_unavailable(self):
        # When sentry_sdk is not installed, is_enabled must return False
        with patch.object(sentry, "_SENTRY_AVAILABLE", False):
            self.assertFalse(sentry.is_enabled())


class TestSchedulerSentryForwarding(unittest.TestCase):
    """scheduler._finish_run forwards failures to capture_exception."""

    def test_finish_run_failed_status_calls_capture_exception(self):
        from app.core import scheduler
        from app.memory import loop_run_store

        run_id = "test-run-id"
        test_exc = ValueError("scheduler boom")

        with patch.object(loop_run_store, "finish_run") as mock_finish, \
             patch("app.utils.sentry.capture_exception") as mock_capture:
            scheduler._finish_run(
                run_id,
                "failed",
                error="scheduler boom",
                exc=test_exc,
            )
            mock_finish.assert_called_once()
            mock_capture.assert_called_once()
            call_args = mock_capture.call_args
            # The exception object must be forwarded
            self.assertEqual(call_args.args[0], test_exc)
            # And the context must include run_id + error
            self.assertEqual(call_args.kwargs.get("job_run_id"), run_id)
            self.assertEqual(call_args.kwargs.get("job_error"), "scheduler boom")

    def test_finish_run_success_status_does_not_call_capture_exception(self):
        from app.core import scheduler
        from app.memory import loop_run_store

        with patch.object(loop_run_store, "finish_run") as mock_finish, \
             patch("app.utils.sentry.capture_exception") as mock_capture:
            scheduler._finish_run("test-run-id", "success", result={"n": 1})
            mock_finish.assert_called_once()
            mock_capture.assert_not_called()

    def test_finish_run_none_run_id_is_noop(self):
        from app.core import scheduler

        with patch("app.utils.sentry.capture_exception") as mock_capture:
            scheduler._finish_run(None, "failed", error="oops")
            mock_capture.assert_not_called()


class TestSentryLifespanIntegration(unittest.TestCase):
    """main.lifespan calls init_sentry with the configured settings."""

    def test_lifespan_calls_init_sentry_with_settings(self):
        # Just verify the lifespan imports + calls init_sentry; we don't need
        # to actually run the lifespan (which would trigger DB migrations etc.).
        import inspect
        from app import main
        source = inspect.getsource(main.lifespan)
        self.assertIn("init_sentry", source)
        self.assertIn("settings.SENTRY_DSN", source)
        self.assertIn("settings.SENTRY_ENVIRONMENT", source)


if __name__ == "__main__":
    unittest.main()
