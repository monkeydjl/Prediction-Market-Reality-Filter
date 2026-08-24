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

    def test_count_events_by_category_ignores_category_filter(self):
        fed = _make_record("fed", value_score=70, estimated=65)
        fed["legacy_analysis"] = {"base_rate_category": "monetary"}
        eth = _make_record("eth", value_score=40, estimated=85)
        eth["legacy_analysis"] = {"base_rate_category": "crypto"}
        old = _make_record("old", value_score=95, estimated=90)
        old["legacy_analysis"] = {"base_rate_category": "crypto"}
        old["tracking"] = {"status": "archived"}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_events([fed, eth, old])
                counts = store.count_events_by_category(status="active")
        self.assertEqual(counts.get("monetary"), 1)
        self.assertEqual(counts.get("crypto"), 1)
        self.assertNotIn("crypto", [c for c, n in counts.items() if n == 2])

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

    def test_generic_prediction_source_category_uses_precise_event_type(self):
        weather = _make_record("weather", value_score=70, estimated=62)
        weather["source"] = {
            "type": "prediction_market",
            "platform": "Polymarket",
            "category": "Prediction",
            "event_type": "weather_event",
        }
        weather["legacy_analysis"] = {}

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_events([weather])
                listed = store.list_events(category="weather_event")
                counts = store.count_events_by_category(status="active")

        self.assertEqual([e["event_id"] for e in listed], ["weather"])
        self.assertEqual(counts, {"weather_event": 1})

    def test_generic_legacy_prediction_category_uses_precise_event_type(self):
        policy = _make_record("policy", value_score=70, estimated=62)
        policy["source"] = {
            "type": "prediction_market",
            "platform": "Polymarket",
            "event_type": "policy_general",
        }
        policy["legacy_analysis"] = {"base_rate_category": "Prediction"}

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_events([policy])
                listed = store.list_events(category="policy_general")
                counts = store.count_events_by_category(status="active")

        self.assertEqual([e["event_id"] for e in listed], ["policy"])
        self.assertEqual(counts, {"policy_general": 1})

    def test_generic_prediction_market_without_precise_category_is_general(self):
        generic = _make_record("generic", value_score=70, estimated=62)
        generic["source"] = {
            "type": "prediction_market",
            "platform": "Polymarket",
            "category": "Prediction",
        }
        generic["legacy_analysis"] = {}

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                self.assertEqual(store._category(generic), "general")
                store.save_events([generic])
                listed = store.list_events(category="general")
                counts = store.count_events_by_category(status="active")

        self.assertEqual([e["event_id"] for e in listed], ["generic"])
        self.assertEqual(counts, {"general": 1})

    def test_generic_prediction_market_infers_category_from_title(self):
        cases = [
            (
                "boe-rates",
                "No change in Bank of England's interest rates after July 2026 meeting?",
                "monetary",
            ),
            (
                "trump-russia",
                "Will Donald Trump visit Russia in 2026?",
                "geopolitics_general",
            ),
            (
                "ufc-tko",
                "Will Conor McGregor win by KO or TKO?",
                "sports_game",
            ),
            (
                "boe-rates-zh",
                "\u82f1\u56fd\u592e\u884c\u5229\u7387\u4e0d\u53d8\uff1f",
                "monetary",
            ),
            (
                "epstein-storage",
                "Epstein storage units raided in 2026?",
                "legal",
            ),
            (
                "israel-litani",
                "Will Israeli forces withdraw from beyond the Litani River by December 31?",
                "geopolitics_general",
            ),
            (
                "lebron-cavaliers",
                "Will LeBron James play for the Cleveland Cavaliers in the 2026-27 season?",
                "sports_general",
            ),
            (
                "israel-airspace",
                "Israel closes its airspace by July 31?",
                "geopolitics_general",
            ),
            (
                "saibari-shots",
                "Ismael Saibari: 1+ shots",
                "sports_game",
            ),
            (
                "hype-hourly",
                "HYPE Up or Down - Hourly",
                "crypto",
            ),
        ]
        for event_id, title, expected in cases:
            with self.subTest(event_id=event_id):
                record = _make_record(event_id, value_score=70, estimated=62)
                record["event_title"] = title
                record["source"] = {
                    "type": "prediction_market",
                    "platform": "Polymarket",
                    "category": "Prediction",
                }
                record["legacy_analysis"] = {}

                with tempfile.TemporaryDirectory() as tmp:
                    path = str(Path(tmp) / "event_store.json")
                    with patch.object(store, "_store_path", return_value=path):
                        store.save_events([record])
                        listed = store.list_events(category=expected)
                        counts = store.count_events_by_category(status="active")

                self.assertEqual([e["event_id"] for e in listed], [event_id])
                self.assertEqual(listed[0]["category"], expected)
                self.assertEqual(counts, {expected: 1})

    def test_get_event_returns_backend_derived_category(self):
        record = _make_record("derived-category", value_score=70, estimated=62)
        record["event_title"] = "No change in Bank of England's interest rates after July 2026 meeting?"
        record["source"] = {
            "type": "prediction_market",
            "platform": "Polymarket",
            "category": "Prediction",
        }
        record["legacy_analysis"] = {}

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_event(record)
                entry = store.get_event("derived-category")

        self.assertIsNotNone(entry)
        self.assertEqual(entry["category"], "monetary")
        self.assertNotIn("category", entry["record"])

    def test_limitless_source_platform_is_not_a_domain_category(self):
        record = _make_record("limitless", value_score=70, estimated=62)
        record["event_title"] = "Will a generic market resolve yes?"
        record["source"] = {
            "type": "prediction_market",
            "platform": "Limitless",
            "category": "Prediction",
        }
        record["legacy_analysis"] = {}

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_events([record])
                listed = store.list_events(category="general")
                counts = store.count_events_by_category(status="active")

        self.assertEqual([e["event_id"] for e in listed], ["limitless"])
        self.assertEqual(counts, {"general": 1})

    def test_unknown_base_rate_falls_back_to_source_event_type(self):
        open_web = _make_record("open-web-policy", value_score=70, estimated=62)
        open_web["event_title"] = "Will Congress pass the budget bill?"
        open_web["source"] = {
            "type": "open_web",
            "platform": "news",
            "event_type": "policy",
        }
        open_web["legacy_analysis"] = {"base_rate_category": "unknown"}

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_event(open_web)
                listed = store.list_events(category="policy")
                count = store.count_events(category="policy")

        self.assertEqual([e["event_id"] for e in listed], ["open-web-policy"])
        self.assertEqual(count, 1)

    def test_missing_base_rate_falls_back_to_source_category_before_type(self):
        sourced = _make_record("source-category", value_score=70, estimated=62)
        sourced["event_title"] = "Will a player win the Golden Boot?"
        sourced["source"] = {
            "type": "prediction_market",
            "platform": "Polymarket",
            "category": "player_awards",
        }
        sourced["legacy_analysis"] = {}

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_event(sourced)
                listed = store.list_events(category="player_awards")
                count = store.count_events(category="player_awards")

        self.assertEqual([e["event_id"] for e in listed], ["source-category"])
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

    def test_category_counts_route(self):
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        client = TestClient(app)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                fed = _make_record("fed", value_score=70)
                fed["legacy_analysis"] = {"base_rate_category": "monetary"}
                eth = _make_record("eth", value_score=40)
                eth["legacy_analysis"] = {"base_rate_category": "crypto"}
                store.save_events([fed, eth])
                resp = client.get("/events/category-counts")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["counts"]["monetary"], 1)
        self.assertEqual(body["counts"]["crypto"], 1)

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


