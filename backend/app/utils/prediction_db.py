"""Database management for World Cup predictions."""

import logging
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.world_cup_prediction import Base

logger = logging.getLogger(__name__)

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

        # Set SQLite PRAGMAs on every new connection (matches sqlite_db.py)
        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return _engine


def init_prediction_db():
    """Initialize the prediction database schema."""
    engine = get_prediction_engine()
    Base.metadata.create_all(engine)


def get_prediction_session() -> Session:
    """Get a database session for predictions (manual use).

    Callers MUST close the session via ``close_prediction_session(session)``
    in a ``finally`` block, or use ``get_prediction_session_dep`` for
    FastAPI dependency injection (auto-closed).
    """
    global _SessionLocal
    if _SessionLocal is None:
        engine = get_prediction_engine()
        _SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    session = _SessionLocal()
    # Always expire all cached objects to get fresh data
    session.expire_all()
    return session


def get_prediction_session_dep() -> Iterator[Session]:
    """FastAPI dependency that yields a session and auto-closes it.

    Usage in route::

        @router.get("/...")
        async def endpoint(session: Session = Depends(get_prediction_session_dep)):
            ...
    """
    session = get_prediction_session()
    try:
        yield session
    finally:
        close_prediction_session(session)


def close_prediction_session(session: Session):
    """Close a prediction database session."""
    session.close()
