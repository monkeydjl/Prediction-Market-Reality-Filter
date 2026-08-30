# backend/tests/test_factor_vote_wrapper_engines.py
"""Engines that vote over a whole distribution, not a factor head (E20).

``dixon_coles``, ``gbm``, ``situational`` and ``ensemble`` each published
``max(probs, key=probs.get)`` over a full 3-way distribution. For the two
wrappers the level case is reachable through a *real* child engine with no
mocking at all: ``EloOddsEngine`` at equal Elo returns
``home_win == away_win == 0.3743`` exactly, because it fuses on neutral ground.
"""
from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest

from app.kernel.engines.confidence import factor_vote
from app.kernel.engines.dixon_coles_engine import (
    DixonColesEngine,
    dixon_coles_probabilities,
    elo_to_xg,
)
from app.kernel.engines.elo_odds_engine import EloOddsEngine
from app.kernel.engines.ensemble_engine import EnsembleEngine
from app.kernel.engines.gbm_engine import GbmEngine
from app.kernel.engines.situational_engine import SituationalEngine
from tests.test_factor_vote_multi_outcome import _football_features


def _row(result, factor: str):
    rows = [e for e in result.explanation if e.factor == factor]
    assert len(rows) == 1, f"expected one {factor} row, got {len(rows)}"
    return rows[0]


class TestTheChildEngineTiesExactly:
    """The lever the two wrapper tests below stand on."""

    def test_elo_odds_fuses_to_an_exact_tie_at_equal_elo(self):
        feats = _football_features(elo_home=1500.0, elo_away=1500.0, odds=None)
        probs = EloOddsEngine().predict(feats, feats.match).outcome_probabilities
        assert probs["home_win"] == probs["away_win"]
        assert probs["home_win"] > probs["draw"], "tie must be at the peak"
        assert factor_vote(probs) is None
        assert max(probs, key=lambda k: probs[k]) == "home_win", (
            "the pre-fix expression, recorded so a direction change is visible"
        )


class TestEnsembleEngine:
    def test_a_level_child_gets_no_vote(self):
        feats = _football_features(elo_home=1500.0, elo_away=1500.0, odds=None)
        engine = EnsembleEngine([EloOddsEngine()])
        row = _row(engine.predict(feats, feats.match), "elo_odds")
        assert row.available is True
        assert row.predicted_outcome is None

    @pytest.mark.parametrize(
        "eh,ea,expected",
        [(1700.0, 1500.0, "home_win"), (1500.0, 1700.0, "away_win")],
    )
    def test_a_decided_child_still_votes(self, eh, ea, expected):
        feats = _football_features(elo_home=eh, elo_away=ea, odds=None)
        engine = EnsembleEngine([EloOddsEngine()])
        row = _row(engine.predict(feats, feats.match), "elo_odds")
        assert row.predicted_outcome == expected

    def test_two_children_are_voted_independently(self):
        """One level child and one decided child in the same prediction."""
        feats = _football_features(elo_home=1500.0, elo_away=1500.0, odds=None)
        engine = EnsembleEngine([EloOddsEngine(), DixonColesEngine()])
        result = engine.predict(feats, feats.match)
        assert _row(result, "elo_odds").predicted_outcome is None
        # Dixon-Coles bakes home advantage into elo_to_xg (1.35 vs 1.15), so
        # equal Elo is *not* level for it — the two children must disagree here.
        assert _row(result, "dixon_coles").predicted_outcome == "home_win"


class TestSituationalEngine:
    """A knockout stage is the lever: it sets must-win on *both* sides, which
    is a symmetric adjustment, so a level base stays level after it."""

    def _knockout(self, elo_home, elo_away, custom=None):
        feats = _football_features(
            elo_home=elo_home, elo_away=elo_away, odds=None
        )
        match = replace(feats.match, stage="final")
        feats = replace(feats, match=match, custom=custom or {})
        return feats, match

    def test_a_level_base_gets_no_vote_while_the_row_is_available(self):
        feats, match = self._knockout(1500.0, 1500.0)
        result = SituationalEngine(base_engine=EloOddsEngine()).predict(
            feats, match
        )
        row = _row(result, "situational")
        # available=True is the point: the adjustment ran and moved mass
        # (draw 0.2515 → 0.1566) yet left home and away exactly equal.
        assert row.available is True
        assert result.outcome_probabilities["home_win"] == (
            result.outcome_probabilities["away_win"]
        )
        assert row.predicted_outcome is None

    def test_an_asymmetric_adjustment_does_vote(self):
        """must-win on one side only — the converse of the test above."""
        feats = _football_features(
            elo_home=1500.0, elo_away=1500.0, odds=None
        )
        feats = replace(feats, custom={"must_win_home": True})
        result = SituationalEngine(base_engine=EloOddsEngine()).predict(
            feats, feats.match
        )
        row = _row(result, "situational")
        assert row.available is True
        assert row.predicted_outcome == "home_win"

    def test_no_context_is_unavailable_and_votes_nothing(self):
        feats = _football_features(
            elo_home=1500.0, elo_away=1500.0, odds=None
        )
        result = SituationalEngine(base_engine=EloOddsEngine()).predict(
            feats, feats.match
        )
        row = _row(result, "situational")
        assert row.available is False
        assert row.detail == "no situational context"
        assert row.predicted_outcome is None

    def test_the_base_engine_rows_pass_through(self):
        feats = _football_features(elo_home=1700.0, elo_away=1500.0, odds=None)
        result = SituationalEngine(base_engine=EloOddsEngine()).predict(
            feats, feats.match
        )
        assert _row(result, "elo").predicted_outcome == "home_win"
        level, match = self._knockout(1500.0, 1500.0)
        result2 = SituationalEngine(base_engine=EloOddsEngine()).predict(
            level, match
        )
        assert _row(result2, "elo").predicted_outcome is None


