import unittest

from app.services.world_cup_tournament_state_service import (
    build_qualification_state,
    qualification_cache_signature,
)


class WorldCupTournamentStateServiceTests(unittest.TestCase):
    def test_build_qualification_state_extracts_eliminated_and_qualified_teams(self):
        facts = [
            {
                "kind": "qualification",
                "team": "Brazil",
                "status": "eliminated",
                "already_eliminated": True,
                "observed_at": "2026-07-05T12:00:00Z",
            },
            {
                "kind": "qualification",
                "team": "Argentina",
                "status": "qualified",
                "already_qualified": True,
                "observed_at": "2026-07-05T13:00:00Z",
            },
            {"kind": "qualification", "team": "Canada", "status": "alive"},
        ]

        state = build_qualification_state(facts)

        self.assertEqual(state["eliminated_teams"], ["Brazil"])
        self.assertEqual(state["qualified_teams"], ["Argentina"])
        self.assertEqual(state["qualification_fact_count"], 3)
        self.assertEqual(state["latest_observed_at"], "2026-07-05T13:00:00Z")

    def test_build_qualification_state_eliminates_knockout_match_result_losers(self):
        facts = [
            {
                "kind": "match_result",
                "stage": "ROUND_OF_32",
                "home_team": "Germany",
                "away_team": "Paraguay",
                "score": {"home": 4, "away": 5},
                "observed_at": "2026-06-30T02:26:08Z",
            },
            {
                "kind": "match_result",
                "stage": "ROUND_OF_16",
                "home_team": "Canada",
                "away_team": "Morocco",
                "score": {"home": 0, "away": 3},
                "observed_at": "2026-07-04T23:33:28Z",
            },
        ]

        state = build_qualification_state(facts)

        self.assertEqual(state["eliminated_teams"], ["Canada", "Germany"])
        self.assertEqual(state["qualification_fact_count"], 0)
        self.assertEqual(state["match_result_fact_count"], 2)
        self.assertEqual(state["knockout_result_fact_count"], 2)
        self.assertEqual(state["latest_observed_at"], "2026-07-04T23:33:28Z")

    def test_build_qualification_state_keeps_current_state_mutually_exclusive(self):
        facts = [
            {
                "kind": "match_result",
                "stage": "ROUND_OF_16",
                "home_team": "Brazil",
                "away_team": "Morocco",
                "score": {"home": 0, "away": 1},
                "observed_at": "2026-07-04T23:33:28Z",
            },
            {
                "kind": "qualification",
                "team": "Brazil",
                "status": "qualified",
                "already_qualified": True,
                "observed_at": "2026-07-05T13:00:00Z",
            },
        ]

        state = build_qualification_state(facts)

        self.assertEqual(state["eliminated_teams"], ["Brazil"])
        self.assertEqual(state["qualified_teams"], ["Morocco"])
        self.assertEqual(
            set(state["eliminated_teams"]) & set(state["qualified_teams"]),
            set(),
        )

    def test_build_qualification_state_eliminates_group_teams_absent_from_first_knockout_round(self):
        facts = [
            {
                "kind": "match_result",
                "stage": "GROUP_STAGE",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "score": {"home": 2, "away": 0},
                "observed_at": "2026-06-23T21:36:30Z",
            },
            {
                "kind": "match_result",
                "stage": "GROUP_STAGE",
                "home_team": "South Korea",
                "away_team": "Czechia",
                "score": {"home": 2, "away": 1},
                "observed_at": "2026-06-23T21:36:30Z",
            },
            {
                "kind": "match_result",
                "stage": "ROUND_OF_32",
                "home_team": "Mexico",
                "away_team": "South Korea",
                "score": {"home": 1, "away": 0},
                "observed_at": "2026-06-30T02:26:08Z",
            },
        ]

        state = build_qualification_state(facts)

        self.assertEqual(state["eliminated_teams"], ["Czechia", "South Africa", "South Korea"])
        self.assertEqual(state["inferred_group_eliminated_count"], 2)

    def test_qualification_cache_signature_changes_when_elimination_changes(self):
        first = build_qualification_state([
            {"team": "Brazil", "status": "alive", "observed_at": "2026-07-05T12:00:00Z"}
        ])
        second = build_qualification_state([
            {"team": "Brazil", "status": "eliminated", "observed_at": "2026-07-05T12:00:00Z"}
        ])

        self.assertNotEqual(
            qualification_cache_signature(first),
            qualification_cache_signature(second),
        )


if __name__ == "__main__":
    unittest.main()
