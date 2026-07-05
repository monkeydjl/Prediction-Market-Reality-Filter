import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import events as events_routes
from app.core.config import settings
from app.memory import event_store as store
from app.services import event_audit_service as audit
from app.utils import sqlite_db


AUTH_HEADERS = {"X-API-Key": "secret"}


def _make_record(event_id="evt1", value_score=50, estimated=60.0):
    """Minimal but complete event record matching the EventRecord contract."""
    return {
        "event_id": event_id,
        "event_title": "Will X happen?",
        "event_summary": "summary",
        "probability": {
            "baseline": 50.0,
            "estimated": estimated,
            "change": round(estimated - 50.0, 2),
            "direction": "rising",
        },
        "credibility": {
            "score": 60,
            "level": "MEDIUM",
            "confidence": 0.6,
            "news_quality": 0.5,
            "evidence_strength": 0.4,
            "source_count": 3,
        },
        "impact": {"score": 55, "level": "MEDIUM", "drivers": ["strong_evidence"]},
        "risk": {"level": "LOW", "flags": []},
        "evidence": {
            "direction": "supports",
            "strength": 0.4,
            "conflict": 0.1,
            "freshness": 0.7,
            "resolution_relevance": 0.5,
        },
        "source": {"type": "manual"},
        "value_score": value_score,
        "intelligence_report": {
            "headline": "h",
            "why_it_matters": "w",
            "probability_assessment": "p",
            "recommended_action": "a",
        },
    }


