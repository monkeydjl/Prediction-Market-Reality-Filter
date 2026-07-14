# backend/tests/test_baseball_feature_builder.py
"""Tests for BaseballFeatureBuilder — FeatureBuilder Protocol."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity,
)
from app.kernel.protocols import FeatureBuilder
from app.sports.baseball.feature_builder import BaseballFeatureBuilder


_BASEBALL = SportIdentity(code="baseball", name="Baseball")
_MLB = CompetitionIdentity(code="mlb", name="MLB", sport=_BASEBALL)


def _make_match() -> MatchIdentity:
    return MatchIdentity(
        match_id="mlb-778812",
        season=SeasonIdentity(competition=_MLB, season_key="2024"),
        stage="regular_season", round=None,
        home=TeamIdentity(code="NYY", name="New York Yankees", competition=_MLB),
        away=TeamIdentity(code="BOS", name="Boston Red Sox", competition=_MLB),
        kickoff_utc=datetime(2024, 7, 4, tzinfo=timezone.utc),
    )


def _make_raw_with_elo():
    return {
        "team": {"elo_home": 1520.0, "elo_away": 1490.0, "form_home": 0.6, "form_away": 0.45},
        "general": {"rest_days_home": 1, "rest_days_away": 2, "days_since_last_match": 1},
        "market": {},
        "player": {"starting_pitcher_home": "Gerrit Cole", "starting_pitcher_away": "Brayan Bello"},
        "environment": {"venue": "Yankee Stadium", "is_home_advantage": True},
        "custom": {
            "pitcher_era_home": 3.15, "pitcher_era_away": 4.10,
            "pitcher_whip_home": 1.02, "pitcher_whip_away": 1.30,
            "team_batting_avg_home": 0.255, "team_batting_avg_away": 0.245,
            "team_era_home": 3.90, "team_era_away": 4.20,
            "pythagorean_win_pct_home": 0.560, "pythagorean_win_pct_away": 0.480,
        },
    }


class TestBaseballFeatureBuilderProtocol:
    def test_satisfies_feature_builder_protocol(self):
        builder = BaseballFeatureBuilder()
        assert isinstance(builder, FeatureBuilder)

    def test_sport_returns_baseball(self):
        builder = BaseballFeatureBuilder()
        sport = builder.sport()
        assert sport.code == "baseball"
        assert sport.name == "Baseball"


class TestBaseballFeatureBuilderBuild:
    def test_full_feature_mapping(self):
        """All layers are mapped correctly from raw dict."""
        builder = BaseballFeatureBuilder()
        features = builder.build(_make_match(), _make_raw_with_elo())

        # General layer
        assert features.general.rest_days_home == 1
        assert features.general.rest_days_away == 2

        # Team layer
        assert features.team.elo_rating_home == 1520.0
        assert features.team.elo_rating_away == 1490.0
        assert features.team.form_home == 0.6
        assert features.team.form_away == 0.45
        assert features.team.h2h_draw_rate is None  # Baseball has no draws
        assert features.team.market_value_home is None

        # Market layer — all None (no odds source)
        assert features.market.odds_home is None
        assert features.market.odds_away is None

        # Environment layer
        assert features.environment.venue == "Yankee Stadium"
        assert features.environment.is_home_advantage is True
        assert features.environment.weather_temp_c is None

        # Custom layer — baseball-specific features
        assert features.custom["pitcher_era_home"] == 3.15
        assert features.custom["pitcher_era_away"] == 4.10
        assert features.custom["pitcher_whip_home"] == 1.02
        assert features.custom["team_batting_avg_home"] == 0.255
        assert features.custom["team_era_away"] == 4.20
        assert features.custom["pythagorean_win_pct_home"] == 0.560

        # Feature version
        assert features.feature_version == "mlb-1.0"

    def test_data_quality_real_when_elo_present(self):
        """Data quality is 'real' when Elo exists, even without odds."""
        builder = BaseballFeatureBuilder()
        features = builder.build(_make_match(), _make_raw_with_elo())
        assert features.data_quality == "real"
        assert "betting_odds_unavailable" not in features.quality_notes

    def test_data_quality_partial_when_elo_missing(self):
        """Data quality is 'partial' when Elo is None."""
        builder = BaseballFeatureBuilder()
        raw = _make_raw_with_elo()
        raw["team"]["elo_home"] = None
        raw["team"]["elo_away"] = None
        features = builder.build(_make_match(), raw)
        assert features.data_quality == "partial"
