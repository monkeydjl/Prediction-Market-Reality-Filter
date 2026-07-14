# backend/tests/test_hockey_feature_builder.py
"""Tests for HockeyFeatureBuilder — FeatureBuilder Protocol."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity,
)
from app.kernel.protocols import FeatureBuilder
from app.sports.hockey.feature_builder import HockeyFeatureBuilder


_HOCKEY = SportIdentity(code="hockey", name="Hockey")
_NHL = CompetitionIdentity(code="nhl", name="NHL", sport=_HOCKEY)


def _make_match() -> MatchIdentity:
    return MatchIdentity(
        match_id="nhl-2023020001",
        season=SeasonIdentity(competition=_NHL, season_key="20232024"),
        stage="regular_season", round=None,
        home=TeamIdentity(code="NJD", name="New Jersey Devils", competition=_NHL),
        away=TeamIdentity(code="NYR", name="New York Rangers", competition=_NHL),
        kickoff_utc=datetime(2024, 1, 15, tzinfo=timezone.utc),
    )


def _make_raw_with_elo():
    return {
        "team": {"elo_home": 1510.0, "elo_away": 1495.0, "form_home": 0.6, "form_away": 0.45},
        "general": {"rest_days_home": 2, "rest_days_away": 1, "days_since_last_match": 2},
        "market": {},
        "player": {"starting_goalie_home": "Igor Shesterkin", "starting_goalie_away": "Juuse Saros"},
        "environment": {"venue": "Prudential Center", "is_home_advantage": True},
        "custom": {
            "goalie_save_pct_home": 0.912, "goalie_save_pct_away": 0.920,
            "team_gf_home": 3.20, "team_gf_away": 3.00,
            "team_ga_home": 2.90, "team_ga_away": 3.10,
            "corsi_pct_home": 52.0, "corsi_pct_away": 48.0,
            "pdo_home": 101.5, "pdo_away": 98.5,
            "went_to_overtime": False, "went_to_shootout": False,
        },
    }


class TestHockeyFeatureBuilderProtocol:
    def test_satisfies_feature_builder_protocol(self):
        builder = HockeyFeatureBuilder()
        assert isinstance(builder, FeatureBuilder)

    def test_sport_returns_hockey(self):
        builder = HockeyFeatureBuilder()
        sport = builder.sport()
        assert sport.code == "hockey"
        assert sport.name == "Hockey"


class TestHockeyFeatureBuilderBuild:
    def test_full_feature_mapping(self):
        """All layers are mapped correctly from raw dict."""
        builder = HockeyFeatureBuilder()
        features = builder.build(_make_match(), _make_raw_with_elo())

        # General layer
        assert features.general.rest_days_home == 2
        assert features.general.rest_days_away == 1

        # Team layer
        assert features.team.elo_rating_home == 1510.0
        assert features.team.elo_rating_away == 1495.0
        assert features.team.form_home == 0.6
        assert features.team.form_away == 0.45
        assert features.team.h2h_draw_rate is None  # Hockey has no draws
        assert features.team.market_value_home is None

        # Market layer — all None (no odds source)
        assert features.market.odds_home is None
        assert features.market.odds_away is None

        # Environment layer
        assert features.environment.venue == "Prudential Center"
        assert features.environment.is_home_advantage is True
        assert features.environment.weather_temp_c is None

        # Custom layer — hockey-specific features
        assert features.custom["goalie_save_pct_home"] == 0.912
        assert features.custom["goalie_save_pct_away"] == 0.920
        assert features.custom["team_gf_home"] == 3.20
        assert features.custom["team_ga_away"] == 3.10
        assert features.custom["corsi_pct_home"] == 52.0
        assert features.custom["pdo_home"] == 101.5
        # Overtime/shootout flags preserved (Constraint 22)
        assert features.custom["went_to_overtime"] is False
        assert features.custom["went_to_shootout"] is False

        # Feature version
        assert features.feature_version == "nhl-1.0"

    def test_data_quality_real_when_elo_present(self):
        """Data quality is 'real' when Elo exists, even without odds."""
        builder = HockeyFeatureBuilder()
        features = builder.build(_make_match(), _make_raw_with_elo())
        assert features.data_quality == "real"
        assert "betting_odds_unavailable" not in features.quality_notes

    def test_data_quality_partial_when_elo_missing(self):
        """Data quality is 'partial' when Elo is None."""
        builder = HockeyFeatureBuilder()
        raw = _make_raw_with_elo()
        raw["team"]["elo_home"] = None
        raw["team"]["elo_away"] = None
        features = builder.build(_make_match(), raw)
        assert features.data_quality == "partial"
