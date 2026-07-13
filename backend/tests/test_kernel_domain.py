"""Tests for kernel domain value objects."""
from datetime import datetime, timezone
from dataclasses import FrozenInstanceError
import pytest

from app.kernel.domain import (
    SportIdentity,
    CompetitionIdentity,
    SeasonIdentity,
    TeamIdentity,
    MatchIdentity,
    MatchOutcome,
    GeneralFeatures,
    TeamFeatures,
    MarketFeatures,
    PlayerFeatures,
    EnvironmentFeatures,
    FeatureSet,
    ContributionItem,
    PredictionResult,
    PredictionError,
    EngineScore,
)


class TestSportIdentity:
    def test_creation(self):
        s = SportIdentity(code="football", name="Football")
        assert s.code == "football"
        assert s.name == "Football"

    def test_frozen(self):
        s = SportIdentity(code="football", name="Football")
        with pytest.raises(FrozenInstanceError):
            s.code = "basketball"

    def test_equality(self):
        a = SportIdentity(code="football", name="Football")
        b = SportIdentity(code="football", name="Football")
        assert a == b

    def test_hashable(self):
        s = SportIdentity(code="football", name="Football")
        assert hash(s) == hash(SportIdentity(code="football", name="Football"))


class TestCompetitionIdentity:
    def test_creation(self):
        sport = SportIdentity(code="football", name="Football")
        c = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
        assert c.code == "world_cup"
        assert c.sport == sport

    def test_frozen(self):
        sport = SportIdentity(code="football", name="Football")
        c = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
        with pytest.raises(FrozenInstanceError):
            c.code = "epl"


class TestMatchIdentity:
    def test_creation(self):
        sport = SportIdentity(code="football", name="Football")
        comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
        season = SeasonIdentity(competition=comp, season_key="2026")
        home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
        away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
        match = MatchIdentity(
            match_id="wc_2026_bra_arg",
            season=season,
            stage="group_stage",
            round=None,
            home=home,
            away=away,
            kickoff_utc=datetime(2026, 6, 13, 18, 0, tzinfo=timezone.utc),
        )
        assert match.match_id == "wc_2026_bra_arg"
        assert match.home.code == "BRA"
        assert match.away.name == "Argentina"

    def test_frozen(self):
        sport = SportIdentity(code="football", name="Football")
        comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
        season = SeasonIdentity(competition=comp, season_key="2026")
        home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
        away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
        match = MatchIdentity(
            match_id="wc_2026_bra_arg",
            season=season,
            stage="group_stage",
            round=None,
            home=home,
            away=away,
            kickoff_utc=datetime(2026, 6, 13, 18, 0, tzinfo=timezone.utc),
        )
        with pytest.raises(FrozenInstanceError):
            match.match_id = "changed"


class TestMatchOutcome:
    def test_creation(self):
        o = MatchOutcome(
            match_id="wc_2026_bra_arg",
            home_score=2,
            away_score=1,
            outcome="home_win",
            finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
        )
        assert o.home_score == 2
        assert o.outcome == "home_win"


class TestFeatureSet:
    def test_creation_with_all_layers(self):
        sport = SportIdentity(code="football", name="Football")
        comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
        season = SeasonIdentity(competition=comp, season_key="2026")
        home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
        away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
        match = MatchIdentity(
            match_id="wc_2026_bra_arg",
            season=season,
            stage="group_stage",
            round=None,
            home=home,
            away=away,
            kickoff_utc=datetime(2026, 6, 13, 18, 0, tzinfo=timezone.utc),
        )
        fs = FeatureSet(
            match=match,
            general=GeneralFeatures(
                rest_days_home=5.0, rest_days_away=4.0,
                travel_distance_km=None, days_since_last_match=5.0,
            ),
            team=TeamFeatures(
                elo_rating_home=1920.0, elo_rating_away=1890.0,
                form_home=0.7, form_away=0.6,
                h2h_home_win_rate=0.55, h2h_draw_rate=0.25,
                market_value_home=None, market_value_away=None,
            ),
            market=MarketFeatures(
                odds_home=2.10, odds_draw=3.30, odds_away=3.50,
                odds_source="the_odds_api", odds_fresh=True,
            ),
            player=PlayerFeatures(
                key_players_available_home=0.9, key_players_available_away=1.0,
                injury_impact_home=0.1, injury_impact_away=0.0,
            ),
            environment=EnvironmentFeatures(
                venue="Maracana", weather_temp_c=25.0,
                weather_condition="clear", is_home_advantage=False,
            ),
            custom={"xg_home": 1.8, "xg_away": 1.2},
            data_quality="real",
            quality_notes=[],
            feature_version="1.0",
        )
        assert fs.team.elo_rating_home == 1920.0
        assert fs.market.odds_home == 2.10
        assert fs.custom["xg_home"] == 1.8
        assert fs.data_quality == "real"

    def test_frozen(self):
        sport = SportIdentity(code="football", name="Football")
        comp = CompetitionIdentity(code="wc", name="WC", sport=sport)
        season = SeasonIdentity(competition=comp, season_key="2026")
        home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
        away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
        match = MatchIdentity(
            match_id="m1", season=season, stage="group", round=None,
            home=home, away=away,
            kickoff_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        fs = FeatureSet(
            match=match,
            general=GeneralFeatures(None, None, None, None),
            team=TeamFeatures(None, None, None, None, None, None, None, None),
            market=MarketFeatures(None, None, None, None, False),
            player=PlayerFeatures(None, None, None, None),
            environment=EnvironmentFeatures(None, None, None, False),
            custom={},
            data_quality="partial",
            quality_notes=["no odds"],
            feature_version="1.0",
        )
        with pytest.raises(FrozenInstanceError):
            fs.data_quality = "real"


class TestPredictionResult:
    def test_creation(self):
        p = PredictionResult(
            predicted_scores={"home": 2.1, "away": 1.3},
            outcome_probabilities={"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
            confidence=0.72,
            engine_name="elo_odds",
            explanation=[
                ContributionItem(
                    factor="elo", direction="support", weight=0.35,
                    available=True, detail="Home team stronger by 120 Elo",
                ),
            ],
            betting_analysis=None,
            feature_version="1.0",
            prediction_timestamp=datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc),
        )
        assert p.engine_name == "elo_odds"
        assert p.explanation[0].factor == "elo"


class TestPredictionError:
    def test_creation(self):
        e = PredictionError(
            match_id="m1", engine="elo_odds",
            score_mae=0.5, outcome_correct=True,
            brier_score=0.18, confidence_calibrated=True,
        )
        assert e.score_mae == 0.5
        assert e.outcome_correct is True


class TestEngineScore:
    def test_creation(self):
        s = EngineScore(
            engine="elo_odds", competition="world_cup",
            accuracy=0.72, avg_mae=0.89, brier_score=0.19,
            sample_count=64, confidence_calibration=0.85,
            last_updated=datetime(2026, 7, 11, tzinfo=timezone.utc),
        )
        assert s.accuracy == 0.72
        assert s.sample_count == 64
