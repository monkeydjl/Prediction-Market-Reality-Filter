"""HTTP tests for review queue API endpoints."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.memory import review_queue_store as rq
from app.utils import sqlite_db


def _db(tmp: str):
    return patch.object(
        sqlite_db,
        "loop_db_path",
        return_value=str(Path(tmp) / "v2_loop.db"),
    )


class TestReviewQueueEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_patch = _db(self._tmp.name)
        self._db_patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._db_patch.stop()
        self._tmp.cleanup()

    def _enqueue(self, event_id: str = "evt-001", trigger: str = "audit_inconsistency") -> str:
        return rq.enqueue_item(
            event_id=event_id,
            trigger=trigger,
            severity="ERROR",
            reason="字段一致性审计发现冲突",
            context={"conflict_type": "outcome_mismatch"},
        )

    def test_list_pending_items(self):
        item_id = self._enqueue()

        response = self.client.get("/api/review-queue")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["items"][0]["item_id"], item_id)
        self.assertEqual(data["items"][0]["event_id"], "evt-001")

    def test_list_resolved_items(self):
        item_id = self._enqueue()
        rq.take_action(item_id=item_id, reviewer="alice", action="confirm", note="ok")

        response = self.client.get("/api/review-queue?status=resolved")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["items"][0]["status"], "resolved")

    def test_get_item_and_audit_log(self):
        item_id = self._enqueue()
        rq.take_action(
            item_id=item_id,
            reviewer="alice",
            action="request_more_evidence",
            note="need source detail",
        )

        item_response = self.client.get(f"/api/review-queue/{item_id}")
        audit_response = self.client.get(f"/api/review-queue/{item_id}/audit")

        self.assertEqual(item_response.status_code, 200)
        self.assertEqual(item_response.json()["item"]["item_id"], item_id)
        self.assertEqual(audit_response.status_code, 200)
        self.assertEqual(audit_response.json()["count"], 1)
        self.assertEqual(audit_response.json()["audit"][0]["action"], "request_more_evidence")

    def test_take_action_requires_write_key_and_resolves_item(self):
        item_id = self._enqueue()
        with patch.object(settings, "API_WRITE_KEY", "secret"):
            unauthorized = self.client.post(
                f"/api/review-queue/{item_id}/action",
                json={"reviewer": "alice", "action": "confirm", "note": "ok"},
            )
            authorized = self.client.post(
                f"/api/review-queue/{item_id}/action",
                headers={"X-API-Key": "secret"},
                json={"reviewer": "alice", "action": "confirm", "note": "ok"},
            )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(authorized.json()["item"]["status"], "resolved")
        self.assertEqual(authorized.json()["item"]["reviewer_decision"], "confirm")

    def test_invalid_status_rejected(self):
        response = self.client.get("/api/review-queue?status=bad")

        self.assertEqual(response.status_code, 422)

    def test_missing_item_returns_404(self):
        response = self.client.get("/api/review-queue/not-found")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
