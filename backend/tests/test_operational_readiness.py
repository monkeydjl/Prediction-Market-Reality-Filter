import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import events as events_routes
from app.core.rate_limit import InMemoryRateLimitMiddleware
from app.core.config import settings
from scripts import backup_stores


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


class _FakeScheduler:
    """Stand-in for the APScheduler whose `running` is a read-only property."""

    def __init__(self, running: bool):
        self.running = running


class HealthTests(unittest.TestCase):
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
