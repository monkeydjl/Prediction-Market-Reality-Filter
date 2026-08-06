"""Type definitions for prediction engine outputs.

These types document the schema but do NOT enforce runtime validation yet.
"""

from typing import TypedDict


class PredictedScore(TypedDict):
    """Predicted score for home and away teams."""
    home: float
    away: float


class OutcomeProbabilities(TypedDict):
    """Win/draw/loss probabilities."""
    home_win: float
    draw: float
    away_win: float


class BasePrediction(TypedDict):
    """Common fields present in all engine outputs."""
    predicted_score: PredictedScore
    outcome_probabilities: OutcomeProbabilities
    confidence: float
    prediction_method: str


class EloOddsExtras(TypedDict, total=False):
    """Extra fields specific to elo_odds engine."""
    elo_ratings: dict
    elo_probabilities: OutcomeProbabilities
    market_probabilities: OutcomeProbabilities | None
    market_favorite: str | None
    has_betting_odds: bool
    score_probability_matrix: dict[str, float]
    top_5_scores: list[dict]
    prediction_interval: dict


class HybridExtras(TypedDict, total=False):
    """Extra fields specific to hybrid engine."""
    rule_score: PredictedScore | None
    ai_score: PredictedScore | None
    ai_reasoning: str | None
    key_factors: list[str]
    factors: dict
    timestamp: str


class EngineOutput(BasePrediction, total=False):
    """Union of all possible engine output fields.

    Different engines return different subsets of these fields.
    This is NOT enforced at runtime - it's a documentation type only.
    """
    # EloOdds extras
    elo_ratings: dict
    elo_probabilities: OutcomeProbabilities
    market_probabilities: OutcomeProbabilities | None
    market_favorite: str | None
    has_betting_odds: bool
    score_probability_matrix: dict[str, float]
    top_5_scores: list[dict]
    prediction_interval: dict

    # Hybrid extras
    rule_score: PredictedScore | None
    ai_score: PredictedScore | None
    ai_reasoning: str | None
    key_factors: list[str]
    factors: dict
    timestamp: str
