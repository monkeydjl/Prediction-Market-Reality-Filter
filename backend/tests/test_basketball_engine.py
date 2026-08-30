# backend/tests/test_basketball_engine.py
"""Tests for BasketballEngine — Bradley-Terry binary prediction engine."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures,
)
from app.kernel.protocols import PredictionEngine
from app.sports.basketball.engines.basketball_engine import BasketballEngine


_BASKETBALL = SportIdentity(code="basketball", name="Basketball")
_NBA = CompetitionIdentity(code="nba", name="NBA", sport=_BASKETBALL)


def _make_features(
    elo_home=1650.0, elo_away=1520.0,
    form_home=0.7, form_away=0.4,
    rest_home=2, rest_away=1,
) -> FeatureSet:
    comp = _NBA
    season = SeasonIdentity(competition=comp, season_key="2024-25")
    home = TeamIdentity(code="BOS", name="Boston Celtics", competition=comp)
    away = TeamIdentity(code="LAL", name="Los Angeles Lakers", competition=comp)
    match = MatchIdentity(
        match_id="nba-123", season=season,
        stage="regular_season", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2024, 12, 25, tzinfo=timezone.utc),
    )
    return FeatureSet(
        match=match,
        general=GeneralFeatures(
            rest_days_home=float(rest_home) if rest_home is not None else None,
            rest_days_away=float(rest_away) if rest_away is not None else None,
            travel_distance_km=None,
            days_since_last_match=None,
        ),
        team=TeamFeatures(
            elo_rating_home=elo_home,
            elo_rating_away=elo_away,
            form_home=form_home,
            form_away=form_away,
            h2h_home_win_rate=None, h2h_draw_rate=None,
            market_value_home=None, market_value_away=None,
        ),
        market=MarketFeatures(None, None, None, None, False),
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures("TD Garden", None, None, True),
        custom={
            "pace_home": 99.5, "pace_away": 97.2,
            "ortg_home": 112.3, "ortg_away": 108.1,
            "drtg_home": 105.0, "drtg_away": 110.5,
            "tpct_home": 0.365, "tpct_away": 0.342,
        },
        data_quality="real",
        quality_notes=[],
        feature_version="nba-1.0",
    )


class TestBasketballEngineProtocol:
    def test_implements_protocol(self):
        engine = BasketballEngine()
        assert isinstance(engine, PredictionEngine)

    def test_name(self):
        assert BasketballEngine().name() == "basketball"

    def test_supported_sports(self):
        assert "basketball" in BasketballEngine().supported_sports()


class TestBasketballEnginePredict:
    def test_predict_returns_binary_probabilities(self):
        """Outcome probabilities have home_win and away_win (no draw)."""
        engine = BasketballEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        assert "home_win" in result.outcome_probabilities
        assert "away_win" in result.outcome_probabilities
        assert "draw" not in result.outcome_probabilities
        # Probabilities sum to 1.0
        total = sum(result.outcome_probabilities.values())
        assert abs(total - 1.0) < 0.01

    def test_stronger_team_higher_win_prob(self):
        """Higher Elo home team → P(home_win) > P(away_win)."""
        engine = BasketballEngine()
        strong = _make_features(elo_home=1800, elo_away=1500)
        result = engine.predict(strong, strong.match)
        assert result.outcome_probabilities["home_win"] > result.outcome_probabilities["away_win"]

    def test_explanation_has_four_factors(self):
        """Explanation contains elo, home_court, rest, form factors."""
        engine = BasketballEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        factor_ids = [e.factor for e in result.explanation]
        assert "elo" in factor_ids
        assert "home_court" in factor_ids
        assert "rest" in factor_ids
        assert "form" in factor_ids

    def test_contribution_item_predicted_outcome_is_binary(self):
        """An available factor votes home_win / away_win, or None when level.

        The ``None`` case is not a loophole: this fixture has no level factor
        (asserted below), so every available row here must carry a real vote.
        ``tests/test_factor_vote_engines.py`` covers the level case, where an
        available factor correctly votes nothing.
        """
        engine = BasketballEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        for item in result.explanation:
            if item.available:
                assert "P(home_win)=0.5;" not in item.detail
                assert not item.detail.endswith("P(home_win)=0.5")
                assert item.predicted_outcome in ("home_win", "away_win")
            else:
                assert item.predicted_outcome is None

    def test_a_level_factor_votes_nothing_while_staying_available(self):
        """Equal rest → p_rest is exactly 0.5 → no vote, still available."""
        engine = BasketballEngine()
        features = _make_features(rest_home=3, rest_away=3)
        result = engine.predict(features, features.match)
        rest = next(e for e in result.explanation if e.factor == "rest")
        assert rest.available is True
        assert "P(home_win)=0.5" in rest.detail
        assert rest.predicted_outcome is None

    def test_no_elo_fallback(self):
        """When Elo is None, engine still produces valid prediction."""
        engine = BasketballEngine()
        features = _make_features(elo_home=None, elo_away=None)
        result = engine.predict(features, features.match)
        # Elo factor should be unavailable
        elo_item = next(e for e in result.explanation if e.factor == "elo")
        assert elo_item.available is False
        # Still produces valid probabilities
        total = sum(result.outcome_probabilities.values())
        assert abs(total - 1.0) < 0.01

    def test_score_conversion_uses_league_avg(self):
        """Predicted scores are centered around league average total."""
        engine = BasketballEngine()
        features = _make_features(elo_home=1500, elo_away=1500)
        result = engine.predict(features, features.match)
        # Equal Elo → scores should be near league_avg/2 each
        home_score = result.predicted_scores["home"]
        away_score = result.predicted_scores["away"]
        # League avg = 220, so each ≈ 110 (plus HFA adjustment)
        assert 100 < home_score < 130
        assert 100 < away_score < 130

class TestBasketballPlayoffStage:
    def test_playoff_reduces_home_edge(self):
        """Playoff HFA/home_court softer than regular-season for equal teams."""
        engine = BasketballEngine()
        reg = _make_features(elo_home=1600, elo_away=1600)
        # clone match as playoff
        from dataclasses import replace
        playoff_match = replace(reg.match, stage="playoff")
        playoff_fs = FeatureSet(
            match=playoff_match,
            general=reg.general,
            team=reg.team,
            market=reg.market,
            player=reg.player,
            environment=reg.environment,
            custom=reg.custom,
            data_quality=reg.data_quality,
            quality_notes=reg.quality_notes,
            feature_version=reg.feature_version,
        )
        r_reg = engine.predict(reg, reg.match)
        r_po = engine.predict(playoff_fs, playoff_match)
        # Equal Elo: regular home edge should be >= playoff (softer HFA)
        assert (
            r_reg.outcome_probabilities["home_win"]
            >= r_po.outcome_probabilities["home_win"] - 1e-9
        )
        hc_reg = next(i for i in r_reg.explanation if i.factor == "home_court")
        hc_po = next(i for i in r_po.explanation if i.factor == "home_court")
        assert hc_reg.weight > 0
        # p_home_court lower in playoff
        assert "0.55" in hc_po.detail


class TestBasketballEngineInjury:
    def test_injury_factor_available_when_both_impacts_set(self):
        engine = BasketballEngine()
        base = _make_features()
        features = FeatureSet(
            match=base.match,
            general=base.general,
            team=base.team,
            market=base.market,
            player=PlayerFeatures(None, None, 0.35, 0.10),
            environment=base.environment,
            custom=base.custom,
            data_quality=base.data_quality,
            quality_notes=base.quality_notes,
            feature_version=base.feature_version,
        )
        result = engine.predict(features, features.match)
        inj = next(e for e in result.explanation if e.factor == "injury")
        assert inj.available is True
        assert 0.0 < result.outcome_probabilities["home_win"] < 1.0

    def test_custom_injury_fallback_shifts_home_win(self):
        """Higher home injury_impact lowers home_win vs the reverse case."""
        engine = BasketballEngine()
        base = _make_features()
        low_home_inj = FeatureSet(
            match=base.match,
            general=base.general,
            team=base.team,
            market=base.market,
            player=PlayerFeatures(None, None, None, None),
            environment=base.environment,
            custom={**base.custom, "injury_impact_home": 0.0, "injury_impact_away": 0.4},
            data_quality=base.data_quality,
            quality_notes=base.quality_notes,
            feature_version=base.feature_version,
        )
        high_home_inj = FeatureSet(
            match=base.match,
            general=base.general,
            team=base.team,
            market=base.market,
            player=PlayerFeatures(None, None, None, None),
            environment=base.environment,
            custom={**base.custom, "injury_impact_home": 0.4, "injury_impact_away": 0.0},
            data_quality=base.data_quality,
            quality_notes=base.quality_notes,
            feature_version=base.feature_version,
        )
        p_low = engine.predict(low_home_inj, low_home_inj.match).outcome_probabilities["home_win"]
        p_high = engine.predict(high_home_inj, high_home_inj.match).outcome_probabilities["home_win"]
        assert p_low > p_high

