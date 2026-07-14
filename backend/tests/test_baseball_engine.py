# backend/tests/test_baseball_engine.py
"""Tests for BaseballEngine — 5-factor Bradley-Terry binary prediction engine."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures,
)
from app.kernel.protocols import PredictionEngine
from app.sports.baseball.engines.baseball_engine import BaseballEngine


_BASEBALL = SportIdentity(code="baseball", name="Baseball")
_MLB = CompetitionIdentity(code="mlb", name="MLB", sport=_BASEBALL)


def _make_features(
    elo_home=1520.0, elo_away=1490.0,
    form_home=0.6, form_away=0.45,
    rest_home=1, rest_away=2,
    era_home=3.15, era_away=4.10,
) -> FeatureSet:
    comp = _MLB
    season = SeasonIdentity(competition=comp, season_key="2024")
    home = TeamIdentity(code="NYY", name="New York Yankees", competition=comp)
    away = TeamIdentity(code="BOS", name="Boston Red Sox", competition=comp)
    match = MatchIdentity(
        match_id="mlb-778812", season=season,
        stage="regular_season", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2024, 7, 4, tzinfo=timezone.utc),
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
        player=PlayerFeatures(True, True, None, None),
        environment=EnvironmentFeatures("Yankee Stadium", None, None, True),
        custom={
            "pitcher_era_home": era_home, "pitcher_era_away": era_away,
            "pitcher_whip_home": 1.02, "pitcher_whip_away": 1.30,
            "team_batting_avg_home": 0.255, "team_batting_avg_away": 0.245,
            "team_era_home": 3.90, "team_era_away": 4.20,
            "pythagorean_win_pct_home": 0.560, "pythagorean_win_pct_away": 0.480,
        },
        data_quality="real",
        quality_notes=[],
        feature_version="mlb-1.0",
    )


class TestBaseballEngineProtocol:
    def test_implements_protocol(self):
        engine = BaseballEngine()
        assert isinstance(engine, PredictionEngine)

    def test_name_and_supported_sports(self):
        engine = BaseballEngine()
        assert engine.name() == "baseball"
        assert "baseball" in engine.supported_sports()


class TestBaseballEnginePredict:
    def test_predict_returns_binary_probabilities(self):
        """Outcome probabilities have home_win and away_win (no draw)."""
        engine = BaseballEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        assert "home_win" in result.outcome_probabilities
        assert "away_win" in result.outcome_probabilities
        assert "draw" not in result.outcome_probabilities
        total = sum(result.outcome_probabilities.values())
        assert abs(total - 1.0) < 0.01

    def test_stronger_team_higher_win_prob(self):
        """Higher Elo home team → P(home_win) > P(away_win)."""
        engine = BaseballEngine()
        strong = _make_features(elo_home=1700, elo_away=1400)
        result = engine.predict(strong, strong.match)
        assert result.outcome_probabilities["home_win"] > result.outcome_probabilities["away_win"]

    def test_explanation_has_five_factors(self):
        """Explanation contains all 5 factors: elo, home_court, rest, form, starting_pitcher."""
        engine = BaseballEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        factor_ids = [e.factor for e in result.explanation]
        assert "elo" in factor_ids
        assert "home_court" in factor_ids
        assert "rest" in factor_ids
        assert "form" in factor_ids
        assert "starting_pitcher" in factor_ids

    def test_contribution_item_predicted_outcome_is_binary(self):
        """Each ContributionItem.predicted_outcome is home_win or away_win."""
        engine = BaseballEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        for item in result.explanation:
            assert item.predicted_outcome in ("home_win", "away_win", None)

    def test_better_home_pitcher_increases_home_win_prob(self):
        """Lower home ERA (better pitcher) → higher P(home_win)."""
        engine = BaseballEngine()
        # Home pitcher much better (lower ERA)
        better_home = _make_features(era_home=2.50, era_away=5.00)
        # Equal pitchers
        equal = _make_features(era_home=4.00, era_away=4.00)
        p_better = engine.predict(better_home, better_home.match).outcome_probabilities["home_win"]
        p_equal = engine.predict(equal, equal.match).outcome_probabilities["home_win"]
        assert p_better > p_equal

    def test_no_elo_fallback(self):
        """When Elo is None, engine still produces valid prediction via weight redistribution."""
        engine = BaseballEngine()
        features = _make_features(elo_home=None, elo_away=None)
        result = engine.predict(features, features.match)
        elo_item = next(e for e in result.explanation if e.factor == "elo")
        assert elo_item.available is False
        total = sum(result.outcome_probabilities.values())
        assert abs(total - 1.0) < 0.01
        # Other factors still contribute
        assert result.outcome_probabilities["home_win"] != 0.5 or \
               result.outcome_probabilities["away_win"] != 0.5

    def test_score_conversion_uses_league_avg(self):
        """Predicted scores are centered around MLB league avg total (8.5)."""
        engine = BaseballEngine()
        features = _make_features(elo_home=1500, elo_away=1500)
        result = engine.predict(features, features.match)
        home_score = result.predicted_scores["home"]
        away_score = result.predicted_scores["away"]
        # League avg = 8.5, so each ~4.25 (plus HFA adjustment)
        assert 3.0 < home_score < 6.0
        assert 3.0 < away_score < 6.0
