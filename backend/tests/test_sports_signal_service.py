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


def _team_stat_fact(team: str, group: str, stat_name: str, stat_value) -> dict:
    return {
        "fact_id": f"wc2026:team_stat:{team.lower().replace(' ', '-')}:{stat_name.lower()}",
        "kind": "team_stat",
        "tournament": WORLD_CUP_TOURNAMENT,
        "team": team,
        "group": group,
        "stat_name": stat_name,
        "stat_value": stat_value,
        "confidence": 1.0,
        "observed_at": "2026-06-01T00:00:00Z",
    }


def _qualification_fact(team: str, group: str) -> dict:
    return {
        "fact_id": f"wc2026:qualification:{team.lower().replace(' ', '-')}",
        "kind": "qualification",
        "tournament": WORLD_CUP_TOURNAMENT,
        "team": team,
        "group": group,
        "status": "group_stage",
        "stage": "group_stage",
        "confidence": 1.0,
        "observed_at": "2026-06-23T00:00:00Z",
    }


def _group_source(team: str, category: str = "group_stage") -> dict:
    return {
        "type": "sports_event",
        "tournament": WORLD_CUP_TOURNAMENT,
        "category": category,
        "entities": [team, WORLD_CUP_TOURNAMENT],
    }


class GroupStrengthSignalTests(unittest.TestCase):
    def test_signal_supports_yes_when_team_ranked_above_group_average(self):
        # Argentina FIFA rank 1 vs group of four (1, 12, 30, 40) -> avg 20.75
        # spread = 19.75 -> high; direction = supports_yes (group_stage is yes-framed)
        facts = [
            _qualification_fact("Argentina", "Group A"),
            _team_stat_fact("Argentina", "Group A", "fifa_rank", 1),
            _team_stat_fact("Rival B", "Group A", "fifa_rank", 12),
            _team_stat_fact("Rival C", "Group A", "fifa_rank", 30),
            _team_stat_fact("Rival D", "Group A", "fifa_rank", 40),
        ]
        bundle = signals.build_sports_signals(
            "Will Argentina win its group at the 2026 FIFA World Cup?",
            _group_source("Argentina"),
            facts,
        )
        sig = bundle["signals"].get("group_strength_signal")
        self.assertIsNotNone(sig)
        self.assertEqual(sig["direction"], "supports_yes")
        self.assertEqual(sig["level"], "high")
        self.assertEqual(sig["team_rank"], 1)
        # Implementation rounds group_avg_rank to 1 decimal place (20.75 -> 20.8)
        self.assertAlmostEqual(sig["group_avg_rank"], 20.8)
        self.assertEqual(sig["group"], "Group A")

    def test_signal_supports_no_when_team_ranked_below_group_average(self):
        # Weak team rank 40 vs strong group (1, 5, 8, 40) -> avg 13.5
        # spread = 26.5 -> high; direction = supports_no
        facts = [
            _qualification_fact("Weak Side", "Group B"),
            _team_stat_fact("Weak Side", "Group B", "fifa_rank", 40),
            _team_stat_fact("Strong A", "Group B", "fifa_rank", 1),
            _team_stat_fact("Strong C", "Group B", "fifa_rank", 5),
            _team_stat_fact("Strong D", "Group B", "fifa_rank", 8),
        ]
        bundle = signals.build_sports_signals(
            "Will Weak Side win its group at the 2026 FIFA World Cup?",
            _group_source("Weak Side"),
            facts,
        )
        sig = bundle["signals"].get("group_strength_signal")
        self.assertIsNotNone(sig)
        self.assertEqual(sig["direction"], "supports_no")
        self.assertEqual(sig["level"], "high")
        self.assertEqual(sig["team_rank"], 40)

    def test_signal_returns_none_when_no_group_on_team(self):
        # No fact carries a `group` for the target team -> cannot resolve group -> None
        facts = [
            {
                "fact_id": "wc2026:team_stat:argentina:fifa_rank",
                "kind": "team_stat",
                "tournament": WORLD_CUP_TOURNAMENT,
                "team": "Argentina",
                "stat_name": "fifa_rank",
                "stat_value": 1,
                "confidence": 1.0,
            },
        ]
        bundle = signals.build_sports_signals(
            "Will Argentina win its group at the 2026 FIFA World Cup?",
            _group_source("Argentina"),
            facts,
        )
        self.assertNotIn("group_strength_signal", bundle["signals"])

    def test_signal_returns_none_when_only_one_team_in_group(self):
        # Only one team_stat in the group -> len(ranks) < 2 -> None
        facts = [
            _qualification_fact("Argentina", "Group A"),
            _team_stat_fact("Argentina", "Group A", "fifa_rank", 1),
        ]
        bundle = signals.build_sports_signals(
            "Will Argentina win its group at the 2026 FIFA World Cup?",
            _group_source("Argentina"),
            facts,
        )
        self.assertNotIn("group_strength_signal", bundle["signals"])


