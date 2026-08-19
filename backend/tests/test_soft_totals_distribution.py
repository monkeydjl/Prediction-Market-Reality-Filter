"""Distribution properties of the soft O/U total (P1-O1).

The over/under used to be summed over a fixed 0..10-per-side score grid. At
basketball scale (~110 points per side) essentially none of the probability
mass falls inside that grid, so ``p_over`` collapsed to 0.0 and ``p_under`` to
1.0 on every NBA match — a wrong number rendered straight into the FE panel.
These tests pin the mean-scaled replacement.
"""
import math

import pytest

from app.kernel.engines.elo_odds_engine import (
    soft_totals_btts_analysis,
    soft_totals_from_scores,
)


# One case per sport scale, each with the line sitting on the expected total.
_SCALES = [
    ("football", 1.4, 1.4, 2.8),
    ("hockey", 3.0, 2.5, 5.5),
    ("baseball", 4.25, 4.25, 8.5),
    ("basketball", 110.0, 110.0, 220.0),
]


def _grid_p_over(lh: float, la: float, line: float, max_g: int) -> float:
    """P(total > line) by explicit 2-D convolution — an independent reference."""
    def pmf(k: int, lam: float) -> float:
        return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))

    over = 0.0
    mass = 0.0
    for h in range(max_g + 1):
        ph = pmf(h, lh)
        for a in range(max_g + 1):
            joint = ph * pmf(a, la)
            mass += joint
            if h + a > line:
                over += joint
    return over / mass


class TestTotalDistribution:
    @pytest.mark.parametrize(("sport", "lh", "la", "line"), _SCALES)
    def test_line_on_the_expected_total_is_near_even(self, sport, lh, la, line):
        out = soft_totals_btts_analysis({"home": lh, "away": la}, line=line)
        # The regression this file exists for: basketball used to return 0.0.
        assert 0.35 < out["p_over"] < 0.65, sport
        assert 0.35 < out["p_under"] < 0.65, sport

    @pytest.mark.parametrize(("sport", "lh", "la", "line"), _SCALES)
    def test_over_and_under_sum_to_one(self, sport, lh, la, line):
        out = soft_totals_btts_analysis({"home": lh, "away": la}, line=line)
        assert out["p_over"] + out["p_under"] == pytest.approx(1.0, abs=1e-9), sport

    @pytest.mark.parametrize(("sport", "lh", "la", "line"), _SCALES)
    def test_matches_an_explicit_two_dimensional_convolution(
        self, sport, lh, la, line,
    ):
        # The sum of two independent Poissons is Poisson, so the 1-D reduction
        # must agree with a grid wide enough to hold the mass at this scale.
        max_g = int(math.ceil(max(lh, la) + 10.0 * math.sqrt(max(lh, la)))) + 10
        out = soft_totals_btts_analysis({"home": lh, "away": la}, line=line)
        assert out["p_over"] == pytest.approx(
            _grid_p_over(lh, la, line, max_g), abs=1e-4,
        ), sport

    @pytest.mark.parametrize(("sport", "lh", "la", "line"), _SCALES)
    def test_raising_the_line_lowers_p_over(self, sport, lh, la, line):
        low = soft_totals_btts_analysis({"home": lh, "away": la}, line=line - 1)
        high = soft_totals_btts_analysis({"home": lh, "away": la}, line=line + 1)
        assert high["p_over"] < low["p_over"], sport

    @pytest.mark.parametrize(("sport", "lh", "la", "line"), _SCALES)
    def test_raising_the_scores_raises_p_over(self, sport, lh, la, line):
        base = soft_totals_btts_analysis({"home": lh, "away": la}, line=line)
        more = soft_totals_btts_analysis(
            {"home": lh * 1.1, "away": la * 1.1}, line=line,
        )
        assert more["p_over"] > base["p_over"], sport

    def test_line_far_above_the_mean_is_effectively_certain_under(self):
        out = soft_totals_btts_analysis({"home": 110.0, "away": 110.0}, line=400.0)
        # Twelve standard deviations above the mean — negligible at any usable
        # bound, unlike the old 0.0 that came from truncating at 20 points.
        assert out["p_over"] < 0.001
        assert out["p_under"] > 0.999

    def test_zero_line_is_almost_certainly_over(self):
        out = soft_totals_btts_analysis({"home": 1.4, "away": 1.4}, line=0.0)
        # Only a 0-0 scoreline stays under a zero line.
        assert out["p_over"] == pytest.approx(1.0 - math.exp(-2.8), abs=1e-4)

    def test_very_large_total_stays_numerically_stable(self):
        # exp(-lam) underflows in naive form well before this scale.
        out = soft_totals_btts_analysis({"home": 300.0, "away": 300.0}, line=600.0)
        assert out["available"] is True
        assert 0.3 < out["p_over"] < 0.7
        assert out["p_over"] + out["p_under"] == pytest.approx(1.0, abs=1e-9)

    def test_btts_matches_the_closed_form(self):
        out = soft_totals_btts_analysis({"home": 1.6, "away": 1.2}, line=2.5)
        expected = 1.0 - math.exp(-1.6) - math.exp(-1.2) + math.exp(-2.8)
        assert out["p_btts_yes"] == pytest.approx(expected, abs=1e-4)
        assert out["p_btts_no"] == pytest.approx(1.0 - expected, abs=1e-4)

    @pytest.mark.parametrize("scores", [
        {"home": "a lot", "away": 1.2},
        {"home": None, "away": 1.2},
    ])
    def test_unusable_scores_report_unavailable(self, scores):
        assert soft_totals_btts_analysis(scores).get("available") is False

    def test_non_positive_scores_are_floored_not_rejected(self):
        out = soft_totals_btts_analysis({"home": 0.0, "away": 0.0}, line=0.5)
        assert out["available"] is True
        assert out["p_over"] < 0.2


class TestSportWrapper:
    def test_basketball_drops_btts_and_reports_a_usable_over(self):
        out = soft_totals_from_scores(
            {"home": 112.0, "away": 108.0}, line=220.0, sport="basketball",
        )
        assert "p_btts_yes" not in out
        assert "p_btts_no" not in out
        assert 0.35 < out["p_over"] < 0.65

    @pytest.mark.parametrize("sport", ["football", "soccer", "hockey"])
    def test_low_scoring_sports_keep_btts(self, sport):
        out = soft_totals_from_scores(
            {"home": 1.5, "away": 1.3}, line=2.5, sport=sport,
        )
        assert "p_btts_yes" in out
        assert out["sport"] == sport

    def test_unavailable_base_is_passed_through(self):
        out = soft_totals_from_scores(
            {"home": "nope", "away": 1.0}, line=2.5, sport="basketball",
        )
        assert out == {"available": False}
