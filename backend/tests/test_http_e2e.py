"""HTTP-level analyze -> resolve flow.

This covers the full FastAPI route wiring and real persistence/audit/resolve
services while keeping external LLM/network calls mocked.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import events as events_routes
from app.core.config import settings
from app.memory import event_store as store
from app.services import event_audit_service as audit
from app.utils import sqlite_db
import app.services.ai_analysis_service as ai


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(events_routes.router, prefix="/events")
    return TestClient(app)


class AnalyzeResolveHttpE2ETests(unittest.TestCase):
    def test_analyze_resolve_and_calibration_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch.object(store, "_store_path", return_value=str(base / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(base / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(base / "v2_loop.db")), \
                    patch.object(settings, "API_WRITE_KEY", "secret"), \
                    patch.object(ai, "_ask_ai", new=AsyncMock(side_effect=RuntimeError("no llm"))), \
                    patch("app.services.cross_validation_service.cross_validate",
                          new=AsyncMock(return_value=None)):
                client = _client()
                analyze = client.post(
                    "/events/analyze",
                    headers={"X-API-Key": "secret"},
                    json={
                        "event_question": "Will the agency approve the policy before the deadline?",
                        "baseline_probability": 50,
                        "news_context": (
                            "official source says the agency confirmed the review timeline; "
                            "independent reporting says the decision is expected before deadline"
                        ),
                    },
                )
                self.assertEqual(analyze.status_code, 200)
                event_id = analyze.json()["event_id"]

                detail_before = client.get(f"/events/{event_id}")
                self.assertEqual(detail_before.status_code, 200)
                self.assertIsNone(detail_before.json()["record"].get("outcome"))

                resolved = client.post(
                    f"/events/{event_id}/resolve",
                    headers={"X-API-Key": "secret"},
                    json={
                        "actual_outcome": 100,
                        "confidence": 1,
                        "notes": "E2E settled yes",
                    },
                )
                self.assertEqual(resolved.status_code, 200)
                resolved_record = resolved.json()["record"]
                self.assertEqual(resolved_record["outcome"]["status"], "resolved")
                self.assertEqual(resolved_record["outcome"]["actual_outcome"], 100.0)
                self.assertIn("calibration", resolved_record)

                calibration = client.get("/events/calibration")
                self.assertEqual(calibration.status_code, 200)
                self.assertEqual(calibration.json()["overall"]["n"], 1)


if __name__ == "__main__":
    unittest.main()
