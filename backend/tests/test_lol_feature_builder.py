# backend/tests/test_lol_feature_builder.py
"""Tests for LolFeatureBuilder — series market features for LoL."""
from datetime import datetime, timezone

from app.kernel.domain import (
    SportIdentity,
    CompetitionIdentity,
    SeasonIdentity,
    TeamIdentity,
    MatchIdentity,
)
from app.sports.lol.feature_builder import LolFeatureBuilder


_LOL = SportIdentity(code="lol", name="League of Legends")
_COMP = CompetitionIdentity(code="lol", name="League of Legends", sport=_LOL)


def _make_match() -> MatchIdentity:
    return MatchIdentity(
        match_id="lol-series-1",
        season=SeasonIdentity(competition=_COMP, season_key="dry-run"),
        stage="regular",
        round=None,
        home=TeamIdentity(code="T1", name="T1", competition=_COMP),
        away=TeamIdentity(code="GEN", name="Gen.G", competition=_COMP),
        kickoff_utc=datetime(2025, 6, 1, tzinfo=timezone.utc),
    )


class TestLolFeatureBuilderSport:
    def test_sport_code_is_lol(self):
        builder = LolFeatureBuilder()
        sport = builder.sport()
        assert sport.code == "lol"
        assert sport.name == "League of Legends"


class TestLolFeatureBuilderBuild:
    def test_without_market_probs_data_quality_partial(self):
        builder = LolFeatureBuilder()
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {"venue": "Bo3", "is_home_advantage": True},
            "custom": {"best_of": 3},
        }
        features = builder.build(_make_match(), raw)
        assert features.data_quality == "partial"
        assert features.custom["best_of"] == 3
        assert features.custom["series_format"] == "Bo3"
        assert features.team.elo_rating_home is None
        assert features.market.odds_home is None

    def test_with_mkt_probs_data_quality_real(self):
        builder = LolFeatureBuilder()
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {"venue": "Bo5"},
            "custom": {
                "best_of": 5,
                "mkt_home": 0.6,
                "mkt_away": 0.4,
            },
        }
        features = builder.build(_make_match(), raw)
        assert features.data_quality == "real"
        assert features.custom["mkt_home"] == 0.6
        assert features.custom["mkt_away"] == 0.4
        assert features.custom["series_format"] == "Bo5"

    def test_feature_version(self):
        builder = LolFeatureBuilder()
        features = builder.build(_make_match(), {"custom": {}})
        assert features.feature_version == "lol-market-0.1"
