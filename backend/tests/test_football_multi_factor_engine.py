"""Tests for FootballMultiFactorEngine."""
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
from app.kernel.factor_registry import FactorRegistry
from app.kernel.kernel_db import close_kernel_session, init_kernel_db
from app.kernel.protocols import PredictionEngine
from app.sports.football.engines.football_multi_factor_engine import (
    FootballMultiFactorEngine,
    _DEFAULT_WEIGHTS,
)


_FOOTBALL = SportIdentity(code="football", name="Football")
_EPL = CompetitionIdentity(code="epl", name="Premier League", sport=_FOOTBALL)


def _make_features(
    *,
    elo_home: float | None = 1800.0,
    elo_away: float | None = 1600.0,
    form_home: float | None = 0.7,
    form_away: float | None = 0.4,
    rest_home: float | None = 5.0,
    rest_away: float | None = 2.0,
    odds_home: float | None = 1.80,
    odds_draw: float | None = 3.50,
    odds_away: float | None = 4.50,
    injury_home: float | None = 0.1,
    injury_away: float | None = 0.4,
    h2h_home: float | None = 0.45,
    h2h_draw: float | None = 0.28,
    odds_fresh: bool = True,
    custom: dict | None = None,
    competition_code: str = "epl",
) -> FeatureSet:
    football = SportIdentity(code="football", name="Football")
    competition = CompetitionIdentity(
        code=competition_code,
        name=competition_code.upper(),
        sport=football,
    )
    season = SeasonIdentity(competition=competition, season_key="2025-26")
    home = TeamIdentity(code="ARS", name="Arsenal", competition=competition)
    away = TeamIdentity(code="CHE", name="Chelsea", competition=competition)
    match = MatchIdentity(
        match_id=f"{competition_code}-1",
        season=season,
        stage="regular_season",
        round=None,
        home=home,
        away=away,
        kickoff_utc=datetime(2025, 12, 1, tzinfo=timezone.utc),
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
            form_home=form_home,
            form_away=form_away,
            h2h_home_win_rate=h2h_home,
            h2h_draw_rate=h2h_draw,
            market_value_home=None,
            market_value_away=None,
        ),
        market=MarketFeatures(
            odds_home, odds_draw, odds_away, "test", True,
        ),
        player=PlayerFeatures(None, None, injury_home, injury_away),
        environment=EnvironmentFeatures("Emirates", None, None, True),
        custom=custom or {},
        data_quality="real",
        quality_notes=[],
        feature_version="football-1.0",
    )


