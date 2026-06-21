import asyncio
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.routes import events as events_routes
from app.core.rate_limit import InMemoryRateLimitMiddleware
from app.core.config import settings
from scripts import backup_stores, healthcheck


class OpenAPIContractTests(unittest.TestCase):
    def test_event_routes_declare_response_models(self):
        missing = [
            route.path
            for route in events_routes.router.routes
            if isinstance(route, APIRoute) and route.response_model is None
        ]
        self.assertEqual(missing, [])


class WriteAuthTests(unittest.TestCase):
    def _client(self):
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        return TestClient(app)

    def test_write_key_blocks_mutating_and_costly_routes_when_configured(self):
        with patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = self._client().post("/events/analyze", json={})
        self.assertEqual(resp.status_code, 401)

    def test_write_key_allows_request_with_matching_header(self):
        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch.object(
                    events_routes,
                    "analyze_event_question",
                    new=AsyncMock(return_value={"event_id": "evt1"}),
                ):
            resp = self._client().post(
                "/events/analyze",
                headers={"X-API-Key": "secret"},
                json={
                    "event_question": "Will the test pass?",
                    "baseline_probability": 50,
                    "news_context": "context",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["event_id"], "evt1")

    def test_write_key_wrong_header_rejected(self):
        with patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = self._client().post(
                "/events/analyze",
                headers={"X-API-Key": "wrong"},
                json={},
            )
        self.assertEqual(resp.status_code, 401)

    def test_keyless_with_opt_in_passes_through(self):
        # Empty key + explicit ALLOW_OPEN_WRITES: the operator opted into public
        # writes, so the request path passes through. (The startup guard — not the
        # request path — is what refuses an *accidental* keyless boot; see
        # StartupGuardTests.)
        with patch.object(settings, "API_WRITE_KEY", ""), \
                patch.object(settings, "ALLOW_OPEN_WRITES", True), \
                patch.object(
                    events_routes,
                    "analyze_event_question",
                    new=AsyncMock(return_value={"event_id": "evt1"}),
                ):
            resp = self._client().post(
                "/events/analyze",
                json={
                    "event_question": "Will the test pass?",
                    "baseline_probability": 50,
                    "news_context": "context",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["event_id"], "evt1")


class StartupGuardTests(unittest.TestCase):
    """The fail-closed P0: the app must refuse to boot with no write key unless
    ALLOW_OPEN_WRITES is explicitly set, so a deploy that forgets the key does
    not come up silently wide open."""

    def test_lifespan_refuses_keyless_boot_without_opt_in(self):
        from app.main import app, lifespan

        with patch.object(settings, "API_WRITE_KEY", ""), \
                patch.object(settings, "ALLOW_OPEN_WRITES", False), \
                patch("app.main.start_scheduler", lambda: None), \
                patch("app.main.stop_scheduler", lambda: None):
            with self.assertRaises(RuntimeError):
                # TestClient as a context manager drives the lifespan startup.
                with TestClient(app):
                    pass

    def test_lifespan_boots_keyless_with_opt_in(self):
        from app.main import app

        started = {"n": 0}
        with patch.object(settings, "API_WRITE_KEY", ""), \
                patch.object(settings, "ALLOW_OPEN_WRITES", True), \
                patch("app.main.start_scheduler", lambda: started.__setitem__("n", started["n"] + 1)), \
                patch("app.main.stop_scheduler", lambda: None):
            with TestClient(app):
                pass
        self.assertEqual(started["n"], 1)

    def test_lifespan_boots_with_key(self):
        from app.main import app

        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch("app.main.start_scheduler", lambda: None), \
                patch("app.main.stop_scheduler", lambda: None):
            with TestClient(app):
                pass

    def test_lifespan_does_not_check_llm_by_default(self):
        from app.main import app

        check = AsyncMock()
        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch.object(settings, "LLM_STARTUP_CHECK_ENABLED", False), \
                patch("app.main.validate_primary_llm_startup", check), \
                patch("app.main.start_scheduler", lambda: None), \
                patch("app.main.stop_scheduler", lambda: None):
            with TestClient(app):
                pass
        check.assert_not_awaited()

    def test_lifespan_runs_llm_startup_check_when_enabled(self):
        from app.main import app

        check = AsyncMock()
        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch.object(settings, "LLM_STARTUP_CHECK_ENABLED", True), \
                patch("app.main.validate_primary_llm_startup", check), \
                patch("app.main.start_scheduler", lambda: None), \
                patch("app.main.stop_scheduler", lambda: None):
            with TestClient(app):
                pass
        check.assert_awaited_once()

    def test_lifespan_refuses_boot_when_llm_startup_check_fails(self):
        from app.main import app

        check = AsyncMock(side_effect=RuntimeError("bad llm key"))
        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch.object(settings, "LLM_STARTUP_CHECK_ENABLED", True), \
                patch("app.main.validate_primary_llm_startup", check), \
                patch("app.main.start_scheduler", lambda: None), \
                patch("app.main.stop_scheduler", lambda: None):
            with self.assertRaises(RuntimeError):
                with TestClient(app):
                    pass
        check.assert_awaited_once()

    def test_lifespan_skips_scheduler_when_disabled(self):
        from app.main import app

        calls = {"start": 0, "stop": 0}
        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch.object(settings, "SCHEDULER_ENABLED", False), \
                patch(
                    "app.main.start_scheduler",
                    lambda: calls.__setitem__("start", calls["start"] + 1),
                ), \
                patch(
                    "app.main.stop_scheduler",
                    lambda: calls.__setitem__("stop", calls["stop"] + 1),
                ):
            with TestClient(app):
                pass
        self.assertEqual(calls, {"start": 0, "stop": 0})


class LLMStartupCheckTests(unittest.TestCase):
    def test_llm_startup_check_rejects_empty_key(self):
        from app.services.llm_startup_check_service import validate_primary_llm_startup

        with patch.object(settings, "OPENAI_API_KEY", ""):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY is empty"):
                asyncio.run(validate_primary_llm_startup())

    def test_llm_startup_check_redacts_key_from_failure(self):
        from app.services.llm_startup_check_service import validate_primary_llm_startup

        class _Completions:
            async def create(self, **kwargs):
                raise RuntimeError("bad secret-key")

        class _Chat:
            completions = _Completions()

        class _Client:
            def __init__(self, **kwargs):
                self.chat = _Chat()

        with patch.object(settings, "OPENAI_API_KEY", "secret-key"), \
                patch("app.services.llm_startup_check_service.AsyncOpenAI", _Client):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(validate_primary_llm_startup())

        message = str(ctx.exception)
        self.assertNotIn("secret-key", message)
        self.assertIn("<redacted>", message)


class RateLimitTests(unittest.TestCase):
    def test_rate_limit_returns_429_after_configured_limit(self):
        app = FastAPI()
        app.add_middleware(InMemoryRateLimitMiddleware)

        @app.get("/ping")
        async def ping():
            return {"ok": True}

        with patch.object(settings, "RATE_LIMIT_ENABLED", True), \
                patch.object(settings, "RATE_LIMIT_WINDOW_SECONDS", 60), \
                patch.object(settings, "RATE_LIMIT_MAX_REQUESTS", 1):
            client = TestClient(app)
            self.assertEqual(client.get("/ping").status_code, 200)
            resp = client.get("/ping")

        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.headers["Retry-After"], "60")

    def test_rate_limit_uses_normalized_path_for_dynamic_segments(self):
        app = FastAPI()
        app.add_middleware(InMemoryRateLimitMiddleware)

        @app.get("/items/{item_id}/resolve")
        async def resolve_item(item_id: str):
            return {"item_id": item_id}

        with patch.object(settings, "RATE_LIMIT_ENABLED", True), \
                patch.object(settings, "RATE_LIMIT_WINDOW_SECONDS", 60), \
                patch.object(settings, "RATE_LIMIT_MAX_REQUESTS", 1):
            client = TestClient(app)
            self.assertEqual(client.get("/items/a/resolve").status_code, 200)
            resp = client.get("/items/b/resolve")

        self.assertEqual(resp.status_code, 429)

    def test_rate_limit_uses_forwarded_client_ip(self):
        app = FastAPI()
        app.add_middleware(InMemoryRateLimitMiddleware)

        @app.get("/ping")
        async def ping():
            return {"ok": True}

        with patch.object(settings, "RATE_LIMIT_ENABLED", True), \
                patch.object(settings, "RATE_LIMIT_WINDOW_SECONDS", 60), \
                patch.object(settings, "RATE_LIMIT_MAX_REQUESTS", 1):
            client = TestClient(app)
            self.assertEqual(
                client.get("/ping", headers={"X-Forwarded-For": "203.0.113.1"}).status_code,
                200,
            )
            self.assertEqual(
                client.get("/ping", headers={"X-Forwarded-For": "203.0.113.1"}).status_code,
                429,
            )
            resp = client.get("/ping", headers={"X-Forwarded-For": "203.0.113.2"})

        self.assertEqual(resp.status_code, 200)


class _FakeScheduler:
    """Stand-in for the APScheduler whose `running` is a read-only property."""

    def __init__(self, running: bool):
        self.running = running


class HealthTests(unittest.TestCase):
    def test_cors_defaults_do_not_use_wildcard_methods_or_headers(self):
        self.assertNotIn("*", settings.CORS_ALLOWED_METHODS)
        self.assertNotIn("*", settings.CORS_ALLOWED_HEADERS)

    def test_cors_rejects_wildcard_origin_with_credentials(self):
        from app.main import _validate_cors_settings

        with patch.object(settings, "CORS_ALLOWED_ORIGINS", ["*"]), \
                patch.object(settings, "CORS_ALLOW_CREDENTIALS", True):
            with self.assertRaises(RuntimeError):
                _validate_cors_settings()

    def test_cors_rejects_wildcard_methods_or_headers(self):
        from app.main import _validate_cors_settings

        with patch.object(settings, "CORS_ALLOWED_METHODS", ["*"]):
            with self.assertRaises(RuntimeError):
                _validate_cors_settings()
        with patch.object(settings, "CORS_ALLOWED_HEADERS", ["*"]):
            with self.assertRaises(RuntimeError):
                _validate_cors_settings()

    def test_cors_preflight_allows_expected_write_headers(self):
        from app.main import app

        resp = TestClient(app).options(
            "/api/events/analyze",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-api-key",
            },
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("POST", resp.headers["access-control-allow-methods"])
        self.assertIn("X-API-Key", resp.headers["access-control-allow-headers"])

    def test_security_headers_are_set(self):
        from app.main import app

        resp = TestClient(app).get("/api")

        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(resp.headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'self'", resp.headers["Content-Security-Policy"])
        self.assertIn("max-age=31536000", resp.headers["Strict-Transport-Security"])

    def test_api_health_ok_returns_200_when_healthy(self):
        from app.main import app

        client = TestClient(app)
        with patch("app.core.scheduler.scheduler", _FakeScheduler(True)), \
                patch(
                    "app.services.loop_status_service.loop_status",
                    return_value={"runs": {}},
                ):
            resp = client.get("/api/health")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("scheduler_running", body)
        self.assertIn("loop", body)

    def test_api_health_requests_sanitized_status_by_default(self):
        from app.main import app

        client = TestClient(app)
        with patch("app.core.scheduler.scheduler", _FakeScheduler(True)), \
                patch(
                    "app.services.loop_status_service.loop_status",
                    return_value={"runs": {}},
                ) as status_mock:
            client.get("/api/health")

        status_mock.assert_called_once_with(
            scheduler_running=True,
            include_run_details=False,
        )

    def test_api_health_allows_run_details_with_write_key(self):
        from app.main import app

        client = TestClient(app)
        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch("app.core.scheduler.scheduler", _FakeScheduler(True)), \
                patch(
                    "app.services.loop_status_service.loop_status",
                    return_value={"runs": {}},
                ) as status_mock:
            client.get("/api/health", headers={"X-API-Key": "secret"})

        status_mock.assert_called_once_with(
            scheduler_running=True,
            include_run_details=True,
        )

    def test_api_health_returns_503_when_degraded(self):
        from app.main import app

        client = TestClient(app)
        # A failed scheduled run must surface as 503 so container/systemd
        # healthchecks and uptime monitors trip instead of seeing a 200.
        with patch("app.core.scheduler.scheduler", _FakeScheduler(True)), \
                patch(
                    "app.services.loop_status_service.loop_status",
                    return_value={"runs": {"event_discover": {"status": "failed"}}},
                ):
            resp = client.get("/api/health")

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["status"], "degraded")

    def test_api_health_returns_503_when_scheduler_stopped(self):
        from app.main import app

        client = TestClient(app)
        with patch("app.core.scheduler.scheduler", _FakeScheduler(False)), \
                patch(
                    "app.services.loop_status_service.loop_status",
                    return_value={"runs": {}},
                ):
            resp = client.get("/api/health")

        self.assertEqual(resp.status_code, 503)

    def test_api_health_ok_when_scheduler_explicitly_disabled(self):
        from app.main import app

        client = TestClient(app)
        with patch.object(settings, "SCHEDULER_ENABLED", False), \
                patch("app.core.scheduler.scheduler", _FakeScheduler(False)), \
                patch(
                    "app.services.loop_status_service.loop_status",
                    return_value={"runs": {}},
                ):
            resp = client.get("/api/health")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertFalse(body["scheduler_enabled"])

    def test_api_health_ok_when_scheduler_lock_owned_elsewhere(self):
        from app.main import app

        client = TestClient(app)
        with patch.object(settings, "SCHEDULER_ENABLED", True), \
                patch("app.core.scheduler.scheduler", _FakeScheduler(False)), \
                patch(
                    "app.core.scheduler.scheduler_start_skipped_due_to_lock",
                    return_value=True,
                ), \
                patch(
                    "app.services.loop_status_service.loop_status",
                    return_value={"runs": {}},
                ):
            resp = client.get("/api/health")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["scheduler_lock_skipped"])


