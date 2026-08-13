"""HTTP-level tests for the review queue routes.

The store itself is covered by test_review_queue_store.py; these tests pin the
route contract: status filtering, 404s, write-key enforcement on the action
endpoint, and that a resolved action lands in the audit log.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.api.routes import review_queue as review_queue_routes
from app.core.config import settings
from app.memory import review_queue_store as rq
from app.utils import sqlite_db

AUTH_HEADERS = {"X-API-Key": "secret"}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(review_queue_routes.router)
    return TestClient(app)


def _db(tmp):
    """Point the store at a throwaway DB, matching test_review_queue_store."""
    return patch.object(
        sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")
    )


def _enqueue(**overrides) -> str:
    payload = {
        "event_id": "evt-001",
        "trigger": "high_value_downgraded",
        "severity": "WARN",
        "reason": "高价值事件被降级为 WAIT",
        "context": {"final_direction": "WAIT"},
    }
    payload.update(overrides)
    return rq.enqueue_item(**payload)


class TestReviewQueueRoutes(unittest.TestCase):
    def test_list_pending_is_empty_on_fresh_db(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            resp = _client().get("/review-queue")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"items": [], "count": 0, "status": "pending"})

    def test_list_pending_returns_enqueued_item(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            item_id = _enqueue()
            body = _client().get("/review-queue").json()
            self.assertEqual(body["count"], 1)
            self.assertEqual(body["items"][0]["item_id"], item_id)
            self.assertEqual(body["items"][0]["status"], "pending")

    def test_list_filters_by_trigger(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            _enqueue(trigger="high_value_downgraded")
            _enqueue(event_id="evt-002", trigger="source_market_conflict",
                     reason="来源与市场判断冲突")
            body = _client().get(
                "/review-queue", params={"trigger": "source_market_conflict"}
            ).json()
            self.assertEqual(body["count"], 1)
            self.assertEqual(body["items"][0]["trigger"], "source_market_conflict")

    def test_get_item_404_for_unknown_id(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            resp = _client().get("/review-queue/does-not-exist")
            self.assertEqual(resp.status_code, 404)

    def test_audit_404_for_unknown_id(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            resp = _client().get("/review-queue/does-not-exist/audit")
            self.assertEqual(resp.status_code, 404)

    def test_action_requires_write_key(self):
        """Without a key the action endpoint must not mutate the queue.

        ALLOW_OPEN_WRITES is forced false: the audit log is the accountability
        record, so an unauthenticated caller must not be able to append to it.
        """
        with tempfile.TemporaryDirectory() as tmp, _db(tmp), \
                patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch.object(settings, "ALLOW_OPEN_WRITES", False):
            item_id = _enqueue()
            resp = _client().post(
                f"/review-queue/{item_id}/action",
                json={"reviewer": "alice", "action": "confirm"},
            )
            self.assertEqual(resp.status_code, 401)
            self.assertEqual(rq.get_item(item_id)["status"], "pending")

    def test_action_resolves_item_and_writes_audit(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            item_id = _enqueue()
            resp = _client().post(
                f"/review-queue/{item_id}/action",
                json={"reviewer": "alice", "action": "confirm", "note": "已核对来源"},
                headers=AUTH_HEADERS,
            )
            self.assertEqual(resp.status_code, 200)
            item = resp.json()["item"]
            self.assertEqual(item["status"], "resolved")
            self.assertEqual(item["reviewer"], "alice")
            self.assertEqual(item["reviewer_decision"], "confirm")

            audit = _client().get(f"/review-queue/{item_id}/audit").json()
            self.assertEqual(audit["count"], 1)
            self.assertEqual(audit["audit"][0]["action"], "confirm")
            self.assertEqual(audit["audit"][0]["reviewer"], "alice")

    def test_resolved_item_appears_under_resolved_status(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            item_id = _enqueue()
            _client().post(
                f"/review-queue/{item_id}/action",
                json={"reviewer": "alice", "action": "override"},
                headers=AUTH_HEADERS,
            )
            client = _client()
            self.assertEqual(client.get("/review-queue").json()["count"], 0)
            resolved = client.get(
                "/review-queue", params={"status": "resolved"}
            ).json()
            self.assertEqual(resolved["count"], 1)
            self.assertEqual(resolved["items"][0]["item_id"], item_id)

    def test_action_404_for_unknown_item(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = _client().post(
                "/review-queue/does-not-exist/action",
                json={"reviewer": "alice", "action": "confirm"},
                headers=AUTH_HEADERS,
            )
            self.assertEqual(resp.status_code, 404)

    def test_invalid_action_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            item_id = _enqueue()
            resp = _client().post(
                f"/review-queue/{item_id}/action",
                json={"reviewer": "alice", "action": "delete_everything"},
                headers=AUTH_HEADERS,
            )
            self.assertEqual(resp.status_code, 422)
            self.assertEqual(rq.get_item(item_id)["status"], "pending")

    def test_banned_vocabulary_in_note_is_rejected(self):
        """The store's vocabulary lock must surface as a 400, not a 500."""
        with tempfile.TemporaryDirectory() as tmp, _db(tmp), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            item_id = _enqueue()
            resp = _client().post(
                f"/review-queue/{item_id}/action",
                json={"reviewer": "alice", "action": "confirm",
                      "note": "open a long position"},
                headers=AUTH_HEADERS,
            )
            self.assertEqual(resp.status_code, 400)
            self.assertEqual(rq.get_item(item_id)["status"], "pending")


class TestReviewQueueRouterWiring(unittest.TestCase):
    def test_review_queue_is_mounted_on_the_api_router(self):
        """A route file nobody includes is the bug this whole change fixes."""
        from app.api.router import api_router

        paths = {route.path for route in api_router.routes}
        self.assertIn("/review-queue", paths)
        self.assertIn("/review-queue/{item_id}/action", paths)


if __name__ == "__main__":
    unittest.main()
