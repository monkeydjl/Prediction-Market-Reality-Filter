# backend/app/kernel/prediction_kernel.py
"""Prediction Kernel — the core orchestrator.

Connects: Adapter -> FeatureBuilder -> Engine -> Learning
The Kernel has zero knowledge of any specific sport or competition.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from app.core import config
from app.kernel.domain import (
    PredictionResult,
    UnknownMatchError,
)
from app.kernel.protocols import DataAdapter, FeatureBuilder
from app.kernel.engine_registry import EngineRegistry
from app.kernel.feature_registry import FeatureRegistry
from app.kernel.factor_registry import FactorRegistry
from app.kernel.learning_service import (
    KernelLearningService,
    apply_linear_calibration,
)

logger = logging.getLogger(__name__)


def _normalize_probs(probs: dict[str, float]) -> dict[str, float]:
    cleaned = {
        k: max(0.0, float(v))
        for k, v in probs.items()
        if v is not None and isinstance(v, (int, float))
    }
    total = sum(cleaned.values())
    if total <= 0:
        n = max(1, len(probs))
        return {k: 1.0 / n for k in probs}
    return {k: v / total for k, v in cleaned.items()}


def apply_conditional_calibration(
    prediction: PredictionResult,
    competition: str,
    learning: KernelLearningService,
    *,
    stage: str | None = None,
    match_id: str | None = None,
) -> PredictionResult:
    """Affine-adjust home_win then renormalize (P1-V5).

    Prefers stage then confidence-bucket rows when samples suffice; falls back
    to competition row. No-op when calibration missing or sample-thin.
    """
    cal = learning.get_conditional_calibration(
        competition,
        prediction.engine_name,
        prediction.confidence,
        stage=stage,
        match_id=match_id,
    )
    if cal is None:
        return prediction
    min_n = max(5, config.settings.MIN_SAMPLES_FOR_CALIBRATION // 2)
    if int(cal.get("sample_count") or 0) < min_n:
        return prediction

    slope = float(cal["slope"])
    intercept = float(cal["intercept"])
    probs = dict(prediction.outcome_probabilities)
    if "home_win" not in probs:
        return prediction

    raw_home = float(probs["home_win"])
    cal_home = apply_linear_calibration(raw_home, slope, intercept)
    # Preserve relative draw/away share of residual mass
    residual_keys = [k for k in probs if k != "home_win"]
    residual_mass = sum(float(probs[k]) for k in residual_keys)
    new_residual = max(1e-4, 1.0 - cal_home)
    if residual_mass <= 0:
        for k in residual_keys:
            probs[k] = new_residual / max(1, len(residual_keys))
    else:
        scale = new_residual / residual_mass
        for k in residual_keys:
            probs[k] = float(probs[k]) * scale
    probs["home_win"] = cal_home
    probs = _normalize_probs(probs)

    ba = dict(prediction.betting_analysis or {})
    ba["conditional_calibration"] = {
        "applied": True,
        "slope": slope,
        "intercept": intercept,
        "sample_count": cal.get("sample_count"),
        "bucket": cal.get("bucket"),
        "source": cal.get("source"),
        "raw_home_win": raw_home,
        "calibrated_home_win": cal_home,
    }
    return replace(
        prediction,
        outcome_probabilities=probs,
        betting_analysis=ba,
    )


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
        """Run a prediction for a single match.

        Raises :class:`UnknownMatchError` when no fixture backs ``match_id``.
        The adapters return a placeholder identity rather than ``None`` in that
        case (see :class:`MatchIdentity`), and without this check the run
        completed on invented inputs: measured against a copy of the live kernel
        DB, an id absent from ``kernel_match_fixtures`` produced confidence
        0.5475 with the Elo factor reported ``available: true`` on the neutral
        1500 default the Elo service substitutes for an unknown team, and
        ``record_prediction`` persisted a row to both ``kernel_predictions`` and
        ``kernel_prediction_history``. The guard is placed before
        ``fetch_all_data`` so an unknown id also stops costing upstream calls.
        """
        match = self._adapter.get_match_identity(match_id)
        if match.is_stub:
            raise UnknownMatchError(match_id)
        raw = self._adapter.fetch_all_data(match)
        features = self._feature_builder.build(match, raw)
        engine_impl = self._engine_registry.select(
            engine,
            competition=match.season.competition.code,
            sport=match.season.competition.sport.code,
        )
        prediction = engine_impl.predict(features, match)

        if config.settings.KERNEL_CONDITIONAL_CALIBRATION_ENABLED:
            try:
                prediction = apply_conditional_calibration(
                    prediction,
                    match.season.competition.code,
                    self._learning,
                    stage=getattr(match, "stage", None),
                    match_id=match.match_id,
                )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "conditional calibration apply skipped for %s",
                    match_id,
                    exc_info=True,
                )

        self._learning.record_prediction(match, prediction)
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
        """Process a match outcome — triggers the learning loop.

        Unlike :meth:`predict`, a missing fixture does not raise here: the
        outcome itself is real (``kernel_match_results`` is a separate table
        from ``kernel_match_fixtures``, so a result can outlive its fixture)
        and recording it is correct. Only the learning step is skipped, because
        its inputs are keyed by ``competition`` and a stub identity would supply
        the adapter's *default* competition code rather than the match's own --
        attributing calibration, weights, and an engine score to a competition
        the match may not belong to. Measured on the live kernel DB: 13,782
        results against 18,717 fixtures with **zero** results lacking a
        fixture, so this path guards a reachable shape that has not yet
        occurred rather than one currently mis-attributing.
        """
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
            if match.is_stub:
                logger.warning(
                    "Outcome for %s recorded, but no fixture backs it; skipping "
                    "the learning step rather than attributing it to the "
                    "adapter's default competition.",
                    match_id,
                )
                return
            competition = match.season.competition.code
            engine = error.engine
            self._learning.update_calibration(competition, engine)
            self._learning.update_weights(competition)
            self._learning.engine_score(engine, competition)