class TestFootballMultiFactorProtocol:
    def test_implements_protocol(self):
        assert isinstance(FootballMultiFactorEngine(), PredictionEngine)

    def test_name(self):
        assert FootballMultiFactorEngine().name() == "football_multi_factor"

    def test_supported_sports(self):
        assert "football" in FootballMultiFactorEngine().supported_sports()

    def test_default_weights_sum_to_one(self):
        assert abs(sum(_DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9


class TestFootballMultiFactorPredict:
    def test_three_way_probabilities(self):
        engine = FootballMultiFactorEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        probs = result.outcome_probabilities
        assert set(probs) == {"home_win", "draw", "away_win"}
        assert abs(sum(probs.values()) - 1.0) < 0.01

    def test_stronger_home_favored(self):
        engine = FootballMultiFactorEngine()
        features = _make_features(elo_home=1900, elo_away=1500)
        result = engine.predict(features, features.match)
        assert (
            result.outcome_probabilities["home_win"]
            > result.outcome_probabilities["away_win"]
        )

    def test_explanation_has_core_and_soft_factors(self):
        engine = FootballMultiFactorEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        ids = {item.factor for item in result.explanation}
        assert ids == {
            "elo", "odds", "form", "rest", "injury", "h2h",
            "travel", "xg", "market_value", "possession", "referee",
        }
        core = {"elo", "odds", "form", "rest", "injury", "h2h"}
        assert all(
            item.available for item in result.explanation if item.factor in core
        )
        soft = {item.factor for item in result.explanation if not item.available}
        assert soft <= {"travel", "xg", "market_value", "possession", "referee"}

    def test_missing_form_redistributes(self):
        engine = FootballMultiFactorEngine()
        features = _make_features(form_home=None, form_away=None)
        result = engine.predict(features, features.match)
        form_item = next(i for i in result.explanation if i.factor == "form")
        assert form_item.available is False
        assert abs(sum(result.outcome_probabilities.values()) - 1.0) < 0.01

    def test_odds_only_still_valid(self):
        engine = FootballMultiFactorEngine()
        features = _make_features(
            elo_home=None,
            elo_away=None,
            form_home=None,
            form_away=None,
            rest_home=None,
            rest_away=None,
            injury_home=None,
            injury_away=None,
            h2h_home=None,
            h2h_draw=None,
        )
        result = engine.predict(features, features.match)
        assert result.engine_name == "football_multi_factor"
        assert abs(sum(result.outcome_probabilities.values()) - 1.0) < 0.01
        available = [i for i in result.explanation if i.available]
        assert len(available) == 1
        assert available[0].factor == "odds"

    def test_xg_soft_factor_favors_higher_attack(self):
        engine = FootballMultiFactorEngine()
        weak = _make_features(custom={"xg_home": 0.8, "xg_away": 1.6})
        strong = _make_features(custom={"xg_home": 2.0, "xg_away": 0.6})
        r_weak = engine.predict(weak, weak.match)
        r_strong = engine.predict(strong, strong.match)
        xg_weak = next(i for i in r_weak.explanation if i.factor == "xg")
        xg_strong = next(i for i in r_strong.explanation if i.factor == "xg")
        assert xg_weak.available and xg_strong.available
        assert (
            r_strong.outcome_probabilities["home_win"]
            > r_weak.outcome_probabilities["home_win"]
        )

    def test_market_value_soft_factor(self):
        engine = FootballMultiFactorEngine()
        features = _make_features()
        # Rebuild with market values on team layer
        features = FeatureSet(
            match=features.match,
            general=features.general,
            team=TeamFeatures(
                features.team.elo_rating_home,
                features.team.elo_rating_away,
                features.team.form_home,
                features.team.form_away,
                features.team.h2h_home_win_rate,
                features.team.h2h_draw_rate,
                market_value_home=500_000_000.0,
                market_value_away=100_000_000.0,
            ),
            market=features.market,
            player=features.player,
            environment=features.environment,
            custom={},
            data_quality=features.data_quality,
            quality_notes=features.quality_notes,
            feature_version=features.feature_version,
        )
        result = engine.predict(features, features.match)
        mv = next(i for i in result.explanation if i.factor == "market_value")
        assert mv.available is True
        assert mv.predicted_outcome == "home_win"


    def test_referee_soft_factor(self):
        engine = FootballMultiFactorEngine()
        low = _make_features(custom={"referee_home_win_rate": 0.30})
        high = _make_features(custom={"referee_home_win_rate": 0.70})
        r_low = engine.predict(low, low.match)
        r_high = engine.predict(high, high.match)
        assert next(i for i in r_high.explanation if i.factor == "referee").available
        assert (
            r_high.outcome_probabilities["home_win"]
            > r_low.outcome_probabilities["home_win"]
        )

    def test_possession_soft_factor(self):

        engine = FootballMultiFactorEngine()
        weak = _make_features(custom={"possession_home": 35, "possession_away": 65})
        strong = _make_features(custom={"possession_home": 65, "possession_away": 35})
        r_weak = engine.predict(weak, weak.match)
        r_strong = engine.predict(strong, strong.match)
        assert next(i for i in r_strong.explanation if i.factor == "possession").available
        assert (
            r_strong.outcome_probabilities["home_win"]
            > r_weak.outcome_probabilities["home_win"]
        )

    def test_engine_name_and_scores(self):
        engine = FootballMultiFactorEngine()
        features = _make_features()
        result = engine.predict(features, features.match)
        assert result.engine_name == "football_multi_factor"
        assert "home" in result.predicted_scores
        assert "away" in result.predicted_scores
        assert 0.0 < result.confidence <= 0.95



    def test_rest_congestion_penalty(self):
        """Home on short rest should lower home_win vs rested home."""
        engine = FootballMultiFactorEngine()
        rested = _make_features(rest_home=6, rest_away=6)
        congest = _make_features(rest_home=1, rest_away=6)
        r0 = engine.predict(rested, rested.match)
        r1 = engine.predict(congest, congest.match)
        assert (
            r1.outcome_probabilities["home_win"]
            < r0.outcome_probabilities["home_win"]
        )
        rest_item = next(i for i in r1.explanation if i.factor == "rest")
        assert rest_item.available

    def test_custom_schedule_congested_with_long_rest(self):
        """True density flag should penalize even when rest_days are equal and long."""
        engine = FootballMultiFactorEngine()
        base = _make_features(rest_home=5, rest_away=5, custom={})
        congest = _make_features(
            rest_home=5,
            rest_away=5,
            custom={"schedule_congested_home": True, "schedule_congested_away": False},
        )
        r0 = engine.predict(base, base.match)
        r1 = engine.predict(congest, congest.match)
        assert (
            r1.outcome_probabilities["home_win"]
            < r0.outcome_probabilities["home_win"]
        )

    def test_injury_custom_fallback(self):
        engine = FootballMultiFactorEngine()
        # player injury empty; custom carries impact
        base = _make_features()
        from dataclasses import replace
        from app.kernel.domain import PlayerFeatures, FeatureSet
        empty_player = PlayerFeatures(None, None, None, None)
        low = FeatureSet(
            match=base.match, general=base.general, team=base.team,
            market=base.market, player=empty_player, environment=base.environment,
            custom={"injury_impact_home": 0.05, "injury_impact_away": 0.40},
            data_quality=base.data_quality, quality_notes=base.quality_notes,
            feature_version=base.feature_version,
        )
        high_home_hurt = FeatureSet(
            match=base.match, general=base.general, team=base.team,
            market=base.market, player=empty_player, environment=base.environment,
            custom={"injury_impact_home": 0.50, "injury_impact_away": 0.05},
            data_quality=base.data_quality, quality_notes=base.quality_notes,
            feature_version=base.feature_version,
        )
        r_low = engine.predict(low, low.match)
        r_hi = engine.predict(high_home_hurt, high_home_hurt.match)
        assert next(i for i in r_low.explanation if i.factor == "injury").available
        assert (
            r_low.outcome_probabilities["home_win"]
            > r_hi.outcome_probabilities["home_win"]
        )


class TestFootballMultiFactorWithRegistry:
    def test_competition_seed_does_not_change_global_elo_odds(
        self, tmp_path,
    ):
        close_kernel_session()
        init_kernel_db(str(tmp_path / "kernel.db"))
        try:
            reg = FactorRegistry()
            reg.ensure_competition_factors("epl")
            assert reg.get_weight("elo", "world_cup") == 0.30
            assert reg.get_weight("odds", "world_cup") == 0.70
            assert reg.get_competition_weight("elo", "epl") is None
            assert reg.get_competition_weight("form", "epl") == 0.09
            assert reg.get_competition_weight("injury", "epl") == 0.05
            assert reg.get_competition_weight("xg", "epl") == 0.06
            assert reg.get_competition_weight("possession", "epl") == 0.04
            assert reg.get_competition_weight("referee", "epl") == 0.02
        finally:
            close_kernel_session()

    def test_competition_weight_override(self, tmp_path):
        close_kernel_session()
        init_kernel_db(str(tmp_path / "kernel.db"))
        try:
            reg = FactorRegistry()
            reg.ensure_competition_factors("epl")
            reg.update_weight("form", "epl", 0.20, "test")
            engine = FootballMultiFactorEngine(factor_registry=reg)
            features = _make_features()
            result = engine.predict(features, features.match)
            form_item = next(i for i in result.explanation if i.factor == "form")
            assert form_item.weight == 0.20
        finally:
            close_kernel_session()


class TestFootballMultiFactorOddsQuality:
    def test_stale_odds_lower_odds_weight(self):
        engine = FootballMultiFactorEngine()
        fresh = engine.predict(_make_features(odds_fresh=True), _make_features().match)
        stale_features = _make_features(odds_fresh=False)
        stale = engine.predict(stale_features, stale_features.match)
        w_fresh = next(i.weight for i in fresh.explanation if i.factor == "odds")
        w_stale = next(i.weight for i in stale.explanation if i.factor == "odds")
        assert w_stale < w_fresh
        odds_item = next(i for i in stale.explanation if i.factor == "odds")
        assert "stale" in (odds_item.detail or "")

    def test_low_liquidity_lowers_odds_weight(self):
        engine = FootballMultiFactorEngine()
        deep = _make_features(custom={"liquidity_factor": 1.0})
        thin = _make_features(custom={"liquidity_factor": 0.0})
        w_deep = next(
            i.weight for i in engine.predict(deep, deep.match).explanation if i.factor == "odds"
        )
        w_thin = next(
            i.weight for i in engine.predict(thin, thin.match).explanation if i.factor == "odds"
        )
        assert w_thin < w_deep


class TestFootballMultiFactorCompetitionProfiles:
    def test_ucl_profile_uses_higher_elo_than_epl_default(self):
        engine = FootballMultiFactorEngine()
        epl = _make_features(competition_code="epl")
        ucl = _make_features(competition_code="ucl")
        w_epl_elo = next(
            i.weight for i in engine.predict(epl, epl.match).explanation if i.factor == "elo"
        )
        w_ucl_elo = next(
            i.weight for i in engine.predict(ucl, ucl.match).explanation if i.factor == "elo"
        )
        # Profiles: epl elo 0.22, ucl elo 0.27 (before any odds mult — elo unchanged)
        assert w_ucl_elo > w_epl_elo
