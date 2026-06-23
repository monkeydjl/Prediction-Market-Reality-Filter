"""End-to-end feed verification for World Cup data pipeline.

Validates: realistic bundle payload → fact conversion → resolution dry-run → signal generation.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, import_sports_facts, load_sports_facts
from app.services.sports_resolution_service import evaluate_world_cup_resolution
from app.services.sports_signal_service import build_sports_signals
from app.services.world_cup_source_bundle import preview_world_cup_source_bundle


def _realistic_bundle() -> dict:
    """Multi-source bundle simulating a mid-tournament API-Football snapshot."""
    return {
        "sources": [
            {
                "kind": "matches",
                "payload": {
                    "source": "api_football",
                    "observed_at": "2026-07-05T22:00:00Z",
                    "response": [
                        {
                            "fixture": {"id": 2001, "status": {"short": "FT"}, "date": "2026-06-15T18:00:00+00:00", "venue": {"name": "MetLife Stadium"}},
                            "league": {"round": "Group A - 1"},
                            "teams": {"home": {"name": "United States", "winner": True}, "away": {"name": "Wales", "winner": False}},
                            "goals": {"home": 2, "away": 1},
                        },
                        {
                            "fixture": {"id": 2002, "status": {"short": "FT"}, "date": "2026-06-19T18:00:00+00:00", "venue": {"name": "Rose Bowl"}},
                            "league": {"round": "Group A - 2"},
                            "teams": {"home": {"name": "United States", "winner": True}, "away": {"name": "Iran", "winner": False}},
                            "goals": {"home": 1, "away": 0},
                        },
                        {
                            "fixture": {"id": 2003, "status": {"short": "FT"}, "date": "2026-06-23T18:00:00+00:00", "venue": {"name": "AT&T Stadium"}},
                            "league": {"round": "Group A - 3"},
                            "teams": {"home": {"name": "Mexico", "winner": False}, "away": {"name": "United States", "winner": True}},
                            "goals": {"home": 0, "away": 2},
                        },
                        {
                            "fixture": {"id": 2004, "status": {"short": "FT"}, "date": "2026-06-28T20:00:00+00:00", "venue": {"name": "Hard Rock Stadium"}},
                            "league": {"round": "Round of 16"},
                            "teams": {"home": {"name": "United States", "winner": True}, "away": {"name": "Japan", "winner": False}},
                            "goals": {"home": 3, "away": 1},
                        },
                        {
                            "fixture": {"id": 2005, "status": {"short": "PEN"}, "date": "2026-07-02T20:00:00+00:00", "venue": {"name": "SoFi Stadium"}},
                            "league": {"round": "Quarter-final"},
                            "teams": {"home": {"name": "United States", "winner": True}, "away": {"name": "Germany", "winner": False}},
                            "goals": {"home": 1, "away": 1},
                            "score": {"penalty": {"home": 5, "away": 3}},
                        },
                        {
                            "fixture": {"id": 2010, "status": {"short": "FT"}, "date": "2026-06-16T15:00:00+00:00", "venue": {"name": "Estadio Azteca"}},
                            "league": {"round": "Group C - 1"},
                            "teams": {"home": {"name": "Mexico", "winner": True}, "away": {"name": "Saudi Arabia", "winner": False}},
                            "goals": {"home": 2, "away": 0},
                        },
                        {
                            "fixture": {"id": 2011, "status": {"short": "FT"}, "date": "2026-06-20T15:00:00+00:00", "venue": {"name": "Estadio Azteca"}},
                            "league": {"round": "Group C - 2"},
                            "teams": {"home": {"name": "Mexico", "winner": False}, "away": {"name": "Poland", "winner": True}},
                            "goals": {"home": 0, "away": 1},
                        },
                        {
                            "fixture": {"id": 2012, "status": {"short": "FT"}, "date": "2026-06-24T15:00:00+00:00", "venue": {"name": "Estadio Azteca"}},
                            "league": {"round": "Group C - 3"},
                            "teams": {"home": {"name": "Mexico", "winner": True}, "away": {"name": "Australia", "winner": False}},
                            "goals": {"home": 1, "away": 0},
                        },
                    ],
                },
            },
            {
                "kind": "standings",
                "payload": {
                    "source": "api_football",
                    "observed_at": "2026-07-05T22:00:00Z",
                    "response": [
                        {
                            "league": {
                                "standings": [
                                    [
                                        {"team": {"name": "United States"}, "group": "Group A", "rank": 1, "description": "Qualified for knockout stage"},
                                        {"team": {"name": "Wales"}, "group": "Group A", "rank": 2, "description": "Qualified for knockout stage"},
                                        {"team": {"name": "Iran"}, "group": "Group A", "rank": 3, "description": "Eliminated"},
                                    ],
                                    [
                                        {"team": {"name": "Mexico"}, "group": "Group C", "rank": 2, "description": "Qualified for knockout stage"},
                                        {"team": {"name": "Poland"}, "group": "Group C", "rank": 1, "description": "Qualified for knockout stage"},
                                        {"team": {"name": "Australia"}, "group": "Group C", "rank": 3, "description": "Eliminated"},
                                    ],
                                ]
                            }
                        }
                    ],
                },
            },
            {
                "kind": "player_awards",
                "payload": {
                    "source": "api_football",
                    "observed_at": "2026-07-05T22:00:00Z",
                    "award": "golden_boot",
                    "response": [
                        {
                            "rank": 1,
                            "player": {"name": "Pulisic"},
                            "statistics": [{"team": {"name": "United States"}, "goals": {"total": 5}}],
                        },
                        {
                            "rank": 2,
                            "player": {"name": "Lozano"},
                            "statistics": [{"team": {"name": "Mexico"}, "goals": {"total": 3}}],
                        },
                    ],
                },
            },
            {
                "kind": "player_status",
                "payload": {
                    "source": "official_injury_feed",
                    "observed_at": "2026-07-05T22:00:00Z",
                    "response": [
                        {"player": {"name": "Weah"}, "team": {"name": "United States"}, "status": "out", "injury": {"type": "knee"}},
                        {"player": {"name": "Jimenez"}, "team": {"name": "Mexico"}, "status": "doubtful", "injury": {"type": "ankle"}},
                    ],
                },
            },
        ],
    }


def _candidate_record(event_id: str, question: str, source_id: str, category: str, entities: list) -> dict:
    return {
        "event_id": event_id,
        "event_title": question,
        "source": {
            "type": "sports_event",
            "platform": "2026 FIFA World Cup",
            "source_id": f"world-cup-2026:{source_id}",
            "question": question,
            "tournament": WORLD_CUP_TOURNAMENT,
            "category": category,
            "entities": entities,
        },
    }


class FeedVerificationTests(unittest.TestCase):
    """Validates the full data pipeline from bundle → facts → resolution → signals."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fact_path = str(Path(self.tmp.name) / "facts.json")
        self.patch = patch.object(settings, "SPORTS_FACT_FILE", self.fact_path)
        self.patch.start()
        result = preview_world_cup_source_bundle(_realistic_bundle())
        import_sports_facts(result["facts"], replace=True)
        self.facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)
        self.bundle_result = result

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_bundle_converts_expected_fact_kinds(self):
        kinds = {fact["kind"] for fact in self.facts}
        self.assertIn("match_result", kinds)
        self.assertIn("qualification", kinds)
        self.assertIn("player_award", kinds)
        self.assertIn("injury", kinds)

    def test_no_facts_with_empty_required_fields(self):
        for fact in self.facts:
            self.assertTrue(fact.get("kind"), f"fact missing kind: {fact}")
            self.assertTrue(fact.get("tournament"), f"fact missing tournament: {fact}")
            self.assertIsNotNone(fact.get("confidence"), f"fact missing confidence: {fact}")

    def test_match_result_facts_have_scores(self):
        match_facts = [f for f in self.facts if f["kind"] == "match_result"]
        self.assertGreater(len(match_facts), 0)
        for fact in match_facts:
            has_scores = (
                ("home_score" in fact and "away_score" in fact)
                or ("home_goals" in fact and "away_goals" in fact)
                or (isinstance(fact.get("score"), dict) and "home" in fact["score"])
            )
            self.assertTrue(has_scores, f"match fact missing score data: {fact}")
            self.assertTrue(fact.get("home_team") or fact.get("team"))
            self.assertTrue(fact.get("away_team") or fact.get("team"))

    def test_qualification_facts_have_team_and_status(self):
        q_facts = [f for f in self.facts if f["kind"] == "qualification"]
        self.assertGreater(len(q_facts), 0)
        for fact in q_facts:
            self.assertTrue(fact.get("team"), f"qualification fact missing team: {fact}")
            self.assertTrue(fact.get("status"), f"qualification fact missing status: {fact}")

    def test_usa_knockout_stage_resolves_yes(self):
        record = _candidate_record(
            "usa-knockout",
            "Will the United States reach the knockout stage of the 2026 FIFA World Cup?",
            "usa-knockout-stage",
            "team_progression",
            ["United States", WORLD_CUP_TOURNAMENT],
        )
        decision = evaluate_world_cup_resolution(record, self.facts)
        self.assertIsNotNone(decision, "USA knockout should resolve with qualification facts present")
        self.assertEqual(decision["actual_outcome"], 100.0)

    def test_mexico_knockout_stage_resolves_yes(self):
        record = _candidate_record(
            "mexico-knockout",
            "Will Mexico reach the knockout stage of the 2026 FIFA World Cup?",
            "mexico-knockout-stage",
            "team_progression",
            ["Mexico", WORLD_CUP_TOURNAMENT],
        )
        decision = evaluate_world_cup_resolution(record, self.facts)
        self.assertIsNotNone(decision, "Mexico knockout should resolve with qualification facts present")
        self.assertEqual(decision["actual_outcome"], 100.0)

    def test_penalty_shootout_resolves_yes(self):
        record = _candidate_record(
            "penalty",
            "Will any 2026 FIFA World Cup knockout match be decided by a penalty shootout?",
            "penalty-shootout",
            "match_format",
            [WORLD_CUP_TOURNAMENT, "penalty shootout"],
        )
        decision = evaluate_world_cup_resolution(record, self.facts)
        self.assertIsNotNone(decision, "Penalty shootout should resolve with PEN status match")
        self.assertEqual(decision["actual_outcome"], 100.0)

    def test_top_scorer_below_threshold_stays_pending(self):
        record = _candidate_record(
            "golden-boot",
            "Will the top scorer at the 2026 FIFA World Cup finish with at least 7 goals?",
            "top-scorer-seven-goals",
            "player_awards",
            [WORLD_CUP_TOURNAMENT, "top scorer", "Golden Boot"],
        )
        decision = evaluate_world_cup_resolution(record, self.facts)
        self.assertIsNone(decision, "Top scorer at 5 goals should stay pending (tournament not complete)")

    def test_total_goals_from_match_facts(self):
        record = _candidate_record(
            "total-goals",
            "Will the 2026 FIFA World Cup have at least 15 total goals?",
            "total-goals-15",
            "tournament_totals",
            [WORLD_CUP_TOURNAMENT, "total goals"],
        )
        decision = evaluate_world_cup_resolution(record, self.facts)
        self.assertIsNotNone(decision, "Total goals should resolve when threshold met")
        self.assertEqual(decision["actual_outcome"], 100.0)

    def test_signals_generated_for_team_progression(self):
        source = {
            "type": "sports_event",
            "category": "team_progression",
            "tournament": WORLD_CUP_TOURNAMENT,
            "source_id": "world-cup-2026:usa-knockout-stage",
            "entities": ["United States", WORLD_CUP_TOURNAMENT],
        }
        bundle = build_sports_signals(
            "Will the United States reach the knockout stage of the 2026 FIFA World Cup?",
            source,
            self.facts,
        )
        self.assertGreater(bundle["fact_count"], 0)
        self.assertIn("qualification_signal", bundle["signals"])
        self.assertEqual(bundle["signals"]["qualification_signal"]["direction"], "supports_yes")

    def test_signals_generated_for_discipline(self):
        source = {
            "type": "sports_event",
            "category": "discipline",
            "tournament": WORLD_CUP_TOURNAMENT,
            "source_id": "world-cup-2026:red-cards-eight",
            "entities": [WORLD_CUP_TOURNAMENT, "red cards"],
        }
        bundle = build_sports_signals(
            "Will the 2026 FIFA World Cup have at least 8 red cards?",
            source,
            self.facts,
        )
        self.assertGreater(bundle["fact_count"], 0)

    def test_injury_signal_for_usa(self):
        source = {
            "type": "sports_event",
            "category": "team_progression",
            "tournament": WORLD_CUP_TOURNAMENT,
            "source_id": "world-cup-2026:usa-knockout-stage",
            "entities": ["United States", WORLD_CUP_TOURNAMENT],
        }
        bundle = build_sports_signals(
            "Will the United States reach the knockout stage of the 2026 FIFA World Cup?",
            source,
            self.facts,
        )
        self.assertIn("injury_signal", bundle["signals"])
        self.assertIn("Weah", bundle["signals"]["injury_signal"]["summary"])

    def test_bundle_result_source_count_matches(self):
        self.assertEqual(self.bundle_result["source_count"], 4)
        self.assertEqual(
            [s["kind"] for s in self.bundle_result["sources"]],
            ["matches", "standings", "player_awards", "player_status"],
        )


if __name__ == "__main__":
    unittest.main()