class TestDixonColesEngine:
    def test_equal_xg_is_an_exact_tie(self):
        """Reachable, but rare: the basin is 0.12 Elo wide near diff = -78.5.

        Measured over the live corpus, 0 of 16,090 fixtures with both Elo
        present land in it, so this is pinned directly on the probability
        function rather than through a contrived fixture.
        """
        probs = dixon_coles_probabilities(1.3, 1.3)
        assert probs["home_win"] == probs["away_win"]
        assert factor_vote(probs) is None

    def test_equal_elo_is_not_level_because_of_the_xg_home_edge(self):
        home_xg, away_xg = elo_to_xg(1500.0, 1500.0)
        assert home_xg > away_xg
        feats = _football_features(elo_home=1500.0, elo_away=1500.0, odds=None)
        row = _row(DixonColesEngine().predict(feats, feats.match), "elo")
        assert row.available is True
        assert row.predicted_outcome == "home_win"

    def test_missing_elo_is_unavailable_and_votes_nothing(self):
        feats = _football_features(elo_home=None, elo_away=None, odds=None)
        row = _row(DixonColesEngine().predict(feats, feats.match), "elo")
        assert row.available is False
        assert row.predicted_outcome is None

    def test_a_level_distribution_reaches_the_engine_row(self):
        """Pinned through the engine, not only through the pure function.

        Asserting ``dixon_coles_probabilities(1.3, 1.3)`` alone leaves the
        engine's own ``factor_vote(probs)`` call unpinned: restoring ``max()``
        there stayed green, because the tie is not reachable from live Elo. The
        substitution is at the model boundary, as with GBM.
        """
        level = {"home_win": 0.3585, "draw": 0.2830, "away_win": 0.3585}
        feats = _football_features(elo_home=1600.0, elo_away=1500.0, odds=None)
        with patch(
            "app.kernel.engines.dixon_coles_engine.dixon_coles_probabilities",
            return_value=level,
        ):
            row = _row(DixonColesEngine().predict(feats, feats.match), "elo")
        assert row.available is True
        assert row.predicted_outcome is None

    def test_that_seam_is_live_and_a_decided_output_still_votes(self):
        """Guards the test above against a dead patch target."""
        decided = {"home_win": 0.20, "draw": 0.25, "away_win": 0.55}
        feats = _football_features(elo_home=1600.0, elo_away=1500.0, odds=None)
        with patch(
            "app.kernel.engines.dixon_coles_engine.dixon_coles_probabilities",
            return_value=decided,
        ):
            result = DixonColesEngine().predict(feats, feats.match)
        # The substituted dict really reached the engine's output.
        assert result.outcome_probabilities == decided
        assert _row(result, "elo").predicted_outcome == "away_win"


class TestGbmEngine:
    """GBM's distribution comes from a fitted artifact, so the tie is forced
    at the model boundary. The code under test is still production's
    ``factor_vote(probs)`` — only the model output is substituted."""

    _TARGET = (
        "app.services.world_cup_engines.world_cup_gbm_engine.predict_match_gbm"
    )

    def _predict(self, raw):
        feats = _football_features(elo_home=1600.0, elo_away=1500.0, odds=None)
        with patch(self._TARGET, return_value=raw):
            return GbmEngine().predict(feats, feats.match)

    def test_a_level_model_output_votes_nothing(self):
        result = self._predict(
            {
                "outcome_probabilities": {
                    "home_win": 0.36,
                    "draw": 0.28,
                    "away_win": 0.36,
                },
                "model_loaded": True,
                "prediction_method": "gbm",
            }
        )
        row = _row(result, "gbm")
        assert row.available is True
        assert row.predicted_outcome is None

    @pytest.mark.parametrize(
        "hw,aw,expected",
        [(0.50, 0.22, "home_win"), (0.22, 0.50, "away_win")],
    )
    def test_a_decided_model_output_still_votes(self, hw, aw, expected):
        result = self._predict(
            {
                "outcome_probabilities": {
                    "home_win": hw,
                    "draw": 0.28,
                    "away_win": aw,
                },
                "model_loaded": True,
                "prediction_method": "gbm",
            }
        )
        assert _row(result, "gbm").predicted_outcome == expected

    def test_a_raising_seam_falls_back_and_votes_nothing(self):
        """The engine swallows every ``Exception`` into a neutral fallback.

        Not asserted against the *unpatched* detail on purpose: the real model
        builds its feature vector from match history, so ``gbm_lightgbm`` is a
        function of DB state and would make this environment-dependent. The
        seam being live is proved by
        ``test_the_substituted_output_really_reaches_the_row`` instead.
        """
        feats = _football_features(elo_home=1600.0, elo_away=1500.0, odds=None)
        with patch(self._TARGET, side_effect=RuntimeError("seam hit")):
            fallback = _row(GbmEngine().predict(feats, feats.match), "gbm")
        assert fallback.detail == "Elo unavailable"
        assert fallback.available is False
        assert fallback.predicted_outcome is None

    def test_the_substituted_output_really_reaches_the_row(self):
        """A distinctive method name proves the patched dict was consumed."""
        result = self._predict(
            {
                "outcome_probabilities": {
                    "home_win": 0.36,
                    "draw": 0.28,
                    "away_win": 0.36,
                },
                "model_loaded": True,
                "prediction_method": "e20-fixture-marker",
            }
        )
        row = _row(result, "gbm")
        assert row.detail == "e20-fixture-marker"
        assert result.outcome_probabilities["home_win"] == (
            result.outcome_probabilities["away_win"]
        )
