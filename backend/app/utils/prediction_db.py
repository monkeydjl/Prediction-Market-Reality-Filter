"""Database management for World Cup predictions."""

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.world_cup_prediction import Base


# Global engine and session factory (singleton)
_engine = None
_SessionLocal = None


def get_prediction_db_path() -> Path:
    """Get the path to the World Cup prediction database."""
    db_path = getattr(settings, "WORLD_CUP_PREDICTION_DB_FILE", "backend/world_cup_predictions.db")
    return Path(db_path)


def get_prediction_engine():
    """Get SQLAlchemy engine for World Cup predictions (singleton)."""
    global _engine
    if _engine is None:
        db_path = get_prediction_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            # Disable connection pooling to avoid stale data
            pool_pre_ping=True,
            pool_recycle=3600
        )
    return _engine


def init_prediction_db():
    """Initialize the prediction database schema."""
    engine = get_prediction_engine()
    Base.metadata.create_all(engine)


def get_prediction_session() -> Session:
    """Get a database session for predictions."""
    global _SessionLocal
    if _SessionLocal is None:
        engine = get_prediction_engine()
        _SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    session = _SessionLocal()
    # Always expire all cached objects to get fresh data
    session.expire_all()
    return session


def close_prediction_session(session: Session):
    """Close a prediction database session."""
    session.close()
