"""Tests for migrated EloOddsEngine.

Covers two layers:
1. Property tests (from the task brief): structural contract of the
   PredictionEngine Protocol, probability sanity, graceful degradation,
   and explanation contributions.
2. Equivalence tests (Global Constraint #8): verify the migrated kernel
   engine produces outcome probabilities that match the legacy
   ``world_cup_elo_odds_engine`` within tolerance. Only the probability
   values are compared because the surrounding output shapes differ
   (PredictionResult dataclass vs. plain dict with extra fields).
"""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures,
)
from app.kernel.engines.elo_odds_engine import EloOddsEngine
from app.services.world_cup_engines.world_cup_elo_odds_engine import (
    predict_match_elo_odds as old_predict_match_elo_odds,
)


def _make_features(
    elo_home=1900, elo_away=1800,
    odds_home=2.10, odds_draw=3.30, odds_away=3.50,
    is_knockout=False,
) -> FeatureSet:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    match = MatchIdentity(
        match_id="m1", season=season,
        stage="final" if is_knockout else "group_stage",
        round=None, home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )
    return FeatureSet(
        match=match,
        general=GeneralFeatures(None, None, None, None),
        team=TeamFeatures(
            elo_rating_home=float(elo_home), elo_rating_away=float(elo_away),
            form_home=None, form_away=None,
            h2h_home_win_rate=None, h2h_draw_rate=None,
            market_value_home=None, market_value_away=None,
        ),
        market=MarketFeatures(
            odds_home=odds_home, odds_draw=odds_draw, odds_away=odds_away,
            odds_source="test", odds_fresh=True,
        ),
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures(None, None, None, False),
        custom={},
        data_quality="real",
        quality_notes=[],
        feature_version="1.0",
    )


class TestEloOddsEngine:
    def test_implements_protocol(self):
        from app.kernel.protocols import PredictionEngine
        engine = EloOddsEngine()
        assert isinstance(engine, PredictionEngine)

    def test_name(self):
        engine = EloOddsEngine()
        assert engine.name() == "elo_odds"

    def test_supported_sports(self):
        engine = EloOddsEngine()
        assert "*" in engine.supported_sports()

    def test_predict_returns_prediction_result(self):
        engine = EloOddsEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        assert result.engine_name == "elo_odds"
        assert "home" in result.predicted_scores
        assert "away" in result.predicted_scores
        assert "home_win" in result.outcome_probabilities
        assert "draw" in result.outcome_probabilities
        assert "away_win" in result.outcome_probabilities
        assert 0.0 <= result.confidence <= 1.0

    def test_probabilities_sum_to_one(self):
        engine = EloOddsEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        total = sum(result.outcome_probabilities.values())
        assert abs(total - 1.0) < 0.01

    def test_stronger_team_higher_win_prob(self):
        engine = EloOddsEngine()
        strong = _make_features(elo_home=2100, elo_away=1500)
        result = engine.predict(strong, strong.match)
        assert result.outcome_probabilities["home_win"] > result.outcome_probabilities["away_win"]

    def test_no_odds_graceful_degradation(self):
        """When odds are None, engine should still produce a prediction from Elo alone."""
        engine = EloOddsEngine()
        features = _make_features(odds_home=None, odds_draw=None, odds_away=None)
        result = engine.predict(features, features.match)
        assert result.engine_name == "elo_odds"
        total = sum(result.outcome_probabilities.values())
        assert abs(total - 1.0) < 0.01

    def test_explanation_contains_elo_contribution(self):
        engine = EloOddsEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        elo_items = [e for e in result.explanation if e.factor == "elo"]
        assert len(elo_items) > 0
        assert elo_items[0].available is True

    def test_explanation_contains_odds_contribution(self):
        engine = EloOddsEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        odds_items = [e for e in result.explanation if e.factor == "odds"]
        assert len(odds_items) > 0
        assert odds_items[0].available is True

    def test_no_odds_shows_odds_unavailable(self):
        engine = EloOddsEngine()
        features = _make_features(odds_home=None, odds_draw=None, odds_away=None)
        result = engine.predict(features, features.match)
        odds_items = [e for e in result.explanation if e.factor == "odds"]
        assert len(odds_items) > 0
        assert odds_items[0].available is False


