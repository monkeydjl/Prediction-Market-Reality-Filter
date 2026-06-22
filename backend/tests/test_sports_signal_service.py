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


if __name__ == "__main__":
    unittest.main()
