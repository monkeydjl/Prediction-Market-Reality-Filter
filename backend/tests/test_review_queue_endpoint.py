"""HTTP-level tests for the review queue routes.

The store itself is covered by test_review_queue_store.py; these tests pin the
route contract: status filtering, 404s, write-key enforcement on the action
endpoint, and that a resolved action lands in the audit log.

Q7 additions: the list response reports ``total``/``truncated`` and hands back
oldest-first pending items carrying ``age_hours``/``severity_rank``, and
``GET /review-queue/sla`` reports depth, oldest wait and breach counts. The
``/sla`` route has to be declared before ``/{item_id}`` or it is shadowed and
answers 404 — `TestReviewQueueSlaEndpoint` pins both the behaviour and the
declaration order.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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


def _backdate(item_id: str, hours: float) -> None:
    """Push an item's ``created_at`` into the past.

    Back-dating with SQL instead of sleeping keeps the age assertions on real
    hour arithmetic; matches the helper in test_review_queue_store.
    """
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    with sqlite_db.writing(sqlite_db.loop_db_path()) as conn:
        conn.execute(
            "UPDATE review_queue_items SET created_at = ? WHERE item_id = ?",
            (stamp, item_id),
        )


def _aged_queue(*specs) -> list[str]:
    """Enqueue ``(event_id, severity, age_hours)`` triples; return the ids."""
    ids = []
    for index, (event_id, severity, age) in enumerate(specs):
        item_id = _enqueue(event_id=event_id, trigger=f"t{index}",
                           severity=severity, reason=f"r{index}", context={})
        _backdate(item_id, age)
        ids.append(item_id)
    return ids


class TestReviewQueueRoutes(unittest.TestCase):
    def test_list_pending_is_empty_on_fresh_db(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            resp = _client().get("/review-queue")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {
                "items": [], "count": 0, "total": 0,
                "truncated": False, "status": "pending",
            })

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


class TestReviewQueueSlaEndpoint(unittest.TestCase):
    """Q7: the age/SLA surface the route did not have."""

    def test_sla_returns_depth_oldest_and_breaches(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp), \
                patch.object(settings, "REVIEW_QUEUE_SLA_ERROR_HOURS", 24.0), \
                patch.object(settings, "REVIEW_QUEUE_SLA_WARN_HOURS", 72.0):
            _aged_queue(("evt-a", "ERROR", 30.0), ("evt-b", "WARN", 5.0))
            resp = _client().get("/review-queue/sla")
            self.assertEqual(resp.status_code, 200)
            sla = resp.json()["sla"]
            self.assertEqual(sla["pending_total"], 2)
            self.assertEqual(sla["breached_total"], 1)
            self.assertAlmostEqual(sla["oldest_age_hours"], 30.0, delta=0.05)
            self.assertEqual(sla["by_severity"]["ERROR"]["breached"], 1)
            self.assertEqual(sla["by_severity"]["WARN"]["breached"], 0)

    def test_sla_is_not_shadowed_by_the_item_route(self):
        """``/{item_id}`` matches the literal ``sla`` too.

        Declared after it, this endpoint would answer 404 "Review item not
        found" for every caller — mounted, documented, and permanently dead.
        """
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            resp = _client().get("/review-queue/sla")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("sla", resp.json())
            self.assertNotIn("item", resp.json())

    def test_sla_route_is_declared_before_the_item_route(self):
        """The structural half of the test above: FastAPI matches in declaration
        order, so moving ``/sla`` below ``/{item_id}`` breaks it silently."""
        paths = [route.path for route in review_queue_routes.router.routes]
        self.assertLess(paths.index("/review-queue/sla"),
                        paths.index("/review-queue/{item_id}"))

    def test_sla_uses_the_configured_budgets(self):
        """Budgets come from settings, not the store's defaults."""
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            _aged_queue(("evt-a", "ERROR", 10.0))
            with patch.object(settings, "REVIEW_QUEUE_SLA_ERROR_HOURS", 1.0), \
                    patch.object(settings, "REVIEW_QUEUE_SLA_WARN_HOURS", 2.0):
                tight = _client().get("/review-queue/sla").json()["sla"]
            with patch.object(settings, "REVIEW_QUEUE_SLA_ERROR_HOURS", 100.0), \
                    patch.object(settings, "REVIEW_QUEUE_SLA_WARN_HOURS", 200.0):
                loose = _client().get("/review-queue/sla").json()["sla"]
            self.assertEqual(tight["breached_total"], 1)
            self.assertEqual(tight["sla_hours"], {"ERROR": 1.0, "WARN": 2.0})
            self.assertEqual(loose["breached_total"], 0)
            self.assertEqual(loose["sla_hours"], {"ERROR": 100.0, "WARN": 200.0})

    def test_sla_exposes_no_event_text(self):
        """It sits next to /api/health, so it reports counts — not reasons."""
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            _enqueue(reason="高价值事件被降级为 WAIT",
                     context={"secret_note": "含事件内文"})
            body = _client().get("/review-queue/sla").text
            self.assertNotIn("高价值事件", body)
            self.assertNotIn("secret_note", body)

    def test_pending_items_carry_age_and_severity_rank(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            _aged_queue(("evt-a", "ERROR", 6.0))
            item = _client().get("/review-queue").json()["items"][0]
            self.assertAlmostEqual(item["age_hours"], 6.0, delta=0.05)
            self.assertEqual(item["severity_rank"], rq.SEVERITY_RANK["ERROR"])

    def test_pending_is_oldest_first_through_the_route(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            newest, oldest = _aged_queue(("evt-new", "WARN", 1.0),
                                         ("evt-old", "ERROR", 90.0))
            ids = [it["item_id"]
                   for it in _client().get("/review-queue").json()["items"]]
            self.assertEqual(ids, [oldest, newest])

    def test_truncation_drops_the_freshest_not_the_oldest(self):
        """The defect this ordering fixes: ``items[:limit]`` used to cut the
        longest-waiting item first, so a full queue hid exactly what was about
        to breach."""
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            ids = _aged_queue(*[(f"evt-{i}", "WARN", float(i))
                                for i in range(1, 6)])
            body = _client().get("/review-queue", params={"limit": 2}).json()
            self.assertEqual(body["total"], 5)
            self.assertEqual(body["count"], 2)
            self.assertTrue(body["truncated"])
            self.assertEqual([it["item_id"] for it in body["items"]],
                             [ids[4], ids[3]])

    def test_untruncated_response_reports_the_full_total(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            _aged_queue(("evt-a", "WARN", 1.0), ("evt-b", "WARN", 2.0))
            body = _client().get("/review-queue").json()
            self.assertEqual((body["total"], body["count"]), (2, 2))
            self.assertFalse(body["truncated"])


class TestReviewQueueRouterWiring(unittest.TestCase):
    def test_review_queue_is_mounted_on_the_api_router(self):
        """A route file nobody includes is the bug this whole change fixes."""
        from app.api.router import api_router

        paths = {route.path for route in api_router.routes}
        self.assertIn("/review-queue", paths)
        self.assertIn("/review-queue/sla", paths)
        self.assertIn("/review-queue/{item_id}/action", paths)


if __name__ == "__main__":
    unittest.main()