class WholeFilePassTests(unittest.TestCase):
    """E1 (scale debt): every event_store call rewrites or re-reads the whole file.

    Measured on the live store (3.455 MB / 235 records): one full load costs
    64 ms, one atomic rewrite 237 ms, so a read-modify-write is ~301 ms. That
    makes the number of whole-file passes per operation the thing to pin, and
    the only honest way to pin it is to count them.

    Counting is done at ``read_json`` / ``read_json_strict`` /
    ``write_json_atomic`` **on the event_store module** — the chokepoint every
    store path shares. Counting inside ``file_store`` instead would also pick
    up the dozen other JSON stores an endpoint touches, and asserting on wall
    clock would be a coin flip on a loaded machine.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmpdir.name) / "event_store.json")
        self._patch = patch.object(store, "_store_path", return_value=self.path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmpdir.cleanup()

    def _counting(self):
        """Context manager yielding a {'reads': n, 'writes': n} tally."""
        from contextlib import contextmanager

        tally = {"reads": 0, "writes": 0}
        real_read = store.read_json
        real_strict = store.read_json_strict
        real_write = store.write_json_atomic

        def rj(path, fallback):
            tally["reads"] += 1
            return real_read(path, fallback)

        def rjs(path, fallback):
            tally["reads"] += 1
            return real_strict(path, fallback)

        def wja(path, data, **kwargs):
            tally["writes"] += 1
            return real_write(path, data, **kwargs)

        @contextmanager
        def _cm():
            with patch.object(store, "read_json", rj), \
                    patch.object(store, "read_json_strict", rjs), \
                    patch.object(store, "write_json_atomic", wja):
                yield tally

        return _cm()

    def _seed(self, n, *, expired=True):
        """n unresolved events, optionally with a source market already closed."""
        records = []
        for index in range(n):
            record = _make_record(f"bulk-{index:02d}", value_score=100 - index)
            if expired:
                record["source"] = {
                    "type": "prediction_market",
                    "platform": "Polymarket",
                    "close_time": "2020-01-01T00:00:00+00:00",
                }
            records.append(record)
        store.save_events(records)

    # ── set_tracking_bulk ────────────────────────────────────────────

    def test_bulk_does_one_write_where_the_loop_did_one_per_event(self):
        self._seed(8)
        ids = [f"bulk-{i:02d}" for i in range(8)]

        with self._counting() as looped:
            for event_id in ids:
                store.set_tracking(event_id, status="watching")

        with self._counting() as batched:
            updated = store.set_tracking_bulk(ids, status="archived")

        self.assertEqual(len(updated), 8)
        self.assertEqual(looped["writes"], 8, "set_tracking is still one write per event")
        self.assertEqual(batched["writes"], 1)
        self.assertEqual(batched["reads"], 1)

    def test_bulk_applies_the_update_to_every_named_event(self):
        self._seed(3)
        store.set_tracking_bulk(
            ["bulk-00", "bulk-02"], status="archived", priority="low",
        )
        # A record that was never tracked carries no ``tracking`` key at all —
        # the store reads that absence as "watching" — so the untouched event is
        # checked through the same status filter the dashboard uses, not by
        # indexing a key it is not required to have.
        archived = store.list_events(status="archived", exclude_expired=False)
        self.assertEqual({e["event_id"] for e in archived}, {"bulk-00", "bulk-02"})
        watching = store.list_events(status="watching", exclude_expired=False)
        self.assertEqual({e["event_id"] for e in watching}, {"bulk-01"})
        self.assertEqual(
            store.get_event("bulk-00")["record"]["tracking"]["priority"], "low",
        )
        self.assertNotIn("tracking", store.get_event("bulk-01")["record"])

    def test_bulk_preserves_first_seen_and_refreshes_last_updated(self):
        self._seed(1)
        before = store.get_event("bulk-00")
        store.set_tracking_bulk(["bulk-00"], status="archived")
        after = store.get_event("bulk-00")
        self.assertEqual(after["first_seen"], before["first_seen"])
        self.assertGreaterEqual(after["last_updated"], before["last_updated"])

    def test_bulk_skips_unknown_ids_without_reporting_them_as_updated(self):
        """A caller that counts the ids it passed in would report work the store
        never did — the same defect as counting requested-vs-written archives."""
        self._seed(2)
        updated = store.set_tracking_bulk(
            ["bulk-00", "ghost", "bulk-01"], status="archived",
        )
        self.assertEqual(updated, ["bulk-00", "bulk-01"])

    def test_one_invalid_record_does_not_abort_the_rest_of_the_batch(self):
        """Mirrors save_events: a single bad record must not cost the batch."""
        self._seed(3)
        real_validate = store.EventRecord.model_validate

        def picky(candidate, *args, **kwargs):
            if candidate.get("event_id") == "bulk-01":
                raise ValueError("synthetic invalid record")
            return real_validate(candidate, *args, **kwargs)

        with patch.object(store.EventRecord, "model_validate", picky):
            updated = store.set_tracking_bulk(
                ["bulk-00", "bulk-01", "bulk-02"], status="archived",
            )

        self.assertEqual(updated, ["bulk-00", "bulk-02"])
        # The rejected record must be left exactly as it was — not written with
        # a partial update, and not gaining a tracking block it never had.
        self.assertNotIn("tracking", store.get_event("bulk-01")["record"])

    def test_a_batch_that_matches_nothing_does_not_rewrite_the_file(self):
        self._seed(1)
        with self._counting() as tally:
            self.assertEqual(store.set_tracking_bulk(["ghost"], status="archived"), [])
        self.assertEqual(tally["writes"], 0)

    def test_an_empty_or_fieldless_batch_touches_the_store_at_all(self):
        self._seed(1)
        with self._counting() as tally:
            self.assertEqual(store.set_tracking_bulk([], status="archived"), [])
            self.assertEqual(store.set_tracking_bulk(["bulk-00"]), [])
        self.assertEqual(tally["reads"], 0)
        self.assertEqual(tally["writes"], 0)

    # ── list_events_page ─────────────────────────────────────────────

    def test_page_and_total_come_from_one_read(self):
        self._seed(12, expired=False)

        with self._counting() as split:
            store.list_events(limit=5)
            store.count_events()

        with self._counting() as combined:
            page, total = store.list_events_page(limit=5)

        self.assertEqual(len(page), 5)
        self.assertEqual(total, 12)
        self.assertEqual(split["reads"], 2, "list_events + count_events is still two reads")
        self.assertEqual(combined["reads"], 1)

    def test_page_is_a_slice_of_the_ranking_the_total_measures(self):
        """The total must describe the same filtered set the page came from —
        a total computed over a wider scope sizes the dashboard's pager wrong."""
        self._seed(6, expired=False)
        store.set_tracking_bulk(["bulk-00", "bulk-01"], status="archived")

        page, total = store.list_events_page(limit=10, status="archived")
        self.assertEqual(total, 2)
        self.assertEqual({e["event_id"] for e in page}, {"bulk-00", "bulk-01"})

        page, total = store.list_events_page(limit=2, offset=0, status="all")
        self.assertEqual(total, 6)
        self.assertEqual(len(page), 2)

    def test_page_agrees_with_list_events_and_count_events(self):
        """The pair it replaces stays the reference: same rows, same total."""
        self._seed(9, expired=False)
        store.set_tracking_bulk(["bulk-00"], status="archived")
        for kwargs in (
            {"limit": 4, "offset": 0},
            {"limit": 4, "offset": 4},
            {"limit": 50, "status": "archived"},
            {"limit": 50, "sort": "probability"},
        ):
            with self.subTest(**kwargs):
                page, total = store.list_events_page(**kwargs)
                self.assertEqual(
                    [e["event_id"] for e in page],
                    [e["event_id"] for e in store.list_events(**kwargs)],
                )
                count_kwargs = {
                    k: v for k, v in kwargs.items() if k not in ("limit", "offset")
                }
                self.assertEqual(total, store.count_events(**count_kwargs))

    def test_offset_past_the_end_returns_no_rows_but_the_real_total(self):
        """A pager that hands back total=0 here would tell the reader there is
        nothing to page back to."""
        self._seed(3, expired=False)
        page, total = store.list_events_page(limit=10, offset=99)
        self.assertEqual(page, [])
        self.assertEqual(total, 3)

    # ── store_bytes ──────────────────────────────────────────────────

    def test_store_bytes_reports_the_file_size_without_parsing_it(self):
        self._seed(4, expired=False)
        with self._counting() as tally:
            size = store.store_bytes()
        self.assertEqual(size, Path(self.path).stat().st_size)
        self.assertGreater(size, 0)
        self.assertEqual(tally["reads"], 0, "the size reading must not re-parse the store")

    def test_store_bytes_is_zero_when_there_is_no_store_yet(self):
        """A fresh deploy has no file; raising here would break /events/loop/status."""
        self.assertFalse(Path(self.path).exists())
        self.assertEqual(store.store_bytes(), 0)

    def test_store_bytes_grows_with_the_store(self):
        """The whole point of the reading: it has to move when the store does,
        or an operator watching it learns nothing."""
        self._seed(2, expired=False)
        small = store.store_bytes()
        self._seed(20, expired=False)
        self.assertGreater(store.store_bytes(), small)

    # ── auto_archive_expired ─────────────────────────────────────────

    def test_auto_archive_counts_what_it_wrote_not_what_it_planned(self):
        """The scan runs before the lock is taken, so an id deleted in that
        window is skipped at write time. Reporting the planned count would
        credit the scheduler run with archiving a record that is gone."""
        self._seed(3)
        events = store.list_all_events()
        # Delete one after the scan list is in hand — exactly the race.
        from app.utils.file_store import locked_file, write_json_atomic
        with locked_file(self.path):
            live = store._load_for_write(self.path)
            live.pop("bulk-01")
            write_json_atomic(self.path, live, indent=2)

        archived = store.auto_archive_expired(events)

        self.assertEqual(archived, 2)
        self.assertIsNone(store.get_event("bulk-01"))
        for event_id in ("bulk-00", "bulk-02"):
            self.assertEqual(
                store.get_event(event_id)["record"]["tracking"]["status"], "archived",
            )


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
