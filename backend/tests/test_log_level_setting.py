"""`LOG_LEVEL` must reach the root logger.

Both overlay templates assign it:

    backend/.env.staging.example:31     LOG_LEVEL=INFO
    backend/.env.production.example:43  LOG_LEVEL=WARNING

Nothing read it. `app/core/logging.py` hardcoded `root.setLevel(logging.INFO)`,
and `LOG_LEVEL` appeared nowhere in `app/`, `scripts/` or `deploy/` -- so the
production overlay declared WARNING and the process ran at INFO. Not a silent
no-op in the harmless direction either: an operator who sets WARNING to cut log
volume gets INFO, and one who would set DEBUG to diagnose an incident has no
switch at all.

Measured over every key assigned in the three templates (414 in the base, 15 in
staging, 21 in production) against the 430 `Settings` attributes plus 334 direct
`os.getenv` / `os.environ` reads in `app/` and `scripts/`: `LOG_LEVEL` was the
only name with no reader. `PMRF_DEADMAN_URL` looked like a second one and is not
-- `scripts/healthcheck.py:63` reads it as `env.get("PMRF_DEADMAN_URL", "")`,
which is why the partition test below counts that shape too.

An unparseable value must not stop the process: logging is configured at import
time in `app/main.py`, so raising there would turn a typo into a boot failure.
"""
from __future__ import annotations

import http.client
import io
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import logging as app_logging
from app.core.config import settings

BACKEND_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = (".env.example", ".env.staging.example", ".env.production.example")


class _RootLoggerRestored(unittest.TestCase):
    """setup_logging() mutates the root logger; put it back."""

    def setUp(self) -> None:
        root = logging.getLogger()
        self._handlers = list(root.handlers)
        self._level = root.level
        self.addCleanup(self._restore)
        root.handlers = []

    def _restore(self) -> None:
        root = logging.getLogger()
        for handler in list(root.handlers):
            if handler not in self._handlers:
                handler.close()
        root.handlers = self._handlers
        root.setLevel(self._level)
        for handler in root.handlers:
            handler.filters = [
                filter_ for filter_ in handler.filters
                if not isinstance(filter_, app_logging._ProductionSafeFilter)
            ]


