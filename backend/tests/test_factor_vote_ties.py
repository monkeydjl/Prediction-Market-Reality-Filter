# backend/tests/test_factor_vote_ties.py
"""A factor that measured its inputs level must cast no vote (E20).

Every engine used to resolve a tie by position -- four with ``p >= 0.5`` and
six with ``max(probs, key=...)`` -- and every engine lists ``home_win`` first,
so a factor with exactly zero evidence was published as a home vote and counted
by ``factor_agreement``.

This is the common case, not a boundary: equal rest days give ``p_rest == 0.5``
exactly, which holds on 88.5% of live MLB, 56.9% of NBA and 46.2% of NHL
fixtures (11,082 of 16,036).
"""
from __future__ import annotations

import pytest

from app.kernel.engines.confidence import (
    binary_factor_vote,
    factor_agreement,
    factor_vote,
)


class TestFactorVote:
    def test_a_tie_at_the_peak_casts_no_vote(self):
        assert factor_vote({"home_win": 0.4, "draw": 0.2, "away_win": 0.4}) is None

    def test_binary_tie_casts_no_vote(self):
        assert factor_vote({"home_win": 0.5, "away_win": 0.5}) is None

    def test_a_strict_peak_still_votes(self):
        assert factor_vote({"home_win": 0.5, "draw": 0.25, "away_win": 0.25}) == (
            "home_win"
        )
        assert factor_vote({"home_win": 0.2, "draw": 0.3, "away_win": 0.5}) == (
            "away_win"
        )

    def test_a_tie_below_the_peak_does_not_block_the_vote(self):
        """Only the top is contested; 2nd == 3rd is irrelevant."""
        assert factor_vote({"home_win": 0.6, "draw": 0.2, "away_win": 0.2}) == (
            "home_win"
        )

    def test_near_tie_is_not_a_tie(self):
        assert factor_vote({"home_win": 0.5001, "away_win": 0.4999}) == "home_win"
        assert factor_vote({"home_win": 0.4999, "away_win": 0.5001}) == "away_win"

    def test_empty_and_single_key(self):
        assert factor_vote({}) is None
        assert factor_vote({"home_win": 1.0}) == "home_win"
        assert factor_vote({"home_win": 0.0}) == "home_win"

    def test_the_result_does_not_depend_on_key_order(self):
        """``max()`` returns the first key at a tie; ``factor_vote`` returns None.

        Both orders are constructed here, so the test cannot pass merely because
        the dict happened to list ``home_win`` first.
        """
        home_first = {"home_win": 0.5, "away_win": 0.5}
        away_first = {"away_win": 0.5, "home_win": 0.5}
        assert max(home_first, key=lambda k: home_first[k]) == "home_win"
        assert max(away_first, key=lambda k: away_first[k]) == "away_win"
        assert factor_vote(home_first) is None
        assert factor_vote(away_first) is None


class TestBinaryFactorVote:
    def test_exactly_level_casts_no_vote(self):
        assert binary_factor_vote(0.5) is None

    def test_either_side_of_level_votes(self):
        assert binary_factor_vote(0.5000001) == "home_win"
        assert binary_factor_vote(0.4999999) == "away_win"
        assert binary_factor_vote(0.0) == "away_win"
        assert binary_factor_vote(1.0) == "home_win"

    def test_the_reachable_rest_values_are_covered(self):
        """rest_diff of 0 / +1 / -1 through the shared 0.5 + diff*0.03 form."""
        assert binary_factor_vote(0.5 + 0 * 0.03) is None
        assert binary_factor_vote(0.5 + 1 * 0.03) == "home_win"
        assert binary_factor_vote(0.5 + -1 * 0.03) == "away_win"


class TestAgreementExcludesNonVoters:
    def test_a_level_factor_leaves_both_numerator_and_denominator(self):
        with_level = factor_agreement(
            ["home_win", "away_win", None], final_outcome="away_win"
        )
        assert with_level == pytest.approx(0.5)

    def test_counting_the_level_factor_as_home_understates_an_away_pick(self):
        """The defect's arithmetic, side by side with the fix's."""
        defect = factor_agreement(
            ["home_win", "away_win", "home_win"], final_outcome="away_win"
        )
        fixed = factor_agreement(
            ["home_win", "away_win", None], final_outcome="away_win"
        )
        assert defect == pytest.approx(1 / 3)
        assert fixed == pytest.approx(1 / 2)
        assert fixed > defect

    def test_and_overstates_a_home_pick(self):
        defect = factor_agreement(
            ["home_win", "away_win", "home_win"], final_outcome="home_win"
        )
        fixed = factor_agreement(
            ["home_win", "away_win", None], final_outcome="home_win"
        )
        assert defect == pytest.approx(2 / 3)
        assert fixed == pytest.approx(1 / 2)
        assert fixed < defect

    def test_all_level_falls_back_to_the_documented_neutral(self):
        assert factor_agreement([None, None], final_outcome="home_win") == 0.5
