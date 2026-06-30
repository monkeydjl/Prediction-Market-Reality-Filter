import unittest

from app.services.world_cup_factor_service import _apply_signal_injury_impact


class WorldCupFactorServiceTests(unittest.TestCase):
    def test_lineup_signal_applies_per_starter_penalty_before_cap(self):
        factors = {
            "home_team": {"team_name": "Home"},
            "away_team": {"team_name": "Away"},
        }
        signals = {
            "signals": {
                "lineup_signal": {
                    "team": "Home",
                    "unavailable_starters": 1,
                    "importance": 1.0,
                }
            }
        }

        _apply_signal_injury_impact(factors, signals)

        self.assertEqual(factors["home_team"]["injury_impact"], -0.1)
        self.assertEqual(factors["away_team"]["injury_impact"], 0.0)

    def test_suspension_signal_applies_per_player_penalty_before_cap(self):
        factors = {
            "home_team": {"team_name": "Home"},
            "away_team": {"team_name": "Away"},
        }
        signals = {
            "signals": {
                "suspension_signal": {
                    "team": "Away",
                    "suspended_count": 1,
                }
            }
        }

        _apply_signal_injury_impact(factors, signals)

        self.assertEqual(factors["home_team"]["injury_impact"], 0.0)
        self.assertEqual(factors["away_team"]["injury_impact"], -0.08)


if __name__ == "__main__":
    unittest.main()
