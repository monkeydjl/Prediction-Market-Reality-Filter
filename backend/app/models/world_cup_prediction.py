"""SQLAlchemy models for World Cup dynamic score predictions."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, JSON
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class MatchFixture(Base):
    """Basic match fixture information."""

    __tablename__ = "match_fixtures"

    match_id = Column(String(64), primary_key=True)
    fixture_id = Column(String(32), nullable=False, index=True)  # API-Football ID
    home_team = Column(String(64), nullable=False)
    away_team = Column(String(64), nullable=False)
    kickoff_utc = Column(DateTime(timezone=True), nullable=False, index=True)
    venue = Column(String(128))
    stage = Column(String(32), nullable=False, index=True)  # group_stage, round_of_16, etc.
    group = Column(String(8))  # A, B, C, etc. (null for knockout)
    status = Column(String(16), default="scheduled")  # scheduled, in_play, finished, postponed

    # Live/final scores
    home_score = Column(Integer)  # null if not started, updated during match
    away_score = Column(Integer)  # null if not started, updated during match

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class MatchPrediction(Base):
    """Current prediction for a match."""

    __tablename__ = "match_predictions"

    match_id = Column(String(64), primary_key=True)

    # Predicted score
    predicted_home_score = Column(Float, nullable=False)
    predicted_away_score = Column(Float, nullable=False)

    # Outcome probabilities
    home_win_prob = Column(Float, nullable=False)
    draw_prob = Column(Float, nullable=False)
    away_win_prob = Column(Float, nullable=False)

    # Confidence and metadata
    confidence = Column(Float, nullable=False)
    prediction_method = Column(String(32), default="hybrid")  # rule_only, ai_only, hybrid

    # Model contributions (for debugging)
    rule_home_score = Column(Float)
    rule_away_score = Column(Float)
    ai_home_score = Column(Float)
    ai_away_score = Column(Float)

    # Prediction factors (JSON blob)
    factors = Column(JSON)

    # AI reasoning (if available)
    ai_reasoning = Column(Text)
    key_factors = Column(JSON)  # List of key factor strings

    # Timestamps
    last_updated = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PredictionHistory(Base):
    """Time-series snapshots of predictions."""

    __tablename__ = "prediction_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String(64), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    # Snapshot of prediction at this time
    predicted_home_score = Column(Float, nullable=False)
    predicted_away_score = Column(Float, nullable=False)
    home_win_prob = Column(Float, nullable=False)
    draw_prob = Column(Float, nullable=False)
    away_win_prob = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)

    # What triggered this snapshot
    trigger = Column(String(32))  # daily_update, live_update, goal_event, etc.

    # Which engine was used for this prediction
    prediction_method = Column(String(128))  # elo_odds_fusion, hybrid, etc.

    # Live context if during match
    match_minute = Column(Integer)
    actual_home_score = Column(Integer)
    actual_away_score = Column(Integer)

    # Performance monitoring (optional)
    execution_time_ms = Column(Float)  # Engine execution time
    data_fetch_time_ms = Column(Float)  # Time to fetch Elo/odds/stats
    total_pipeline_time_ms = Column(Float)  # End-to-end pipeline time


class MatchResult(Base):
    """Final result and prediction accuracy."""

    __tablename__ = "match_results"

    match_id = Column(String(64), primary_key=True)

    # Actual result
    final_home_score = Column(Integer, nullable=False)
    final_away_score = Column(Integer, nullable=False)
    outcome = Column(String(16), nullable=False)  # home_win, draw, away_win
    finished_at = Column(DateTime(timezone=True), nullable=False)

    # Prediction made (copy of last prediction before match)
    predicted_home_score = Column(Float)
    predicted_away_score = Column(Float)
    predicted_outcome_prob = Column(Float)  # Probability assigned to actual outcome

    # Accuracy metrics
    score_mae = Column(Float)  # Mean absolute error on score
    outcome_correct = Column(Integer)  # 1 if outcome correct, 0 otherwise
    brier_score = Column(Float)  # Probabilistic accuracy

    # Error analysis
    home_error = Column(Float)  # predicted - actual (home)
    away_error = Column(Float)  # predicted - actual (away)
    confidence_calibrated = Column(Integer)  # 1 if confidence matched accuracy

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PredictionAccuracy(Base):
    """Aggregated accuracy metrics."""

    __tablename__ = "prediction_accuracy"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Scope
    stage = Column(String(32))  # group_stage, round_of_16, all, etc.
    matches_evaluated = Column(Integer, nullable=False)

    # Score accuracy
    exact_score_correct = Column(Integer)  # Exact score matches
    goal_diff_correct = Column(Integer)  # Goal difference correct
    score_mae = Column(Float)  # Average mean absolute error

    # Outcome accuracy
    outcome_correct = Column(Integer)  # Win/draw/loss correct
    outcome_accuracy = Column(Float)  # Percentage


class AIAnalysisHistory(Base):
    """Store AI analysis results to avoid redundant API calls."""

    __tablename__ = "ai_analysis_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String(64), nullable=False, index=True)

    # Analysis content
    analysis_text = Column(Text, nullable=False)

    # Context snapshot (to detect if re-analysis is needed)
    predicted_home_score = Column(Float, nullable=False)
    predicted_away_score = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    prediction_method = Column(String(128))

    # Metadata
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    api_cost_tokens = Column(Integer)  # Track token usage if available


class AIOptimizedPrediction(Base):
    """Store AI-optimized predictions for comparison with original engine predictions."""

    __tablename__ = "ai_optimized_predictions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String(64), nullable=False, index=True)

    # Original engine prediction
    original_engine = Column(String(128), nullable=False)  # elo_odds, hybrid, etc.
    original_home_score = Column(Float, nullable=False)
    original_away_score = Column(Float, nullable=False)
    original_home_win_prob = Column(Float, nullable=False)
    original_draw_prob = Column(Float, nullable=False)
    original_away_win_prob = Column(Float, nullable=False)
    original_confidence = Column(Float, nullable=False)

    # AI optimized prediction
    optimized_home_score = Column(Float, nullable=False)
    optimized_away_score = Column(Float, nullable=False)
    optimized_home_win_prob = Column(Float, nullable=False)
    optimized_draw_prob = Column(Float, nullable=False)
    optimized_away_win_prob = Column(Float, nullable=False)
    optimized_confidence = Column(Float, nullable=False)

    # AI reasoning
    blind_spots = Column(JSON)  # List of identified blind spots
    calibration_issues = Column(JSON)  # List of calibration issues
    optimization_reasoning = Column(Text)

    # Accuracy comparison (filled after match finishes)
    actual_home_score = Column(Integer)
    actual_away_score = Column(Integer)
    original_error = Column(Float)  # MAE of original prediction
    optimized_error = Column(Float)  # MAE of optimized prediction
    optimization_improved = Column(Integer)  # 1 if optimized was better, 0 otherwise

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class EngineCalibration(Base):
    """Store engine calibration adjustments learned from AI optimization patterns."""

    __tablename__ = "engine_calibration"

    id = Column(Integer, primary_key=True, autoincrement=True)
    engine_name = Column(String(128), nullable=False, index=True)  # elo_odds, hybrid

    # Calibration parameters (JSON for flexibility)
    calibration_params = Column(JSON, nullable=False)
    # Example structure:
    # {
    #   "home_advantage_bias": -0.1,  # Reduce home advantage by 0.1
    #   "draw_probability_shift": 0.05,  # Increase draw prob by 5%
    #   "confidence_deflation": 0.9,  # Multiply confidence by 0.9
    #   "strong_team_overconfidence": -0.08  # Reduce strong team win prob
    # }

    # Learning metadata
    based_on_matches = Column(Integer, nullable=False)  # How many matches informed this
    avg_improvement = Column(Float)  # Average error reduction when applied
    confidence_score = Column(Float)  # How confident we are in this calibration

    # Version control
    version = Column(Integer, nullable=False, default=1)
    is_active = Column(Integer, nullable=False, default=1)  # 1 = active, 0 = superseded

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class TeamMarketValue(Base):
    """Team squad market value from Transfermarkt."""

    __tablename__ = "team_market_values"

    team_name = Column(String(64), primary_key=True)
    total_market_value = Column(Float, nullable=False)  # millions of euros
    avg_player_value = Column(Float, nullable=False)  # millions of euros
    num_players = Column(Integer, nullable=False)
    url = Column(String(256))
    scraped_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TeamSentiment(Base):
    """Team sentiment from news and social media."""

    __tablename__ = "team_sentiment"

    team_name = Column(String(64), primary_key=True)
    overall_sentiment = Column(Float, nullable=False)  # -1 to 1
    news_sentiment = Column(Float, nullable=False)  # -1 to 1
    reddit_sentiment = Column(Float, nullable=False)  # -1 to 1
    confidence = Column(Float, nullable=False)  # 0 to 1 (data volume)
    article_count = Column(Integer, nullable=False)
    scraped_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
