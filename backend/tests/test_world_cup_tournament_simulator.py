import unittest
from unittest.mock import patch

from app.services.world_cup_tournament_simulator import simulate_tournament


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

        def fake_group(group_teams, elo_cache, odds_cache=None):
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


if __name__ == "__main__":
    unittest.main()
