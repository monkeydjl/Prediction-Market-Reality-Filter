# backend/tests/test_calibration_keeps_claims_together.py
"""Conditional calibration must not leave the scoreline behind.

``apply_conditional_calibration`` shifts ``outcome_probabilities`` and used to
return the *pre*-calibration ``predicted_scores`` untouched. For the engines whose
scoreline is defined as ``_probabilities_to_scores(outcome_probabilities)`` that
publishes a ``PredictionResult`` whose two claims disagree -- the shape #78
measured across engines, reintroduced by the pipeline for the very engines #78
recorded as "coherent by construction".

``situational_engine`` already applies the correct rule to itself: recompute the
scoreline when it moves probability mass, keep the base one when it does not.
"""
from datetime import datetime, timezone

import pytest

from app.kernel.domain import (
    CompetitionIdentity,
    ContributionItem,
    MatchIdentity,
    PredictionResult,
    SeasonIdentity,
    SportIdentity,
    TeamIdentity,
)
from app.kernel.engines.elo_odds_engine import _probabilities_to_scores
from app.kernel.prediction_kernel import (
    PROBABILITY_DERIVED_SCORE_ENGINES,
    apply_conditional_calibration,
)


def _match(competition="epl"):
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code=competition, name="T", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    return MatchIdentity(
        match_id=f"{competition}-1", season=season, stage="group", round=None,
        home=TeamIdentity(code="H", name="H", competition=comp),
        away=TeamIdentity(code="A", name="A", competition=comp),
        kickoff_utc=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )


def _prediction(engine, probs, scores):
    return PredictionResult(
        predicted_scores=dict(scores),
        outcome_probabilities=dict(probs),
        confidence=0.6,
        engine_name=engine,
        explanation=[ContributionItem(
            factor="elo", direction="support", weight=0.3,
            available=True, detail="", predicted_outcome="home_win",
        )],
        betting_analysis=None,
        feature_version="1.0",
        prediction_timestamp=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )


class _Learning:
    """Returns one calibration row, well inside the clamps."""

    def __init__(self, slope=1.15, intercept=-0.03, sample_count=40):
        self._row = {
            "slope": slope, "intercept": intercept,
            "sample_count": sample_count, "bucket": "mid", "source": "competition",
        }

    def get_conditional_calibration(self, *_args, **_kwargs):
        return self._row


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    from app.core import config
    monkeypatch.setattr(
        config.settings, "KERNEL_CONDITIONAL_CALIBRATION_ENABLED", True,
    )


FOOTBALL_PROBS = {"home_win": 0.40, "draw": 0.30, "away_win": 0.30}


class TestProbabilityDerivedScorelineFollowsCalibration:
    def test_the_scoreline_is_re_derived_from_the_calibrated_probabilities(self):
        stale = _probabilities_to_scores(FOOTBALL_PROBS)
        pred = _prediction("elo_odds", FOOTBALL_PROBS, stale)

        out = apply_conditional_calibration(pred, "epl", _Learning())

        expected = _probabilities_to_scores(out.outcome_probabilities)
        assert out.predicted_scores == expected
        # And it actually moved: otherwise this would pass on a no-op calibration.
        assert out.predicted_scores != stale
        assert out.betting_analysis["conditional_calibration"]["scores_recomputed"] is True

    @pytest.mark.parametrize("engine", sorted(PROBABILITY_DERIVED_SCORE_ENGINES))
    def test_every_declared_engine_gets_the_recompute(self, engine):
        stale = _probabilities_to_scores(FOOTBALL_PROBS)
        out = apply_conditional_calibration(
            _prediction(engine, FOOTBALL_PROBS, stale), "epl", _Learning(),
        )
        assert out.predicted_scores == _probabilities_to_scores(
            out.outcome_probabilities,
        )

    def test_the_two_claims_name_the_same_side_after_calibration(self):
        """The property that matters, stated directly rather than via equality."""
        # A near-even set where a home-lifting calibration flips the argmax.
        probs = {"home_win": 0.34, "draw": 0.30, "away_win": 0.36}
        pred = _prediction("elo_odds", probs, _probabilities_to_scores(probs))
        out = apply_conditional_calibration(
            pred, "epl", _Learning(slope=1.6, intercept=0.02),
        )
        p = out.outcome_probabilities
        s = out.predicted_scores
        prob_side = max(p, key=lambda k: p[k])
        score_side = (
            "home_win" if s["home"] > s["away"]
            else "away_win" if s["away"] > s["home"] else "draw"
        )
        if prob_side in ("home_win", "away_win"):
            assert score_side == prob_side, (p, s)


