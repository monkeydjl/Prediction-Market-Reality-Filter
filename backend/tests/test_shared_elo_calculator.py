# backend/tests/test_shared_elo_calculator.py
"""Tests for shared Elo calculator — stateless functions consistency with NBA version."""
import pytest

from app.sports._shared.elo_calculator import (
    compute_expected_score,
    update_elo,
    apply_season_regression,
    seed_elo_from_games,
)
from app.sports.basketball.elo_calculator import (
    compute_expected_score as nba_compute_expected_score,
    update_elo as nba_update_elo,
    apply_season_regression as nba_apply_season_regression,
    seed_elo_from_games as nba_seed_elo_from_games,
)


class TestSharedEloConsistency:
    """Verify shared Elo functions produce identical results to NBA's version."""

    def test_compute_expected_score_matches_nba(self):
        """Shared compute_expected_score == NBA's compute_expected_score."""
        for hfa in (0, 50, 55, 100):
            for elo_home in (1400.0, 1500.0, 1600.0, 1800.0):
                for elo_away in (1400.0, 1500.0, 1600.0, 1800.0):
                    shared = compute_expected_score(elo_home, elo_away, hfa)
                    nba = nba_compute_expected_score(elo_home, elo_away, hfa)
                    assert shared == nba, f"mismatch hfa={hfa} h={elo_home} a={elo_away}"

    def test_update_elo_matches_nba(self):
        """Shared update_elo == NBA's update_elo."""
        for elo in (1400.0, 1500.0, 1600.0):
            for expected in (0.3, 0.5, 0.7):
                for actual in (0.0, 1.0):
                    for k in (20, 30):
                        shared = update_elo(elo, expected, actual, k)
                        nba = nba_update_elo(elo, expected, actual, k)
                        assert shared == nba

    def test_apply_season_regression_matches_nba(self):
        """Shared apply_season_regression == NBA's apply_season_regression."""
        for elo in (1300.0, 1500.0, 1700.0):
            for mean in (1500.0, 1600.0):
                for carry in (0.7, 0.75, 0.8):
                    shared = apply_season_regression(elo, mean, carry)
                    nba = nba_apply_season_regression(elo, mean, carry)
                    assert shared == nba

    def test_seed_elo_from_games_matches_nba(self):
        """Shared seed_elo_from_games == NBA's seed_elo_from_games."""
        games = [
            {"home_team": "Yankees", "away_team": "Red Sox",
             "home_score": 5, "away_score": 3, "is_playoff": False, "season": 2023},
            {"home_team": "Red Sox", "away_team": "Yankees",
             "home_score": 7, "away_score": 2, "is_playoff": False, "season": 2023},
            {"home_team": "Yankees", "away_team": "Red Sox",
             "home_score": 4, "away_score": 4, "is_playoff": False, "season": 2024},
        ]
        shared = seed_elo_from_games(games, hfa=50, k_regular=20, k_playoff=30)
        nba = nba_seed_elo_from_games(games, hfa=50, k_regular=20, k_playoff=30)
        assert shared == nba
        assert "Yankees" in shared
        assert "Red Sox" in shared