class LogLevelTests(_RootLoggerRestored):
    def test_the_level_comes_from_the_setting(self):
        for name, expected in (
            ("WARNING", logging.WARNING),
            ("DEBUG", logging.DEBUG),
            ("ERROR", logging.ERROR),
        ):
            with self.subTest(level=name):
                logging.getLogger().handlers = []
                with patch.object(settings, "LOG_LEVEL", name), \
                        patch.object(settings, "LOG_FILE", ""):
                    app_logging.setup_logging()
                self.assertEqual(logging.getLogger().level, expected)

    def test_the_level_is_case_insensitive(self):
        with patch.object(settings, "LOG_LEVEL", "warning"), \
                patch.object(settings, "LOG_FILE", ""):
            app_logging.setup_logging()
        self.assertEqual(logging.getLogger().level, logging.WARNING)

    def test_an_unparseable_level_falls_back_to_info(self):
        """A typo must not stop the boot: main.py configures logging on import."""
        with patch.object(settings, "LOG_LEVEL", "VERBOSE"), \
                patch.object(settings, "LOG_FILE", ""):
            app_logging.setup_logging()  # must not raise
        self.assertEqual(logging.getLogger().level, logging.INFO)

    def test_the_default_is_info(self):
        self.assertEqual(str(settings.LOG_LEVEL).upper(), "INFO")

    def test_the_level_applies_even_when_handlers_already_exist(self):
        """The handler guard must not skip the level.

        `setup_logging` returns early when the root logger already has handlers,
        so putting the level assignment after that guard would make the setting
        depend on import order.
        """
        root = logging.getLogger()
        root.addHandler(logging.NullHandler())
        with patch.object(settings, "LOG_LEVEL", "ERROR"), \
                patch.object(settings, "LOG_FILE", ""):
            app_logging.setup_logging()
        self.assertEqual(root.level, logging.ERROR)

    def test_httpx_info_request_urls_are_suppressed(self):
        root = logging.getLogger()
        stream = io.StringIO()
        root.addHandler(logging.StreamHandler(stream))
        httpx_logger = logging.getLogger("httpx")
        original_level = httpx_logger.level
        self.addCleanup(httpx_logger.setLevel, original_level)

        with patch.object(settings, "LOG_LEVEL", "INFO"), \
                patch.object(settings, "LOG_FILE", ""):
            app_logging.setup_logging()

        secret = "httpx-query-secret"
        httpx_logger.info("HTTP Request: GET https://upstream.example/path?key=%s", secret)
        self.assertNotIn(secret, stream.getvalue())

    def test_production_logs_redact_exception_details_urls_and_absolute_paths(self):
        root = logging.getLogger()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root.addHandler(handler)

        with patch.object(settings, "PMRF_ENV", "production"), \
                patch.object(settings, "LOG_LEVEL", "INFO"), \
                patch.object(settings, "LOG_FILE", ""):
            app_logging.setup_logging()

        secret = "upstream-query-secret"
        upstream_url = f"https://upstream.example/private?token={secret}"
        absolute_path = "D:/private/runtime/kernel.db"
        try:
            raise RuntimeError(f"SELECT secret FROM ledger at {absolute_path} via {upstream_url}")
        except RuntimeError as exc:
            logging.getLogger("tests.production.redaction").error(
                "Upstream failed: %s url=%s store=%s",
                exc,
                upstream_url,
                absolute_path,
                exc_info=True,
            )

        text = stream.getvalue()
        self.assertIn("Upstream failed", text)
        self.assertIn("RuntimeError", text)
        self.assertNotIn(secret, text)
        self.assertNotIn(upstream_url, text)
        self.assertNotIn(absolute_path, text)
        self.assertNotIn("SELECT secret", text)
        self.assertNotIn("Traceback", text)

    def test_production_exc_info_true_outside_an_exception_does_not_break_logging(self):
        root = logging.getLogger()
        stream = io.StringIO()
        root.addHandler(logging.StreamHandler(stream))

        with patch.object(settings, "PMRF_ENV", "production"), \
                patch.object(settings, "LOG_LEVEL", "INFO"), \
                patch.object(settings, "LOG_FILE", ""):
            app_logging.setup_logging()

        logging.getLogger("tests.production.no-active-exception").error(
            "Feed failed: %s",
            RuntimeError("private detail"),
            exc_info=True,
        )

        text = stream.getvalue()
        self.assertIn("Feed failed", text)
        self.assertIn("RuntimeError", text)
        self.assertNotIn("private detail", text)
        self.assertNotIn("Traceback", text)

    def test_production_logs_preserve_mapping_style_arguments(self):
        root = logging.getLogger()
        stream = io.StringIO()
        root.addHandler(logging.StreamHandler(stream))

        with patch.object(settings, "PMRF_ENV", "production"), \
                patch.object(settings, "LOG_LEVEL", "INFO"), \
                patch.object(settings, "LOG_FILE", ""):
            app_logging.setup_logging()

        logging.getLogger("tests.production.mapping").warning(
            "job=%(job)s outcome=%(outcome)s url=%(url)s store=%(store)s",
            {
                "job": "startup",
                "outcome": "failed",
                "url": "https://upstream.example/private?token=mapping-secret",
                "store": "D:/private/runtime/kernel.db",
            },
        )

        text = stream.getvalue()
        self.assertIn("job=startup outcome=failed", text)
        self.assertNotIn("mapping-secret", text)
        self.assertNotIn("upstream.example", text)
        self.assertNotIn("D:/private/runtime/kernel.db", text)

    def test_production_logs_redact_url_and_path_string_arguments(self):
        root = logging.getLogger()
        stream = io.StringIO()
        root.addHandler(logging.StreamHandler(stream))

        with patch.object(settings, "PMRF_ENV", "production"), \
                patch.object(settings, "LOG_LEVEL", "INFO"), \
                patch.object(settings, "LOG_FILE", ""):
            app_logging.setup_logging()

        secret = "ordinary-query-secret"
        upstream_url = f"https://upstream.example/private?token={secret}"
        absolute_path = "D:/private/runtime/kernel.db"
        logging.getLogger("tests.production.arguments").error(
            "Upstream read failed url=%s store=%s",
            upstream_url,
            absolute_path,
        )

        text = stream.getvalue()
        self.assertIn("Upstream read failed", text)
        self.assertNotIn(secret, text)
        self.assertNotIn(upstream_url, text)
        self.assertNotIn(absolute_path, text)

    def test_production_logs_redact_an_already_rendered_message(self):
        root = logging.getLogger()
        stream = io.StringIO()
        root.addHandler(logging.StreamHandler(stream))

        with patch.object(settings, "PMRF_ENV", "production"), \
                patch.object(settings, "LOG_LEVEL", "INFO"), \
                patch.object(settings, "LOG_FILE", ""):
            app_logging.setup_logging()

        logging.getLogger("tests.production.rendered").error(
            "Upstream failed at "
            "https://upstream.example/private?token=rendered-query-secret"
        )

        text = stream.getvalue()
        self.assertIn("Upstream failed at <redacted>", text)
        self.assertNotIn("rendered-query-secret", text)
        self.assertNotIn("upstream.example", text)

    def test_production_logs_redact_absolute_paths_in_an_already_rendered_message(self):
        root = logging.getLogger()
        stream = io.StringIO()
        root.addHandler(logging.StreamHandler(stream))

        with patch.object(settings, "PMRF_ENV", "production"), \
                patch.object(settings, "LOG_LEVEL", "INFO"), \
                patch.object(settings, "LOG_FILE", ""):
            app_logging.setup_logging()

        logging.getLogger("tests.production.rendered-path").error(
            "Store failed at D:/private/runtime/kernel.db"
        )

        text = stream.getvalue()
        self.assertNotIn("D:/private/runtime/kernel.db", text)

    def test_production_logs_redact_sql_in_an_already_rendered_message(self):
        root = logging.getLogger()
        stream = io.StringIO()
        root.addHandler(logging.StreamHandler(stream))

        with patch.object(settings, "PMRF_ENV", "production"), \
                patch.object(settings, "LOG_LEVEL", "INFO"), \
                patch.object(settings, "LOG_FILE", ""):
            app_logging.setup_logging()

        logging.getLogger("tests.production.rendered-sql").error(
            "Store failed: SELECT private_value FROM secret_table WHERE token='sql-secret'"
        )

        text = stream.getvalue()
        self.assertNotIn("SELECT private_value", text)
        self.assertNotIn("sql-secret", text)

    def test_production_logs_preserve_non_sql_update_messages(self):
        root = logging.getLogger()
        stream = io.StringIO()
        root.addHandler(logging.StreamHandler(stream))

        with patch.object(settings, "PMRF_ENV", "production"), \
                patch.object(settings, "LOG_LEVEL", "INFO"), \
                patch.object(settings, "LOG_FILE", ""):
            app_logging.setup_logging()

        logging.getLogger("tests.production.update").info(
            "World Cup live update completed"
        )

        self.assertIn("World Cup live update completed", stream.getvalue())

    def test_production_uvicorn_lifespan_message_is_suppressed_without_exc_info(self):
        logger = logging.getLogger("uvicorn")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)

        with patch.object(settings, "PMRF_ENV", "production"), \
                patch.object(settings, "LOG_LEVEL", "INFO"), \
                patch.object(settings, "LOG_FILE", ""):
            app_logging.setup_logging()

        logger.error(
            "Traceback (most recent call last):\n"
            "  File \"D:/private/runtime/app/main.py\", line 1\n"
            "RuntimeError: SELECT private_value FROM secret_table"
        )

        text = stream.getvalue()
        self.assertIn("suppressed", text)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("D:/private/runtime/app/main.py", text)
        self.assertNotIn("SELECT private_value", text)

    def test_production_uvicorn_access_logs_do_not_render_query_strings(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]

        secret = "access-query-secret"
        env = dict(os.environ)
        env.update({
            "PMRF_ENV": "production",
            "PMRF_ENV_FILE_REQUIRED": "false",
            "API_WRITE_KEY": "test-production-write-key",
            "ALLOW_OPEN_WRITES": "false",
            "LLM_DAILY_COST_CAP_USD": "25",
            "OPENAPI_ENABLED": "false",
            "SERVER_RELOAD": "false",
            "CORS_ALLOWED_ORIGINS": "https://frontend.example",
            "BACKUP_SCHEDULE_ENABLED": "false",
            "PHASE10_REALTIME_PUSH_ENABLED": "false",
            "SCHEDULER_ENABLED": "false",
            "LOG_LEVEL": "WARNING",
            "LOG_FILE": "",
        })
        with tempfile.TemporaryDirectory() as tmp:
            env.update({
                "LOOP_DB_FILE": str(Path(tmp) / "loop.db"),
                "EVENT_STORE_FILE": str(Path(tmp) / "events.json"),
                "WORLD_CUP_PREDICTION_DB_FILE": str(
                    Path(tmp) / "predictions.db"
                ),
            })
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.entrypoint:load_application",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                cwd=BACKEND_DIR,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            output = ""
            try:
                deadline = time.monotonic() + 30
                while True:
                    if process.poll() is not None:
                        stdout, stderr = process.communicate(timeout=5)
                        self.fail(
                            "uvicorn exited before accepting requests:\n"
                            f"{stdout}{stderr}"
                        )
                    try:
                        connection = http.client.HTTPConnection(
                            "127.0.0.1", port, timeout=1
                        )
                        connection.request("GET", f"/api?token={secret}")
                        response = connection.getresponse()
                        response.read()
                        connection.close()
                        self.assertEqual(response.status, 200)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            self.fail("uvicorn did not accept requests within 30 seconds")
                        time.sleep(0.05)
            finally:
                if sys.platform == "win32":
                    process.send_signal(1)
                else:
                    process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=10)
                output = stdout + stderr

        self.assertNotIn(secret, output)
        self.assertNotIn("?token=", output)
        self.assertNotIn("Traceback", output)

    def test_production_uvicorn_lifespan_failure_hides_traceback_and_paths(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]

        env = dict(os.environ)
        env.update({
            "PMRF_ENV": "production",
            "PMRF_ENV_FILE_REQUIRED": "false",
            "API_WRITE_KEY": "test-production-write-key",
            "ALLOW_OPEN_WRITES": "false",
            "LLM_DAILY_COST_CAP_USD": "25",
            "OPENAPI_ENABLED": "true",
            "SERVER_RELOAD": "false",
            "CORS_ALLOWED_ORIGINS": "https://frontend.example",
            "BACKUP_SCHEDULE_ENABLED": "false",
            "PHASE10_REALTIME_PUSH_ENABLED": "false",
            "SCHEDULER_ENABLED": "false",
            "LOG_LEVEL": "WARNING",
            "LOG_FILE": "",
        })
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.entrypoint:load_application",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--lifespan",
                "on",
            ],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Application startup failed", output)
        self.assertNotIn("Traceback", output)
        self.assertNotIn(str(BACKEND_DIR), output)
        self.assertNotIn(str(BACKEND_DIR.resolve()), output)


