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


class GroupStageResolutionTests(unittest.TestCase):
    def _group_fact(self, team, rank=None, points=None, goal_diff=None,
                    status="group_stage", stage="group_stage",
                    observed_at="2026-06-23T00:00:00Z"):
        fact = {
            "fact_id": f"wc2026:qualification:{team.lower().replace(' ', '-')}",
            "kind": "qualification",
            "tournament": WORLD_CUP_TOURNAMENT,
            "team": team,
            "status": status,
            "stage": stage,
            "confidence": 1.0,
            "observed_at": observed_at,
        }
        if rank is not None:
            fact["rank"] = rank
        if points is not None:
            fact["points"] = points
        if goal_diff is not None:
            fact["goal_diff"] = goal_diff
        return fact

    def test_advance_from_group_resolves_yes_with_rank_2(self):
        record = _sports_record(
            "usa-advance",
            "Will the United States advance from the group stage of the 2026 FIFA World Cup?",
            "world-cup-2026:usa-advance-from-group",
            "group_stage",
            ["United States", "USMNT", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [
            self._group_fact("United States", rank=2, points=6),
        ])
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 100.0)

    def test_advance_from_group_resolves_yes_with_already_qualified(self):
        record = _sports_record(
            "mexico-advance",
            "Will Mexico advance from the group stage of the 2026 FIFA World Cup?",
            "world-cup-2026:mexico-advance-from-group",
            "group_stage",
            ["Mexico", WORLD_CUP_TOURNAMENT],
        )
        fact = self._group_fact("Mexico", rank=1, points=9)
        fact["already_qualified"] = True
        decision = resolver.evaluate_world_cup_resolution(record, [fact])
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 100.0)

    def test_advance_from_group_resolves_no_when_eliminated_and_group_complete(self):
        record = _sports_record(
            "canada-advance",
            "Will Canada advance from the group stage of the 2026 FIFA World Cup?",
            "world-cup-2026:canada-advance-from-group",
            "group_stage",
            ["Canada", WORLD_CUP_TOURNAMENT],
        )
        fact = self._group_fact("Canada", rank=4, points=1, status="eliminated")
        fact["already_eliminated"] = True
        decision = resolver.evaluate_world_cup_resolution(record, [
            fact,
            {
                "fact_id": "wc2026:tournament-status",
                "kind": "tournament_status",
                "tournament": WORLD_CUP_TOURNAMENT,
                "status": "complete",
                "stage": "group_stage",
                "confidence": 1.0,
            },
        ])
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 0.0)

    def test_advance_from_group_pending_before_complete(self):
        record = _sports_record(
            "canada-advance",
            "Will Canada advance from the group stage of the 2026 FIFA World Cup?",
            "world-cup-2026:canada-advance-from-group",
            "group_stage",
            ["Canada", WORLD_CUP_TOURNAMENT],
        )
        fact = self._group_fact("Canada", rank=3, points=3, status="group_stage")
        decision = resolver.evaluate_world_cup_resolution(record, [fact])
        self.assertIsNone(decision)

    def test_win_group_resolves_yes_with_rank_1(self):
        record = _sports_record(
            "argentina-win-group",
            "Will Argentina win its group at the 2026 FIFA World Cup?",
            "world-cup-2026:argentina-win-group",
            "group_stage",
            ["Argentina", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [
            self._group_fact("Argentina", rank=1, points=9),
        ])
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 100.0)
        self.assertIn("won its group", decision["reason"])

    def test_win_group_resolves_no_when_rank_2_and_complete(self):
        record = _sports_record(
            "argentina-win-group",
            "Will Argentina win its group at the 2026 FIFA World Cup?",
            "world-cup-2026:argentina-win-group",
            "group_stage",
            ["Argentina", WORLD_CUP_TOURNAMENT],
        )
        facts = [
            self._group_fact("Argentina", rank=2, points=6),
            {
                "fact_id": "wc2026:tournament-status",
                "kind": "tournament_status",
                "tournament": WORLD_CUP_TOURNAMENT,
                "status": "complete",
                "stage": "group_stage",
                "confidence": 1.0,
            },
        ]
        decision = resolver.evaluate_world_cup_resolution(record, facts)
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 0.0)

    def test_points_threshold_resolves_yes(self):
        record = _sports_record(
            "brazil-points",
            "Will Brazil finish the group stage with at least 7 points at the 2026 FIFA World Cup?",
            "world-cup-2026:brazil-7-points-in-group",
            "group_stage",
            ["Brazil", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [
            self._group_fact("Brazil", rank=1, points=9),
        ])
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 100.0)
        self.assertIn("7", decision["reason"])

    def test_points_threshold_resolves_no_when_complete_and_below(self):
        record = _sports_record(
            "brazil-points",
            "Will Brazil finish the group stage with at least 7 points at the 2026 FIFA World Cup?",
            "world-cup-2026:brazil-7-points-in-group",
            "group_stage",
            ["Brazil", WORLD_CUP_TOURNAMENT],
        )
        facts = [
            self._group_fact("Brazil", rank=2, points=5),
            {
                "fact_id": "wc2026:tournament-status",
                "kind": "tournament_status",
                "tournament": WORLD_CUP_TOURNAMENT,
                "status": "complete",
                "stage": "group_stage",
                "confidence": 1.0,
            },
        ]
        decision = resolver.evaluate_world_cup_resolution(record, facts)
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 0.0)

    def test_points_threshold_pending_when_below_not_complete(self):
        record = _sports_record(
            "brazil-points",
            "Will Brazil finish the group stage with at least 7 points at the 2026 FIFA World Cup?",
            "world-cup-2026:brazil-7-points-in-group",
            "group_stage",
            ["Brazil", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [
            self._group_fact("Brazil", rank=1, points=3),
        ])
        self.assertIsNone(decision)

    def test_group_stage_uses_latest_observed_fact(self):
        record = _sports_record(
            "usa-advance",
            "Will the United States advance from the group stage of the 2026 FIFA World Cup?",
            "world-cup-2026:usa-advance-from-group",
            "group_stage",
            ["United States", "USMNT", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [
            self._group_fact("United States", rank=3, points=0,
                             observed_at="2026-06-11T00:00:00Z"),
            self._group_fact("United States", rank=1, points=7,
                             observed_at="2026-06-23T12:00:00Z"),
        ])
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 100.0)

    def test_goal_diff_threshold_resolves_yes(self):
        record = _sports_record(
            "argentina-gd",
            "Will Argentina finish the group stage with a goal difference of at least +5 at the 2026 FIFA World Cup?",
            "world-cup-2026:argentina-group-goal-diff-plus-five",
            "group_stage",
            ["Argentina", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [
            self._group_fact("Argentina", rank=1, points=9, goal_diff=7),
        ])
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 100.0)
        self.assertIn("goal difference", decision["reason"])

    def test_goal_diff_threshold_resolves_no_when_complete_and_below(self):
        record = _sports_record(
            "brazil-gd",
            "Will Brazil finish the group stage with a goal difference of at least +5 at the 2026 FIFA World Cup?",
            "world-cup-2026:brazil-group-goal-diff-plus-five",
            "group_stage",
            ["Brazil", WORLD_CUP_TOURNAMENT],
        )
        facts = [
            self._group_fact("Brazil", rank=2, points=6, goal_diff=3),
            {
                "fact_id": "wc2026:tournament-status",
                "kind": "tournament_status",
                "tournament": WORLD_CUP_TOURNAMENT,
                "status": "complete",
                "stage": "group_stage",
                "confidence": 1.0,
            },
        ]
        decision = resolver.evaluate_world_cup_resolution(record, facts)
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 0.0)

    def test_goal_diff_threshold_pending_when_below_not_complete(self):
        record = _sports_record(
            "france-gd",
            "Will France finish the group stage with a goal difference of at least +5 at the 2026 FIFA World Cup?",
            "world-cup-2026:france-group-goal-diff-plus-five",
            "group_stage",
            ["France", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [
            self._group_fact("France", rank=1, points=4, goal_diff=2),
        ])
        self.assertIsNone(decision)

    def test_runner_up_resolves_yes_with_rank_2(self):
        record = _sports_record(
            "france-ru",
            "Will France finish as runner-up in its group at the 2026 FIFA World Cup?",
            "world-cup-2026:france-runner-up-group",
            "group_stage",
            ["France", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [
            self._group_fact("France", rank=2, points=6),
        ])
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 100.0)
        self.assertIn("runner-up", decision["reason"])

    def test_runner_up_resolves_no_when_rank_1_and_complete(self):
        record = _sports_record(
            "argentina-ru",
            "Will Argentina finish as runner-up in its group at the 2026 FIFA World Cup?",
            "world-cup-2026:argentina-runner-up-group",
            "group_stage",
            ["Argentina", WORLD_CUP_TOURNAMENT],
        )
        facts = [
            self._group_fact("Argentina", rank=1, points=9),
            {
                "fact_id": "wc2026:tournament-status",
                "kind": "tournament_status",
                "tournament": WORLD_CUP_TOURNAMENT,
                "status": "complete",
                "stage": "group_stage",
                "confidence": 1.0,
            },
        ]
        decision = resolver.evaluate_world_cup_resolution(record, facts)
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 0.0)

    def test_runner_up_pending_when_rank_1_not_complete(self):
        record = _sports_record(
            "spain-ru",
            "Will Spain finish as runner-up in its group at the 2026 FIFA World Cup?",
            "world-cup-2026:spain-runner-up-group",
            "group_stage",
            ["Spain", WORLD_CUP_TOURNAMENT],
        )
        decision = resolver.evaluate_world_cup_resolution(record, [
            self._group_fact("Spain", rank=1, points=6),
        ])
        self.assertIsNone(decision)


