"""SQLAlchemy models for World Cup dynamic score predictions."""

from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, JSON
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class MatchFixture(Base):
    """Basic match fixture information."""

    __tablename__ = "match_fixtures"

    match_id = Column(String(64), primary_key=True)
    fixture_id = Column(String(32), nullable=False, index=True)  # API-Football ID
    home_team = Column(String(64), nullable=False)
    away_team = Column(String(64), nullable=False)
    kickoff_utc = Column(DateTime, nullable=False, index=True)
    venue = Column(String(128))
    stage = Column(String(32), nullable=False, index=True)  # group_stage, round_of_16, etc.
    group = Column(String(8))  # A, B, C, etc. (null for knockout)
    status = Column(String(16), default="scheduled")  # scheduled, in_play, finished, postponed
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class PredictionHistory(Base):
    """Time-series snapshots of predictions."""

    __tablename__ = "prediction_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String(64), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Snapshot of prediction at this time
    predicted_home_score = Column(Float, nullable=False)
    predicted_away_score = Column(Float, nullable=False)
    home_win_prob = Column(Float, nullable=False)
    draw_prob = Column(Float, nullable=False)
    away_win_prob = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)

    # What triggered this snapshot
    trigger = Column(String(32))  # daily_update, live_update, goal_event, etc.

    # Live context if during match
    match_minute = Column(Integer)
    actual_home_score = Column(Integer)
    actual_away_score = Column(Integer)


class MatchResult(Base):
    """Final result and prediction accuracy."""

    __tablename__ = "match_results"

    match_id = Column(String(64), primary_key=True)

    # Actual result
    final_home_score = Column(Integer, nullable=False)
    final_away_score = Column(Integer, nullable=False)
    outcome = Column(String(16), nullable=False)  # home_win, draw, away_win
    finished_at = Column(DateTime, nullable=False)

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

    created_at = Column(DateTime, default=datetime.utcnow)


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

    # Probabilistic accuracy
    avg_brier_score = Column(Float)
    avg_confidence = Column(Float)
    calibration_score = Column(Float)

    # Updated timestamp
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TeamMarketValue(Base):
    """Team squad market value from Transfermarkt."""

    __tablename__ = "team_market_values"

    team_name = Column(String(64), primary_key=True)
    total_market_value = Column(Float, nullable=False)  # millions of euros
    avg_player_value = Column(Float, nullable=False)  # millions of euros
    num_players = Column(Integer, nullable=False)
    url = Column(String(256))
    scraped_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class TeamSentiment(Base):
    """Team sentiment from news and social media."""

    __tablename__ = "team_sentiment"

    team_name = Column(String(64), primary_key=True)
    overall_sentiment = Column(Float, nullable=False)  # -1 to 1
    news_sentiment = Column(Float, nullable=False)  # -1 to 1
    reddit_sentiment = Column(Float, nullable=False)  # -1 to 1
    confidence = Column(Float, nullable=False)  # 0 to 1 (data volume)
    article_count = Column(Integer, nullable=False)
    scraped_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
