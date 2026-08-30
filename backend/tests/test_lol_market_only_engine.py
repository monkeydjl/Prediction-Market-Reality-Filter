# backend/tests/test_lol_market_only_engine.py
"""Tests for LolMarketOnlyEngine — binary series winner from market probs."""
import unittest
from datetime import datetime, timezone

from app.kernel.domain import (
    SportIdentity,
    CompetitionIdentity,
    SeasonIdentity,
    TeamIdentity,
    MatchIdentity,
    FeatureSet,
    GeneralFeatures,
    TeamFeatures,
    MarketFeatures,
    PlayerFeatures,
    EnvironmentFeatures,
)
from app.kernel.protocols import PredictionEngine
from app.sports.lol.engines.market_only_engine import LolMarketOnlyEngine


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


def _make_features(custom: dict | None = None) -> FeatureSet:
    match = _make_match()
    return FeatureSet(
        match=match,
        general=GeneralFeatures(None, None, None, None),
        team=TeamFeatures(None, None, None, None, None, None, None, None),
        market=MarketFeatures(None, None, None, None, False),
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures(None, None, None, False),
        custom=dict(custom or {}),
        data_quality="partial" if not custom else "real",
        quality_notes=[],
        feature_version="lol-market-0.1",
    )


class TestLolMarketOnlyEngineIdentity:
    def test_name_and_supported_sports(self):
        engine = LolMarketOnlyEngine()
        assert engine.name() == "lol_market_only"
        assert engine.supported_sports() == ["lol"]

    def test_implements_protocol(self):
        assert isinstance(LolMarketOnlyEngine(), PredictionEngine)


class TestLolMarketOnlyEnginePredict:
    def test_equal_market_missing_low_confidence(self):
        engine = LolMarketOnlyEngine()
        features = _make_features(custom={})
        result = engine.predict(features, features.match)

        assert abs(result.outcome_probabilities["home_win"] - 0.5) < 1e-9
        assert abs(result.outcome_probabilities["away_win"] - 0.5) < 1e-9
        assert result.confidence <= 0.2
        assert result.engine_name == "lol_market_only"

    def test_mkt_preserved_after_norm(self):
        engine = LolMarketOnlyEngine()
        features = _make_features(custom={"mkt_home": 0.7, "mkt_away": 0.3})
        result = engine.predict(features, features.match)

        assert abs(result.outcome_probabilities["home_win"] - 0.7) < 1e-9
        assert abs(result.outcome_probabilities["away_win"] - 0.3) < 1e-9
        total = sum(result.outcome_probabilities.values())
        assert abs(total - 1.0) < 1e-9

    def test_no_draw_in_outcome_probabilities(self):
        engine = LolMarketOnlyEngine()
        features = _make_features(custom={"mkt_home": 0.6, "mkt_away": 0.4})
        result = engine.predict(features, features.match)

        assert "home_win" in result.outcome_probabilities
        assert "away_win" in result.outcome_probabilities
        assert "draw" not in result.outcome_probabilities
        assert isinstance(result.predicted_scores, dict)


class TestLolMarketOnlyEngineVote(unittest.TestCase):
    """E20: a symmetric book has called nothing and must vote nothing."""

    def test_a_symmetric_book_votes_nothing(self):
        features = _make_features(custom={"mkt_home": 0.5, "mkt_away": 0.5})
        result = LolMarketOnlyEngine().predict(features, features.match)
        row = result.explanation[0]
        assert row.factor == "market"
        # available=True: the market was quoted, both sides equally.
        assert row.available is True
        assert row.predicted_outcome is None

    def test_unnormalised_but_equal_odds_also_vote_nothing(self):
        """The tie survives normalisation: 0.9/0.9 → p_h == 0.5 exactly."""
        features = _make_features(custom={"mkt_home": 0.9, "mkt_away": 0.9})
        result = LolMarketOnlyEngine().predict(features, features.match)
        assert result.outcome_probabilities["home_win"] == 0.5
        assert result.explanation[0].available is True
        assert result.explanation[0].predicted_outcome is None

    def test_an_asymmetric_book_still_votes(self):
        for mh, ma, expected in [(0.6, 0.4, "home_win"), (0.4, 0.6, "away_win")]:
            with self.subTest(mkt_home=mh):
                features = _make_features(custom={"mkt_home": mh, "mkt_away": ma})
                result = LolMarketOnlyEngine().predict(features, features.match)
                assert result.explanation[0].available is True
                assert result.explanation[0].predicted_outcome == expected

    def test_no_market_is_unavailable_and_votes_nothing(self):
        features = _make_features()
        result = LolMarketOnlyEngine().predict(features, features.match)
        assert result.explanation[0].available is False
        assert result.explanation[0].predicted_outcome is None