class EventStoreTests(unittest.TestCase):
    def test_save_and_get_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_event(_make_record("evtA", value_score=10))
                entry = store.get_event("evtA")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["event_id"], "evtA")
        self.assertEqual(entry["record"]["value_score"], 10)
        self.assertIn("first_seen", entry)
        self.assertIn("last_updated", entry)

    def test_upsert_keeps_single_entry_and_first_seen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                first = store.save_event(_make_record("evtB", estimated=55.0))
                second = store.save_event(_make_record("evtB", estimated=70.0))
                listed = store.list_events()
        self.assertEqual(len(listed), 1)
        self.assertEqual(first["first_seen"], second["first_seen"])
        self.assertEqual(listed[0]["record"]["probability"]["estimated"], 70.0)

    def test_list_events_sorted_by_value_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_events([
                    _make_record("low", value_score=10),
                    _make_record("high", value_score=90),
                    _make_record("mid", value_score=50),
                ])
                listed = store.list_events(limit=2)
        self.assertEqual([e["event_id"] for e in listed], ["high", "mid"])

    def test_list_events_supports_offset_after_sorting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_events([
                    _make_record("low", value_score=10),
                    _make_record("high", value_score=90),
                    _make_record("mid", value_score=50),
                ])
                listed = store.list_events(limit=2, offset=1)
        self.assertEqual([e["event_id"] for e in listed], ["mid", "low"])

    def test_list_events_resolved_only_filters_before_pagination(self):
        outcome = {
            "status": "resolved",
            "actual_outcome": 100.0,
            "confidence": 1.0,
            "resolved_at": "2026-07-05T00:00:00+00:00",
            "source": "manual",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                records = []
                for index in range(15):
                    records.append(_make_record(f"resolved-{index:02d}", value_score=100 - index))
                for index in range(5):
                    records.append(_make_record(f"open-{index:02d}", value_score=200 - index))
                store.save_events(records)
                for index in range(15):
                    store.resolve_event(f"resolved-{index:02d}", outcome)

                first_page = store.list_events(limit=10, offset=0, resolved_only=True, exclude_expired=False)
                second_page = store.list_events(limit=10, offset=10, resolved_only=True, exclude_expired=False)
                total = store.count_events(resolved_only=True, exclude_expired=False)

        self.assertEqual(len(first_page), 10)
        self.assertEqual(len(second_page), 5)
        self.assertEqual(total, 15)
        self.assertTrue(all((entry["record"].get("outcome") or {}).get("actual_outcome") is not None for entry in first_page))
        self.assertNotIn("open-00", [entry["event_id"] for entry in first_page])

    def test_list_events_filters_and_counts_same_scope(self):
        fed = _make_record("fed", value_score=70, estimated=65)
        fed["event_title"] = "Federal Reserve rate cut"
        fed["legacy_analysis"] = {"base_rate_category": "monetary"}
        fed["tracking"] = {"status": "tracking", "priority": "high"}
        eth = _make_record("eth", value_score=40, estimated=85)
        eth["event_title"] = "Ethereum ETF approval"
        eth["legacy_analysis"] = {"base_rate_category": "crypto"}
        eth["tracking"] = {"status": "watching", "priority": "medium"}
        old = _make_record("old", value_score=95, estimated=90)
        old["event_title"] = "Archived crypto item"
        old["legacy_analysis"] = {"base_rate_category": "crypto"}
        old["tracking"] = {"status": "archived", "priority": "low"}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_events([fed, eth, old])
                listed = store.list_events(
                    query="ethereum",
                    status="watching",
                    category="crypto",
                    sort="probability",
                )
                count = store.count_events(
                    query="ethereum",
                    status="watching",
                    category="crypto",
                    sort="probability",
                )
        self.assertEqual([e["event_id"] for e in listed], ["eth"])
        self.assertEqual(count, 1)

    def test_sports_events_filter_by_source_type_over_base_rate_category(self):
        sports = _make_record("world-cup", value_score=70, estimated=62)
        sports["event_title"] = "Will Brazil reach the World Cup semifinals?"
        sports["source"] = {"type": "sports_event", "platform": "world_cup_2026"}
        sports["legacy_analysis"] = {"base_rate_category": "geopolitics"}
        other = _make_record("geopolitics", value_score=60, estimated=58)
        other["event_title"] = "Will a policy meeting happen?"
        other["source"] = {"type": "manual"}
        other["legacy_analysis"] = {"base_rate_category": "geopolitics"}

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_events([sports, other])
                listed = store.list_events(category="sports_event")
                count = store.count_events(category="sports_event")

        self.assertEqual([e["event_id"] for e in listed], ["world-cup"])
        self.assertEqual(count, 1)

    def test_save_event_rejects_missing_event_id(self):
        bad = _make_record()
        del bad["event_id"]
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                with self.assertRaises(Exception):
                    store.save_event(bad)

    def test_save_events_skips_invalid_record_in_batch(self):
        bad = _make_record()
        del bad["event_id"]
        good = _make_record("evtGood", value_score=80)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                with self.assertLogs("app.memory.event_store", level="WARNING"):
                    saved = store.save_events([bad, good])
                listed = store.list_events()

        self.assertEqual([entry["event_id"] for entry in saved], ["evtGood"])
        self.assertEqual([entry["event_id"] for entry in listed], ["evtGood"])

    def test_resolve_event_attaches_outcome_and_preserves_first_seen(self):
        outcome = {
            "status": "resolved",
            "actual_outcome": 100.0,
            "confidence": 1.0,
            "resolved_at": "2026-06-14T00:00:00+00:00",
            "source": "manual",
            "notes": "court ruling",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                first = store.save_event(_make_record("evtR", value_score=30))
                updated = store.resolve_event("evtR", outcome)
                after = store.get_event("evtR")
        self.assertIsNotNone(updated)
        self.assertEqual(updated["first_seen"], first["first_seen"])
        self.assertEqual(updated["record"]["outcome"]["actual_outcome"], 100.0)
        self.assertEqual(updated["record"]["outcome"]["source"], "manual")
        self.assertEqual(after["record"]["outcome"]["status"], "resolved")

    def test_set_tracking_updates_and_preserves_first_seen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                first = store.save_event(_make_record("evtT", value_score=30))
                updated = store.set_tracking("evtT", status="tracking", priority="high")
                after = store.get_event("evtT")
        self.assertIsNotNone(updated)
        self.assertEqual(updated["first_seen"], first["first_seen"])
        self.assertEqual(after["record"]["tracking"]["status"], "tracking")
        self.assertEqual(after["record"]["tracking"]["priority"], "high")

    def test_set_tracking_returns_none_for_unknown_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                self.assertIsNone(store.set_tracking("nope", status="tracking"))

    def test_save_events_preserves_user_tracking_across_rescan(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_event(_make_record("evtK", value_score=30))
                store.set_tracking("evtK", status="archived", priority="low")
                # A re-scan re-saves the record with default tracking ...
                rescan = _make_record("evtK", value_score=40)
                rescan["tracking"] = {"status": "watching", "priority": "medium"}
                store.save_event(rescan)
                after = store.get_event("evtK")
        # ... but the user's earlier decision survives the upsert.
        self.assertEqual(after["record"]["tracking"]["status"], "archived")
        self.assertEqual(after["record"]["tracking"]["priority"], "low")
        self.assertEqual(after["record"]["value_score"], 40)

    def test_rescan_does_not_revert_resolved_outcome(self):
        # A resolved event re-discovered by the same event_id (the discovery
        # record carries no outcome/calibration) must NOT be reverted to
        # unresolved - the resolution result is preserved across the upsert.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_event(_make_record("evtRes", value_score=30))
                store.resolve_event(
                    "evtRes",
                    {"status": "resolved", "actual_outcome": 100.0, "confidence": 1.0,
                     "resolved_at": "t", "source": "auto_market"},
                    calibration={"brier_score": 0.04, "skill_score": 0.84,
                                 "grade": "EXCELLENT", "estimated_probability": 80.0,
                                 "actual_outcome": 100.0, "trajectory_observations": 2},
                )
                # A fresh discovery pass re-saves the record WITHOUT outcome/calibration.
                store.save_event(_make_record("evtRes", value_score=40))
                after = store.get_event("evtRes")
                resolved_ids = [e["event_id"] for e in store.list_resolved_events()]
        self.assertIsNotNone(after["record"].get("outcome"))          # not reverted
        self.assertEqual(after["record"]["outcome"]["status"], "resolved")
        self.assertEqual(after["record"]["outcome"]["actual_outcome"], 100.0)
        self.assertIsNotNone(after["record"].get("calibration"))      # sample kept
        self.assertIn("evtRes", resolved_ids)                          # still resolved
        self.assertEqual(after["record"]["value_score"], 40)          # other fields refresh

    def test_resolve_event_returns_none_for_unknown_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                result = store.resolve_event("does-not-exist", {
                    "status": "resolved", "actual_outcome": 0.0,
                    "confidence": 1.0, "resolved_at": "t", "source": "manual",
                })
        self.assertIsNone(result)

    def test_resolve_event_validates_outcome(self):
        """A malformed outcome must raise rather than corrupt the store."""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_event(_make_record("evtBad", value_score=30))
                with self.assertRaises(Exception):
                    # actual_outcome out of range (model validation via record)
                    store.resolve_event("evtBad", {
                        "status": "resolved", "actual_outcome": "not-a-number",
                        "confidence": 1.0, "resolved_at": "t", "source": "manual",
                    })
                # Store is unchanged: no outcome on the record.
                self.assertIsNone(store.get_event("evtBad")["record"].get("outcome"))

    def test_resolve_event_attaches_calibration(self):
        outcome = {
            "status": "resolved", "actual_outcome": 100.0,
            "confidence": 1.0, "resolved_at": "t", "source": "manual",
        }
        calibration = {
            "brier_score": 0.09, "skill_score": 0.64, "grade": "GOOD",
            "estimated_probability": 70.0, "actual_outcome": 100.0,
            "trajectory_observations": 3, "trajectory_span_hours": 12.0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_event(_make_record("evtC", value_score=30))
                updated = store.resolve_event("evtC", outcome, calibration=calibration)
                after = store.get_event("evtC")
        self.assertEqual(updated["record"]["calibration"]["brier_score"], 0.09)
        self.assertEqual(updated["record"]["calibration"]["grade"], "GOOD")
        self.assertEqual(after["record"]["calibration"]["estimated_probability"], 70.0)

    def test_list_resolved_events_returns_only_resolved(self):
        outcome = {
            "status": "resolved", "actual_outcome": 0.0,
            "confidence": 1.0, "resolved_at": "t", "source": "manual",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_event(_make_record("resolved1", value_score=10))
                store.save_event(_make_record("resolved2", value_score=90))
                store.save_event(_make_record("unresolved", value_score=50))
                store.resolve_event("resolved1", outcome)
                store.resolve_event("resolved2", outcome)
                resolved = store.list_resolved_events()
        resolved_ids = {e["event_id"] for e in resolved}
        self.assertEqual(resolved_ids, {"resolved1", "resolved2"})
        self.assertNotIn("unresolved", resolved_ids)


class EventAuditServiceTests(unittest.TestCase):
    def test_record_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path):
                audit.record_event(_make_record("evtZ", estimated=72.0))
                audit.record_event(_make_record("evtZ", estimated=80.0))
                lines = audit.load_recent_events()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["event_id"], "evtZ")
        self.assertEqual(lines[-1]["estimated"], 80.0)

    def test_record_outcome_appends_kind_outcome_snapshot(self):
        outcome = {
            "status": "resolved",
            "actual_outcome": 100.0,
            "confidence": 1.0,
            "source": "manual",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path):
                audit.record_outcome("evtO", "Will X happen?", outcome)
                lines = audit.load_recent_events()
        self.assertEqual(len(lines), 1)
        snap = lines[0]
        self.assertEqual(snap["kind"], "outcome")
        self.assertEqual(snap["event_id"], "evtO")
        # estimated is intentionally None so analyze_trend skips it.
        self.assertIsNone(snap["estimated"])
        self.assertEqual(snap["outcome"]["actual_outcome"], 100.0)

    def test_compaction_preserves_outcome_separately_from_probabilities(self):
        """Outcome snapshots must not consume the probability budget nor be
        crowded out by probabilities."""
        outcome = {"status": "resolved", "actual_outcome": 0.0,
                   "confidence": 1.0, "source": "manual"}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path), \
                    patch.object(audit.settings, "EVENT_AUDIT_COMPACTION_THRESHOLD", 3), \
                    patch.object(audit.settings, "EVENT_AUDIT_MAX_PER_EVENT", 2):
                for i in range(5):
                    audit.record_event(_make_record("evtC", estimated=10.0 + i))
                audit.record_outcome("evtC", "Will X happen?", outcome)
                lines = audit.load_recent_events(limit=10_000)
        probabilities = [s for s in lines if s.get("kind") != "outcome"]
        outcomes = [s for s in lines if s.get("kind") == "outcome"]
        # 2 most-recent probability snapshots survive (13.0, 14.0) ...
        self.assertEqual([s["estimated"] for s in probabilities], [13.0, 14.0])
        # ... and the single outcome snapshot survives alongside them.
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["outcome"]["actual_outcome"], 0.0)

    def test_compaction_keeps_most_recent_per_event(self):
        """When the log exceeds the threshold it compacts, keeping only the
        most recent EVENT_AUDIT_MAX_PER_EVENT snapshots per event_id."""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path), \
                    patch.object(audit.settings, "EVENT_AUDIT_COMPACTION_THRESHOLD", 3), \
                    patch.object(audit.settings, "EVENT_AUDIT_MAX_PER_EVENT", 2):
                # 10 writes total trigger several compactions; MAX_PER_EVENT=2
                # means each event ends with at most its 2 most-recent snapshots.
                for i in range(5):
                    audit.record_event(_make_record("evtA", estimated=50.0 + i))
                for i in range(5):
                    audit.record_event(_make_record("evtB", estimated=20.0 + i))
                lines = audit.load_recent_events(limit=10_000)
        by_event = {}
        for snap in lines:
            by_event.setdefault(snap["event_id"], []).append(snap["estimated"])
        self.assertEqual(by_event["evtA"], [53.0, 54.0])  # last 2 of 50..54
        self.assertEqual(by_event["evtB"], [23.0, 24.0])  # last 2 of 20..24

    def test_compaction_disabled_when_threshold_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path), \
                    patch.object(audit.settings, "EVENT_AUDIT_COMPACTION_THRESHOLD", 0):
                for i in range(20):
                    audit.record_event(_make_record("evtC", estimated=10.0 + i))
                lines = audit.load_recent_events(limit=10_000)
        # No compaction: all 20 snapshots survive.
        self.assertEqual(len(lines), 20)

    def test_compaction_preserves_oldest_to_newest_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path), \
                    patch.object(audit.settings, "EVENT_AUDIT_COMPACTION_THRESHOLD", 3), \
                    patch.object(audit.settings, "EVENT_AUDIT_MAX_PER_EVENT", 2):
                for i in range(4):
                    audit.record_event(_make_record("evtD", estimated=1.0 * i))
                lines = audit.load_recent_events(limit=10_000)
        estimates = [snap["estimated"] for snap in lines]
        self.assertEqual(estimates, [2.0, 3.0])  # ascending, last 2 of 0..3


