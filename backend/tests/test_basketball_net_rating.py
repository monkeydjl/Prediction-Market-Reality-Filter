"""BasketballEngine net_rating + b2b rest (P1-B2 / P1-B3)."""
from datetime import datetime, timezone

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
from app.sports.basketball.engines.basketball_engine import BasketballEngine


_BB = SportIdentity(code="basketball", name="Basketball")
_NBA = CompetitionIdentity(code="nba", name="NBA", sport=_BB)


def _features(
    *,
    rest_home: float = 2.0,
    rest_away: float = 2.0,
    custom: dict | None = None,
) -> FeatureSet:
    season = SeasonIdentity(competition=_NBA, season_key="2025-26")
    home = TeamIdentity(code="BOS", name="Boston", competition=_NBA)
    away = TeamIdentity(code="LAL", name="Lakers", competition=_NBA)
    match = MatchIdentity(
        "nba-1",
        season,
        "regular_season",
        None,
        home,
        away,
        datetime(2025, 12, 1, tzinfo=timezone.utc),
    )
    return FeatureSet(
        match=match,
        general=GeneralFeatures(rest_home, rest_away, None, None),
        team=TeamFeatures(1600.0, 1550.0, 0.6, 0.5, None, None, None, None),
        market=MarketFeatures(1.9, None, 1.95, "test", True),
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures(None, None, None, True),
        custom=custom or {},
        data_quality="real",
        quality_notes=[],
        feature_version="nba-1.0",
    )


def test_net_rating_shifts_home_when_home_stronger():
    eng = BasketballEngine()
    weak = eng.predict(
        _features(custom={"ortg_home": 110, "drtg_home": 112, "ortg_away": 112, "drtg_away": 110}),
        _features().match,
    )
    strong = eng.predict(
        _features(custom={"ortg_home": 118, "drtg_home": 105, "ortg_away": 108, "drtg_away": 115}),
        _features().match,
    )
    assert strong.outcome_probabilities["home_win"] > weak.outcome_probabilities["home_win"]
    net = next(i for i in strong.explanation if i.factor == "net_rating")
    assert net.available is True


def test_b2b_home_lowers_rest_factor():
    eng = BasketballEngine()
    fresh = eng.predict(_features(rest_home=3.0, rest_away=3.0), _features().match)
    b2b = eng.predict(
        _features(rest_home=1.0, rest_away=3.0, custom={"b2b_home": True, "b2b_away": False}),
        _features(rest_home=1.0, rest_away=3.0).match,
    )
    assert b2b.outcome_probabilities["home_win"] < fresh.outcome_probabilities["home_win"]