class TestEloOddsEquivalence:
    """Verify the migrated kernel EloOddsEngine produces outcome probabilities
    matching the legacy world_cup_elo_odds_engine within tolerance.

    Global Constraint #8 requires equivalence tests on engine migrations.
    Only the probability values (home_win, draw, away_win) are compared
    because the output envelopes differ: the kernel returns a
    PredictionResult dataclass while the legacy engine returns a plain dict
    carrying extra fields (score matrix, prediction interval, ...). The
    probability pipeline (BTD -> odds normalization -> 30/70 fusion) is
    intentionally identical between the two implementations, so the values
    should match to the 4-decimal rounding both apply.
    """

    # Tolerance: both engines round to 4 decimals, so values match exactly
    # in practice. 1e-6 leaves headroom for floating-point representation
    # while remaining far tighter than the brief's suggested 0.01.
    _TOL = 1e-6

    def _run_both(self, features: FeatureSet) -> tuple[dict, dict]:
        """Run the new kernel engine and the legacy engine on the same inputs.

        Derives ``is_knockout`` from the match stage exactly as the kernel
        engine does, so both engines see an identical knockout flag.
        """
        engine = EloOddsEngine()
        new_result = engine.predict(features, features.match)
        new_probs = dict(new_result.outcome_probabilities)

        is_knockout = features.match.stage not in (
            "group_stage", "regular_season",
        )
        old_result = old_predict_match_elo_odds(
            home_team=features.match.home.name,
            away_team=features.match.away.name,
            elo_home=features.team.elo_rating_home,
            elo_away=features.team.elo_rating_away,
            odds_home=features.market.odds_home,
            odds_draw=features.market.odds_draw,
            odds_away=features.market.odds_away,
            is_knockout=is_knockout,
        )
        old_probs = dict(old_result["outcome_probabilities"])
        return new_probs, old_probs

    def test_typical_case_with_odds(self):
        """Scenario (a): typical group-stage match with full odds."""
        features = _make_features(
            elo_home=1900, elo_away=1800,
            odds_home=2.10, odds_draw=3.30, odds_away=3.50,
            is_knockout=False,
        )
        new_probs, old_probs = self._run_both(features)
        for key in ("home_win", "draw", "away_win"):
            assert abs(new_probs[key] - old_probs[key]) < self._TOL, (
                f"{key}: new={new_probs[key]} old={old_probs[key]}"
            )

    def test_different_elo_ratings(self):
        """Scenario (b): strongly mismatched teams, still with odds."""
        features = _make_features(
            elo_home=2100, elo_away=1500,
            odds_home=1.55, odds_draw=4.20, odds_away=6.00,
            is_knockout=False,
        )
        new_probs, old_probs = self._run_both(features)
        for key in ("home_win", "draw", "away_win"):
            assert abs(new_probs[key] - old_probs[key]) < self._TOL, (
                f"{key}: new={new_probs[key]} old={old_probs[key]}"
            )

    def test_no_odds_elo_only_fallback(self):
        """Scenario (c): no odds supplied; both engines must fall back to
        Elo-only probabilities (which, by construction, are identical)."""
        features = _make_features(
            elo_home=1900, elo_away=1800,
            odds_home=None, odds_draw=None, odds_away=None,
            is_knockout=False,
        )
        new_probs, old_probs = self._run_both(features)
        for key in ("home_win", "draw", "away_win"):
            assert abs(new_probs[key] - old_probs[key]) < self._TOL, (
                f"{key}: new={new_probs[key]} old={old_probs[key]}"
            )

    def test_knockout_with_odds(self):
        """Extra scenario: knockout stage with odds. Verifies the knockout
        gamma scaling path is wired identically through BTD in both engines."""
        features = _make_features(
            elo_home=1850, elo_away=1850,
            odds_home=2.80, odds_draw=3.10, odds_away=2.80,
            is_knockout=True,
        )
        new_probs, old_probs = self._run_both(features)
        for key in ("home_win", "draw", "away_win"):
            assert abs(new_probs[key] - old_probs[key]) < self._TOL, (
                f"{key}: new={new_probs[key]} old={old_probs[key]}"
            )
