"""SQLAlchemy models for World Cup dynamic score predictions.

Typing note: these use SQLAlchemy 2.0 ``Mapped[T]`` / ``mapped_column()`` rather
than bare ``Column()``. With ``Column()`` at class level, a type checker sees
``fixture.home_team`` as ``Column[str]`` instead of ``str``, which is the single
largest source of the mypy baseline in this repo — every caller then trips
``arg-type`` / ``assignment`` on values that are plain strings at runtime.

``nullable=`` is stated explicitly on every column, including where it merely
restates the annotation. It is not redundant: ``Column(String(128))`` defaults to
``nullable=True`` while ``mapped_column()`` with a non-Optional ``Mapped[str]``
defaults to ``nullable=False``. Spelling it out means the emitted DDL cannot
silently flip if an annotation is edited later.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Float, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for the World Cup prediction tables.

    A ``DeclarativeBase`` subclass rather than ``declarative_base()`` so the base
    is a real class: mypy rejects the dynamically produced one as ``Invalid base
    class``, which cost one error per model. ``elo_ratings_service`` and
    ``odds_cache_service`` still hang legacy ``Column()`` models off this same
    base, which 2.0 declarative continues to accept.
    """


class MatchFixture(Base):
    """Basic match fixture information."""

    __tablename__ = "match_fixtures"

    match_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # API-Football ID
    fixture_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    home_team: Mapped[str] = mapped_column(String(64), nullable=False)
    away_team: Mapped[str] = mapped_column(String(64), nullable=False)
    kickoff_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    venue: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # group_stage, round_of_16, etc.
    stage: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # A, B, C, etc. (null for knockout)
    group: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # scheduled, in_play, finished, postponed
    status: Mapped[str | None] = mapped_column(
        String(16), default="scheduled", nullable=True
    )

    # Live/final scores — null if not started, updated during match
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=True,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=True,
    )


class MatchPrediction(Base):
    """Current prediction for a match."""

    __tablename__ = "match_predictions"

    match_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    # Predicted score
    predicted_home_score: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_away_score: Mapped[float] = mapped_column(Float, nullable=False)

    # Outcome probabilities
    home_win_prob: Mapped[float] = mapped_column(Float, nullable=False)
    draw_prob: Mapped[float] = mapped_column(Float, nullable=False)
    away_win_prob: Mapped[float] = mapped_column(Float, nullable=False)

    # Confidence and metadata
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # rule_only, ai_only, hybrid
    prediction_method: Mapped[str | None] = mapped_column(
        String(32), default="hybrid", nullable=True
    )

    # Model contributions (for debugging)
    rule_home_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rule_away_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_home_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_away_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Prediction factors (JSON blob). Annotated Any rather than a dict shape:
    # nothing validates the payload, so a stricter annotation would assert a
    # contract the code does not keep.
    factors: Mapped[Any] = mapped_column(JSON, nullable=True)

    # AI reasoning (if available)
    ai_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_factors: Mapped[Any] = mapped_column(JSON, nullable=True)  # list of strings

    # Timestamps
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=True,
    )


class PredictionHistory(Base):
    """Time-series snapshots of predictions."""

    __tablename__ = "prediction_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    # Snapshot of prediction at this time
    predicted_home_score: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_away_score: Mapped[float] = mapped_column(Float, nullable=False)
    home_win_prob: Mapped[float] = mapped_column(Float, nullable=False)
    draw_prob: Mapped[float] = mapped_column(Float, nullable=False)
    away_win_prob: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # What triggered this snapshot — daily_update, live_update, goal_event, etc.
    trigger: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Which engine was used — elo_odds_fusion, hybrid, etc.
    prediction_method: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Live context if during match
    match_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Performance monitoring (optional)
    execution_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_fetch_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_pipeline_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class MatchResult(Base):
    """Final result and prediction accuracy."""

    __tablename__ = "match_results"

    match_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    # Actual result
    final_home_score: Mapped[int] = mapped_column(Integer, nullable=False)
    final_away_score: Mapped[int] = mapped_column(Integer, nullable=False)
    # home_win, draw, away_win
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Prediction made (copy of last prediction before match)
    predicted_home_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_away_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Probability assigned to actual outcome
    predicted_outcome_prob: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Accuracy metrics
    score_mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 1 if outcome correct, 0 otherwise
    outcome_correct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    brier_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Error analysis — predicted minus actual
    home_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    away_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 1 if confidence matched accuracy
    confidence_calibrated: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=True,
    )


