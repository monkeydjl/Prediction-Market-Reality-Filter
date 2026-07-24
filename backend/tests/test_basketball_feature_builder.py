# backend/tests/test_basketball_feature_builder.py
"""Tests for BasketballFeatureBuilder — FeatureBuilder Protocol."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity,
)
from app.kernel.protocols import FeatureBuilder
from app.sports.basketball.feature_builder import BasketballFeatureBuilder


_BASKETBALL = SportIdentity(code="basketball", name="Basketball")
_NBA = CompetitionIdentity(code="nba", name="NBA", sport=_BASKETBALL)


def _make_match() -> MatchIdentity:
    return MatchIdentity(
        match_id="nba-123",
        season=SeasonIdentity(competition=_NBA, season_key="2024-25"),
        stage="regular_season", round=None,
        home=TeamIdentity(code="BOS", name="Boston Celtics", competition=_NBA),
        away=TeamIdentity(code="LAL", name="Los Angeles Lakers", competition=_NBA),
        kickoff_utc=datetime(2024, 12, 25, tzinfo=timezone.utc),
    )


def _make_raw_with_elo():
    return {
        "team": {"elo_home": 1650.0, "elo_away": 1520.0, "form_home": 0.7, "form_away": 0.4},
        "general": {"rest_days_home": 2, "rest_days_away": 1, "days_since_last_match": 2},
        "market": {},
        "player": {},
        "environment": {"venue": "TD Garden", "is_home_advantage": True},
        "custom": {
            "pace_home": 99.5, "pace_away": 97.2,
            "ortg_home": 112.3, "ortg_away": 108.1,
            "drtg_home": 105.0, "drtg_away": 110.5,
            "tpct_home": 0.365, "tpct_away": 0.342,
        },
    }


class TestBasketballFeatureBuilderProtocol:
    def test_satisfies_feature_builder_protocol(self):
        builder = BasketballFeatureBuilder()
        assert isinstance(builder, FeatureBuilder)

    def test_sport_returns_basketball(self):
        builder = BasketballFeatureBuilder()
        sport = builder.sport()
        assert sport.code == "basketball"


class TestBasketballFeatureBuilderBuild:
    def test_full_feature_mapping(self):
        """All layers are mapped correctly from raw dict."""
        builder = BasketballFeatureBuilder()
        features = builder.build(_make_match(), _make_raw_with_elo())

        # General layer
        assert features.general.rest_days_home == 2
        assert features.general.rest_days_away == 1

        # Team layer
        assert features.team.elo_rating_home == 1650.0
        assert features.team.elo_rating_away == 1520.0
        assert features.team.form_home == 0.7
        assert features.team.form_away == 0.4
        # Basketball has no draws
        assert features.team.h2h_draw_rate is None
        assert features.team.market_value_home is None

        # Market layer — all None (free tier has no odds)
        assert features.market.odds_home is None
        assert features.market.odds_away is None

        # Environment layer
        assert features.environment.venue == "TD Garden"
        assert features.environment.is_home_advantage is True
        # Weather not applicable to basketball
        assert features.environment.weather_temp_c is None

        # Custom layer — basketball-specific features
        assert features.custom["pace_home"] == 99.5
        assert features.custom["ortg_home"] == 112.3
        assert features.custom["drtg_away"] == 110.5
        assert features.custom["tpct_home"] == 0.365

        # Feature version
        assert features.feature_version == "nba-1.0"

    def test_data_quality_real_when_elo_present(self):
        """Data quality is 'real' when Elo exists, even without odds."""
        builder = BasketballFeatureBuilder()
        features = builder.build(_make_match(), _make_raw_with_elo())
        assert features.data_quality == "real"
        # No odds-related quality notes (unlike football)
        assert "betting_odds_unavailable" not in features.quality_notes

    def test_data_quality_partial_when_elo_missing(self):
        """Data quality is 'partial' when Elo is None."""
        builder = BasketballFeatureBuilder()
        raw = _make_raw_with_elo()
        raw["team"]["elo_home"] = None
        raw["team"]["elo_away"] = None
        features = builder.build(_make_match(), raw)
        assert features.data_quality == "partial"

    def test_injury_impact_passthrough_from_player_raw(self):
        builder = BasketballFeatureBuilder()
        raw = _make_raw_with_elo()
        raw["player"] = {
            "injury_impact_home": 0.35,
            "injury_impact_away": 0.26,
        }
        features = builder.build(_make_match(), raw)
        assert features.player.injury_impact_home == pytest.approx(0.35)
        assert features.player.injury_impact_away == pytest.approx(0.26)

    def test_injury_impact_defaults_none_when_absent(self):
        builder = BasketballFeatureBuilder()
        features = builder.build(_make_match(), _make_raw_with_elo())
        assert features.player.injury_impact_home is None
        assert features.player.injury_impact_away is None
