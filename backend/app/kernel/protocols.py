# backend/app/kernel/protocols.py
"""Protocol interfaces for the Prediction Kernel.

These Protocols define the contracts between the Kernel and its adapters.
The Kernel never imports concrete implementations — only these Protocols.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.kernel.domain import (
    SportIdentity, TeamIdentity, MatchIdentity, MatchOutcome,
    FeatureSet, PredictionResult, PredictionError, EngineScore,
)


@dataclass(frozen=True)
class ScheduleFilter:
    """Filter parameters for schedule queries."""
    competition: str | None = None
    season: str | None = None
    status: str | None = None
    stage: str | None = None
    limit: int | None = None


@dataclass(frozen=True)
class RawMatchData:
    """Raw data from an adapter, pre-feature-building."""
    match: MatchIdentity
    raw_json: dict


@runtime_checkable
class DataAdapter(Protocol):
    """Fetches raw data from external sources. Does NOT compute features."""

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]: ...
    def fetch_team_data(self, team: TeamIdentity) -> dict: ...
    def fetch_player_data(self, team: TeamIdentity) -> dict: ...
    def fetch_market_data(self, match: MatchIdentity) -> dict: ...
    def fetch_outcome(self, match_id: str) -> MatchOutcome | None: ...
    def sync_schedule(self) -> int: ...
    def get_match_identity(self, match_id: str) -> MatchIdentity: ...
    def fetch_all_data(self, match: MatchIdentity) -> dict: ...


@runtime_checkable
class FeatureBuilder(Protocol):
    """Computes a standardized FeatureSet from raw data."""

    def build(self, match: MatchIdentity, raw: dict) -> FeatureSet: ...
    def sport(self) -> SportIdentity: ...


@runtime_checkable
class PredictionEngine(Protocol):
    """Pure function engine: FeatureSet in, PredictionResult out."""

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult: ...
    def name(self) -> str: ...
    def supported_sports(self) -> list[str]: ...


@runtime_checkable
class LearningService(Protocol):
    """Post-match learning loop for continuous improvement."""

    def record_prediction(self, match: MatchIdentity,
                          prediction: PredictionResult) -> None: ...
    def record_outcome(self, outcome: MatchOutcome) -> None: ...
    def compute_error(self, match_id: str) -> PredictionError | None: ...
    def update_calibration(self, competition: str,
                           engine: str) -> None: ...
    def update_weights(self, competition: str) -> None: ...
    def engine_score(self, engine: str,
                     competition: str | None = None) -> EngineScore | None: ...