class PredictionAccuracy(Base):
    """Aggregated accuracy metrics."""

    __tablename__ = "prediction_accuracy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Scope — group_stage, round_of_16, all, etc.
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    matches_evaluated: Mapped[int] = mapped_column(Integer, nullable=False)

    # Score accuracy
    exact_score_correct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goal_diff_correct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_mae: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Outcome accuracy
    outcome_correct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)


class AIAnalysisHistory(Base):
    """Store AI analysis results to avoid redundant API calls."""

    __tablename__ = "ai_analysis_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Analysis content
    analysis_text: Mapped[str] = mapped_column(Text, nullable=False)

    # Context snapshot (to detect if re-analysis is needed)
    predicted_home_score: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_away_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    prediction_method: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    # Track token usage if available
    api_cost_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AIOptimizedPrediction(Base):
    """Store AI-optimized predictions for comparison with original engine predictions."""

    __tablename__ = "ai_optimized_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Original engine prediction — elo_odds, hybrid, etc.
    original_engine: Mapped[str] = mapped_column(String(128), nullable=False)
    original_home_score: Mapped[float] = mapped_column(Float, nullable=False)
    original_away_score: Mapped[float] = mapped_column(Float, nullable=False)
    original_home_win_prob: Mapped[float] = mapped_column(Float, nullable=False)
    original_draw_prob: Mapped[float] = mapped_column(Float, nullable=False)
    original_away_win_prob: Mapped[float] = mapped_column(Float, nullable=False)
    original_confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # AI optimized prediction
    optimized_home_score: Mapped[float] = mapped_column(Float, nullable=False)
    optimized_away_score: Mapped[float] = mapped_column(Float, nullable=False)
    optimized_home_win_prob: Mapped[float] = mapped_column(Float, nullable=False)
    optimized_draw_prob: Mapped[float] = mapped_column(Float, nullable=False)
    optimized_away_win_prob: Mapped[float] = mapped_column(Float, nullable=False)
    optimized_confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # AI reasoning — lists of identified blind spots / calibration issues
    blind_spots: Mapped[Any] = mapped_column(JSON, nullable=True)
    calibration_issues: Mapped[Any] = mapped_column(JSON, nullable=True)
    optimization_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Accuracy comparison (filled after match finishes)
    actual_home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # MAE of the original / optimized prediction
    original_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    optimized_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 1 if optimized was better, 0 otherwise
    optimization_improved: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class EngineCalibration(Base):
    """Store engine calibration adjustments learned from AI optimization patterns."""

    __tablename__ = "engine_calibration"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # elo_odds, hybrid
    engine_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    # Calibration parameters (JSON for flexibility). Example structure:
    # {
    #   "home_advantage_bias": -0.1,  # Reduce home advantage by 0.1
    #   "draw_probability_shift": 0.05,  # Increase draw prob by 5%
    #   "confidence_deflation": 0.9,  # Multiply confidence by 0.9
    #   "strong_team_overconfidence": -0.08  # Reduce strong team win prob
    # }
    calibration_params: Mapped[Any] = mapped_column(JSON, nullable=False)

    # Learning metadata
    # How many matches informed this
    based_on_matches: Mapped[int] = mapped_column(Integer, nullable=False)
    # Average error reduction when applied
    avg_improvement: Mapped[float | None] = mapped_column(Float, nullable=True)
    # How confident we are in this calibration
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Version control
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 1 = active, 0 = superseded
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=True,
    )


class TeamMarketValue(Base):
    """Team squad market value from Transfermarkt."""

    __tablename__ = "team_market_values"

    team_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    # millions of euros
    total_market_value: Mapped[float] = mapped_column(Float, nullable=False)
    avg_player_value: Mapped[float] = mapped_column(Float, nullable=False)
    num_players: Mapped[int] = mapped_column(Integer, nullable=False)
    url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=True,
    )


class TeamSentiment(Base):
    """Team sentiment from news and social media."""

    __tablename__ = "team_sentiment"

    team_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    # -1 to 1
    overall_sentiment: Mapped[float] = mapped_column(Float, nullable=False)
    news_sentiment: Mapped[float] = mapped_column(Float, nullable=False)
    reddit_sentiment: Mapped[float] = mapped_column(Float, nullable=False)
    # 0 to 1 (data volume)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    article_count: Mapped[int] = mapped_column(Integer, nullable=False)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=True,
    )