class RedCardGrainRegressionTests(unittest.TestCase):
    """A card reported at two grains must not settle the market twice over.

    A data-source bundle carries both the per-card `discipline` rows and the
    per-match `home_red_cards` + `away_red_cards` total, and their fact_ids
    differ by construction, so the store's upsert cannot merge them. Summing
    every fact that carried `red_cards` settled the 8-card market YES, at
    confidence 100, on four real cards.
    """

    @staticmethod
    def _record():
        return _sports_record(
            "red-cards",
            "Will the 2026 FIFA World Cup have at least 8 red cards?",
            "world-cup-2026:red-cards-eight",
            "discipline",
            [WORLD_CUP_TOURNAMENT, "red cards"],
        )

    @staticmethod
    def _both_grains(match_count: int):
        facts = []
        for i in range(1, match_count + 1):
            facts.append({
                "fact_id": f"card-{i}",
                "kind": "discipline",
                "tournament": WORLD_CUP_TOURNAMENT,
                "match_id": f"m{i}",
                "player": f"P{i}",
                "status": "red_card",
                "red_cards": 1,
                "confidence": 1.0,
            })
            facts.append({
                "fact_id": f"wc2026:match:m{i}",
                "kind": "match_result",
                "tournament": WORLD_CUP_TOURNAMENT,
                "match_id": f"m{i}",
                "status": "finished",
                "red_cards": 1,
                "confidence": 1.0,
            })
        return facts

    def test_four_real_cards_at_two_grains_stays_pending(self):
        decision = resolver.evaluate_world_cup_resolution(
            self._record(), self._both_grains(4)
        )
        self.assertIsNone(decision)

    def test_eight_real_cards_at_two_grains_still_resolves_yes(self):
        decision = resolver.evaluate_world_cup_resolution(
            self._record(), self._both_grains(8)
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 100.0)
        self.assertIn("8 red cards", decision["reason"])

    def test_decision_cites_only_the_facts_it_counted(self):
        decision = resolver.evaluate_world_cup_resolution(
            self._record(), self._both_grains(8)
        )
        self.assertEqual(len(decision["facts"]), 8)
        self.assertTrue(all(fid.startswith("card-") for fid in decision["facts"]))

    def test_below_threshold_at_two_grains_resolves_no_when_complete(self):
        facts = self._both_grains(4) + [{
            "fact_id": "ts",
            "kind": "tournament_status",
            "tournament": WORLD_CUP_TOURNAMENT,
            "status": "completed",
            "tournament_complete": True,
            "confidence": 1.0,
        }]
        decision = resolver.evaluate_world_cup_resolution(self._record(), facts)
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 0.0)
        self.assertIn("4 red cards", decision["reason"])