class HealthcheckScriptTests(unittest.TestCase):
    def _run_healthcheck(self, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return healthcheck.run_healthcheck(*args, **kwargs)

    def test_healthcheck_pings_deadman_after_local_health_is_ok(self):
        calls = []

        def fetch(url, timeout):
            calls.append((url, timeout))
            if url == "http://local/api/health":
                return 200, b'{"status":"ok"}'
            return 200, b"ok"

        code = self._run_healthcheck(
            {
                "PMRF_HEALTHCHECK_URL": "http://local/api/health",
                "PMRF_DEADMAN_URL": "https://deadman.example/ping",
                "PMRF_HEALTHCHECK_TIMEOUT_SECONDS": "2",
            },
            fetch=fetch,
        )

        self.assertEqual(code, 0)
        self.assertEqual(
            calls,
            [
                ("http://local/api/health", 2.0),
                ("https://deadman.example/ping", 2.0),
            ],
        )

    def test_healthcheck_does_not_ping_deadman_when_health_is_degraded(self):
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            return 200, b'{"status":"degraded"}'

        code = self._run_healthcheck(
            {
                "PMRF_HEALTHCHECK_URL": "http://local/api/health",
                "PMRF_DEADMAN_URL": "https://deadman.example/ping",
            },
            fetch=fetch,
        )

        self.assertEqual(code, 1)
        self.assertEqual(calls, ["http://local/api/health"])

    def test_healthcheck_skips_deadman_when_not_configured(self):
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            return 200, b'{"status":"ok"}'

        code = self._run_healthcheck(
            {"PMRF_HEALTHCHECK_URL": "http://local/api/health"},
            fetch=fetch,
        )

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["http://local/api/health"])


