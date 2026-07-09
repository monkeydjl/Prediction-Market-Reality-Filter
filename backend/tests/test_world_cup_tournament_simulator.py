import unittest
from unittest.mock import patch

from app.services.world_cup_tournament_simulator import (
    simulate_remaining_knockout,
    simulate_tournament,
)


def _fake_engine(_engine_name):
    def _predict(**kwargs):
        elo_home = float(kwargs.get("elo_home", 1500.0))
        elo_away = float(kwargs.get("elo_away", 1500.0))
        total = max(1.0, elo_home + elo_away)
        home = max(0.05, min(0.9, elo_home / total))
        away = max(0.05, min(0.9, elo_away / total))
        draw = 0.15
        scale = 1.0 - draw
        side_total = home + away
        return {
            "outcome_probabilities": {
                "home_win": home / side_total * scale,
                "draw": draw,
                "away_win": away / side_total * scale,
            },
            "predicted_score": {
                "home": max(0.0, elo_home / 700.0),
                "away": max(0.0, elo_away / 700.0),
            },
        }

    return _predict


class WorldCupTournamentSimulatorTests(unittest.TestCase):
    def test_full_2026_field_uses_32_team_knockout_without_crashing(self):
        groups = {
            f"GROUP_{group}": [f"{group}{seed}" for seed in range(1, 5)]
            for group in "ABCDEFGHIJKL"
        }
        elo_cache = {
            team: 1500.0
            for teams in groups.values()
            for team in teams
        }

        def fake_group(group_teams, elo_cache, odds_cache=None, prediction_cache=None):
            return [
                (team, 12 - index, 4 - index)
                for index, team in enumerate(group_teams)
            ]

        with (
            patch(
                "app.services.world_cup_tournament_simulator._simulate_group",
                side_effect=fake_group,
            ),
            patch(
                "app.services.world_cup_tournament_simulator._simulate_match",
                return_value={"home_win": 1.0, "draw": 0.0, "away_win": 0.0},
            ),
        ):
            result = simulate_tournament(
                groups=groups,
                elo_cache=elo_cache,
                num_simulations=3,
            )

        self.assertEqual(result["simulations"], 3)
        self.assertIn(result["most_likely_winner"], elo_cache)
        self.assertAlmostEqual(sum(result["win_probability"].values()), 1.0, places=3)
        self.assertGreater(max(result["reach_semifinal"].values()), 0)

    def test_eliminated_team_is_removed_from_title_progression_probability_results(self):
        groups = {
            "A": ["Brazil", "Argentina", "Canada", "Qatar"],
            "B": ["France", "Germany", "Japan", "USA"],
        }
        elo_cache = {
            "Brazil": 2400.0,
            "Argentina": 1800.0,
            "Canada": 1500.0,
            "Qatar": 1400.0,
            "France": 1900.0,
            "Germany": 1850.0,
            "Japan": 1700.0,
            "USA": 1650.0,
        }

        with patch("app.services.world_cup_tournament_simulator.get_engine", _fake_engine):
            result = simulate_tournament(
                groups,
                elo_cache=elo_cache,
                num_simulations=200,
                eliminated_teams={"Brazil"},
            )

        self.assertNotIn("Brazil", result["win_probability"])
        self.assertNotIn("Brazil", result["reach_final"])
        self.assertNotIn("Brazil", result["reach_semifinal"])
        self.assertNotEqual(result["most_likely_winner"], "Brazil")
        self.assertIn("Brazil", result["excluded_teams"])

    def test_simulation_reports_skipped_runs_when_active_field_is_too_small(self):
        groups = {"A": ["Brazil", "Argentina"], "B": ["France", "Germany"]}
        elo_cache = {team: 1500.0 for teams in groups.values() for team in teams}

        with patch("app.services.world_cup_tournament_simulator.get_engine", _fake_engine):
            result = simulate_tournament(
                groups,
                elo_cache=elo_cache,
                num_simulations=25,
                eliminated_teams={"Brazil", "Argentina", "France"},
            )

        self.assertEqual(result["completed_simulations"], 0)
        self.assertEqual(result["skipped_simulations"], 25)
        self.assertEqual(result["win_probability"]["Germany"], 0.0)
        self.assertEqual(result["most_likely_winner"], None)

    def test_remaining_knockout_uses_finished_results_and_only_simulates_unplayed_matches(self):
        fixtures = [
            {
                "stage": "ROUND_OF_16",
                "status": "finished",
                "home_team": "Canada",
                "away_team": "Morocco",
                "home_score": 0,
                "away_score": 3,
            },
            {
                "stage": "ROUND_OF_16",
                "status": "finished",
                "home_team": "Paraguay",
                "away_team": "France",
                "home_score": 0,
                "away_score": 1,
            },
            {
                "stage": "ROUND_OF_16",
                "status": "scheduled",
                "home_team": "Portugal",
                "away_team": "Spain",
            },
            {
                "stage": "ROUND_OF_16",
                "status": "scheduled",
                "home_team": "United States",
                "away_team": "Belgium",
            },
        ]
        elo_cache = {
            "Canada": 2500.0,
            "Morocco": 1500.0,
            "Paraguay": 1500.0,
            "France": 1700.0,
            "Portugal": 1600.0,
            "Spain": 1800.0,
            "United States": 1550.0,
            "Belgium": 1650.0,
        }

        with patch("app.services.world_cup_tournament_simulator.get_engine", _fake_engine):
            result = simulate_remaining_knockout(
                fixtures=fixtures,
                elo_cache=elo_cache,
                num_simulations=100,
            )

        self.assertNotIn("Canada", result["win_probability"])
        self.assertNotIn("Paraguay", result["win_probability"])
        self.assertIn("Morocco", result["win_probability"])
        self.assertIn("France", result["win_probability"])
        self.assertEqual(result["locked_result_count"], 2)
        self.assertEqual(result["simulated_match_count"], 2)
        self.assertEqual(result["remaining_team_count"], 6)
        self.assertEqual(result["simulation_basis"], "knockout_fixtures")
        self.assertEqual(
            result["locked_results"],
            [
                {
                    "stage": "ROUND_OF_16",
                    "status": "finished",
                    "home_team": "Canada",
                    "away_team": "Morocco",
                    "home_score": 0,
                    "away_score": 3,
                    "winner": "Morocco",
                    "loser": "Canada",
                },
                {
                    "stage": "ROUND_OF_16",
                    "status": "finished",
                    "home_team": "Paraguay",
                    "away_team": "France",
                    "home_score": 0,
                    "away_score": 1,
                    "winner": "France",
                    "loser": "Paraguay",
                },
            ],
        )
        self.assertEqual(
            result["simulated_fixtures"],
            [
                {
                    "stage": "ROUND_OF_16",
                    "status": "scheduled",
                    "home_team": "Portugal",
                    "away_team": "Spain",
                },
                {
                    "stage": "ROUND_OF_16",
                    "status": "scheduled",
                    "home_team": "United States",
                    "away_team": "Belgium",
                },
            ],
        )

    def test_remaining_knockout_uses_verified_winner_for_tied_penalty_result(self):
        fixtures = [
            {
                "stage": "ROUND_OF_16",
                "status": "finished",
                "home_team": "Switzerland",
                "away_team": "Colombia",
                "home_score": 0,
                "away_score": 0,
                "winner": "Switzerland",
                "penalty_score": {"home": 4, "away": 3},
            },
            {
                "stage": "ROUND_OF_16",
                "status": "scheduled",
                "home_team": "Portugal",
                "away_team": "Spain",
            },
            {
                "stage": "ROUND_OF_16",
                "status": "scheduled",
                "home_team": "France",
                "away_team": "Belgium",
            },
            {
                "stage": "ROUND_OF_16",
                "status": "scheduled",
                "home_team": "Argentina",
                "away_team": "Germany",
            },
        ]
        elo_cache = {
            "Switzerland": 1800.0,
            "Colombia": 2500.0,
            "Portugal": 1600.0,
            "Spain": 1800.0,
            "France": 1700.0,
            "Belgium": 1650.0,
            "Argentina": 1750.0,
            "Germany": 1725.0,
        }

        with patch("app.services.world_cup_tournament_simulator.get_engine", _fake_engine):
            result = simulate_remaining_knockout(
                fixtures=fixtures,
                elo_cache=elo_cache,
                num_simulations=100,
            )

        self.assertIn("Switzerland", result["win_probability"])
        self.assertNotIn("Colombia", result["win_probability"])
        self.assertEqual(result["locked_result_count"], 1)
        self.assertEqual(
            result["locked_results"][0],
            {
                "stage": "ROUND_OF_16",
                "status": "finished",
                "home_team": "Switzerland",
                "away_team": "Colombia",
                "home_score": 0,
                "away_score": 0,
                "winner": "Switzerland",
                "loser": "Colombia",
                "penalty_score": {"home": 4, "away": 3},
            },
        )


if __name__ == "__main__":
    unittest.main()