class EventReadRouteTests(unittest.TestCase):
    def test_get_event_route_returns_record_or_404(self):
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        client = TestClient(app)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_event(_make_record("evtRoute", value_score=42))
                found = client.get("/events/evtRoute")
                missing = client.get("/events/does-not-exist")
        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.json()["event_id"], "evtRoute")
        self.assertEqual(found.json()["record"]["value_score"], 42)
        self.assertEqual(missing.status_code, 404)

    def test_tracking_route_updates_and_validates(self):
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        client = TestClient(app)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path), \
                    patch.object(settings, "API_WRITE_KEY", "secret"):
                store.save_event(_make_record("evtTrk", value_score=42))
                ok = client.patch(
                    "/events/evtTrk/tracking",
                    headers=AUTH_HEADERS,
                    json={"status": "tracking", "priority": "high"},
                )
                bad = client.patch(
                    "/events/evtTrk/tracking",
                    headers=AUTH_HEADERS,
                    json={"status": "bogus"},
                )
                missing = client.patch(
                    "/events/none/tracking",
                    headers=AUTH_HEADERS,
                    json={"status": "tracking"},
                )
                entry = client.get("/events/evtTrk")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["record"]["tracking"]["status"], "tracking")
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(entry.json()["record"]["tracking"]["priority"], "high")

    def test_list_events_route(self):
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        client = TestClient(app)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_events([
                    _make_record("e-lo", value_score=10),
                    _make_record("e-hi", value_score=90),
                ])
                resp = client.get("/events/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["events"][0]["event_id"], "e-hi")

    def test_list_events_route_resolved_only_counts_filtered_rows(self):
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        client = TestClient(app)
        outcome = {
            "status": "resolved",
            "actual_outcome": 100.0,
            "confidence": 1.0,
            "resolved_at": "2026-07-05T00:00:00+00:00",
            "source": "manual",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_event(_make_record("open-high", value_score=500))
                for index in range(12):
                    event_id = f"resolved-route-{index:02d}"
                    store.save_event(_make_record(event_id, value_score=100 - index))
                    store.resolve_event(event_id, outcome)
                resp = client.get("/events/?limit=10&offset=0&resolved_only=true&exclude_expired=false")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 10)
        self.assertEqual(body["total"], 12)
        self.assertTrue(all(e["event_id"].startswith("resolved-route-") for e in body["events"]))

    def test_event_history_route(self):
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        client = TestClient(app)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path):
                audit.record_event(_make_record("e-hist", estimated=55.0))
                audit.record_event(_make_record("e-hist", estimated=72.0))
                ok = client.get("/events/e-hist/history")
                missing = client.get("/events/none/history")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["count"], 2)
        self.assertEqual(ok.json()["history"][-1]["estimated"], 72.0)
        self.assertEqual(missing.status_code, 404)

    def test_resolve_route_attaches_outcome_and_returns_entry(self):
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        client = TestClient(app)
        with tempfile.TemporaryDirectory() as tmp:
            store_path = str(Path(tmp) / "event_store.json")
            audit_path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(store, "_store_path", return_value=store_path), \
                    patch.object(audit, "_audit_path", return_value=audit_path), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(settings, "API_WRITE_KEY", "secret"):
                store.save_event(_make_record("evtRes", value_score=42))
                resp = client.post(
                    "/events/evtRes/resolve",
                    headers=AUTH_HEADERS,
                    json={"actual_outcome": 100.0, "confidence": 0.9, "notes": "settled"},
                )
                missing = client.post(
                    "/events/unknown/resolve",
                    headers=AUTH_HEADERS,
                    json={"actual_outcome": 0.0},
                )
                # GET now reflects the resolved outcome.
                entry = client.get("/events/evtRes")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["record"]["outcome"]["actual_outcome"], 100.0)
        self.assertEqual(body["record"]["outcome"]["source"], "manual")
        self.assertEqual(body["record"]["outcome"]["confidence"], 0.9)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(entry.json()["record"]["outcome"]["status"], "resolved")

    def test_history_route_filters_outcome_snapshots(self):
        """Outcome snapshots must not appear in the probability-history view."""
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        client = TestClient(app)
        outcome = {"status": "resolved", "actual_outcome": 100.0,
                   "confidence": 1.0, "source": "manual"}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(audit, "_audit_path", return_value=path):
                audit.record_event(_make_record("e-filter", estimated=55.0))
                audit.record_outcome("e-filter", "Will X happen?", outcome)
                resp = client.get("/events/e-filter/history")
        self.assertEqual(resp.status_code, 200)
        kinds = [snap.get("kind") for snap in resp.json()["history"]]
        self.assertNotIn("outcome", kinds)
        self.assertEqual(len(resp.json()["history"]), 1)

    def test_resolve_route_attaches_calibration_scored_on_latest_estimate(self):
        """Resolve scores the latest probability estimate vs the outcome."""
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        client = TestClient(app)
        with tempfile.TemporaryDirectory() as tmp:
            store_path = str(Path(tmp) / "event_store.json")
            audit_path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(store, "_store_path", return_value=store_path), \
                    patch.object(audit, "_audit_path", return_value=audit_path), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(settings, "API_WRITE_KEY", "secret"):
                store.save_event(_make_record("evtCal", estimated=70.0, value_score=42))
                # Record a probability trajectory: latest estimate is 80%.
                audit.record_event(_make_record("evtCal", estimated=80.0))
                resp = client.post(
                    "/events/evtCal/resolve",
                    headers=AUTH_HEADERS,
                    json={"actual_outcome": 100.0},
                )
        self.assertEqual(resp.status_code, 200)
        calibration = resp.json()["record"]["calibration"]
        # Brier((80-100)/100)^2 = 0.04 -> EXCELLENT
        self.assertAlmostEqual(calibration["brier_score"], 0.04)
        self.assertEqual(calibration["grade"], "EXCELLENT")
        self.assertEqual(calibration["estimated_probability"], 80.0)
        self.assertEqual(calibration["actual_outcome"], 100.0)
        self.assertEqual(calibration["trajectory_observations"], 1)

    def test_calibration_route_aggregates_resolved_events(self):
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        client = TestClient(app)
        with tempfile.TemporaryDirectory() as tmp:
            store_path = str(Path(tmp) / "event_store.json")
            audit_path = str(Path(tmp) / "event_audit.jsonl")
            with patch.object(store, "_store_path", return_value=store_path), \
                    patch.object(audit, "_audit_path", return_value=audit_path), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(settings, "API_WRITE_KEY", "secret"):
                # Before any resolution: no_data.
                empty = client.get("/events/calibration")
                # Resolve two events from different sources and base-rate
                # categories with known scores.
                rec1 = _make_record("evtA", estimated=50.0, value_score=30)
                rec1["source"] = {"type": "prediction_market", "platform": "Polymarket"}
                rec1["legacy_analysis"] = {"base_rate_category": "fed_hike"}
                rec2 = _make_record("evtB", estimated=50.0, value_score=30)
                rec2["source"] = {"type": "prediction_market", "platform": "Manifold"}
                rec2["legacy_analysis"] = {"base_rate_category": "crypto_price_btc"}
                store.save_event(rec1)
                store.save_event(rec2)
                client.post(
                    "/events/evtA/resolve",
                    headers=AUTH_HEADERS,
                    json={"actual_outcome": 100.0},
                )
                client.post(
                    "/events/evtB/resolve",
                    headers=AUTH_HEADERS,
                    json={"actual_outcome": 0.0},
                )
                # Both estimated=50 vs outcome: brier=0.25 each.
                report = client.get("/events/calibration")
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["overall"]["n"], 0)
        self.assertEqual(empty.json()["overall"]["grade"], "no_data")
        self.assertEqual(report.status_code, 200)
        overall = report.json()["overall"]
        self.assertEqual(overall["n"], 2)
        self.assertAlmostEqual(overall["brier_score"], 0.25)
        by_source = report.json()["by_source"]
        self.assertEqual(set(by_source.keys()), {"Polymarket", "Manifold"})
        by_category = report.json()["by_base_rate_category"]
        self.assertEqual(set(by_category.keys()), {"fed_hike", "crypto_price_btc"})


