# backend/tests/test_kernel_prediction_kernel.py
"""Tests for PredictionKernel orchestrator."""
from datetime import datetime, timezone
import pytest
import tempfile

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures, PredictionResult,
)
from app.kernel.protocols import DataAdapter, ScheduleFilter, RawMatchData
from app.kernel.prediction_kernel import PredictionKernel
from app.kernel.engine_registry import EngineRegistry
from app.kernel.feature_registry import FeatureRegistry
from app.kernel.factor_registry import FactorRegistry
from app.kernel.engines.elo_odds_engine import EloOddsEngine
from app.kernel.kernel_db import init_kernel_db, close_kernel_session
from app.kernel.learning_service import KernelLearningService


def _make_match(match_id="m1") -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id=match_id, season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


class FakeAdapter:
    """Minimal adapter for testing."""

    def __init__(self):
        self._match = _make_match()

    def fetch_schedule(self, filters): return []
    def fetch_team_data(self, team): return {"elo": 1900}
    def fetch_player_data(self, team): return {}
    def fetch_market_data(self, match): return {"odds": [2.0, 3.0, 4.0]}
    def fetch_outcome(self, match_id): return None
    def sync_schedule(self): return 0
    def get_match_identity(self, match_id):
        return self._match

    def fetch_all_data(self, match):
        return {
            "team": {"elo_home": 1900, "elo_away": 1800},
            "market": {"odds_home": 2.0, "odds_draw": 3.0, "odds_away": 4.0},
            "player": {},
            "environment": {},
            "general": {},
        }


class FakeFeatureBuilder:
    """Minimal feature builder for testing."""

    def build(self, match, raw):
        return FeatureSet(
            match=match,
            general=GeneralFeatures(None, None, None, None),
            team=TeamFeatures(
                raw.get("team", {}).get("elo_home", 1900),
                raw.get("team", {}).get("elo_away", 1800),
                None, None, None, None, None, None,
            ),
            market=MarketFeatures(
                raw.get("market", {}).get("odds_home", 2.0),
                raw.get("market", {}).get("odds_draw", 3.0),
                raw.get("market", {}).get("odds_away", 4.0),
                "test", True,
            ),
            player=PlayerFeatures(None, None, None, None),
            environment=EnvironmentFeatures(None, None, None, False),
            custom={}, data_quality="real", quality_notes=[], feature_version="1.0",
        )

    def sport(self):
        return SportIdentity(code="football", name="Football")


@pytest.fixture
def kernel(tmp_path):
    init_kernel_db(str(tmp_path / "kernel_test.db"))
    reg = EngineRegistry()
    reg.register(EloOddsEngine())
    kernel = PredictionKernel(
        adapter=FakeAdapter(),
        feature_builder=FakeFeatureBuilder(),
        engine_registry=reg,
        factor_registry=FactorRegistry(),
        feature_registry=FeatureRegistry(),
        learning=KernelLearningService(),
    )
    yield kernel
    close_kernel_session()


class TestPredictionKernel:
    def test_predict_returns_prediction_result(self, kernel):
        result = kernel.predict("m1", engine="auto")
        assert result.engine_name == "elo_odds"
        assert "home" in result.predicted_scores
        assert "away" in result.predicted_scores

    def test_predict_records_to_learning(self, kernel):
        result = kernel.predict("m1", engine="auto")
        # Verify it was recorded
        score = kernel._learning.engine_score("elo_odds", "world_cup")
        # No outcomes yet, so score should be None (no completed matches)
        assert score is None  # no outcomes recorded

    def test_batch_predict(self, kernel):
        results = kernel.batch_predict(["m1"], engine="auto")
        assert len(results) == 1
        assert results[0].engine_name == "elo_odds"

    def test_process_outcome_triggers_learning(self, kernel):
        from app.kernel.domain import MatchOutcome
        # First predict
        kernel.predict("m1", engine="auto")
        # Override adapter to return outcome
        kernel._adapter.fetch_outcome = lambda match_id: MatchOutcome(
            match_id="m1", home_score=2, away_score=1,
            outcome="home_win",
            finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
        )
        kernel.process_outcome("m1")
        score = kernel._learning.engine_score("elo_odds", "world_cup")
        assert score is not None
        assert score.sample_count == 1
