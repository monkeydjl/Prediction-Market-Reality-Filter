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


class StageAliasResolutionTests(unittest.TestCase):
    def test_quarterfinal_stage_resolves_yes(self):
        record = _sports_record(
            "france-qf",
            "Will France reach the quarterfinals of the 2026 FIFA World Cup?",
            "world-cup-2026:france-quarterfinal",
            "team_progression",
            ["France", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [{
            "fact_id": "france-qf",
            "kind": "qualification",
            "tournament": WORLD_CUP_TOURNAMENT,
            "team": "France",
            "stage": "quarterfinal",
            "status": "reached_quarterfinal",
            "confidence": 1.0,
        }])
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 100.0)

    def test_round_of_16_stage_resolves_yes(self):
        record = _sports_record(
            "japan-r16",
            "Will Japan reach the round of 16 of the 2026 FIFA World Cup?",
            "world-cup-2026:japan-round-of-16",
            "team_progression",
            ["Japan", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [{
            "fact_id": "japan-r16",
            "kind": "qualification",
            "tournament": WORLD_CUP_TOURNAMENT,
            "team": "Japan",
            "stage": "round_of_16",
            "status": "advanced",
            "confidence": 1.0,
        }])
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 100.0)

    def test_final_winner_resolves_yes(self):
        record = _sports_record(
            "brazil-win",
            "Will Brazil win the 2026 FIFA World Cup?",
            "world-cup-2026:brazil-champion",
            "team_progression",
            ["Brazil", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [{
            "fact_id": "brazil-champ",
            "kind": "qualification",
            "tournament": WORLD_CUP_TOURNAMENT,
            "team": "Brazil",
            "status": "champion",
            "confidence": 1.0,
        }])
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 100.0)

    def test_final_winner_not_resolved_by_mere_qualification(self):
        record = _sports_record(
            "brazil-win",
            "Will Brazil win the 2026 FIFA World Cup?",
            "world-cup-2026:brazil-champion",
            "team_progression",
            ["Brazil", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [{
            "fact_id": "brazil-qf",
            "kind": "qualification",
            "tournament": WORLD_CUP_TOURNAMENT,
            "team": "Brazil",
            "stage": "quarterfinal",
            "status": "reached_quarterfinal",
            "confidence": 1.0,
        }])
        self.assertIsNone(decision)

    def test_quarterfinal_eliminated_resolves_no(self):
        record = _sports_record(
            "france-qf",
            "Will France reach the quarterfinals of the 2026 FIFA World Cup?",
            "world-cup-2026:france-quarterfinal",
            "team_progression",
            ["France", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [{
            "fact_id": "france-out",
            "kind": "qualification",
            "tournament": WORLD_CUP_TOURNAMENT,
            "team": "France",
            "status": "eliminated",
            "already_eliminated": True,
            "confidence": 1.0,
        }])
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 0.0)


class TotalGoalsResolutionTests(unittest.TestCase):
    def test_total_goals_above_threshold_resolves_yes(self):
        record = _sports_record(
            "total-goals",
            "Will the 2026 FIFA World Cup have at least 140 total goals?",
            "world-cup-2026:total-goals-140",
            "tournament_totals",
            [WORLD_CUP_TOURNAMENT, "total goals"],
        )
        facts = [
            {
                "fact_id": f"m{i}",
                "kind": "match_result",
                "tournament": WORLD_CUP_TOURNAMENT,
                "home_goals": 2,
                "away_goals": 1,
                "confidence": 1.0,
            }
            for i in range(47)
        ]
        decision = resolver.evaluate_world_cup_resolution(record, facts)
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 100.0)
        self.assertIn("141", decision["reason"])

    def test_total_goals_below_threshold_pending_before_complete(self):
        record = _sports_record(
            "total-goals",
            "Will the 2026 FIFA World Cup have at least 140 total goals?",
            "world-cup-2026:total-goals-140",
            "tournament_totals",
            [WORLD_CUP_TOURNAMENT, "total goals"],
        )
        facts = [{
            "fact_id": "m1",
            "kind": "match_result",
            "tournament": WORLD_CUP_TOURNAMENT,
            "home_goals": 2,
            "away_goals": 1,
            "confidence": 1.0,
        }]
        decision = resolver.evaluate_world_cup_resolution(record, facts)
        self.assertIsNone(decision)

    def test_total_goals_below_threshold_resolves_no_when_complete(self):
        record = _sports_record(
            "total-goals",
            "Will the 2026 FIFA World Cup have at least 140 total goals?",
            "world-cup-2026:total-goals-140",
            "tournament_totals",
            [WORLD_CUP_TOURNAMENT, "total goals"],
        )
        facts = [
            {
                "fact_id": "m1",
                "kind": "match_result",
                "tournament": WORLD_CUP_TOURNAMENT,
                "home_goals": 2,
                "away_goals": 1,
                "confidence": 1.0,
            },
            {
                "fact_id": "ts",
                "kind": "tournament_status",
                "tournament": WORLD_CUP_TOURNAMENT,
                "status": "completed",
                "tournament_complete": True,
                "confidence": 1.0,
            },
        ]
        decision = resolver.evaluate_world_cup_resolution(record, facts)
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 0.0)

    def test_total_goals_title_fallback_without_category(self):
        record = _sports_record(
            "total-goals-2",
            "Will the 2026 FIFA World Cup have at least 100 total goals?",
            "world-cup-2026:total-goals-100",
            "match_format",
            [WORLD_CUP_TOURNAMENT, "total goals"],
        )
        facts = [
            {
                "fact_id": f"m{i}",
                "kind": "match_result",
                "tournament": WORLD_CUP_TOURNAMENT,
                "home_goals": 3,
                "away_goals": 1,
                "confidence": 1.0,
            }
            for i in range(26)
        ]
        decision = resolver.evaluate_world_cup_resolution(record, facts)
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 100.0)


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


