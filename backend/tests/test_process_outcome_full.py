# backend/tests/test_process_outcome_full.py
"""Tests for process_outcome full loop (Phase 3)."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome, PredictionResult,
    FeatureSet, GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures, PredictionError, ContributionItem,
)
from app.kernel.kernel_db import init_kernel_db, close_kernel_session
from app.kernel.learning_service import KernelLearningService
from app.kernel.factor_registry import FactorRegistry
from app.kernel.prediction_kernel import PredictionKernel
from app.kernel.engine_registry import EngineRegistry
from app.kernel.engines.elo_odds_engine import EloOddsEngine


def _make_match(match_id="m1", competition="world_cup") -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code=competition, name="Test", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id=match_id, season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


def _make_outcome(match_id="m1") -> MatchOutcome:
    return MatchOutcome(
        match_id=match_id, home_score=2, away_score=1,
        outcome="home_win",
        finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
    )


def _make_prediction() -> PredictionResult:
    return PredictionResult(
        predicted_scores={"home": 2.0, "away": 1.0},
        outcome_probabilities={"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
        confidence=0.72, engine_name="elo_odds",
        explanation=[
            ContributionItem(factor="elo", direction="support", weight=0.30,
                             available=True, detail="Elo", predicted_outcome="home_win"),
            ContributionItem(factor="odds", direction="support", weight=0.70,
                             available=True, detail="Odds", predicted_outcome="home_win"),
        ],
        betting_analysis=None, feature_version="1.0",
        prediction_timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )


@pytest.fixture
def kernel_setup(tmp_path):
    """Set up a full kernel with temp DB."""
    db_path = str(tmp_path / "kernel_test.db")
    init_kernel_db(db_path)

    factor_reg = FactorRegistry()
    learning = KernelLearningService(factor_registry=factor_reg)
    engine = EloOddsEngine(factor_registry=factor_reg)
    reg = EngineRegistry(learning_service=learning)
    reg.register(engine)

    # Mock adapter
    adapter = MagicMock()
    adapter.get_match_identity.return_value = _make_match()
    adapter.fetch_outcome.return_value = _make_outcome()

    # Mock feature builder
    feature_builder = MagicMock()
    feature_builder.build.return_value = FeatureSet(
        match=_make_match(),
        general=GeneralFeatures(None, None, None, None),
        team=TeamFeatures(1900, 1800, None, None, None, None, None, None),
        market=MarketFeatures(2.0, 3.0, 4.0, "test", True),
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures(None, None, None, False),
        custom={}, data_quality="real", quality_notes=[], feature_version="1.0",
    )

    from app.kernel.feature_registry import FeatureRegistry
    kernel = PredictionKernel(
        adapter=adapter, feature_builder=feature_builder,
        engine_registry=reg, factor_registry=factor_reg,
        feature_registry=FeatureRegistry(), learning=learning,
    )
    yield kernel, adapter, learning
    close_kernel_session()


class TestProcessOutcomeFull:
    def test_outcome_none_skips(self, kernel_setup):
        """When fetch_outcome returns None, process_outcome does nothing."""
        kernel, adapter, learning = kernel_setup
        adapter.fetch_outcome.return_value = None
        kernel.process_outcome("m1")
        # record_outcome should not have been called
        # (verify by checking no outcome in DB)
        error = learning.compute_error("m1")
        assert error is None

    @patch("app.kernel.prediction_kernel.config")
    def test_phase3_off_only_records_and_computes(self, mock_config, kernel_setup):
        """When PHASE3_LEARNING_ENABLED=false, only record_outcome + compute_error run."""
        mock_config.settings.PHASE3_LEARNING_ENABLED = False
        kernel, adapter, learning = kernel_setup

        # Seed a prediction first
        learning.record_prediction(_make_match("m1"), _make_prediction())

        kernel.process_outcome("m1")

        # compute_error should have run (outcome recorded + error computed)
        error = learning.compute_error("m1")
        assert error is not None

    @patch("app.kernel.prediction_kernel.config")
    def test_phase3_on_runs_full_loop(self, mock_config, kernel_setup):
        """When PHASE3_LEARNING_ENABLED=true, all 5 steps run."""
        mock_config.settings.PHASE3_LEARNING_ENABLED = True
        kernel, adapter, learning = kernel_setup

        # Seed a prediction first
        learning.record_prediction(_make_match("m1"), _make_prediction())

        # Patch learning methods to track calls
        with patch.object(learning, "update_calibration") as mock_cal, \
             patch.object(learning, "update_weights") as mock_weights, \
             patch.object(learning, "engine_score") as mock_score:
            kernel.process_outcome("m1")
            mock_cal.assert_called_once_with("world_cup", "elo_odds")
            mock_weights.assert_called_once_with("world_cup")
            mock_score.assert_called_once_with("elo_odds", "world_cup")

    @patch("app.kernel.prediction_kernel.config")
    def test_phase3_on_no_prediction_skips_learning(self, mock_config, kernel_setup):
        """When no prediction exists, compute_error returns None and learning skips."""
        mock_config.settings.PHASE3_LEARNING_ENABLED = True
        kernel, adapter, learning = kernel_setup
        # No prediction seeded → compute_error returns None
        kernel.process_outcome("m1")
        # Learning methods should not crash

    @patch("app.kernel.prediction_kernel.config")
    def test_phase3_on_calls_engine_score_last(self, mock_config, kernel_setup):
        """engine_score is called after calibration and weights."""
        mock_config.settings.PHASE3_LEARNING_ENABLED = True
        kernel, adapter, learning = kernel_setup

        learning.record_prediction(_make_match("m1"), _make_prediction())

        call_order = []
        with patch.object(learning, "update_calibration",
                          side_effect=lambda *a: call_order.append("cal")), \
             patch.object(learning, "update_weights",
                          side_effect=lambda *a: call_order.append("weights")), \
             patch.object(learning, "engine_score",
                          side_effect=lambda *a: call_order.append("score")):
            kernel.process_outcome("m1")
            assert call_order == ["cal", "weights", "score"]
