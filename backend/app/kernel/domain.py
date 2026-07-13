"""Domain value objects for the Prediction Kernel.

All objects are frozen dataclasses, safe for use as cache keys,
database primary keys, and event bus messages.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SportIdentity:
    """Identifies a sport (football, basketball, ...)."""
    code: str
    name: str


@dataclass(frozen=True)
class CompetitionIdentity:
    """Identifies a competition within a sport (world_cup, epl, nba, ...)."""
    code: str
    name: str
    sport: SportIdentity


@dataclass(frozen=True)
class SeasonIdentity:
    """Identifies a season within a competition."""
    competition: CompetitionIdentity
    season_key: str


@dataclass(frozen=True)
class TeamIdentity:
    """Identifies a team within a competition."""
    code: str
    name: str
    competition: CompetitionIdentity


@dataclass(frozen=True)
class MatchIdentity:
    """Identifies a single match."""
    match_id: str
    season: SeasonIdentity
    stage: str
    round: str | None
    home: TeamIdentity
    away: TeamIdentity
    kickoff_utc: datetime


@dataclass(frozen=True)
class MatchOutcome:
    """Final result of a completed match."""
    match_id: str
    home_score: int
    away_score: int
    outcome: str
    finished_at: datetime


@dataclass(frozen=True)
class GeneralFeatures:
    """Cross-sport general features."""
    rest_days_home: float | None
    rest_days_away: float | None
    travel_distance_km: float | None
    days_since_last_match: float | None


@dataclass(frozen=True)
class TeamFeatures:
    """Team-level features (cross-sport)."""
    elo_rating_home: float | None
    elo_rating_away: float | None
    form_home: float | None
    form_away: float | None
    h2h_home_win_rate: float | None
    h2h_draw_rate: float | None
    market_value_home: float | None
    market_value_away: float | None


@dataclass(frozen=True)
class MarketFeatures:
    """Betting market features (cross-sport)."""
    odds_home: float | None
    odds_draw: float | None
    odds_away: float | None
    odds_source: str | None
    odds_fresh: bool


@dataclass(frozen=True)
class PlayerFeatures:
    """Player-level features (cross-sport)."""
    key_players_available_home: float | None
    key_players_available_away: float | None
    injury_impact_home: float | None
    injury_impact_away: float | None


@dataclass(frozen=True)
class EnvironmentFeatures:
    """Environment features (cross-sport)."""
    venue: str | None
    weather_temp_c: float | None
    weather_condition: str | None
    is_home_advantage: bool


@dataclass(frozen=True)
class FeatureSet:
    """Standardized feature package consumed by prediction engines."""
    match: MatchIdentity
    general: GeneralFeatures
    team: TeamFeatures
    market: MarketFeatures
    player: PlayerFeatures
    environment: EnvironmentFeatures
    custom: dict[str, float]
    data_quality: str
    quality_notes: list[str]
    feature_version: str


@dataclass(frozen=True)
class ContributionItem:
    """A single factor contribution in a prediction explanation."""
    factor: str
    direction: str
    weight: float
    available: bool
    detail: str | None


@dataclass(frozen=True)
class PredictionResult:
    """Unified prediction output from any engine."""
    predicted_scores: dict[str, float]
    outcome_probabilities: dict[str, float]
    confidence: float
    engine_name: str
    explanation: list[ContributionItem]
    betting_analysis: dict | None
    feature_version: str
    prediction_timestamp: datetime


@dataclass(frozen=True)
class PredictionError:
    """Prediction error metrics after match completion."""
    match_id: str
    engine: str
    score_mae: float
    outcome_correct: bool
    brier_score: float
    confidence_calibrated: bool


@dataclass(frozen=True)
class EngineScore:
    """Aggregated performance score for an engine."""
    engine: str
    competition: str | None
    accuracy: float
    avg_mae: float
    brier_score: float
    sample_count: int
    confidence_calibration: float
    last_updated: datetime