class BackupTests(unittest.TestCase):
    def test_backup_includes_existing_runtime_stores(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            event_store = base / "event_store.json"
            event_audit = base / "event_audit.jsonl"
            loop_db = base / "v2_loop.db"
            event_store.write_text("{}", encoding="utf-8")
            event_audit.write_text("", encoding="utf-8")
            loop_db.write_text("sqlite", encoding="utf-8")

            with patch.object(settings, "EVENT_STORE_FILE", str(event_store)), \
                    patch.object(settings, "EVENT_AUDIT_FILE", str(event_audit)), \
                    patch.object(settings, "EVENT_CACHE_FILE", str(base / "missing.json")), \
                    patch.object(settings, "LOOP_DB_FILE", str(loop_db)):
                archive = backup_stores.create_backup(str(base / "backups"))

            self.assertTrue(archive.exists())
            names = set(__import__("zipfile").ZipFile(archive).namelist())

        self.assertIn("event_store.json", names)
        self.assertIn("event_audit.jsonl", names)
        self.assertIn("v2_loop.db", names)

    def test_backup_prunes_old_archives_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            backups = base / "backups"
            backups.mkdir()
            event_store = base / "event_store.json"
            event_store.write_text("{}", encoding="utf-8")
            old_names = [
                "pmrf-backup-20260101-000000Z.zip",
                "pmrf-backup-20260102-000000Z.zip",
                "pmrf-backup-20260103-000000Z.zip",
            ]
            for name in old_names:
                (backups / name).write_text("old", encoding="utf-8")
            (backups / "manual-note.txt").write_text("keep me", encoding="utf-8")

            with patch.object(settings, "EVENT_STORE_FILE", str(event_store)), \
                    patch.object(settings, "EVENT_AUDIT_FILE", str(base / "missing.jsonl")), \
                    patch.object(settings, "EVENT_CACHE_FILE", str(base / "missing.json")), \
                    patch.object(settings, "LOOP_DB_FILE", str(base / "missing.db")):
                archive = backup_stores.create_backup(str(backups), keep=2)

            remaining = {path.name for path in backups.iterdir()}

        self.assertIn(archive.name, remaining)
        self.assertIn("pmrf-backup-20260103-000000Z.zip", remaining)
        self.assertNotIn("pmrf-backup-20260101-000000Z.zip", remaining)
        self.assertNotIn("pmrf-backup-20260102-000000Z.zip", remaining)
        self.assertIn("manual-note.txt", remaining)


if __name__ == "__main__":
    unittest.main()
