import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.memory import event_store as store
from app.services import event_audit_service as audit
from app.services import sports_resolution_service as resolver
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, import_sports_facts
from app.utils import sqlite_db
from tests.test_event_store import _make_record


def _sports_record(event_id: str, question: str, source_id: str, category: str, entities):
    record = _make_record(event_id, estimated=65.0, value_score=30)
    record["event_title"] = question
    record["source"] = {
        "type": "sports_event",
        "platform": "2026 FIFA World Cup",
        "source_id": source_id,
        "tournament": WORLD_CUP_TOURNAMENT,
        "category": category,
        "entities": list(entities),
    }
    return record


class SportsResolutionRuleTests(unittest.TestCase):
    def test_red_card_threshold_resolves_yes(self):
        record = _sports_record(
            "cards",
            "Will the 2026 FIFA World Cup have at least 8 red cards?",
            "world-cup-2026:red-cards-eight",
            "discipline",
            [WORLD_CUP_TOURNAMENT, "red cards"],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [{
            "fact_id": "f1",
            "kind": "discipline",
            "tournament": WORLD_CUP_TOURNAMENT,
            "red_cards": 8,
            "confidence": 1.0,
        }])
        self.assertEqual(decision["actual_outcome"], 100.0)
        self.assertIn("8", decision["reason"])

    def test_final_finished_without_extra_time_resolves_no(self):
        record = _sports_record(
            "final",
            "Will the 2026 FIFA World Cup final go to extra time?",
            "world-cup-2026:final-extra-time",
            "match_format",
            [WORLD_CUP_TOURNAMENT, "final", "extra time"],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [{
            "fact_id": "final-result",
            "kind": "match_result",
            "tournament": WORLD_CUP_TOURNAMENT,
            "stage": "final",
            "status": "finished",
            "extra_time": False,
            "confidence": 0.98,
        }])
        self.assertEqual(decision["actual_outcome"], 0.0)
        self.assertEqual(decision["confidence"], 0.98)

    def test_penalty_shootout_resolves_yes(self):
        record = _sports_record(
            "penalties",
            "Will any 2026 FIFA World Cup knockout match be decided by a penalty shootout?",
            "world-cup-2026:penalty-shootout",
            "match_format",
            [WORLD_CUP_TOURNAMENT, "penalty shootout"],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [{
            "fact_id": "ko-1",
            "kind": "match_result",
            "tournament": WORLD_CUP_TOURNAMENT,
            "stage": "round_of_16",
            "status": "finished",
            "penalty_shootout": True,
            "confidence": 1.0,
        }])
        self.assertEqual(decision["actual_outcome"], 100.0)

    def test_team_eliminated_resolves_progression_no(self):
        record = _sports_record(
            "mexico",
            "Will Mexico reach the knockout stage of the 2026 FIFA World Cup?",
            "world-cup-2026:mexico-knockout-stage",
            "team_progression",
            ["Mexico", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [{
            "fact_id": "mexico-out",
            "kind": "qualification",
            "tournament": WORLD_CUP_TOURNAMENT,
            "team": "Mexico",
            "status": "eliminated",
            "already_eliminated": True,
            "confidence": 1.0,
        }])
        self.assertEqual(decision["actual_outcome"], 0.0)

    def test_top_scorer_goal_threshold_resolves_yes(self):
        record = _sports_record(
            "golden-boot",
            "Will the top scorer at the 2026 FIFA World Cup finish with at least 7 goals?",
            "world-cup-2026:top-scorer-seven-goals",
            "player_awards",
            [WORLD_CUP_TOURNAMENT, "top scorer", "Golden Boot"],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [{
            "fact_id": "golden-boot",
            "kind": "player_award",
            "tournament": WORLD_CUP_TOURNAMENT,
            "award": "golden_boot",
            "player": "Player A",
            "goals": 7,
            "status": "current",
            "confidence": 0.9,
        }])
        self.assertEqual(decision["actual_outcome"], 100.0)
        self.assertEqual(decision["confidence"], 0.9)

    def test_final_top_scorer_below_threshold_resolves_no(self):
        record = _sports_record(
            "golden-boot",
            "Will the top scorer at the 2026 FIFA World Cup finish with at least 7 goals?",
            "world-cup-2026:top-scorer-seven-goals",
            "player_awards",
            [WORLD_CUP_TOURNAMENT, "top scorer", "Golden Boot"],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [{
            "fact_id": "golden-boot",
            "kind": "player_award",
            "tournament": WORLD_CUP_TOURNAMENT,
            "award": "golden_boot",
            "player": "Player A",
            "goals": 6,
            "status": "official",
            "confidence": 1.0,
        }])
        self.assertEqual(decision["actual_outcome"], 0.0)

    def test_top_scorer_below_threshold_stays_pending_before_final(self):
        record = _sports_record(
            "golden-boot",
            "Will the top scorer at the 2026 FIFA World Cup finish with at least 7 goals?",
            "world-cup-2026:top-scorer-seven-goals",
            "player_awards",
            [WORLD_CUP_TOURNAMENT, "top scorer", "Golden Boot"],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [{
            "fact_id": "golden-boot",
            "kind": "player_award",
            "tournament": WORLD_CUP_TOURNAMENT,
            "award": "golden_boot",
            "player": "Player A",
            "goals": 6,
            "status": "current",
            "confidence": 1.0,
        }])
        self.assertIsNone(decision)


class SportsResolutionWorkflowTests(unittest.TestCase):
    def test_dry_run_reports_without_writing_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch.object(store, "_store_path", return_value=str(base / "event_store.json")), \
                    patch.object(settings, "SPORTS_FACT_FILE", str(base / "sports_facts.json")):
                store.save_event(_sports_record(
                    "evtCards",
                    "Will the 2026 FIFA World Cup have at least 8 red cards?",
                    "world-cup-2026:red-cards-eight",
                    "discipline",
                    [WORLD_CUP_TOURNAMENT, "red cards"],
                ))
                import_sports_facts([{
                    "kind": "discipline",
                    "red_cards": 8,
                    "source": "manual",
                }])
                result = asyncio.run(resolver.resolve_world_cup_events(dry_run=True))
                after = store.get_event("evtCards")

        self.assertEqual(result["resolved_count"], 1)
        self.assertEqual(result["matches"][0]["result"], "would_resolve")
        self.assertIsNone(after["record"].get("outcome"))

    def test_apply_writes_outcome_through_shared_resolve_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch.object(store, "_store_path", return_value=str(base / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(base / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(base / "v2_loop.db")), \
                    patch.object(settings, "SPORTS_FACT_FILE", str(base / "sports_facts.json")):
                store.save_event(_sports_record(
                    "evtMexico",
                    "Will Mexico reach the knockout stage of the 2026 FIFA World Cup?",
                    "world-cup-2026:mexico-knockout-stage",
                    "team_progression",
                    ["Mexico", WORLD_CUP_TOURNAMENT],
                ))
                import_sports_facts([{
                    "kind": "qualification",
                    "team": "Mexico",
                    "status": "qualified",
                    "already_qualified": True,
                    "source": "manual",
                }])
                result = asyncio.run(resolver.resolve_world_cup_events(dry_run=False))
                after = store.get_event("evtMexico")

        self.assertEqual(result["resolved_count"], 1)
        self.assertEqual(result["unresolved_events"], 0)
        self.assertEqual(after["record"]["outcome"]["actual_outcome"], 100.0)
        self.assertEqual(after["record"]["outcome"]["source"], "auto_sports")


if __name__ == "__main__":
    unittest.main()
