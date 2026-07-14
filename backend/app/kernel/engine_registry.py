# backend/app/kernel/engine_registry.py
"""Engine registration and selection."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.kernel.protocols import PredictionEngine

if TYPE_CHECKING:
    from app.kernel.protocols import LearningService

# Minimum samples for dynamic engine selection (hardcoded — see spec Section 3.4)
_MIN_SAMPLES_FOR_ENGINE_SELECT = 5


class EngineRegistry:
    """Registers engines and selects them by name or strategy."""

    def __init__(self, learning_service: LearningService | None = None) -> None:
        self._engines: dict[str, PredictionEngine] = {}
        self._default_name: str | None = None
        self._learning_service = learning_service

    def register(self, engine: PredictionEngine) -> None:
        name = engine.name()
        self._engines[name] = engine
        if self._default_name is None:
            self._default_name = name

    def get(self, name: str) -> PredictionEngine:
        if name not in self._engines:
            available = list(self._engines.keys())
            raise KeyError(f"Unknown engine: {name}. Available: {available}")
        return self._engines[name]

    def list_engines(self) -> list[str]:
        return list(self._engines.keys())

    def select(self, engine_name: str,
               competition: str | None = None) -> PredictionEngine:
        """Select an engine by name or 'auto' for dynamic selection.

        When engine_name is 'auto' and a LearningService is available,
        selects the engine with the highest accuracy that has at least
        _MIN_SAMPLES_FOR_ENGINE_SELECT samples. Falls back to default
        engine if no engine has enough samples.
        """
        if engine_name != "auto":
            return self.get(engine_name)

        if self._default_name is None:
            raise KeyError("No engines registered")

        # Dynamic selection via LearningService
        if self._learning_service is not None:
            best_engine = None
            best_accuracy = -1.0
            for name, engine in self._engines.items():
                score = self._learning_service.engine_score(name, competition)
                if score and score.sample_count >= _MIN_SAMPLES_FOR_ENGINE_SELECT:
                    if score.accuracy > best_accuracy:
                        best_accuracy = score.accuracy
                        best_engine = engine
            if best_engine is not None:
                return best_engine

        return self._engines[self._default_name]