class RewriteLinesAtomicTests(unittest.TestCase):
    """Regression for P0-2: jsonl rewrites (audit compaction, legacy
    resolve_by_question) must be atomic so a crash mid-write cannot truncate
    the log. rewrite_lines_atomic writes a temp file then os.replace's it."""

    def test_rewrites_file_atomically(self):
        from app.utils.file_store import rewrite_lines_atomic
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "log.jsonl")
            Path(path).write_text('{"old":1}\n{"old":2}\n', encoding="utf-8")
            rewrite_lines_atomic(path, ['{"new":1}', '{"new":2}', '{"new":3}'])
            content = Path(path).read_text(encoding="utf-8")
        self.assertEqual(
            content, '{"new":1}\n{"new":2}\n{"new":3}\n',
        )

    def test_creates_file_when_missing(self):
        from app.utils.file_store import rewrite_lines_atomic
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "fresh.jsonl")
            rewrite_lines_atomic(path, ['{"a":1}'])
            self.assertTrue(Path(path).exists())
            self.assertEqual(Path(path).read_text(encoding="utf-8"), '{"a":1}\n')

    def test_lines_without_trailing_newline_get_one(self):
        from app.utils.file_store import rewrite_lines_atomic
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "log.jsonl")
            rewrite_lines_atomic(path, ['{"a":1}', '{"b":2}\n'])
            content = Path(path).read_text(encoding="utf-8")
        # Both end with newline regardless of input.
        self.assertEqual(content, '{"a":1}\n{"b":2}\n')


if __name__ == "__main__":
    unittest.main()