class KnockoutResolutionTests(unittest.TestCase):
    def test_tournament_winner_resolves_yes(self):
        record = _sports_record(
            "argentina-winner",
            "Will Argentina win the 2026 FIFA World Cup?",
            "world-cup-2026:argentina-winner",
            "tournament_winner",
            ["Argentina", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [{
            "fact_id": "arg-champ",
            "kind": "qualification",
            "tournament": WORLD_CUP_TOURNAMENT,
            "team": "Argentina",
            "status": "champion",
            "confidence": 1.0,
        }])
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 100.0)

    def test_multi_team_progression_resolves_yes_when_any_advances(self):
        record = _sports_record(
            "host-semifinal",
            "Will a host nation reach the semifinals of the 2026 FIFA World Cup?",
            "world-cup-2026:host-nation-semifinal",
            "team_progression",
            ["United States", "Mexico", "Canada", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [
            {
                "fact_id": "usa-out",
                "kind": "qualification",
                "tournament": WORLD_CUP_TOURNAMENT,
                "team": "United States",
                "status": "eliminated",
                "already_eliminated": True,
                "confidence": 1.0,
            },
            {
                "fact_id": "mex-sf",
                "kind": "qualification",
                "tournament": WORLD_CUP_TOURNAMENT,
                "team": "Mexico",
                "status": "reached_semifinal",
                "stage": "semifinal",
                "confidence": 1.0,
            },
        ])
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 100.0)
        self.assertIn("Mexico", decision["reason"])

    def test_multi_team_progression_resolves_no_when_all_eliminated(self):
        record = _sports_record(
            "host-semifinal",
            "Will a host nation reach the semifinals of the 2026 FIFA World Cup?",
            "world-cup-2026:host-nation-semifinal",
            "team_progression",
            ["United States", "Mexico", "Canada", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [
            {
                "fact_id": "usa-out",
                "kind": "qualification",
                "tournament": WORLD_CUP_TOURNAMENT,
                "team": "United States",
                "status": "eliminated",
                "already_eliminated": True,
                "confidence": 1.0,
            },
            {
                "fact_id": "mex-out",
                "kind": "qualification",
                "tournament": WORLD_CUP_TOURNAMENT,
                "team": "Mexico",
                "status": "eliminated",
                "already_eliminated": True,
                "confidence": 1.0,
            },
            {
                "fact_id": "can-out",
                "kind": "qualification",
                "tournament": WORLD_CUP_TOURNAMENT,
                "team": "Canada",
                "status": "eliminated",
                "already_eliminated": True,
                "confidence": 1.0,
            },
        ])
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 0.0)

    def test_multi_team_progression_pending_when_one_eliminated_others_unknown(self):
        record = _sports_record(
            "host-semifinal",
            "Will a host nation reach the semifinals of the 2026 FIFA World Cup?",
            "world-cup-2026:host-nation-semifinal",
            "team_progression",
            ["United States", "Mexico", "Canada", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [{
            "fact_id": "usa-out",
            "kind": "qualification",
            "tournament": WORLD_CUP_TOURNAMENT,
            "team": "United States",
            "status": "eliminated",
            "already_eliminated": True,
            "confidence": 1.0,
        }])
        self.assertIsNone(decision)


if __name__ == "__main__":
    unittest.main()
