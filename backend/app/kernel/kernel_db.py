# backend/app/kernel/kernel_db.py
"""Database management for the Prediction Kernel.

Uses a separate SQLite database (kernel_predictions.db) with kernel_ prefixed
tables. Does NOT touch the existing world_cup_predictions.db.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker, DeclarativeBase

logger = logging.getLogger(__name__)

_engine = None
_SessionLocal = None


class KernelBase(DeclarativeBase):
    pass


# Define tables as SQLAlchemy models
from sqlalchemy import Column, String, Float, Integer, Text, DateTime, JSON


class KernelPrediction(KernelBase):
    __tablename__ = "kernel_predictions"

    match_id = Column(String, primary_key=True)
    sport = Column(String, nullable=False)
    competition = Column(String, nullable=False)
    season = Column(String, nullable=False)
    engine = Column(String, nullable=False)
    predicted_scores = Column(JSON, nullable=False)
    outcome_probabilities = Column(JSON, nullable=False)
    confidence = Column(Float, nullable=False)
    feature_version = Column(String, nullable=False)
    explanation = Column(JSON)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class KernelPredictionHistory(KernelBase):
    __tablename__ = "kernel_prediction_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String, nullable=False)
    engine = Column(String, nullable=False)
    predicted_scores = Column(JSON)
    outcome_probabilities = Column(JSON)
    confidence = Column(Float)
    trigger = Column(String)
    created_at = Column(DateTime)


class KernelMatchOutcome(KernelBase):
    __tablename__ = "kernel_match_outcomes"

    match_id = Column(String, primary_key=True)
    home_score = Column(Integer)
    away_score = Column(Integer)
    outcome = Column(String)
    engine = Column(String)
    score_mae = Column(Float)
    outcome_correct = Column(Integer)
    brier_score = Column(Float)
    finished_at = Column(DateTime)
    created_at = Column(DateTime)


class KernelEngineScore(KernelBase):
    __tablename__ = "kernel_engine_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    engine = Column(String, nullable=False)
    competition = Column(String)  # NULL = global
    accuracy = Column(Float)
    avg_mae = Column(Float)
    brier_score = Column(Float)
    sample_count = Column(Integer, default=0)
    last_updated = Column(DateTime)


class KernelFactor(KernelBase):
    __tablename__ = "kernel_factors"

    factor_id = Column(String, primary_key=True)
    category = Column(String, nullable=False)
    version = Column(String, nullable=False)
    weight = Column(Float, default=1.0)
    competition = Column(String)  # NULL = global
    enabled = Column(Integer, default=1)
    source = Column(String, default="manual")
    updated_at = Column(DateTime)


def init_kernel_db(db_path: str | None = None) -> None:
    """Initialize the kernel database. Creates tables if they don't exist."""
    global _engine, _SessionLocal
    if _engine is not None:
        return
    if db_path is None:
        db_path = str(Path(__file__).resolve().parents[2] / "kernel_predictions.db")
    _engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
        pool_recycle=3600,
    )

    @event.listens_for(_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    KernelBase.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    logger.info("Kernel DB initialized at %s", db_path)


def get_kernel_session() -> Session:
    if _SessionLocal is None:
        init_kernel_db()
    return _SessionLocal()


def close_kernel_session() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
