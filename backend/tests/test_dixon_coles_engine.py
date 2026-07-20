"""Tests for Kernel DixonColesEngine."""
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
from app.kernel.engines.dixon_coles_engine import (
    DixonColesEngine,
    dixon_coles_probabilities,
    elo_to_xg,
)
from app.kernel.protocols import PredictionEngine


_FOOTBALL = SportIdentity(code="football", name="Football")
_WC = CompetitionIdentity(code="wc", name="World Cup", sport=_FOOTBALL)


def _features(elo_h=1800.0, elo_a=1600.0, form_h=0.6, form_a=0.4) -> FeatureSet:
    season = SeasonIdentity(competition=_WC, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=_WC)
    away = TeamIdentity(code="ARG", name="Argentina", competition=_WC)
    match = MatchIdentity(
        match_id="wc-1",
        season=season,
        stage="group",
        round=None,
        home=home,
        away=away,
        kickoff_utc=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )
    return FeatureSet(
        match=match,
        general=GeneralFeatures(3.0, 3.0, None, None),
        team=TeamFeatures(elo_h, elo_a, form_h, form_a, None, None, None, None),
        market=MarketFeatures(None, None, None, None, False),
        player=PlayerFeatures(None, None, 0.1, 0.2),
        environment=EnvironmentFeatures("Neutral", None, None, False),
        custom={},
        data_quality="real",
        quality_notes=[],
        feature_version="football-1.0",
    )


def test_protocol():
    assert isinstance(DixonColesEngine(), PredictionEngine)
    assert DixonColesEngine().name() == "dixon_coles"


def test_elo_to_xg_stronger_home_higher_xg():
    h, a = elo_to_xg(1900, 1500)
    assert h > a


def test_dc_probs_sum_to_one():
    probs = dixon_coles_probabilities(1.5, 1.1, rho=-0.1)
    assert abs(sum(probs.values()) - 1.0) < 0.01


def test_negative_rho_increases_draw_vs_zero():
    base = dixon_coles_probabilities(1.2, 1.2, rho=0.0)
    boosted = dixon_coles_probabilities(1.2, 1.2, rho=-0.15)
    assert boosted["draw"] >= base["draw"]


def test_predict_stronger_home():
    engine = DixonColesEngine()
    result = engine.predict(_features(1900, 1500), _features(1900, 1500).match)
    assert result.outcome_probabilities["home_win"] > result.outcome_probabilities["away_win"]
    assert abs(sum(result.outcome_probabilities.values()) - 1.0) < 0.01
    assert result.engine_name == "dixon_coles"
    assert "home" in result.predicted_scores


def test_missing_elo_neutral():
    engine = DixonColesEngine()
    fs = _features()
    # rebuild with None elo via object replace is hard (frozen); construct manually
    season = fs.match.season
    match = fs.match
    fs2 = FeatureSet(
        match=match,
        general=fs.general,
        team=TeamFeatures(None, None, None, None, None, None, None, None),
        market=fs.market,
        player=fs.player,
        environment=fs.environment,
        custom={},
        data_quality="proxy",
        quality_notes=["no elo"],
        feature_version="football-1.0",
    )
    result = engine.predict(fs2, match)
    assert abs(sum(result.outcome_probabilities.values()) - 1.0) < 0.01
