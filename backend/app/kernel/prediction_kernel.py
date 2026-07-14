# backend/app/kernel/prediction_kernel.py
"""Prediction Kernel — the core orchestrator.

Connects: Adapter -> FeatureBuilder -> Engine -> Learning
The Kernel has zero knowledge of any specific sport or competition.
"""
from __future__ import annotations

import logging

from app.core import config
from app.kernel.domain import (
    MatchIdentity, FeatureSet, PredictionResult, MatchOutcome,
)
from app.kernel.protocols import DataAdapter, FeatureBuilder
from app.kernel.engine_registry import EngineRegistry
from app.kernel.feature_registry import FeatureRegistry
from app.kernel.factor_registry import FactorRegistry
from app.kernel.learning_service import KernelLearningService

logger = logging.getLogger(__name__)


class PredictionKernel:
    """Core orchestrator connecting all Kernel components."""

    def __init__(
        self,
        adapter: DataAdapter,
        feature_builder: FeatureBuilder,
        engine_registry: EngineRegistry,
        factor_registry: FactorRegistry,
        feature_registry: FeatureRegistry,
        learning: KernelLearningService,
    ) -> None:
        self._adapter = adapter
        self._feature_builder = feature_builder
        self._engine_registry = engine_registry
        self._factor_registry = factor_registry
        self._feature_registry = feature_registry
        self._learning = learning

    def predict(self, match_id: str, engine: str = "auto") -> PredictionResult:
        """Run a prediction for a single match."""
        # 1. Get match identity
        match = self._adapter.get_match_identity(match_id)
        # 2. Fetch raw data
        raw = self._adapter.fetch_all_data(match)
        # 3. Build features
        features = self._feature_builder.build(match, raw)
        # 4. Select engine
        engine_impl = self._engine_registry.select(engine, competition=match.season.competition.code)
        # 5. Run prediction
        prediction = engine_impl.predict(features, match)
        # 6. Record for learning
        self._learning.record_prediction(match, prediction)
        # 7. Return result
        return prediction

    def batch_predict(
        self, match_ids: list[str], engine: str = "auto",
    ) -> list[PredictionResult]:
        """Run predictions for multiple matches."""
        results = []
        for match_id in match_ids:
            try:
                result = self.predict(match_id, engine=engine)
                results.append(result)
            except Exception as e:
                logger.error("Prediction failed for %s: %s", match_id, e)
        return results

    def process_outcome(self, match_id: str) -> None:
        """Process a match outcome — triggers the learning loop."""
        outcome = self._adapter.fetch_outcome(match_id)
        if outcome is None:
            logger.warning("No outcome found for match %s", match_id)
            return
        self._learning.record_outcome(outcome)
        error = self._learning.compute_error(match_id)
        if error is None:
            return

        if config.settings.PHASE3_LEARNING_ENABLED:
            match = self._adapter.get_match_identity(match_id)
            competition = match.season.competition.code
            engine = error.engine
            self._learning.update_calibration(competition, engine)
            self._learning.update_weights(competition)
            self._learning.engine_score(engine, competition)
