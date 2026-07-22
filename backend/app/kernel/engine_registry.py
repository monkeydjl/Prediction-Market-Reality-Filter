# backend/app/kernel/engine_registry.py
"""Engine registration and selection."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.core import config
from app.kernel.protocols import PredictionEngine

if TYPE_CHECKING:
    from app.kernel.protocols import LearningService


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

    def select(
        self,
        engine_name: str,
        competition: str | None = None,
        sport: str | None = None,
    ) -> PredictionEngine:
        """Select an engine by name or 'auto' for dynamic selection.

        When engine_name is 'auto':
        1. Restrict candidates to engines whose ``supported_sports`` includes
           *sport* (or ``"*"``). If *sport* is omitted, resolve it from
           *competition* via COMPETITION_SPORT when possible.
        2. If a LearningService is available, pick the highest-accuracy
           candidate with enough samples (MIN_SAMPLES_FOR_ENGINE_SELECT).
        3. Else return the first registered sport-compatible engine
           (registration order), not a global football default.
        """
        if engine_name != "auto":
            return self.get(engine_name)

        if self._default_name is None:
            raise KeyError("No engines registered")

        candidates = self._sport_candidates(sport=sport, competition=competition)
        if not candidates:
            resolved = self._resolve_sport(sport, competition)
            available = list(self._engines.keys())
            raise KeyError(
                f"No engine supports sport={resolved!r} "
                f"(competition={competition!r}). Registered: {available}"
            )

        # Dynamic selection via LearningService (sport-filtered)
        if self._learning_service is not None:
            best_engine = None
            best_accuracy = -1.0
            min_samples = config.settings.MIN_SAMPLES_FOR_ENGINE_SELECT
            for name, engine in candidates.items():
                score = self._learning_service.engine_score(name, competition)
                if score and score.sample_count >= min_samples:
                    if score.accuracy > best_accuracy:
                        best_accuracy = score.accuracy
                        best_engine = engine
            if best_engine is not None:
                return best_engine

        # First registered compatible engine (stable order)
        for name in self._engines:
            if name in candidates:
                return candidates[name]
        return next(iter(candidates.values()))

    def _resolve_sport(
        self,
        sport: str | None,
        competition: str | None,
    ) -> str | None:
        if sport:
            return sport.strip().lower() or None
        if not competition:
            return None
        try:
            from app.kernel.competition_codes import (
                COMPETITION_SPORT,
                normalize_competition_code,
            )
            norm = normalize_competition_code(competition) or competition.strip().lower()
            return COMPETITION_SPORT.get(norm)
        except Exception:  # pragma: no cover - defensive
            return None

    def _engine_supports(self, engine: PredictionEngine, sport: str | None) -> bool:
        if not sport:
            return True
        try:
            sports = list(engine.supported_sports())
        except Exception:  # pragma: no cover
            sports = ["*"]
        return "*" in sports or sport in sports

    def _sport_candidates(
        self,
        sport: str | None,
        competition: str | None,
    ) -> dict[str, PredictionEngine]:
        resolved = self._resolve_sport(sport, competition)
        if not resolved:
            return dict(self._engines)
        return {
            name: eng
            for name, eng in self._engines.items()
            if self._engine_supports(eng, resolved)
        }