class TotalGoalsGrainRegressionTests(unittest.TestCase):
    """One match must contribute its goals once, however often it was imported.

    The generated fact_id seeds on `source` and `observed_at`, so the same match
    arriving from two feeds persists as two `match_result` facts.
    """

    @staticmethod
    def _record():
        return _sports_record(
            "total-goals",
            "Will the 2026 FIFA World Cup have at least 8 total goals?",
            "world-cup-2026:total-goals-140",
            "tournament_totals",
            [WORLD_CUP_TOURNAMENT, "total goals"],
        )

    @staticmethod
    def _match(fact_id: str, match_id: str, home: int, away: int, **extra):
        fact = {
            "fact_id": fact_id,
            "kind": "match_result",
            "tournament": WORLD_CUP_TOURNAMENT,
            "match_id": match_id,
            "status": "finished",
            "score": {"home": home, "away": away},
            "confidence": 1.0,
        }
        fact.update(extra)
        return fact

    def test_one_match_from_two_feeds_counts_its_goals_once(self):
        facts = [
            self._match("sports:wc:match_result:aaa", "m1", 3, 2, source="official_csv"),
            self._match("sports:wc:match_result:bbb", "m1", 3, 2, source="api_football"),
        ]
        decision = resolver.evaluate_world_cup_resolution(self._record(), facts)
        self.assertIsNone(decision)

    def test_distinct_matches_still_reach_the_threshold(self):
        facts = [
            self._match("m1", "m1", 2, 1),
            self._match("m2", "m2", 1, 1),
            self._match("m3", "m3", 2, 1),
        ]
        decision = resolver.evaluate_world_cup_resolution(self._record(), facts)
        self.assertIsNotNone(decision)
        self.assertEqual(decision["actual_outcome"], 100.0)
        self.assertIn("8 total goals", decision["reason"])

    def test_live_snapshot_does_not_lower_a_finished_score(self):
        facts = [
            self._match("live", "m1", 1, 0, status="live"),
            self._match("final", "m1", 5, 4),
        ]
        decision = resolver.evaluate_world_cup_resolution(self._record(), facts)
        self.assertIsNotNone(decision)
        self.assertEqual(decision["facts"], ["final"])


if __name__ == "__main__":
    unittest.main()