class TestExcludedEnginesKeepTheirOwnScoreline:
    @pytest.mark.parametrize("engine", ["basketball", "baseball", "hockey"])
    def test_an_elo_derived_scoreline_is_not_overwritten(self, engine):
        """Recomputing would replace their model, and they carry no draw key.

        Two independent guards cover these three: exclusion from the declared set,
        *and* the fact that their probabilities have no ``draw`` key for
        ``_probabilities_to_scores`` to index. Injection confirmed the second one
        alone rescues them -- flipping the set test to ``if True`` left these green
        and reddened only ``gbm``. So the assertion below is not by itself proof
        that the set is load-bearing; ``test_a_model_predicted_scoreline_is_not_overwritten``
        is. Membership is asserted directly so the intent survives either guard
        being changed.
        """
        assert engine not in PROBABILITY_DERIVED_SCORE_ENGINES
        probs = {"home_win": 0.55, "away_win": 0.45}
        scores = {"home": 112.0, "away": 104.0}
        out = apply_conditional_calibration(
            _prediction(engine, probs, scores), "nba", _Learning(),
        )
        assert out.predicted_scores == scores
        assert out.betting_analysis["conditional_calibration"]["scores_recomputed"] is False
        # The probabilities were still calibrated -- only the scoreline is held.
        assert out.outcome_probabilities["home_win"] != pytest.approx(0.55)

    def test_a_model_predicted_scoreline_is_not_overwritten(self):
        """gbm may carry raw.get("predicted_score"); that is a real model output.

        This is the case that proves the declared set does work: gbm's
        probabilities *do* carry ``draw``, so nothing but the set stops the
        recompute from discarding the model's own scoreline.
        """
        scores = {"home": 2.4, "away": 0.9}
        out = apply_conditional_calibration(
            _prediction("gbm", FOOTBALL_PROBS, scores), "epl", _Learning(),
        )
        assert out.predicted_scores == scores
        assert out.betting_analysis["conditional_calibration"]["scores_recomputed"] is False

    def test_an_engine_with_no_scoreline_stays_empty(self):
        out = apply_conditional_calibration(
            _prediction("lol_market_only", {"home_win": 0.6, "away_win": 0.4}, {}),
            "lol", _Learning(),
        )
        assert out.predicted_scores == {}


class TestTheBucketsPartitionEveryEngine:
    def test_no_engine_is_left_undecided(self):
        """A new engine must be classified, not silently inherit a default.

        Scoreline provenance is not inferable from the engine class, so the only
        way this stays correct is for the set to be declared and pinned.
        """
        from app.services.prediction_consistency_service import (
            ELO_ONLY_SCORE_ENGINES,
        )

        # Every engine name the repo ships, read off the name() returns.
        all_engines = {
            "elo_odds", "dixon_coles", "football_multi_factor", "ensemble",
            "situational", "gbm", "basketball", "baseball", "hockey",
            "lol_market_only",
        }
        excluded = ELO_ONLY_SCORE_ENGINES | {"gbm", "lol_market_only"}

        assert PROBABILITY_DERIVED_SCORE_ENGINES | excluded == all_engines
        assert not (PROBABILITY_DERIVED_SCORE_ENGINES & excluded)

    def test_the_declared_set_matches_the_engines_that_derive_scores(self):
        """Guard against the set drifting from the source it describes.

        ``_probabilities_to_scores`` appears in each of these engine modules; if a
        module stops calling it, or a new one starts, this names the set to update.
        """
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parents[1] / "app"
        module_for = {
            "elo_odds": "kernel/engines/elo_odds_engine.py",
            "dixon_coles": "kernel/engines/dixon_coles_engine.py",
            "ensemble": "kernel/engines/ensemble_engine.py",
            "situational": "kernel/engines/situational_engine.py",
            "football_multi_factor":
                "sports/football/engines/football_multi_factor_engine.py",
        }
        assert set(module_for) == set(PROBABILITY_DERIVED_SCORE_ENGINES)
        for engine, rel in module_for.items():
            src = (root / rel).read_text(encoding="utf-8")
            assert re.search(r"_probabilities_to_scores\(", src), engine
