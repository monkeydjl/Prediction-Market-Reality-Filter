"""P1-O1 soft totals/BTTS + P1-M4 platoon smoke."""
from datetime import datetime, timezone

from app.kernel.engines.elo_odds_engine import soft_totals_btts_analysis
from app.kernel.domain import *
from app.sports.football.engines.football_multi_factor_engine import FootballMultiFactorEngine
from app.sports.baseball.engines.baseball_engine import BaseballEngine, _DEFAULT_WEIGHTS


def test_soft_totals_reasonable():
    out = soft_totals_btts_analysis({"home": 1.6, "away": 1.2}, line=2.5)
    assert out["available"] is True
    assert 0.2 < out["p_over"] < 0.9
    assert abs(out["p_over"] + out["p_under"] - 1.0) < 0.05
    assert 0.2 < out["p_btts_yes"] < 0.9
    assert out["expected_total"] == 2.8


def test_multifactor_includes_soft_totals():
    sport = SportIdentity("football", "F")
    comp = CompetitionIdentity("epl", "EPL", sport)
    season = SeasonIdentity(comp, "25")
    m = MatchIdentity(
        "e1", season, "rs", None,
        TeamIdentity("H", "H", comp), TeamIdentity("A", "A", comp),
        datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    fs = FeatureSet(
        m,
        GeneralFeatures(5, 2, None, None),
        TeamFeatures(1800, 1600, 0.7, 0.4, 0.45, 0.28, None, None),
        MarketFeatures(1.8, 3.5, 4.5, "t", True),
        PlayerFeatures(None, None, 0.1, 0.4),
        EnvironmentFeatures(None, None, None, True),
        {},
        "real",
        [],
        "1",
    )
    r = FootballMultiFactorEngine().predict(fs, m)
    assert r.betting_analysis
    soft = r.betting_analysis.get("soft_totals_btts")
    assert soft and soft["available"] is True


def test_platoon_factor_weights_sum():
    assert abs(sum(_DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9
    assert "platoon" in _DEFAULT_WEIGHTS


def test_platoon_moves_home_win():
    sport = SportIdentity("baseball", "B")
    comp = CompetitionIdentity("mlb", "MLB", sport)
    season = SeasonIdentity(comp, "2024")
    m = MatchIdentity(
        "g1", season, "regular_season", None,
        TeamIdentity("NYY", "NYY", comp), TeamIdentity("BOS", "BOS", comp),
        datetime(2024, 6, 1, tzinfo=timezone.utc),
    )
    def mk(custom):
        return FeatureSet(
            m,
            GeneralFeatures(1, 1, None, None),
            TeamFeatures(1550, 1550, 0.5, 0.5, None, None, None, None),
            MarketFeatures(None, None, None, None, False),
            PlayerFeatures(None, None, None, None),
            EnvironmentFeatures(None, None, None, True),
            custom,
            "real",
            [],
            "1",
        )
    engine = BaseballEngine()
    low = engine.predict(mk({"platoon_ops_home": 0.650, "platoon_ops_away": 0.780}), m)
    high = engine.predict(mk({"platoon_ops_home": 0.820, "platoon_ops_away": 0.650}), m)
    assert next(i for i in high.explanation if i.factor == "platoon").available
    assert high.outcome_probabilities["home_win"] > low.outcome_probabilities["home_win"]


def test_basketball_soft_totals():
    from app.sports.basketball.engines.basketball_engine import BasketballEngine
    from tests.test_basketball_engine import _make_features

    fs = _make_features(elo_home=1600, elo_away=1550)
    r = BasketballEngine().predict(fs, fs.match)
    soft = r.betting_analysis.get("soft_totals_btts")
    assert soft and soft["available"]
    assert soft.get("sport") == "basketball"
    assert "p_btts_yes" not in soft


def test_guardrails_demote_stale():
    from app.kernel.sport_recommendation_service import _apply_sport_guardrails

    d, a, flags, notes = _apply_sport_guardrails(
        "act", 1.5,
        stale=True, trust=0.8, liquidity_factor=0.9,
        risk_level="low", review_priority="normal", calibrated=True,
    )
    assert d == "watch"
    assert a == 0.0
    assert "stale_market" in flags


def test_altitude_factor_high_venue():
    from app.sports.football.engines.football_multi_factor_engine import FootballMultiFactorEngine
    from tests.test_football_multi_factor_engine import _make_features

    engine = FootballMultiFactorEngine()
    low = _make_features(custom={})
    high = _make_features(custom={"venue_altitude_m": 3600})
    r0 = engine.predict(low, low.match)
    r1 = engine.predict(high, high.match)
    assert next(i for i in r1.explanation if i.factor == "altitude").available
    # equal other factors: high altitude should not lower home_win vs no altitude
    # (soft home edge)
    assert r1.outcome_probabilities["home_win"] >= r0.outcome_probabilities["home_win"] - 1e-9
