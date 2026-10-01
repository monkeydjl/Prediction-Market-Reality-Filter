"""Tests for the P0 round-2 fixes (G1-G5).

Covers:
- G2: OVERLAY_LATENCY histogram is actually observed at each overlay
  build site (decision_quality / market_quality / source_reliability /
  merge / llm_telemetry). Verified via static source inspection since
  the wireup lives inside feature-flagged try/except blocks.
- G3: restore_stores._check_service_running falls back to a health
  endpoint probe on Windows (no fcntl) instead of silently returning
  False. Verified by patching the import to fail and asserting a
  urllib request is attempted. The probe goes through
  ``_open_health_url``, which is the seam these tests drive: for a
  loopback URL it builds an opener with an empty ``ProxyHandler``
  (audit section 35 -- with a global HTTP_PROXY the proxy otherwise
  answers for a dead loopback port and the warning is permanent).
- G5: event_store.save_events increments FINAL_DIRECTION_CHANGE when
  an existing event's final_displayed_direction differs from the
  incoming candidate. Verified by static source inspection + a fixture
  test that patches the counter.
"""
from __future__ import annotations

import os
import socket
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestG2OverlayLatencyWiredUp(unittest.TestCase):
    """G2: OVERLAY_LATENCY must be observed at each overlay build site,
    not just defined in metrics.py."""

    def _read_source(self, path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def test_decision_quality_records_latency(self):
        src = self._read_source("app/services/event_intelligence_service.py")
        self.assertIn('record_overlay_latency("decision_quality"', src)

    def test_market_quality_records_latency(self):
        src = self._read_source("app/services/event_intelligence_service.py")
        self.assertIn('record_overlay_latency("market_quality"', src)

    def test_source_reliability_records_latency(self):
        src = self._read_source("app/services/event_intelligence_service.py")
        self.assertIn('record_overlay_latency("source_reliability"', src)

    def test_merge_records_latency(self):
        src = self._read_source("app/services/event_intelligence_service.py")
        self.assertIn('record_overlay_latency("merge"', src)

    def test_llm_telemetry_records_latency(self):
        src = self._read_source("app/services/event_intelligence_service.py")
        self.assertIn('record_overlay_latency("llm_telemetry"', src)

    def test_uses_perf_counter_for_timing(self):
        """All overlay blocks should use time.perf_counter() for
        monotonic timing (not time.time which is wall-clock)."""
        src = self._read_source("app/services/event_intelligence_service.py")
        self.assertIn("import time", src)
        self.assertIn("time.perf_counter()", src)


class TestG3RestoreWindowsHealthProbe(unittest.TestCase):
    """G3: on Windows (no fcntl), _check_service_running must probe the
    health endpoint instead of silently returning False.

    We simulate the Windows path by patching the ``fcntl`` module in
    ``sys.modules`` with a stub that raises ImportError when accessed.
    This is cleaner than patching ``__import__`` (which triggers infinite
    recursion on Python's internal imports)."""

    def _patch_no_fcntl(self):
        """Return a context manager that makes ``import fcntl`` fail by
        injecting a stub module that raises ImportError on attribute
        access. The restore_stores code uses ``import fcntl`` inside a
        try/except ImportError, so we need the import itself to raise."""
        # Use a module-like object whose __getattr__ raises ImportError.
        # But Python's import machinery checks sys.modules first; if the
        # key exists, the import succeeds. So we need to DELETE the key
        # AND make the subsequent import fail. The cleanest way is to
        # patch the import system via patch.dict on sys.modules to
        # remove fcntl, and rely on the real import failing (it will,
        # because we're on Windows OR because we patch the finder).
        #
        # Simpler: patch the function's source module so the try/except
        # takes the ImportError branch. We do this by patching
        # ``sys.modules['fcntl']`` with None (which makes ``import fcntl``
        # raise ImportError in Python 3.6+ when the cached value is None).
        from unittest.mock import patch as _patch
        return _patch.dict("sys.modules", {"fcntl": None})

    def test_windows_path_uses_health_endpoint(self):
        """When fcntl is unavailable (Windows), the function should try
        a urllib request to PMRF_HEALTHCHECK_URL.

        Driven at ``_open_health_url``, the seam that decides whether the
        environment's proxy is used. Patching ``urllib.request.urlopen`` no
        longer reaches the probe for a loopback URL (audit section 35).
        """
        from scripts import restore_stores

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        # Setting sys.modules['fcntl'] = None makes ``import fcntl`` raise
        # ImportError (Python 3.6+ treats None as "not found").
        with patch.dict("sys.modules", {"fcntl": None}):
            with patch.object(
                restore_stores, "_open_health_url", return_value=mock_resp
            ) as mock_open:
                result = restore_stores._check_service_running()

        self.assertTrue(result)
        mock_open.assert_called_once()

    def test_windows_path_returns_false_on_connection_error(self):
        """When the health endpoint is unreachable (connection refused),
        the function should return False (service not running)."""
        from scripts import restore_stores

        with patch.dict("sys.modules", {"fcntl": None}):
            with patch.object(
                restore_stores,
                "_open_health_url",
                side_effect=ConnectionError("refused"),
            ):
                result = restore_stores._check_service_running()

        self.assertFalse(result)

    def test_windows_path_returns_false_on_url_error(self):
        """urllib.error.URLError (e.g. timeout) -> service not running."""
        from scripts import restore_stores
        import urllib.error

        with patch.dict("sys.modules", {"fcntl": None}):
            with patch.object(
                restore_stores,
                "_open_health_url",
                side_effect=urllib.error.URLError("timeout"),
            ):
                result = restore_stores._check_service_running()

        self.assertFalse(result)

    def test_windows_path_returns_true_on_http_503(self):
        """H2: a 503 (degraded but running) service must NOT be treated as
        'not running'. ``urllib.request.urlopen`` raises ``HTTPError`` for
        4xx/5xx responses; without the HTTPError-specific except clause,
        it would fall through to the URLError handler and (incorrectly)
        return False — causing restore to clobber a live-but-degraded DB.
        """
        from scripts import restore_stores
        import urllib.error

        http_err = urllib.error.HTTPError(
            url="http://localhost:8000/api/health",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=None,
        )

        with patch.dict("sys.modules", {"fcntl": None}):
            with patch.object(
                restore_stores, "_open_health_url", side_effect=http_err
            ):
                result = restore_stores._check_service_running()

        self.assertTrue(result)

    # ── Proxy handling (audit section 35) ────────────────────────────────

    def test_loopback_urls_are_recognised(self):
        from scripts import restore_stores

        for url in (
            "http://localhost:8000/api/health",
            "http://LOCALHOST:8000/api/health",
            "http://127.0.0.1:8000/api/health",
            "http://127.1.2.3:8000/api/health",
            "http://[::1]:8000/api/health",
        ):
            with self.subTest(url=url):
                self.assertTrue(restore_stores._is_loopback_url(url))

    def test_remote_urls_are_not_treated_as_loopback(self):
        """The bypass stays limited on purpose: an operator who points
        PMRF_HEALTHCHECK_URL at a reverse proxy did so deliberately, and
        stripping the proxy there would change what the check means.
        """
        from scripts import restore_stores

        for url in (
            "https://pmrf.example.com/api/health",
            "http://192.168.1.10:8000/api/health",
            "http://10.0.0.1/api/health",
            "http://0.0.0.0:8000/api/health",
            "",
        ):
            with self.subTest(url=url):
                self.assertFalse(restore_stores._is_loopback_url(url))

    def test_a_loopback_probe_builds_an_opener_with_no_proxy(self):
        from scripts import restore_stores

        fake_opener = MagicMock()
        with patch(
            "urllib.request.build_opener", return_value=fake_opener
        ) as mock_build:
            with patch("urllib.request.ProxyHandler") as mock_handler:
                restore_stores._open_health_url(
                    "http://127.0.0.1:8000/api/health", 1
                )

        mock_handler.assert_called_once_with({})
        mock_build.assert_called_once()
        fake_opener.open.assert_called_once()

    def test_a_remote_probe_keeps_the_default_opener(self):
        from scripts import restore_stores

        with patch("urllib.request.build_opener") as mock_build:
            with patch("urllib.request.urlopen") as mock_urlopen:
                restore_stores._open_health_url("https://pmrf.example.com/x", 1)

        mock_build.assert_not_called()
        mock_urlopen.assert_called_once()

    def test_a_proxy_answering_everything_cannot_make_a_dead_port_look_alive(self):
        """Section 35's finding, reproduced end to end.

        On the machine the audit ran on, ``HTTP_PROXY`` was set and ``NO_PROXY``
        was not, so the probe reached the proxy instead of the port. The proxy
        answered 502 for a port with no listener, and 502 counts as "a service
        is bound here" -- so the check reported a service that was not running
        and the restore warning stayed on permanently.

        Here a stand-in proxy answers 502 to everything, and the probe targets a
        loopback port that is held open but never listening. Only two answers
        are possible: refused (nothing there) or spoken for by the proxy.
        """
        from scripts import restore_stores

        proxy = _Always502Proxy()
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        dead_port = holder.getsockname()[1]
        try:
            with patch.dict(
                os.environ,
                {
                    "HTTP_PROXY": proxy.url,
                    "http_proxy": proxy.url,
                    "HTTPS_PROXY": proxy.url,
                    "https_proxy": proxy.url,
                    "NO_PROXY": "",
                    "no_proxy": "",
                },
            ):
                with patch.dict("sys.modules", {"fcntl": None}):
                    with patch.object(
                        restore_stores.settings,
                        "PMRF_HEALTHCHECK_URL",
                        f"http://127.0.0.1:{dead_port}/api/health",
                    ):
                        result = restore_stores._check_service_running()
        finally:
            holder.close()
            proxy.stop()

        self.assertFalse(
            result,
            "a proxy answered for a loopback port nothing was listening on",
        )


class _Always502Proxy:
    """A stand-in HTTP proxy that answers 502 to everything.

    Modelled on the proxy section 35 found: any request gets a response, so
    ``urlopen`` sees a healthy HTTP exchange for a port with no listener.
    """

    def __init__(self):
        import http.server

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 -- http.server's naming
                self.send_error(502, "upstream connect failed")

            def log_message(self, *args):
                del args  # keep the test output quiet

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class TestG5DirectionChangeCounterInSaveEvents(unittest.TestCase):
    """G5: save_events must increment FINAL_DIRECTION_CHANGE when an
    existing event's final_displayed_direction differs from the incoming
    candidate. This catches both guardrail-induced and re-scan drift."""

    def _read_source(self, path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def test_save_events_has_direction_change_detection(self):
        """Source must contain the pre/post direction comparison and
        the FINAL_DIRECTION_CHANGE.inc() call."""
        src = self._read_source("app/memory/event_store.py")
        self.assertIn("_pre_dir", src)
        self.assertIn("_post_dir", src)
        self.assertIn("FINAL_DIRECTION_CHANGE.inc()", src)

    def test_save_events_only_counts_when_both_directions_present(self):
        """The guard requires both _pre_dir and _post_dir to be non-None
        — a fresh record (no pre_dir) or a record without overlay (no
        post_dir) must NOT increment the counter."""
        src = self._read_source("app/memory/event_store.py")
        # The condition is: _pre_dir is not None AND _post_dir is not None
        # AND _pre_dir != _post_dir.
        self.assertIn("_pre_dir is not None and _post_dir is not None", src)


class TestG4MigrationScriptIdempotent(unittest.TestCase):
    """G4: the migrate_event_store_schema script is idempotent — running
    on already-current records is a no-op (no write)."""

    def test_idempotent_no_write_when_nothing_to_upgrade(self):
        """Already-current records -> upgraded_count=0 -> no write_json_atomic."""
        from scripts.migrate_event_store_schema import migrate_event_store_schema
        from app.services.event_schema import CURRENT_SCHEMA_VERSION

        # A store with one record already at current schema_version.
        store_data = {
            "evt1": {
                "event_id": "evt1",
                "record": {
                    "event_id": "evt1",
                    "schema_version": CURRENT_SCHEMA_VERSION,
                    "decision_quality": None,
                    "market_quality": None,
                    "source_reliability": None,
                    "final_displayed_direction": None,
                    "final_downgrade_reason": None,
                    "llm_telemetry": None,
                },
            }
        }

        with patch("scripts.migrate_event_store_schema.read_json_strict", return_value=store_data):
            with patch("scripts.migrate_event_store_schema.Path") as mock_path_cls:
                mock_path = MagicMock()
                mock_path.exists.return_value = True
                mock_path_cls.return_value.resolve.return_value = mock_path

                with patch("scripts.migrate_event_store_schema.locked_file"):
                    with patch("scripts.migrate_event_store_schema.write_json_atomic") as mock_write:
                        result = migrate_event_store_schema(apply=True)

        self.assertEqual(result["upgraded_count"], 0)
        mock_write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