class DisciplineSignalGrainTests(unittest.TestCase):
    """`threshold_progress` is shown to the operator, so it must not double count.

    The same card arrives as a per-card `discipline` fact and inside the
    per-match total on `match_result`; adding both put the operator's progress
    bar at twice the real value.
    """

    _SOURCE = {
        "type": "sports_event",
        "category": "discipline",
        "tournament": WORLD_CUP_TOURNAMENT,
        "source_id": "world-cup-2026:red-cards-eight",
        "entities": [WORLD_CUP_TOURNAMENT, "red cards"],
    }
    _QUESTION = "Will the 2026 FIFA World Cup have at least 8 red cards?"

    def _signal(self, facts):
        bundle = signals.build_sports_signals(self._QUESTION, self._SOURCE, facts)
        return bundle["signals"]["discipline_signal"]

    def test_both_grains_for_one_match_report_the_real_card_count(self):
        facts = [
            {
                "fact_id": "card-1", "kind": "discipline", "tournament": WORLD_CUP_TOURNAMENT,
                "match_id": "m1", "player": "P1", "status": "red_card",
                "red_cards": 1, "confidence": 1.0,
            },
            {
                "fact_id": "card-2", "kind": "discipline", "tournament": WORLD_CUP_TOURNAMENT,
                "match_id": "m1", "player": "P2", "status": "red_card",
                "red_cards": 1, "confidence": 1.0,
            },
            {
                "fact_id": "wc2026:match:m1", "kind": "match_result",
                "tournament": WORLD_CUP_TOURNAMENT, "match_id": "m1",
                "status": "finished", "red_cards": 2, "confidence": 1.0,
            },
        ]
        signal = self._signal(facts)
        self.assertEqual(signal["red_card_total"], 2)
        self.assertEqual(signal["threshold_progress"], 0.25)
        self.assertEqual(signal["direction"], "neutral")

    def test_a_suspension_that_reaches_the_signal_is_not_counted_as_a_card(self):
        # `applies_to` carrying the source_id is the one path that admits a
        # suspension fact past `_is_relevant_fact` for a discipline event; the
        # category filter alone would drop it before the tally.
        facts = [
            {
                "fact_id": "card-1", "kind": "discipline", "tournament": WORLD_CUP_TOURNAMENT,
                "match_id": "m1", "player": "P1", "status": "red_card",
                "red_cards": 1, "confidence": 1.0,
            },
            {
                "fact_id": "susp-1", "kind": "suspension", "tournament": WORLD_CUP_TOURNAMENT,
                "match_id": "m1", "player": "P1", "status": "suspended",
                "red_cards": 1, "applies_to": ["world-cup-2026:red-cards-eight"],
                "confidence": 1.0,
            },
        ]
        signal = self._signal(facts)
        # The card is counted once, from the fact that reports the card itself.
        self.assertEqual(signal["red_card_total"], 1)
        # The suspension still counts as a suspension.
        self.assertEqual(signal["suspensions"], 1)


if __name__ == "__main__":
    unittest.main()
