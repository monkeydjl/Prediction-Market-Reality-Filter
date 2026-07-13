# backend/app/kernel/engine_registry.py
"""Engine registration and selection."""
from __future__ import annotations

from app.kernel.domain import FeatureSet
from app.kernel.protocols import PredictionEngine


class EngineRegistry:
    """Registers engines and selects them by name or strategy."""

    def __init__(self) -> None:
        self._engines: dict[str, PredictionEngine] = {}
        self._default_name: str | None = None

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

    def select(self, strategy: str, features: FeatureSet) -> PredictionEngine:
        if strategy == "auto":
            if self._default_name is None:
                raise KeyError("No engines registered")
            return self._engines[self._default_name]
        return self.get(strategy)