class TemplateKeyReaderTests(unittest.TestCase):
    """Every key a template assigns must have something that reads it.

    This is the test whose absence let `LOG_LEVEL` ship in two overlays with no
    reader. It is a partition over the templates rather than a list of known-good
    keys, so a new setting cannot join a template without one.
    """

    @staticmethod
    def _assigned_keys(path: Path) -> list[str]:
        keys = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key = stripped.split("=", 1)[0].strip()
            if re.fullmatch(r"[A-Z0-9_]+", key):
                keys.append(key)
        return keys

    @staticmethod
    def _readers() -> set[str]:
        names = set(vars(type(settings)))
        # Direct environment reads, in every shape this repo actually uses --
        # `os.getenv`, `os.environ[...]`, `os.environ.get(...)` and the
        # `env.get(...)` in scripts/healthcheck.py, which takes os.environ as an
        # argument so the module-qualified patterns miss it.
        patterns = (
            r"os\.getenv\(\s*[\"']([A-Z0-9_]+)[\"']",
            r"os\.environ\[\s*[\"']([A-Z0-9_]+)[\"']\]",
            r"os\.environ\.get\(\s*[\"']([A-Z0-9_]+)[\"']",
            r"env\.get\(\s*[\"']([A-Z0-9_]+)[\"']",
        )
        for root in ("app", "scripts"):
            for py in (BACKEND_DIR / root).rglob("*.py"):
                text = py.read_text(encoding="utf-8", errors="replace")
                for pattern in patterns:
                    names |= set(re.findall(pattern, text))
        return names

    def test_the_scan_sees_the_settings_and_the_env_reads(self):
        readers = self._readers()
        self.assertGreater(len(readers), 400, len(readers))
        self.assertIn("API_WRITE_KEY", readers)          # a Settings attribute
        self.assertIn("PMRF_DEADMAN_URL", readers)       # only an env.get(...)
        self.assertIn("PMRF_ENV", readers)               # read before Settings exists

    def test_every_assigned_key_has_a_reader(self):
        readers = self._readers()
        for name in TEMPLATES:
            path = BACKEND_DIR / name
            with self.subTest(template=name):
                self.assertTrue(path.exists(), f"{name} is gone")
                unread = [k for k in self._assigned_keys(path) if k not in readers]
                self.assertEqual(
                    unread,
                    [],
                    f"{name} assigns keys nothing reads: {unread}. Either give the "
                    "name a reader or comment the line out -- an assignment that "
                    "changes nothing reads as configuration that works.",
                )

    def test_the_partition_would_notice_an_unread_key(self):
        """Guard the instrument against a reader scan that matches everything."""
        readers = self._readers()
        self.assertNotIn("PMRF_NOT_A_REAL_SETTING", readers)


if __name__ == "__main__":
    unittest.main()
