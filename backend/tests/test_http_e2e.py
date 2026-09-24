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

    def test_challenge_failure_hides_exception_in_response_and_store(self):
        sensitive = (
            "Authorization=Bearer fake-api-key ticket=fake-ticket "
            "C:/private/secret.db"
        )
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch.object(store, "_store_path", return_value=str(base / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(base / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(base / "v2_loop.db")), \
                    patch.object(settings, "API_WRITE_KEY", "secret"), \
                    patch.object(settings, "CONCLUSION_CHALLENGE_ENABLED", True), \
                    patch.object(settings, "EVENT_CHALLENGE_ENABLED", True), \
                    patch.object(settings, "CONCLUSION_CHALLENGE_LLM_CRITIC_ENABLED", False), \
                    patch.object(settings, "CONCLUSION_CHALLENGE_STRICTNESS", "normal"), \
                    patch.object(ai, "_ask_ai", new=AsyncMock(side_effect=RuntimeError("no llm"))), \
                    patch("app.services.cross_validation_service.cross_validate",
                          new=AsyncMock(return_value=None)), \
                    patch(
                        "app.services.conclusion_challenge_service.challenge_conclusion",
                        side_effect=RuntimeError(sensitive),
                    ):
                client = _client()
                response = client.post(
                    "/events/analyze",
                    headers={"X-API-Key": "secret"},
                    json={
                        "event_question": (
                            "Will the agency approve the policy before the deadline?"
                        ),
                        "baseline_probability": 50,
                        "news_context": (
                            "Official sources confirmed the review timeline; "
                            "independent reporting expects a decision before deadline."
                        ),
                    },
                )

                self.assertEqual(response.status_code, 200)
                body = response.json()
                stored = store.get_event(body["event_id"])

            self.assertIsNotNone(stored)
            durable_record = stored["record"]
            for record in (body, durable_record):
                challenge = record["conclusion_challenge"]
                self.assertEqual(challenge["verdict"], "pass_with_warnings")
                self.assertEqual(challenge["required_action"], "allow_output")
                self.assertEqual(
                    challenge["warnings"][0]["details"],
                    {"error": "RuntimeError"},
                )
            for fragment in (
                "Authorization",
                "fake-api-key",
                "fake-ticket",
                "C:/private",
                "secret.db",
                "Traceback",
            ):
                self.assertNotIn(fragment, response.text)
                self.assertNotIn(fragment, repr(durable_record))


if __name__ == "__main__":
    unittest.main()
