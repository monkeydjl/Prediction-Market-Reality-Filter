# backend/tests/test_confidence_decision_arity.py
"""`decision_strength` must read its flat baseline off the outcome arity (E19).

The baseline used to be the literal ``1/3`` with the divisor ``2/3``, which is
correct only for football's 3-way distribution. Three of the five engines that
call it -- basketball, baseball, hockey -- pass a **binary** ``{home_win,
away_win}`` distribution, whose flat case is ``peak == 0.5`` and therefore
scored ``0.25`` instead of ``0.0``: the bottom quarter of the scale was
unreachable and every stored binary confidence was inflated.

These tests pin three separate things, because a fix that satisfied only one of
them would still be wrong:

1. the binary baseline is 0.5 (the arity is actually read),
2. the 3-way values are **bit-identical** to the old constants, so football is
   untouched (checked over the whole simplex grid, not one sample), and
3. the inflation is really gone end-to-end through ``compute_confidence``, at
   the peaks the live engines actually produce.
"""
from __future__ import annotations

import pytest

from app.kernel.engines.confidence import (
    compute_confidence,
    confidence_breakdown,
    decision_strength,
)


def _legacy_strength(probs: dict[str, float]) -> float:
    """The pre-E19 body, verbatim, as the oracle for the 3-way no-drift check."""
    if not probs:
        return 0.0
    vals = [max(0.0, float(v)) for v in probs.values()]
    total = sum(vals) or 1.0
    norm = [v / total for v in vals]
    peak = max(norm)
    return max(0.0, min(1.0, (peak - 1.0 / 3.0) / (2.0 / 3.0)))


def _simplex_grid(step: int = 1, denom: int = 100) -> list[dict[str, float]]:
    """Every 3-way distribution on a 0.01 lattice, boundaries included."""
    out: list[dict[str, float]] = []
    for h in range(0, denom + 1, step):
        for d in range(0, denom - h + 1, step):
            a = denom - h - d
            out.append(
                {
                    "home_win": h / denom,
                    "draw": d / denom,
                    "away_win": a / denom,
                }
            )
    return out


