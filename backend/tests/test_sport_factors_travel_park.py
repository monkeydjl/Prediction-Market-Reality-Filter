"""P1-B1/B3, P1-M1/M2/M3, P1-H3: travel, injury, park, bullpen, weather factors."""
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
from app.sports.baseball.engines.baseball_engine import BaseballEngine
from app.sports.basketball.engines.basketball_engine import BasketballEngine
from app.sports.hockey.engines.hockey_engine import HockeyEngine


def _bb_features(
    *,
    custom: dict | None = None,
    injury_home=None,
    injury_away=None,
    travel_km=None,
) -> FeatureSet:
    sport = SportIdentity(code="basketball", name="Basketball")
    comp = CompetitionIdentity(code="nba", name="NBA", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2025-26")
    home = TeamIdentity(code="BOS", name="Boston", competition=comp)
    away = TeamIdentity(code="LAL", name="Lakers", competition=comp)
    match = MatchIdentity(
        "nba-x",
        season,
        "regular_season",
        None,
        home,
        away,
        datetime(2025, 12, 1, tzinfo=timezone.utc),
    )
    return FeatureSet(
        match=match,
        general=GeneralFeatures(2.0, 2.0, travel_km, None),
        team=TeamFeatures(1600.0, 1550.0, 0.6, 0.5, None, None, None, None),
        market=MarketFeatures(None, None, None, None, False),
        player=PlayerFeatures(None, None, injury_home, injury_away),
        environment=EnvironmentFeatures(None, None, None, True),
        custom=custom or {},
        data_quality="real",
        quality_notes=[],
        feature_version="nba-1.0",
    )


def _mlb_features(*, custom: dict | None = None) -> FeatureSet:
    sport = SportIdentity(code="baseball", name="Baseball")
    comp = CompetitionIdentity(code="mlb", name="MLB", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2025")
    home = TeamIdentity(code="NYY", name="Yankees", competition=comp)
    away = TeamIdentity(code="BOS", name="Red Sox", competition=comp)
    match = MatchIdentity(
        "mlb-x",
        season,
        "regular_season",
        None,
        home,
        away,
        datetime(2025, 7, 4, tzinfo=timezone.utc),
    )
    base_custom = {
        "pitcher_era_home": 3.5,
        "pitcher_era_away": 3.5,
        "park_factor": 1.0,
        "bullpen_era_home": 4.0,
        "bullpen_era_away": 4.0,
    }
    base_custom.update(custom or {})
    return FeatureSet(
        match=match,
        general=GeneralFeatures(1.0, 1.0, None, None),
        team=TeamFeatures(1520.0, 1520.0, 0.5, 0.5, None, None, None, None),
        market=MarketFeatures(None, None, None, None, False),
        player=PlayerFeatures(True, True, None, None),
        environment=EnvironmentFeatures("Park", None, None, True),
        custom=base_custom,
        data_quality="real",
        quality_notes=[],
        feature_version="mlb-1.0",
    )


def _nhl_features(*, custom: dict | None = None) -> FeatureSet:
    sport = SportIdentity(code="hockey", name="Hockey")
    comp = CompetitionIdentity(code="nhl", name="NHL", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2025-26")
    home = TeamIdentity(code="TOR", name="Toronto", competition=comp)
    away = TeamIdentity(code="VAN", name="Vancouver", competition=comp)
    match = MatchIdentity(
        "nhl-x",
        season,
        "regular_season",
        None,
        home,
        away,
        datetime(2025, 12, 1, tzinfo=timezone.utc),
    )
    return FeatureSet(
        match=match,
        general=GeneralFeatures(2.0, 2.0, None, None),
        team=TeamFeatures(1520.0, 1520.0, 0.5, 0.5, None, None, None, None),
        market=MarketFeatures(None, None, None, None, False),
        player=PlayerFeatures(True, True, None, None),
        environment=EnvironmentFeatures(None, None, None, True),
        custom=custom or {},
        data_quality="real",
        quality_notes=[],
        feature_version="nhl-1.0",
    )


def test_basketball_travel_boosts_home():
    eng = BasketballEngine()
    near = eng.predict(
        _bb_features(custom={"travel_km_away": 200, "timezone_offset_hours_away": 0}),
        _bb_features().match,
    )
    far = eng.predict(
        _bb_features(custom={"travel_km_away": 4200, "timezone_offset_hours_away": 3}),
        _bb_features().match,
    )
    assert far.outcome_probabilities["home_win"] > near.outcome_probabilities["home_win"]
    travel = next(i for i in far.explanation if i.factor == "travel")
    assert travel.available is True


def test_basketball_injury_away_worse_helps_home():
    eng = BasketballEngine()
    even = eng.predict(
        _bb_features(injury_home=0.1, injury_away=0.1),
        _bb_features().match,
    )
    away_hurt = eng.predict(
        _bb_features(injury_home=0.0, injury_away=0.4),
        _bb_features().match,
    )
    assert away_hurt.outcome_probabilities["home_win"] > even.outcome_probabilities["home_win"]
    inj = next(i for i in away_hurt.explanation if i.factor == "injury")
    assert inj.available is True


def test_baseball_park_and_bullpen():
    eng = BaseballEngine()
    neutral = eng.predict(_mlb_features(custom={"park_factor": 1.0}), _mlb_features().match)
    hitter = eng.predict(_mlb_features(custom={"park_factor": 1.12}), _mlb_features().match)
    assert hitter.outcome_probabilities["home_win"] >= neutral.outcome_probabilities["home_win"]
    park = next(i for i in hitter.explanation if i.factor == "park")
    assert park.available is True

    weak_pen = eng.predict(
        _mlb_features(custom={"bullpen_era_home": 5.5, "bullpen_era_away": 3.2}),
        _mlb_features().match,
    )
    strong_pen = eng.predict(
        _mlb_features(custom={"bullpen_era_home": 3.0, "bullpen_era_away": 5.0}),
        _mlb_features().match,
    )
    assert strong_pen.outcome_probabilities["home_win"] > weak_pen.outcome_probabilities["home_win"]
    bull = next(i for i in strong_pen.explanation if i.factor == "bullpen")
    assert bull.available is True


def test_baseball_weather_factor_present():
    eng = BaseballEngine()
    r = eng.predict(
        _mlb_features(custom={"weather_temp_c": 32, "weather_wind_mph": 18}),
        _mlb_features().match,
    )
    weather = next(i for i in r.explanation if i.factor == "weather")
    assert weather.available is True


def test_hockey_travel_factor():
    eng = HockeyEngine()
    near = eng.predict(
        _nhl_features(custom={"travel_km_away": 300, "timezone_offset_hours_away": 0}),
        _nhl_features().match,
    )
    far = eng.predict(
        _nhl_features(custom={"travel_km_away": 3500, "timezone_offset_hours_away": 3}),
        _nhl_features().match,
    )
    assert far.outcome_probabilities["home_win"] > near.outcome_probabilities["home_win"]
    travel = next(i for i in far.explanation if i.factor == "travel")
    assert travel.available is True


def test_hockey_attack_share_from_corsi():
    sport = SportIdentity(code="hockey", name="Hockey")
    comp = CompetitionIdentity(code="nhl", name="NHL", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2023")
    home = TeamIdentity(code="BOS", name="Bruins", competition=comp)
    away = TeamIdentity(code="TOR", name="Leafs", competition=comp)
    match = MatchIdentity(
        "nhl-x",
        season,
        "regular_season",
        None,
        home,
        away,
        datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    weak = FeatureSet(
        match,
        GeneralFeatures(2, 2, None, None),
        TeamFeatures(1500, 1500, 0.5, 0.5, None, None, None, None),
        MarketFeatures(None, None, None, None, False),
        PlayerFeatures(None, None, None, None),
        EnvironmentFeatures(None, None, None, True),
        {
            "corsi_pct_home": 40,
            "corsi_pct_away": 60,
            "goalie_save_pct_home": 0.91,
            "goalie_save_pct_away": 0.91,
        },
        "real",
        [],
        "1",
    )
    strong = FeatureSet(
        match,
        GeneralFeatures(2, 2, None, None),
        TeamFeatures(1500, 1500, 0.5, 0.5, None, None, None, None),
        MarketFeatures(None, None, None, None, False),
        PlayerFeatures(None, None, None, None),
        EnvironmentFeatures(None, None, None, True),
        {
            "corsi_pct_home": 60,
            "corsi_pct_away": 40,
            "goalie_save_pct_home": 0.91,
            "goalie_save_pct_away": 0.91,
        },
        "real",
        [],
        "1",
    )
    eng = HockeyEngine()
    rw, rs = eng.predict(weak, match), eng.predict(strong, match)
    assert next(i for i in rs.explanation if i.factor == "attack_share").available
    assert rs.outcome_probabilities["home_win"] > rw.outcome_probabilities["home_win"]

