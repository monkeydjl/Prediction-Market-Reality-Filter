# backend/tests/test_hockey_engine.py
"""Tests for HockeyEngine — 5-factor Bradley-Terry binary prediction engine."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures,
)
from app.kernel.protocols import PredictionEngine
from app.sports.hockey.engines.hockey_engine import HockeyEngine


_HOCKEY = SportIdentity(code="hockey", name="Hockey")
_NHL = CompetitionIdentity(code="nhl", name="NHL", sport=_HOCKEY)


def _make_features(
    elo_home=1510.0, elo_away=1495.0,
    form_home=0.6, form_away=0.45,
    rest_home=2, rest_away=1,
    sv_pct_home=0.912, sv_pct_away=0.920,
) -> FeatureSet:
    comp = _NHL
    season = SeasonIdentity(competition=comp, season_key="20232024")
    home = TeamIdentity(code="NJD", name="New Jersey Devils", competition=comp)
    away = TeamIdentity(code="NYR", name="New York Rangers", competition=comp)
    match = MatchIdentity(
        match_id="nhl-2023020001", season=season,
        stage="regular_season", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2024, 1, 15, tzinfo=timezone.utc),
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
        environment=EnvironmentFeatures("Prudential Center", None, None, True),
        custom={
            "goalie_save_pct_home": sv_pct_home, "goalie_save_pct_away": sv_pct_away,
            "team_gf_home": 3.20, "team_gf_away": 3.00,
            "team_ga_home": 2.90, "team_ga_away": 3.10,
            "corsi_pct_home": 52.0, "corsi_pct_away": 48.0,
            "pdo_home": 101.5, "pdo_away": 98.5,
            "went_to_overtime": False, "went_to_shootout": False,
        },
        data_quality="real",
        quality_notes=[],
        feature_version="nhl-1.0",
    )


class TestHockeyEngineProtocol:
    def test_implements_protocol(self):
        engine = HockeyEngine()
        assert isinstance(engine, PredictionEngine)

    def test_name_and_supported_sports(self):
        engine = HockeyEngine()
        assert engine.name() == "hockey"
        assert "hockey" in engine.supported_sports()


class TestHockeyEnginePredict:
    def test_predict_returns_binary_probabilities(self):
        """Outcome probabilities have home_win and away_win (no draw)."""
        engine = HockeyEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        assert "home_win" in result.outcome_probabilities
        assert "away_win" in result.outcome_probabilities
        assert "draw" not in result.outcome_probabilities
        total = sum(result.outcome_probabilities.values())
        assert abs(total - 1.0) < 0.01

    def test_stronger_team_higher_win_prob(self):
        """Higher Elo home team → P(home_win) > P(away_win)."""
        engine = HockeyEngine()
        strong = _make_features(elo_home=1700, elo_away=1400)
        result = engine.predict(strong, strong.match)
        assert result.outcome_probabilities["home_win"] > result.outcome_probabilities["away_win"]

    def test_explanation_has_five_factors(self):
        """Explanation contains all 5 factors: elo, home_court, rest, form, goalie."""
        engine = HockeyEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        factor_ids = [e.factor for e in result.explanation]
        assert "elo" in factor_ids
        assert "home_court" in factor_ids
        assert "rest" in factor_ids
        assert "form" in factor_ids
        assert "goalie" in factor_ids

    def test_contribution_item_predicted_outcome_is_binary(self):
        """Each ContributionItem.predicted_outcome is home_win or away_win."""
        engine = HockeyEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        for item in result.explanation:
            assert item.predicted_outcome in ("home_win", "away_win", None)

    def test_better_home_goalie_increases_home_win_prob(self):
        """Higher home save% (better goalie) → higher P(home_win)."""
        engine = HockeyEngine()
        # Home goalie much better
        better_home = _make_features(sv_pct_home=0.930, sv_pct_away=0.890)
        # Equal goalies
        equal = _make_features(sv_pct_home=0.910, sv_pct_away=0.910)
        p_better = engine.predict(better_home, better_home.match).outcome_probabilities["home_win"]
        p_equal = engine.predict(equal, equal.match).outcome_probabilities["home_win"]
        assert p_better > p_equal

    def test_no_elo_fallback(self):
        """When Elo is None, engine still produces valid prediction via weight redistribution."""
        engine = HockeyEngine()
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
        """Predicted scores are centered around NHL league avg total (5.5)."""
        engine = HockeyEngine()
        features = _make_features(elo_home=1500, elo_away=1500)
        result = engine.predict(features, features.match)
        home_score = result.predicted_scores["home"]
        away_score = result.predicted_scores["away"]
        # League avg = 5.5, so each ~2.75 (plus HFA adjustment)
        assert 1.5 < home_score < 4.5
        assert 1.5 < away_score < 4.5
