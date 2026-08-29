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
    """Identifies a single match.

    ``is_stub`` marks an identity that no fixture backs. Every adapter's
    ``get_match_identity`` is declared ``-> MatchIdentity`` and returns a
    placeholder (teams literally named "Home"/"Away") when the fixture is
    missing, so callers cannot use ``is None`` to detect an unknown match --
    and one route tried to, leaving a permanently dead 404 branch. The flag is
    the provenance signal that closes that gap; it mirrors
    :attr:`MarketFeatures.odds_source`, which already travels beside the odds
    it describes so consumers can tell a real value from a substituted one.

    Defaulted to ``False`` so that the ~50 existing construction sites, all of
    which build identities from real fixture rows, keep their meaning.
    """
    match_id: str
    season: SeasonIdentity
    stage: str
    round: str | None
    home: TeamIdentity
    away: TeamIdentity
    kickoff_utc: datetime
    is_stub: bool = False


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
    """Team-level features (cross-sport).

    ``elo_source`` carries the provenance of the two Elo ratings, in the
    ``"<home>/<away>"`` form that
    :func:`app.services.world_cup_data_quality.all_sources_look_real` splits on.
    Without it an engine cannot tell a measured rating from an invented one:
    ``elo_ratings_service.get_elo_rating`` returns ``1500.0`` with
    ``source="default"`` for any team it does not know, and both football
    adapters used to read ``elo_rating`` and discard ``source`` -- so a defaulted
    pair reached the engine as ``available=True`` with the detail string
    ``"Elo 1500.0 vs 1500.0"``, and (with odds present) lifted
    ``data_quality`` from ``partial`` to ``real``.  ``MarketFeatures`` has
    carried ``odds_source`` and ``odds_fresh`` since P1-E4 for exactly this
    reason; this is the team-side half.

    ``None`` means "this adapter does not report Elo provenance", not "not
    real".  The MLB/NBA/NHL/LoL paths read a ratings table that yields ``None``
    when a team is absent, so they never invent a value and need no label --
    the same absence convention the optional discovery sources use.
    """
    elo_rating_home: float | None
    elo_rating_away: float | None
    form_home: float | None
    form_away: float | None
    h2h_home_win_rate: float | None
    h2h_draw_rate: float | None
    market_value_home: float | None
    market_value_away: float | None
    elo_source: str | None = None


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
    predicted_outcome: str | None = None


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


class UnknownMatchError(LookupError):
    """Raised when an operation needs a fixture the match id has no row for.

    ``LookupError`` so that it sits beside the ``KeyError`` that
    :meth:`EngineRegistry.select` already raises for an unknown engine, and so
    ``PredictionKernel.batch_predict`` -- which catches ``Exception`` per match
    and logs -- keeps degrading one match at a time rather than aborting a run.
    """

    def __init__(self, match_id: str) -> None:
        self.match_id = match_id
        super().__init__(f"No fixture for match_id {match_id!r}")


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
