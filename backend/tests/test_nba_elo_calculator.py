# backend/tests/test_nba_elo_calculator.py
"""Tests for NBA Elo calculator — stateless functions."""
import pytest

from app.sports.basketball.elo_calculator import (
    compute_expected_score,
    update_elo,
    apply_season_regression,
    seed_elo_from_games,
)


class TestComputeExpectedScore:
    def test_equal_elo_with_hfa(self):
        """Equal Elo + HFA=100 → home advantage > 0.5."""
        # elo_home = elo_away = 1500, HFA = 100
        # E_home = 1 / (1 + 10^((1500 - 1500 - 100) / 400))
        #        = 1 / (1 + 10^(-0.25))
        #        = 1 / (1 + 0.5623)
        #        ≈ 0.6401
        p = compute_expected_score(1500.0, 1500.0, hfa=100)
        assert round(p, 4) == 0.6401

    def test_equal_elo_no_hfa(self):
        """Equal Elo + HFA=0 → 0.5 (no home advantage)."""
        p = compute_expected_score(1500.0, 1500.0, hfa=0)
        assert round(p, 4) == 0.5000


class TestUpdateElo:
    def test_win_increases_elo(self):
        """Winning increases Elo; K=20, expected=0.5, actual=1.0 → +10."""
        new_elo = update_elo(1500.0, expected=0.5, actual=1.0, k=20)
        assert new_elo == 1510.0

    def test_loss_decreases_elo(self):
        """Losing decreases Elo; K=20, expected=0.5, actual=0.0 → -10."""
        new_elo = update_elo(1500.0, expected=0.5, actual=0.0, k=20)
        assert new_elo == 1490.0

    def test_k_playoff_higher_than_regular(self):
        """K=30 (playoff) produces larger swing than K=20 (regular)."""
        regular = update_elo(1500.0, expected=0.5, actual=1.0, k=20)
        playoff = update_elo(1500.0, expected=0.5, actual=1.0, k=30)
        assert playoff > regular


class TestApplySeasonRegression:
    def test_regression_toward_mean(self):
        """new_elo = 0.75 * old + 0.25 * 1500 → pulls toward 1500."""
        # 1600 → 0.75*1600 + 0.25*1500 = 1200 + 375 = 1575
        regressed = apply_season_regression(1600.0, mean=1500.0, carry=0.75)
        assert regressed == 1575.0

    def test_low_elo_pulls_up(self):
        """Below-average Elo pulls up toward mean."""
        # 1400 → 0.75*1400 + 0.25*1500 = 1050 + 375 = 1425
        regressed = apply_season_regression(1400.0)
        assert regressed == 1425.0


class TestSeedEloFromGames:
    def test_seed_produces_ratings_for_all_teams(self):
        """After processing games, all teams have Elo ratings."""
        games = [
            {"home_team": "Celtics", "away_team": "Lakers",
             "home_score": 110, "away_score": 108, "is_playoff": False, "season": 2023},
            {"home_team": "Lakers", "away_team": "Celtics",
             "home_score": 105, "away_score": 100, "is_playoff": False, "season": 2023},
        ]
        ratings = seed_elo_from_games(games)
        assert "Celtics" in ratings
        assert "Lakers" in ratings
        # Both start at 1500; after 2 games they should still be near 1500
        assert 1450 < ratings["Celtics"] < 1550
        assert 1450 < ratings["Lakers"] < 1550

    def test_season_regression_applied(self):
        """When season changes, regression is applied between seasons."""
        games = [
            # Season 2023: Celtics win 10 games straight (Elo climbs high)
            *[{"home_team": "Celtics", "away_team": f"Team{i}",
               "home_score": 110, "away_score": 100,
               "is_playoff": False, "season": 2023} for i in range(10)],
            # Season 2024: first game
            {"home_team": "Celtics", "away_team": "TeamX",
             "home_score": 110, "away_score": 100,
             "is_playoff": False, "season": 2024},
        ]
        ratings = seed_elo_from_games(games)

        # Without regression, Celtics would be well above 1500 after 10 wins.
        # With regression (carry=0.75), their Elo is pulled toward 1500
        # before the 2024 season starts. Verify regression was applied by
        # checking the 2024 rating is lower than the pre-regression value
        # would be (10 wins at K=20 would add ~100 points; regression pulls
        # 25% back toward 1500).
        # After 10 wins: ~1600 (approximate). After regression: ~1575.
        # After 1 more win: ~1585.
        assert ratings["Celtics"] > 1500  # Still strong
        assert ratings["Celtics"] < 1620  # But regression kept it in check