class TestBinaryBaselineIsAHalf:
    def test_flat_binary_scores_zero(self):
        """The whole point: a coin flip is no signal at all, not a quarter of one."""
        assert decision_strength({"home_win": 0.5, "away_win": 0.5}) == 0.0

    def test_legacy_scored_flat_binary_at_a_quarter(self):
        """Guards the test above from being vacuous: 0.25 is what it must not be."""
        legacy = _legacy_strength({"home_win": 0.5, "away_win": 0.5})
        assert legacy == pytest.approx(0.25, abs=1e-12)
        assert legacy > 0.0

    def test_binary_matches_the_closed_form(self):
        """(peak - 1/2) / (1/2) exactly -- both constants are exact in binary."""
        for peak in (0.50, 0.5003, 0.5052, 0.5381, 0.60, 0.6525, 0.7443, 0.90):
            probs = {"home_win": peak, "away_win": 1.0 - peak}
            assert decision_strength(probs) == pytest.approx(
                (peak - 0.5) / 0.5, abs=1e-12
            ), peak

    def test_certain_binary_is_full_strength(self):
        assert decision_strength({"home_win": 1.0, "away_win": 0.0}) == 1.0

    def test_baseline_follows_arity_not_key_names(self):
        """A 2-key dict is binary whatever the keys are called."""
        assert decision_strength({"over": 0.5, "under": 0.5}) == 0.0
        assert decision_strength({"yes": 0.75, "no": 0.25}) == pytest.approx(0.5)

    def test_four_way_baseline_is_a_quarter(self):
        """Arity-derived, so it generalizes past the two shapes we ship today."""
        flat4 = {"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}
        assert decision_strength(flat4) == 0.0
        peaky4 = {"a": 0.50, "b": 0.25, "c": 0.15, "d": 0.10}
        assert decision_strength(peaky4) == pytest.approx((0.50 - 0.25) / 0.75)

    def test_peak_is_taken_after_normalisation(self):
        """Unnormalised equal weights are still flat, not confident."""
        assert decision_strength({"home_win": 0.6, "away_win": 0.6}) == 0.0
        assert decision_strength({"home_win": 60.0, "away_win": 40.0}) == pytest.approx(
            0.2
        )

    def test_single_outcome_and_empty_are_defined(self):
        """n < 2 has no rival outcome; must not divide by zero."""
        assert decision_strength({"home_win": 1.0}) == 1.0
        assert decision_strength({"home_win": 0.0}) == 1.0
        assert decision_strength({}) == 0.0

    def test_all_zero_binary_does_not_blow_up(self):
        """total falls back to 1.0, so the peak is 0 and the result clamps at 0."""
        assert decision_strength({"home_win": 0.0, "away_win": 0.0}) == 0.0

    def test_negatives_are_floored_not_trusted(self):
        assert decision_strength({"home_win": 1.0, "away_win": -5.0}) == 1.0


class TestFootballIsBitIdentical:
    """The 3-way path must not move by a single bit -- it was already correct."""

    def test_whole_simplex_grid_is_unchanged(self):
        grid = _simplex_grid()
        assert len(grid) == 5151, "grid construction changed; recheck the claim"
        drifted = [
            (probs, decision_strength(probs), _legacy_strength(probs))
            for probs in grid
            if decision_strength(probs) != _legacy_strength(probs)
        ]
        assert drifted == [], f"{len(drifted)} of {len(grid)} 3-way values moved"

    def test_the_grid_check_can_actually_fail(self):
        """Same comparison over binary points must find differences everywhere.

        Without this, ``test_whole_simplex_grid_is_unchanged`` would still pass
        if the two functions had been made trivially equal.
        """
        # h == 100 is excluded: both forms saturate the upper clamp at 1.0.
        binary = [
            {"home_win": h / 100, "away_win": 1.0 - h / 100} for h in range(50, 100)
        ]
        same = [p for p in binary if decision_strength(p) == _legacy_strength(p)]
        assert same == [], "binary values must differ from the legacy baseline"

    def test_divisor_form_is_exact_for_three_way(self):
        """(n-1)/n and the old literal 2/3 are the same float; 1 - 1/n is not."""
        assert (3 - 1.0) / 3 == 2.0 / 3.0
        assert 1.0 - 1.0 / 3.0 != 2.0 / 3.0


# (fixture, binary peak of the stored probabilities, confidence under the fix,
#  confidence under the legacy 1/3 baseline, measured inflation) -- measured on
# the three live stored rows; the peaks are the real ones, the surround here is
# fixed so the arithmetic is checkable.
_LIVE_PEAKS = (
    ("mlb-824514", 0.5381, 0.6498, 0.7248, 0.0751),
    ("nhl-2026010012", 0.5003, 0.6252, 0.7064, 0.0812),
    ("nba-21716138", 0.5052, 0.6284, 0.7088, 0.0804),
)

# The most decisive binary call each engine can physically make: the fused peak
# is capped by the factor clamps (nba 0.7443 / mlb 0.6525 / nhl 0.7223), so the
# inflation never becomes negligible on any real fixture.
_CLAMP_CAPS = (("nba", 0.7443), ("mlb", 0.6525), ("nhl", 0.7223))

_FULL_SURROUND: dict[str, object] = {
    "available_flags": [True, True, True, True],
    "predicted_outcomes": ["home_win"] * 4,
    "data_quality": "real",
    "odds_fresh": True,
}


class TestInflationIsGoneEndToEnd:
    def test_live_peaks_lose_the_inflation(self, subtests):
        for fixture_id, peak, expected_new, expected_legacy, delta in _LIVE_PEAKS:
            with subtests.test(fixture=fixture_id):
                probs = {"home_win": peak, "away_win": 1.0 - peak}
                got = compute_confidence(probs, **_FULL_SURROUND)  # type: ignore[arg-type]
                assert got == pytest.approx(expected_new, abs=2e-4)
                assert got < expected_legacy
                # 0.50 * strength inside a 0.65 rescale, damp == 1.0 here.
                measured = 0.325 * (
                    _legacy_strength(probs) - decision_strength(probs)
                )
                assert measured == pytest.approx(delta, abs=1e-4)

    def test_inflation_is_never_small_even_at_the_clamp_cap(self, subtests):
        """Below ~0.042 would mean the defect had been cosmetic. It was not."""
        for sport, cap in _CLAMP_CAPS:
            with subtests.test(sport=sport):
                probs = {"home_win": cap, "away_win": 1.0 - cap}
                inflation = 0.325 * (
                    _legacy_strength(probs) - decision_strength(probs)
                )
                assert inflation > 0.040, f"{sport} inflation {inflation}"

    def test_a_near_flat_binary_call_drops_below_the_calibration_threshold(self):
        """`confidence_calibrated` splits on a hard 0.5, so this flips a verdict.

        Two of four factors available and split 1-1, so completeness and
        agreement are both 0.5 and the strength term decides the side of 0.5.
        """
        probs = {"home_win": 0.52, "away_win": 0.48}
        surround: dict[str, object] = {
            "available_flags": [True, True, False, False],
            "predicted_outcomes": ["home_win", "away_win", None, None],
            "data_quality": None,
            "odds_fresh": True,
        }
        got = compute_confidence(probs, **surround)  # type: ignore[arg-type]
        assert got == pytest.approx(0.4755, abs=1e-4)
        assert got < 0.5
        legacy_total = got + 0.325 * (
            _legacy_strength(probs) - decision_strength(probs)
        )
        assert legacy_total > 0.5, "the legacy value must be on the other side"

    def test_flat_binary_contributes_nothing_to_the_band(self):
        """A coin flip with nothing else known must score the band's own floor.

        No factors available -> completeness 0.0; no votes -> agreement 0.5.
        So the only term left is the strength one, which must now be zero.
        """
        probs = {"home_win": 0.5, "away_win": 0.5}
        flat = compute_confidence(
            probs,
            available_flags=[False, False],
            predicted_outcomes=[None, None],
        )
        assert flat == pytest.approx(0.30 + 0.65 * (0.25 * 0.5), abs=1e-4)
        legacy_total = flat + 0.325 * _legacy_strength(probs)
        assert legacy_total - flat > 0.08, "legacy added a full quarter of strength"

    def test_breakdown_reports_the_same_strength_it_scores(self):
        probs = {"home_win": 0.5381, "away_win": 0.4619}
        bd = confidence_breakdown(probs, **_FULL_SURROUND)  # type: ignore[arg-type]
        assert bd["decision_strength"] == round(decision_strength(probs), 4)
        assert bd["decision_strength"] == pytest.approx(0.0762, abs=1e-4)
        assert bd["total"] == compute_confidence(probs, **_FULL_SURROUND)  # type: ignore[arg-type]


def _hockey_features(*, elo_home: float, elo_away: float):
    """Minimal real NHL FeatureSet -- enough for the engine to price a game."""
    from datetime import datetime, timezone

    from app.kernel.domain import (
        CompetitionIdentity,
        EnvironmentFeatures,
        FeatureSet,
        GeneralFeatures,
        MarketFeatures,
        MatchIdentity,
        PlayerFeatures,
        SeasonIdentity,
        SportIdentity,
        TeamFeatures,
        TeamIdentity,
    )

    sport = SportIdentity(code="hockey", name="Hockey")
    comp = CompetitionIdentity(code="nhl", name="NHL", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="20252026")
    home = TeamIdentity(code="NJD", name="New Jersey Devils", competition=comp)
    away = TeamIdentity(code="NYR", name="New York Rangers", competition=comp)
    return FeatureSet(
        match=MatchIdentity(
            match_id="nhl-2026010012",
            season=season,
            stage="regular_season",
            round=None,
            home=home,
            away=away,
            kickoff_utc=datetime(2026, 1, 12, tzinfo=timezone.utc),
        ),
        general=GeneralFeatures(
            rest_days_home=2.0,
            rest_days_away=2.0,
            travel_distance_km=None,
            days_since_last_match=None,
        ),
        team=TeamFeatures(
            elo_rating_home=elo_home,
            elo_rating_away=elo_away,
            form_home=0.5,
            form_away=0.5,
            h2h_home_win_rate=None,
            h2h_draw_rate=None,
            market_value_home=None,
            market_value_away=None,
        ),
        market=MarketFeatures(None, None, None, None, False),
        player=PlayerFeatures(True, True, None, None),
        environment=EnvironmentFeatures("Prudential Center", None, None, True),
        custom={
            "goalie_save_pct_home": 0.910,
            "goalie_save_pct_away": 0.910,
        },
        data_quality="real",
        quality_notes=[],
        feature_version="nhl-1.0",
    )


class TestBinaryEngineIsActuallyWired:
    """The engines really do reach this code with a 2-key distribution.

    ``tests/test_hockey_engine.py`` already pins that each binary engine emits
    ``{home_win, away_win}`` with no ``draw``; what is unpinned is that the
    stored ``confidence`` is computed from *that* dict, which is what made the
    baseline bug reach production.
    """

    def test_hockey_confidence_is_the_binary_scored_value(self):
        from app.sports.hockey.engines.hockey_engine import HockeyEngine

        features = _hockey_features(elo_home=1500.0, elo_away=1500.0)
        result = HockeyEngine().predict(features, features.match)
        probs = result.outcome_probabilities
        assert set(probs) == {"home_win", "away_win"}

        recomputed = compute_confidence(
            probs,
            available_flags=[True] * len(result.explanation),
            predicted_outcomes=[e.predicted_outcome for e in result.explanation],
            data_quality=features.data_quality,
            odds_fresh=None,
            custom=features.custom,
        )
        # The engine may mark some factors unavailable, so this is an upper
        # bound rather than equality -- what matters is the binary arity.
        assert 0.20 <= result.confidence <= recomputed + 1e-9

    def test_evenly_matched_teams_score_near_the_bottom_of_the_band(self):
        """Home-ice is a +0.05 constant, so the peak is small but not flat."""
        from app.sports.hockey.engines.hockey_engine import HockeyEngine

        features = _hockey_features(elo_home=1500.0, elo_away=1500.0)
        result = HockeyEngine().predict(features, features.match)
        peak = max(result.outcome_probabilities.values())
        assert peak < 0.60, f"fixture is not near-flat any more: {peak}"
        strength = decision_strength(result.outcome_probabilities)
        assert strength < 0.20
        assert _legacy_strength(result.outcome_probabilities) > strength + 0.20
