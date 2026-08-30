# backend/tests/test_factor_vote_engines.py
"""Every binary engine must publish an absent vote for a level factor (E20).

The level case is reached through ``rest``: all three engines compute
``p_rest = 0.5 + rest_diff * 0.03``, so equal rest days give exactly 0.5 with
the factor *available*. Each engine gets the level case **and its converse**
(unequal rest, either direction) so a test cannot pass by the factor merely
being unavailable.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.kernel.domain import (
    CompetitionIdentity,
    EnvironmentFeatures,
    FeatureSet,
    GeneralFeatures,
    MarketFeatures,
    MatchIdentity,
    PlayerFeatures,
    SeasonIdentity,
    SportIdentity,
    TeamFeatures,
    TeamIdentity,
)
from app.sports.baseball.engines.baseball_engine import BaseballEngine
from app.sports.basketball.engines.basketball_engine import BasketballEngine
from app.sports.hockey.engines.hockey_engine import HockeyEngine


def _features(
    *,
    sport_code: str,
    comp_code: str,
    rest_home: float | None,
    rest_away: float | None,
    elo_home: float | None = 1600.0,
    elo_away: float | None = 1500.0,
) -> FeatureSet:
    sport = SportIdentity(code=sport_code, name=sport_code.title())
    comp = CompetitionIdentity(code=comp_code, name=comp_code.upper(), sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2024-25")
    home = TeamIdentity(code="HHH", name="Home Team", competition=comp)
    away = TeamIdentity(code="AAA", name="Away Team", competition=comp)
    match = MatchIdentity(
        match_id=f"{comp_code}-vote-1",
        season=season,
        stage="regular_season",
        round=None,
        home=home,
        away=away,
        kickoff_utc=datetime(2025, 1, 15, tzinfo=timezone.utc),
    )
    return FeatureSet(
        match=match,
        general=GeneralFeatures(
            rest_days_home=rest_home,
            rest_days_away=rest_away,
            travel_distance_km=None,
            days_since_last_match=None,
        ),
        team=TeamFeatures(
            elo_rating_home=elo_home,
            elo_rating_away=elo_away,
            form_home=None,
            form_away=None,
            h2h_home_win_rate=None,
            h2h_draw_rate=None,
            market_value_home=None,
            market_value_away=None,
        ),
        market=MarketFeatures(None, None, None, None, False),
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures("Some Arena", None, None, True),
        custom={},
        data_quality="real",
        quality_notes=[],
        feature_version=f"{comp_code}-1.0",
    )


_ENGINES = [
    ("baseball", "mlb", BaseballEngine),
    ("basketball", "nba", BasketballEngine),
    ("hockey", "nhl", HockeyEngine),
]


def _rest_row(result):
    rows = [e for e in result.explanation if e.factor == "rest"]
    assert len(rows) == 1, f"expected exactly one rest row, got {len(rows)}"
    return rows[0]


class TestLevelFactorCastsNoVote:
    @pytest.mark.parametrize("sport,comp,engine_cls", _ENGINES)
    def test_equal_rest_is_available_and_votes_nothing(self, sport, comp, engine_cls):
        feats = _features(
            sport_code=sport, comp_code=comp, rest_home=2.0, rest_away=2.0
        )
        row = _rest_row(engine_cls().predict(feats, feats.match))
        # available=True is the whole point: the factor ran, measured, and found
        # the sides level. It must not pass this test by being unavailable.
        assert row.available is True
        assert "P(home_win)=0.5" in row.detail
        assert row.predicted_outcome is None

    @pytest.mark.parametrize("sport,comp,engine_cls", _ENGINES)
    def test_more_home_rest_votes_home(self, sport, comp, engine_cls):
        feats = _features(
            sport_code=sport, comp_code=comp, rest_home=4.0, rest_away=2.0
        )
        row = _rest_row(engine_cls().predict(feats, feats.match))
        assert row.available is True
        assert row.predicted_outcome == "home_win"

    @pytest.mark.parametrize("sport,comp,engine_cls", _ENGINES)
    def test_more_away_rest_votes_away(self, sport, comp, engine_cls):
        feats = _features(
            sport_code=sport, comp_code=comp, rest_home=2.0, rest_away=4.0
        )
        row = _rest_row(engine_cls().predict(feats, feats.match))
        assert row.available is True
        assert row.predicted_outcome == "away_win"

    @pytest.mark.parametrize("sport,comp,engine_cls", _ENGINES)
    def test_missing_rest_is_unavailable_and_also_votes_nothing(
        self, sport, comp, engine_cls
    ):
        """The two ``None`` reasons are distinguishable by ``available``."""
        feats = _features(
            sport_code=sport, comp_code=comp, rest_home=None, rest_away=None
        )
        row = _rest_row(engine_cls().predict(feats, feats.match))
        assert row.available is False
        assert row.predicted_outcome is None
