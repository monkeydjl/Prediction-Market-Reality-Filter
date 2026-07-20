"""Tests for P1-E8 situational soft adjustments."""
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
from app.kernel.engines.elo_odds_engine import EloOddsEngine
from app.kernel.engines.situational_adjust import (
    apply_situational_adjustment,
    extract_situational_context,
)
from app.kernel.engines.situational_engine import SituationalEngine
from app.kernel.protocols import PredictionEngine


_FOOTBALL = SportIdentity(code="football", name="Football")
_WC = CompetitionIdentity(code="wc", name="World Cup", sport=_FOOTBALL)


def _match(stage: str = "group_stage") -> MatchIdentity:
    season = SeasonIdentity(competition=_WC, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=_WC)
    away = TeamIdentity(code="GER", name="Germany", competition=_WC)
    return MatchIdentity(
        "wc-1",
        season,
        stage,
        None,
        home,
        away,
        datetime(2026, 6, 20, tzinfo=timezone.utc),
    )


def _features(
    stage: str = "group_stage",
    custom: dict | None = None,
) -> FeatureSet:
    match = _match(stage)
    return FeatureSet(
        match=match,
        general=GeneralFeatures(4.0, 3.0, None, None),
        team=TeamFeatures(1900.0, 1750.0, 0.7, 0.5, None, None, None, None),
        market=MarketFeatures(1.85, 3.40, 4.50, "test", True),
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures(None, None, None, True),
        custom=custom or {},
        data_quality="real",
        quality_notes=[],
        feature_version="1.0",
    )


def test_protocol():
    eng = SituationalEngine()
    assert isinstance(eng, PredictionEngine)
    assert eng.name() == "situational"
    assert "football" in eng.supported_sports() or "*" in eng.supported_sports()


def test_no_context_matches_base_probs():
    base = EloOddsEngine()
    eng = SituationalEngine(base)
    fs = _features(stage="group_stage", custom={})
    base_r = base.predict(fs, fs.match)
    sit_r = eng.predict(fs, fs.match)
    for k in ("home_win", "draw", "away_win"):
        assert abs(base_r.outcome_probabilities[k] - sit_r.outcome_probabilities[k]) < 1e-6
    sit_item = next(i for i in sit_r.explanation if i.factor == "situational")
    assert sit_item.available is False


def test_knockout_reduces_draw():
    eng = SituationalEngine()
    group = eng.predict(_features("group_stage"), _match("group_stage"))
    ko = eng.predict(_features("semifinal"), _match("semifinal"))
    assert ko.outcome_probabilities["draw"] < group.outcome_probabilities["draw"]
    sit = next(i for i in ko.explanation if i.factor == "situational")
    assert sit.available is True
    assert "knockout" in (sit.detail or "")


def test_must_win_home_boosts_home():
    base_probs = {"home_win": 0.40, "draw": 0.30, "away_win": 0.30}
    ctx = extract_situational_context(
        "group_stage",
        {"must_win_home": True, "must_win_away": False},
    )
    adj, applied = apply_situational_adjustment(base_probs, ctx)
    assert applied
    assert adj["home_win"] > base_probs["home_win"]
    assert abs(sum(adj.values()) - 1.0) < 0.01


def test_both_must_win_lowers_draw():
    base_probs = {"home_win": 0.40, "draw": 0.30, "away_win": 0.30}
    ctx = extract_situational_context(
        "group_stage",
        {"must_win_home": True, "must_win_away": True},
    )
    adj, applied = apply_situational_adjustment(base_probs, ctx)
    assert applied
    assert adj["draw"] < base_probs["draw"]


def test_shift_is_capped():
    base_probs = {"home_win": 0.50, "draw": 0.25, "away_win": 0.25}
    ctx = extract_situational_context(
        "final",
        {
            "must_win_home": True,
            "must_win_away": True,
            "home_group_status": "eliminated",
            "away_group_status": "eliminated",
        },
    )
    adj, applied = apply_situational_adjustment(base_probs, ctx)
    assert applied
    for k in base_probs:
        assert abs(adj[k] - base_probs[k]) <= 0.12 + 1e-6


def test_engine_records_base_in_betting_analysis():
    eng = SituationalEngine()
    fs = _features("final", {"must_win_home": True})
    r = eng.predict(fs, fs.match)
    assert r.betting_analysis is not None
    assert r.betting_analysis.get("base_engine") == "elo_odds"
    assert r.betting_analysis.get("situational_applied") is True
