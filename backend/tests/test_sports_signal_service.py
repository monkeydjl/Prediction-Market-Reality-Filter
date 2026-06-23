import unittest

from app.services import sports_signal_service as signals
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT


class SportsSignalServiceTests(unittest.TestCase):
    def test_injury_fact_becomes_team_progression_signal(self):
        source = {
            "type": "sports_event",
            "category": "team_progression",
            "tournament": WORLD_CUP_TOURNAMENT,
            "source_id": "world-cup-2026:brazil-semifinal",
            "entities": ["Brazil", WORLD_CUP_TOURNAMENT],
        }
        facts = [{
            "fact_id": "f1",
            "kind": "injury",
            "tournament": WORLD_CUP_TOURNAMENT,
            "team": "Brazil",
            "player": "Player A",
            "status": "out",
            "severity": "high",
            "source": "manual",
            "confidence": 0.9,
        }]

        bundle = signals.build_sports_signals(
            "Will Brazil reach the semifinals of the 2026 FIFA World Cup?",
            source,
            facts,
        )
        context = signals.render_sports_context(bundle)

        self.assertEqual(bundle["fact_count"], 1)
        self.assertEqual(bundle["signals"]["injury_signal"]["level"], "high")
        self.assertEqual(
            bundle["signals"]["injury_signal"]["direction"],
            "supports_no",
        )
        self.assertIn("SPORTS FACT SIGNALS", context)
        self.assertIn("Player A", context)

    def test_red_card_threshold_progress(self):
        source = {
            "type": "sports_event",
            "category": "discipline",
            "tournament": WORLD_CUP_TOURNAMENT,
            "source_id": "world-cup-2026:red-cards-eight",
            "entities": [WORLD_CUP_TOURNAMENT, "red cards"],
        }
        facts = [{
            "fact_id": "cards",
            "kind": "discipline",
            "tournament": WORLD_CUP_TOURNAMENT,
            "red_cards": 6,
            "source": "manual",
            "confidence": 1.0,
        }]

        bundle = signals.build_sports_signals(
            "Will the 2026 FIFA World Cup have at least 8 red cards?",
            source,
            facts,
        )

        discipline = bundle["signals"]["discipline_signal"]
        self.assertEqual(discipline["red_card_total"], 6)
        self.assertEqual(discipline["red_card_threshold"], 8)
        self.assertEqual(discipline["threshold_progress"], 0.75)

    def test_qualification_fact_can_resolve_direction(self):
        source = {
            "type": "sports_event",
            "category": "team_progression",
            "tournament": WORLD_CUP_TOURNAMENT,
            "source_id": "world-cup-2026:mexico-knockout-stage",
            "entities": ["Mexico", WORLD_CUP_TOURNAMENT],
        }
        facts = [{
            "fact_id": "q1",
            "kind": "qualification",
            "tournament": WORLD_CUP_TOURNAMENT,
            "team": "Mexico",
            "status": "qualified",
            "already_qualified": True,
            "source": "manual",
            "confidence": 1.0,
        }]

        bundle = signals.build_sports_signals(
            "Will Mexico reach the knockout stage of the 2026 FIFA World Cup?",
            source,
            facts,
        )

        qualification = bundle["signals"]["qualification_signal"]
        self.assertEqual(qualification["direction"], "supports_yes")
        self.assertTrue(qualification["already_qualified"])

    def test_player_award_fact_becomes_goal_threshold_signal(self):
        source = {
            "type": "sports_event",
            "category": "player_awards",
            "tournament": WORLD_CUP_TOURNAMENT,
            "source_id": "world-cup-2026:top-scorer-seven-goals",
            "entities": [WORLD_CUP_TOURNAMENT, "top scorer", "Golden Boot"],
        }
        facts = [{
            "fact_id": "golden-boot",
            "kind": "player_award",
            "tournament": WORLD_CUP_TOURNAMENT,
            "award": "golden_boot",
            "player": "Player A",
            "goals": 7,
            "rank": 1,
            "status": "current",
            "source": "manual",
            "confidence": 0.9,
        }]

        bundle = signals.build_sports_signals(
            "Will the top scorer at the 2026 FIFA World Cup finish with at least 7 goals?",
            source,
            facts,
        )
        context = signals.render_sports_context(bundle)

        award = bundle["signals"]["player_award_signal"]
        self.assertEqual(award["direction"], "supports_yes")
        self.assertEqual(award["goal_threshold"], 7)
        self.assertEqual(award["top_scorer_goals"], 7)
        self.assertIn("goals=7", context)


    def test_schedule_fatigue_signal_high_when_three_matches_in_five_days(self):
        source = {
            "type": "sports_event",
            "category": "team_progression",
            "tournament": WORLD_CUP_TOURNAMENT,
            "source_id": "world-cup-2026:brazil-semifinal",
            "entities": ["Brazil", WORLD_CUP_TOURNAMENT],
        }
        facts = [
            {
                "fact_id": "m1",
                "kind": "match_result",
                "tournament": WORLD_CUP_TOURNAMENT,
                "home_team": "Brazil",
                "away_team": "Serbia",
                "kickoff_at": "2026-06-15T18:00:00+00:00",
                "source": "manual",
                "confidence": 1.0,
            },
            {
                "fact_id": "m2",
                "kind": "match_result",
                "tournament": WORLD_CUP_TOURNAMENT,
                "home_team": "Switzerland",
                "away_team": "Brazil",
                "kickoff_at": "2026-06-18T18:00:00+00:00",
                "source": "manual",
                "confidence": 1.0,
            },
            {
                "fact_id": "m3",
                "kind": "match_result",
                "tournament": WORLD_CUP_TOURNAMENT,
                "home_team": "Brazil",
                "away_team": "Cameroon",
                "kickoff_at": "2026-06-20T18:00:00+00:00",
                "source": "manual",
                "confidence": 1.0,
            },
        ]

        bundle = signals.build_sports_signals(
            "Will Brazil reach the semifinals of the 2026 FIFA World Cup?",
            source,
            facts,
        )

        fatigue = bundle["signals"].get("schedule_fatigue_signal")
        self.assertIsNotNone(fatigue)
        self.assertEqual(fatigue["level"], "high")
        self.assertEqual(fatigue["matches_in_window"], 3)
        self.assertEqual(fatigue["team"], "Brazil")

    def test_schedule_fatigue_signal_absent_when_one_match(self):
        source = {
            "type": "sports_event",
            "category": "team_progression",
            "tournament": WORLD_CUP_TOURNAMENT,
            "source_id": "world-cup-2026:brazil-semifinal",
            "entities": ["Brazil", WORLD_CUP_TOURNAMENT],
        }
        facts = [{
            "fact_id": "m1",
            "kind": "match_result",
            "tournament": WORLD_CUP_TOURNAMENT,
            "home_team": "Brazil",
            "away_team": "Serbia",
            "kickoff_at": "2026-06-15T18:00:00+00:00",
            "source": "manual",
            "confidence": 1.0,
        }]

        bundle = signals.build_sports_signals(
            "Will Brazil reach the semifinals of the 2026 FIFA World Cup?",
            source,
            facts,
        )

        self.assertNotIn("schedule_fatigue_signal", bundle["signals"])

    def test_lineup_signal_detects_unavailable_starters(self):
        source = {
            "type": "sports_event",
            "category": "team_progression",
            "tournament": WORLD_CUP_TOURNAMENT,
            "source_id": "world-cup-2026:england-semifinal",
            "entities": ["England", WORLD_CUP_TOURNAMENT],
        }
        facts = [
            {
                "fact_id": "l1",
                "kind": "lineup",
                "tournament": WORLD_CUP_TOURNAMENT,
                "team": "England",
                "player": "Kane",
                "status": "starting",
                "source": "manual",
                "confidence": 1.0,
            },
            {
                "fact_id": "l2",
                "kind": "lineup",
                "tournament": WORLD_CUP_TOURNAMENT,
                "team": "England",
                "player": "Bellingham",
                "status": "starting",
                "source": "manual",
                "confidence": 1.0,
            },
            {
                "fact_id": "i1",
                "kind": "injury",
                "tournament": WORLD_CUP_TOURNAMENT,
                "team": "England",
                "player": "Kane",
                "status": "injured",
                "source": "manual",
                "confidence": 0.9,
            },
            {
                "fact_id": "s1",
                "kind": "suspension",
                "tournament": WORLD_CUP_TOURNAMENT,
                "team": "England",
                "player": "Bellingham",
                "status": "suspended",
                "source": "manual",
                "confidence": 1.0,
            },
        ]

        bundle = signals.build_sports_signals(
            "Will England reach the semifinals of the 2026 FIFA World Cup?",
            source,
            facts,
        )

        lineup = bundle["signals"].get("lineup_signal")
        self.assertIsNotNone(lineup)
        self.assertEqual(lineup["level"], "high")
        self.assertEqual(lineup["direction"], "supports_no")
        self.assertEqual(lineup["unavailable_starters"], 2)

    def test_lineup_signal_absent_when_no_starters_unavailable(self):
        source = {
            "type": "sports_event",
            "category": "team_progression",
            "tournament": WORLD_CUP_TOURNAMENT,
            "source_id": "world-cup-2026:england-semifinal",
            "entities": ["England", WORLD_CUP_TOURNAMENT],
        }
        facts = [
            {
                "fact_id": "l1",
                "kind": "lineup",
                "tournament": WORLD_CUP_TOURNAMENT,
                "team": "England",
                "player": "Kane",
                "status": "starting",
                "source": "manual",
                "confidence": 1.0,
            },
        ]

        bundle = signals.build_sports_signals(
            "Will England reach the semifinals of the 2026 FIFA World Cup?",
            source,
            facts,
        )

        self.assertNotIn("lineup_signal", bundle["signals"])

    def test_suspension_signal_multiple_players(self):
        source = {
            "type": "sports_event",
            "category": "team_progression",
            "tournament": WORLD_CUP_TOURNAMENT,
            "source_id": "world-cup-2026:argentina-semifinal",
            "entities": ["Argentina", WORLD_CUP_TOURNAMENT],
        }
        facts = [
            {
                "fact_id": "sus1",
                "kind": "suspension",
                "tournament": WORLD_CUP_TOURNAMENT,
                "team": "Argentina",
                "player": "Di Maria",
                "status": "suspended",
                "source": "manual",
                "confidence": 1.0,
            },
            {
                "fact_id": "sus2",
                "kind": "suspension",
                "tournament": WORLD_CUP_TOURNAMENT,
                "team": "Argentina",
                "player": "De Paul",
                "status": "suspended",
                "source": "manual",
                "confidence": 1.0,
            },
        ]

        bundle = signals.build_sports_signals(
            "Will Argentina reach the semifinals of the 2026 FIFA World Cup?",
            source,
            facts,
        )

        suspension = bundle["signals"].get("suspension_signal")
        self.assertIsNotNone(suspension)
        self.assertEqual(suspension["level"], "high")
        self.assertEqual(suspension["direction"], "supports_no")
        self.assertEqual(suspension["suspended_count"], 2)
        self.assertIn("Di Maria", suspension["summary"])

    def test_suspension_signal_absent_when_no_suspensions(self):
        source = {
            "type": "sports_event",
            "category": "team_progression",
            "tournament": WORLD_CUP_TOURNAMENT,
            "source_id": "world-cup-2026:argentina-semifinal",
            "entities": ["Argentina", WORLD_CUP_TOURNAMENT],
        }
        facts = [{
            "fact_id": "q1",
            "kind": "qualification",
            "tournament": WORLD_CUP_TOURNAMENT,
            "team": "Argentina",
            "status": "qualified",
            "source": "manual",
            "confidence": 1.0,
        }]

        bundle = signals.build_sports_signals(
            "Will Argentina reach the semifinals of the 2026 FIFA World Cup?",
            source,
            facts,
        )

        self.assertNotIn("suspension_signal", bundle["signals"])


if __name__ == "__main__":
    unittest.main()
