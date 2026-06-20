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


class HealthTests(unittest.TestCase):
    def test_api_health_returns_standard_status_payload(self):
        from app.main import app

        client = TestClient(app)
        resp = client.get("/api/health")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn(body["status"], {"ok", "degraded"})
        self.assertIn("scheduler_running", body)
        self.assertIn("loop", body)


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


if __name__ == "__main__":
    unittest.main()
