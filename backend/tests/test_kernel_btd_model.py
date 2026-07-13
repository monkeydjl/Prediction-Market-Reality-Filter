"""Equivalence tests for migrated BTD model."""
import pytest

from app.kernel.engines.btd_model import calculate_btd_probabilities
from app.services.world_cup_engines.world_cup_btd_model import (
    calculate_btd_probabilities as old_calculate_btd_probabilities,
)


class TestBTDEquivalence:
    """Verify the kernel BTD model produces identical output to the old one."""

    @pytest.mark.parametrize("elo_home,elo_away,is_neutral,is_knockout", [
        (1900, 1800, True, False),
        (1500, 1600, True, False),
        (2000, 2000, True, False),
        (1900, 1800, True, True),
        (1700, 2100, True, True),
        (1850, 1850, False, False),
        (2200, 1600, False, True),
    ])
    def test_output_matches_old_engine(self, elo_home, elo_away, is_neutral, is_knockout):
        # The original world_cup_btd_model.calculate_btd_probabilities declares
        # is_neutral/is_knockout as keyword-only (after ``*``), so they must be
        # passed by name. The kernel copy accepts either form; we use keywords
        # for both to exercise them identically.
        old = old_calculate_btd_probabilities(
            elo_home, elo_away, is_neutral=is_neutral, is_knockout=is_knockout
        )
        new = calculate_btd_probabilities(
            elo_home, elo_away, is_neutral=is_neutral, is_knockout=is_knockout
        )
        assert new == old


class TestBTDProperties:
    def test_probabilities_sum_to_one(self):
        probs = calculate_btd_probabilities(1900, 1800, is_neutral=True, is_knockout=False)
        total = probs["home_win"] + probs["draw"] + probs["away_win"]
        assert abs(total - 1.0) < 1e-6

    def test_stronger_team_higher_win_prob(self):
        probs = calculate_btd_probabilities(2000, 1500, is_neutral=True, is_knockout=False)
        assert probs["home_win"] > probs["away_win"]

    def test_equal_teams_equal_prob(self):
        probs = calculate_btd_probabilities(1800, 1800, is_neutral=True, is_knockout=False)
        assert abs(probs["home_win"] - probs["away_win"]) < 1e-6

    def test_knockout_reduces_draw(self):
        group = calculate_btd_probabilities(1800, 1800, is_neutral=True, is_knockout=False)
        knockout = calculate_btd_probabilities(1800, 1800, is_neutral=True, is_knockout=True)
        assert knockout["draw"] < group["draw"]
