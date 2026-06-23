"""Database management for World Cup predictions."""

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.world_cup_prediction import Base


def get_prediction_db_path() -> Path:
    """Get the path to the World Cup prediction database."""
    db_path = getattr(settings, "WORLD_CUP_PREDICTION_DB_FILE", "backend/world_cup_predictions.db")
    return Path(db_path)


def get_prediction_engine():
    """Get SQLAlchemy engine for World Cup predictions."""
    db_path = get_prediction_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})


def init_prediction_db():
    """Initialize the prediction database schema."""
    engine = get_prediction_engine()
    Base.metadata.create_all(engine)


def get_prediction_session() -> Session:
    """Get a database session for predictions."""
    engine = get_prediction_engine()
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def close_prediction_session(session: Session):
    """Close a prediction database session."""
    session.close()
