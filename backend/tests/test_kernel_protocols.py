# backend/tests/test_kernel_protocols.py
"""Tests for kernel Protocol interfaces."""
import pytest
from datetime import datetime, timezone

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures, PredictionResult,
)
from app.kernel.protocols import (
    DataAdapter, FeatureBuilder, PredictionEngine, LearningService,
    ScheduleFilter, RawMatchData,
)


def _make_match() -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id="m1", season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


class TestScheduleFilter:
    def test_creation(self):
        f = ScheduleFilter(competition="world_cup", season="2026", status="scheduled")
        assert f.competition == "world_cup"

    def test_defaults(self):
        f = ScheduleFilter()
        assert f.competition is None
        assert f.status is None


class TestRawMatchData:
    def test_creation(self):
        m = _make_match()
        raw = RawMatchData(match=m, raw_json={"elo": 1900})
        assert raw.match.match_id == "m1"
        assert raw.raw_json["elo"] == 1900


class TestProtocolCompliance:
    """Verify that concrete classes can satisfy Protocol interfaces."""

    def test_data_adapter_protocol(self):
        class FakeAdapter:
            def fetch_schedule(self, filters):
                return []
            def fetch_team_data(self, team):
                return {}
            def fetch_player_data(self, team):
                return {}
            def fetch_market_data(self, match):
                return {}
            def fetch_outcome(self, match_id):
                return None
            def sync_schedule(self):
                return 0
            def get_match_identity(self, match_id):
                return _make_match()
            def fetch_all_data(self, match):
                return {}

        adapter = FakeAdapter()
        assert isinstance(adapter, DataAdapter)

    def test_feature_builder_protocol(self):
        class FakeBuilder:
            def build(self, match, raw):
                pass
            def sport(self):
                return SportIdentity(code="football", name="Football")

        builder = FakeBuilder()
        assert isinstance(builder, FeatureBuilder)

    def test_prediction_engine_protocol(self):
        class FakeEngine:
            def predict(self, features, match):
                pass
            def name(self):
                return "fake"
            def supported_sports(self):
                return ["*"]

        engine = FakeEngine()
        assert isinstance(engine, PredictionEngine)

    def test_learning_service_protocol(self):
        class FakeLearning:
            def record_prediction(self, match, prediction):
                pass
            def record_outcome(self, outcome):
                pass
            def compute_error(self, match_id):
                return None
            def update_calibration(self, competition, engine):
                return None
            def update_weights(self, competition):
                # The Protocol declares dict[str, Any]. runtime_checkable only
                # checks that the attribute exists, so returning None here would
                # leave the fake green while violating the contract it claims to
                # satisfy -- and a fake is where a caller's expectations get
                # written down.
                return {"updated": False, "reason": "fake"}
            def engine_score(self, engine, competition=None):
                return None

        learning = FakeLearning()
        assert isinstance(learning, LearningService)
        # isinstance passes on attribute presence alone, so assert the shape the
        # Protocol promises its callers.
        assert learning.update_weights("nba")["updated"] is False
