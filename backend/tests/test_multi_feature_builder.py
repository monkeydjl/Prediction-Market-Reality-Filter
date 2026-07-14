# backend/tests/test_multi_feature_builder.py
"""Tests for MultiFeatureBuilder — prefix-dispatch FeatureBuilder proxy."""
from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures,
)
from app.kernel.protocols import FeatureBuilder
from app.kernel.multi_feature_builder import MultiFeatureBuilder


_FOOTBALL = SportIdentity(code="football", name="Football")
_BASKETBALL = SportIdentity(code="basketball", name="Basketball")


def _make_match(match_id="wc-123") -> MatchIdentity:
    if match_id.startswith("wc-"):
        comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=_FOOTBALL)
    else:
        comp = CompetitionIdentity(code="nba", name="NBA", sport=_BASKETBALL)
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=comp, season_key="2024-25"),
        stage="regular_season", round=None,
        home=TeamIdentity(code="HOME", name="Home", competition=comp),
        away=TeamIdentity(code="AWAY", name="Away", competition=comp),
        kickoff_utc=datetime(2024, 12, 25, tzinfo=timezone.utc),
    )


def _mock_builder(sport: SportIdentity) -> MagicMock:
    """Create a MagicMock that satisfies FeatureBuilder Protocol."""
    builder = MagicMock()
    builder.sport.return_value = sport
    builder.build.return_value = FeatureSet(
        match=_make_match(),
        general=GeneralFeatures(None, None, None, None),
        team=TeamFeatures(None, None, None, None, None, None, None, None),
        market=MarketFeatures(None, None, None, None, False),
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures(None, None, None, False),
        custom={}, data_quality="real", quality_notes=[],
        feature_version="1.0",
    )
    return builder


class TestMultiFeatureBuilderProtocol:
    def test_satisfies_feature_builder_protocol(self):
        fb = MultiFeatureBuilder({"wc-": _mock_builder(_FOOTBALL)})
        assert isinstance(fb, FeatureBuilder)


class TestPrefixDispatch:
    def test_wc_prefix_dispatches_to_football_builder(self):
        football = _mock_builder(_FOOTBALL)
        basketball = _mock_builder(_BASKETBALL)
        mfb = MultiFeatureBuilder({"wc-": football, "nba-": basketball})

        match = _make_match("wc-123")
        mfb.build(match, {})
        football.build.assert_called_once_with(match, {})
        basketball.build.assert_not_called()

    def test_nba_prefix_dispatches_to_basketball_builder(self):
        football = _mock_builder(_FOOTBALL)
        basketball = _mock_builder(_BASKETBALL)
        mfb = MultiFeatureBuilder({"wc-": football, "nba-": basketball})

        match = _make_match("nba-456")
        mfb.build(match, {})
        basketball.build.assert_called_once_with(match, {})
        football.build.assert_not_called()

    def test_unknown_prefix_falls_back_to_default(self):
        football = _mock_builder(_FOOTBALL)
        mfb = MultiFeatureBuilder({"wc-": football})

        match = _make_match("unknown-789")
        mfb.build(match, {})
        football.build.assert_called_once_with(match, {})


class TestSport:
    def test_sport_returns_default_builder_sport(self):
        football = _mock_builder(_FOOTBALL)
        basketball = _mock_builder(_BASKETBALL)
        mfb = MultiFeatureBuilder({"wc-": football, "nba-": basketball})
        # Default is first builder (football)
        assert mfb.sport() == _FOOTBALL
