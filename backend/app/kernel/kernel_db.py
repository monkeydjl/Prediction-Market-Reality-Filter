# backend/app/kernel/kernel_db.py
"""Database management for the Prediction Kernel.

Uses a separate SQLite database (kernel_predictions.db) with kernel_ prefixed
tables. Does NOT touch the existing world_cup_predictions.db.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker, DeclarativeBase

logger = logging.getLogger(__name__)

_engine = None
_SessionLocal = None


class KernelBase(DeclarativeBase):
    pass


# Define tables as SQLAlchemy models
from sqlalchemy import Column, String, Float, Integer, Text, DateTime, JSON, UniqueConstraint, Boolean, Index


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
    feature_version = Column(String)
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
    confidence_calibration = Column(Float, default=0.0)
    last_updated = Column(DateTime)


class KernelFactor(KernelBase):
    __tablename__ = "kernel_factors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    factor_id = Column(String, nullable=False)
    category = Column(String, nullable=False)
    version = Column(String, nullable=False)
    weight = Column(Float, default=1.0)
    competition = Column(String)  # NULL = global
    enabled = Column(Integer, default=1)
    source = Column(String, default="manual")
    updated_at = Column(DateTime)

    __table_args__ = (
        UniqueConstraint("factor_id", "competition", name="uq_factor_id_competition"),
    )


class KernelCalibration(KernelBase):
    __tablename__ = "kernel_calibration"

    id = Column(Integer, primary_key=True, autoincrement=True)
    engine = Column(String(50), nullable=False)
    competition = Column(String(50), nullable=False)
    slope = Column(Float, nullable=False)
    intercept = Column(Float, nullable=False)
    sample_count = Column(Integer, nullable=False, default=0)
    avg_confidence = Column(Float, nullable=False, default=0.0)
    avg_accuracy = Column(Float, nullable=False, default=0.0)
    last_updated = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("engine", "competition", name="uq_calibration_engine_competition"),
    )


class KernelMatchFixture(KernelBase):
    """Fixture table for UCL/EPL matches (kernel_ prefixed)."""
    __tablename__ = "kernel_match_fixtures"

    match_id = Column(String, primary_key=True)
    competition = Column(String, nullable=False)
    season = Column(String, nullable=False)
    home_team = Column(String, nullable=False)
    away_team = Column(String, nullable=False)
    kickoff_utc = Column(DateTime)
    stage = Column(String)
    status = Column(String, default="scheduled")
    home_score = Column(Integer)
    away_score = Column(Integer)
    venue = Column(String)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class KernelMatchResult(KernelBase):
    """Match result table for UCL/EPL matches."""
    __tablename__ = "kernel_match_results"

    match_id = Column(String, primary_key=True)
    home_score = Column(Integer)
    away_score = Column(Integer)
    outcome = Column(String)
    finished_at = Column(DateTime)
    created_at = Column(DateTime)


class KernelClubEloCache(KernelBase):
    """Cache for club Elo ratings from ClubElo.com."""
    __tablename__ = "kernel_club_elo_cache"

    team_name = Column(String, primary_key=True)
    elo_rating = Column(Float, nullable=False)
    source = Column(String, default="clubelo")
    fetched_at = Column(DateTime, nullable=False)
    country = Column(String)
    level = Column(Integer)


class KernelEloRating(KernelBase):
    """Self-computed Elo ratings for sports without external Elo sources.

    Used by NBA (basketball) where no external Elo API exists. Follows
    the kernel_ prefix convention. Can be reused for future self-computed
    Elo in other sports.
    """
    __tablename__ = "kernel_elo_ratings"

    team_name = Column(String, primary_key=True)
    sport = Column(String, nullable=False)
    competition = Column(String, nullable=False)
    elo_rating = Column(Float, nullable=False)
    source = Column(String, default="self_computed")
    updated_at = Column(DateTime, nullable=False)


class KernelSportMarketLink(KernelBase):
    """Link between a sports match (match_id) and a prediction-market contract.

    Fail-closed: downstream consumers must use get_verified_links which
    returns only verified=True rows. Unique on (match_id, contract_id,
    outcome_label) so one match can carry multiple outcome rows without dupes.
    """
    __tablename__ = "kernel_sport_market_links"
    __table_args__ = (
        UniqueConstraint("match_id", "contract_id", "outcome_label", name="uq_sport_market_link"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String, nullable=False, index=True)
    contract_id = Column(String, nullable=False, index=True)
    source = Column(String, nullable=False)  # "polymarket" | "odds_api"
    outcome_label = Column(String, nullable=False)  # "YES" | "NO" | "home" | "away" | "draw"
    mapped_outcome = Column(String, nullable=False)  # "home_win" | "away_win" | "draw"
    link_method = Column(String, nullable=False)  # "rule" | "llm" | "odds_api" | "manual"
    link_confidence = Column(Float, nullable=False, default=0.0)
    verified = Column(Integer, nullable=False, default=0, index=True)
    market_question = Column(String)
    implied_prob = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class KernelMarketSnapshot(KernelBase):
    """Price time-series for a sport market link (append-only)."""
    __tablename__ = "kernel_market_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    link_id = Column(Integer, nullable=False, index=True)
    implied_prob = Column(Float, nullable=False)
    price = Column(Float)
    liquidity = Column(Float)
    volume = Column(Float)
    captured_at = Column(DateTime)


class KernelSportEdge(KernelBase):
    """Edge snapshot time-series for sports matches (append-only).

    One row per (match_id, mapped_outcome, captured_at). raw_edge can be
    negative (model predicts lower than market). spread is None for now
    (known limitation: requires both YES and NO prices on separate links).
    """
    __tablename__ = "kernel_sport_edges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String, nullable=False, index=True)
    mapped_outcome = Column(String, nullable=False)  # "home_win" | "draw" | "away_win"
    model_prob = Column(Float, nullable=False)        # 0-1
    market_prob = Column(Float, nullable=False)       # 0-1, liquidity-weighted
    raw_edge = Column(Float, nullable=False)          # model_prob - market_prob, -1.0 to +1.0
    trust = Column(Float, nullable=False)             # 0-1, from KernelCalibration
    liquidity_factor = Column(Float, nullable=False)  # 0-1
    adjusted_edge = Column(Float, nullable=False)     # raw_edge * trust * liquidity_factor
    spread = Column(Float, nullable=True)             # Polymarket YES+NO-1; None for traditional odds
    sources_count = Column(Integer, nullable=False)
    stale = Column(Boolean, nullable=False, default=False)
    captured_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_kernel_sport_edges_match_outcome_captured", "match_id", "mapped_outcome", "captured_at"),
    )


class KernelMarketSettlement(KernelBase):
    """One settlement record per (match_id, mapped_outcome).

    Records the market's settlement price (last snapshot before match finished)
    and the error against B's persisted model_prob. Idempotent via unique
    constraint on (match_id, mapped_outcome).
    """
    __tablename__ = "kernel_market_settlements"
    __table_args__ = (
        UniqueConstraint("match_id", "mapped_outcome", name="uq_market_settlement_match_outcome"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String, nullable=False, index=True)
    mapped_outcome = Column(String, nullable=False)
    engine = Column(String, nullable=False)
    competition = Column(String, nullable=False)
    settlement_implied_prob = Column(Float)
    settlement_captured_at = Column(DateTime)
    link_id = Column(Integer)
    model_prob = Column(Float)
    market_prob_at_detection = Column(Float)
    raw_edge = Column(Float)
    adjusted_edge = Column(Float)
    brier_score = Column(Float)
    signed_error = Column(Float)
    direction_correct = Column(Integer)
    status = Column(String, nullable=False, default="processed")
    skip_reason = Column(String)
    match_finished_at = Column(DateTime, nullable=False)
    processed_at = Column(DateTime, nullable=False)


class KernelMarketCalibration(KernelBase):
    """Market-settlement-based calibration per (engine, competition).

    Parallel to KernelCalibration (which uses match-outcome-based learning).
    Fitted by linear regression: settlement_implied_prob ~ slope * model_prob + intercept.
    """
    __tablename__ = "kernel_market_calibrations"
    __table_args__ = (
        UniqueConstraint("engine", "competition", name="uq_market_calibration_engine_competition"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    engine = Column(String(50), nullable=False)
    competition = Column(String(50), nullable=False)
    slope = Column(Float, nullable=False, default=1.0)
    intercept = Column(Float, nullable=False, default=0.0)
    sample_count = Column(Integer, nullable=False, default=0)
    avg_brier = Column(Float, nullable=False, default=0.0)
    avg_signed_error = Column(Float, nullable=False, default=0.0)
    direction_accuracy = Column(Float, nullable=False, default=0.0)
    last_updated = Column(DateTime, nullable=False)


class KernelTraditionalOddsSnapshot(KernelBase):
    """Traditional sportsbook odds snapshot (separate from Polymarket snapshots).

    No link_id — traditional odds bypass the three-layer matching engine.
    Unique constraint on (match_id, mapped_outcome, captured_at) for idempotent
    scheduler retries.
    """
    __tablename__ = "kernel_traditional_odds_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "match_id", "mapped_outcome", "captured_at",
            name="uq_traditional_odds_match_outcome_time"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String, nullable=False, index=True)
    mapped_outcome = Column(String, nullable=False)
    competition = Column(String, nullable=False)
    implied_prob = Column(Float, nullable=False)
    decimal_odds = Column(Float, nullable=False)
    bookmaker = Column(String, nullable=True)
    bookmakers_count = Column(Integer, default=0)
    captured_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class KernelOptimizedParams(KernelBase):
    """Stores optimized parameter sets from Phase 9 backtesting."""
    __tablename__ = "kernel_optimized_params"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sport = Column(String, nullable=False, index=True)
    competition = Column(String, nullable=False, index=True)
    factor_weights = Column(Text, nullable=False)  # JSON
    elo_params = Column(Text, nullable=False)  # JSON
    score = Column(Float, nullable=False)
    accuracy = Column(Float, nullable=False)
    brier_score = Column(Float, nullable=False)
    mae = Column(Float, nullable=False)
    sample_count = Column(Integer, nullable=False)
    trial_number = Column(Integer, nullable=True)
    status = Column(String, default="candidate")  # candidate / applied / archived
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    applied_at = Column(DateTime, nullable=True)


class KernelFuturesLink(KernelBase):
    """Futures/championship market link (competition+season+team -> contract).

    Distinct from KernelSportMarketLink which is match-level (match_id).
    Futures markets are season-level: one event -> N contracts (one per team).
    """
    __tablename__ = "kernel_futures_links"
    __table_args__ = (
        UniqueConstraint(
            "competition", "season", "team", "source",
            name="uq_futures_links_comp_season_team_source"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    competition = Column(String, nullable=False, index=True)
    season = Column(String, nullable=False, index=True)
    team = Column(String, nullable=False)
    contract_id = Column(String, nullable=False)
    source = Column(String, nullable=False)
    market_question = Column(String, nullable=True)
    implied_prob = Column(Float, nullable=True)
    verified = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class KernelFuturesSnapshot(KernelBase):
    """Price snapshot for a futures link (one row per capture)."""
    __tablename__ = "kernel_futures_snapshots"
    __table_args__ = (
        Index("ix_futures_snapshots_link_id", "link_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    link_id = Column(Integer, nullable=False)
    implied_prob = Column(Float, nullable=False)
    price = Column(Float, nullable=True)
    liquidity = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    captured_at = Column(DateTime, nullable=False)


def _get_engine(db_path: str):
    """Create a SQLAlchemy engine for an isolated SQLite DB (used by tests)."""
    return create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})


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

    # Phase 3 migration: drop dormant tables with old schema so create_all
    # recreates them with the new columns. Safe because these tables were
    # never written to in Phase 1/2.
    _migrate_dormant_tables(_engine)

    KernelBase.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    logger.info("Kernel DB initialized at %s", db_path)


def _migrate_dormant_tables(engine) -> None:
    """Drop dormant tables that have old schema so they get recreated.

    Detects old schema by checking if kernel_factors has factor_id as its
    primary key (old) instead of id (new). If old schema detected, drops
    kernel_factors, kernel_engine_scores, kernel_prediction_history so
    create_all recreates them with the new schema.
    """
    from sqlalchemy import inspect as sqlinspect
    from sqlalchemy import text

    inspector = sqlinspect(engine)
    table_names = inspector.get_table_names()

    if "kernel_factors" not in table_names:
        return  # Fresh DB — create_all will build everything correctly

    # Check if kernel_factors has old schema (factor_id as PK, no id column)
    factors_pk = inspector.get_pk_constraint("kernel_factors")
    pk_cols = factors_pk.get("constrained_columns", [])

    if "id" in pk_cols:
        return  # Already has new schema

    # Old schema detected — drop the three dormant tables
    logger.info("Phase 3 migration: dropping dormant tables with old schema")
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS kernel_factors"))
        conn.execute(text("DROP TABLE IF EXISTS kernel_engine_scores"))
        conn.execute(text("DROP TABLE IF EXISTS kernel_prediction_history"))
        conn.commit()


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


# Alias: disposes the engine + resets module state so the next init_kernel_db
# call rebuilds the schema. Used by tests that point the kernel DB at a fresh
# tmp_path SQLite file per test (mirrors the kernel_db fixture pattern).
close_kernel_db = close_kernel_session


def get_latest_prediction(match_id: str) -> KernelPrediction | None:
    """Get the latest prediction for a match from the kernel_predictions table.

    The table uses match_id as primary key, so each match has at most one row
    (updated on each prediction).
    """
    session = get_kernel_session()
    try:
        return session.query(KernelPrediction).filter_by(match_id=match_id).one_or_none()
    except Exception:
        return None
    finally:
        session.close()


def get_calibration(engine_name: str, competition: str) -> KernelCalibration | None:
    """Read sports calibration for trust computation.

    Returns None if no row exists (cold start). Used by EdgeDetectorService
    to compute trust from KernelCalibration.avg_accuracy. Does NOT modify
    the KernelCalibration table.
    """
    session = get_kernel_session()
    try:
        return (
            session.query(KernelCalibration)
            .filter_by(engine=engine_name, competition=competition)
            .one_or_none()
        )
    except Exception:
        return None
    finally:
        session.close()


def get_match_ids_with_predictions(match_ids: list[str]) -> set[str]:
    """Batch query: return the subset of match_ids that have a prediction row.

    Used by the list endpoint to populate has_prediction without N+1 queries.
    """
    if not match_ids:
        return set()
    session = get_kernel_session()
    try:
        rows = session.query(KernelPrediction.match_id).filter(
            KernelPrediction.match_id.in_(match_ids)
        ).all()
        return {row[0] for row in rows}
    except Exception:
        return set()
    finally:
        session.close()


# --- Learning Dashboard query functions ---


def get_engine_scores(engine: str | None = None,
                      competition: str | None = None,
                      sport: str | None = None) -> list[KernelEngineScore]:
    """Get engine performance scores, optionally filtered.

    Args:
        engine: Filter by engine name.
        competition: Filter by competition code.
        sport: Filter by sport code — reverse-lookup via COMPETITION_SPORT
               mapping (defined in predictions.py to avoid circular import).
               Here we accept sport and convert to competition list.

    Note: COMPETITION_SPORT is imported lazily to avoid circular dependency.
    """
    session = get_kernel_session()
    try:
        query = session.query(KernelEngineScore)
        if engine is not None:
            query = query.filter(KernelEngineScore.engine == engine)
        if competition is not None:
            query = query.filter(KernelEngineScore.competition == competition)
        if sport is not None:
            # Reverse-lookup: sport → competition list
            from app.api.routes.predictions import COMPETITION_SPORT
            competitions = [c for c, s in COMPETITION_SPORT.items() if s == sport]
            if competitions:
                query = query.filter(KernelEngineScore.competition.in_(competitions))
            else:
                return []  # No matching competitions
        return query.all()
    except Exception:
        return []
    finally:
        session.close()


def get_prediction_history(sport: str | None = None,
                           competition: str | None = None,
                           limit: int = 50,
                           offset: int = 0) -> tuple[list[dict], int]:
    """Get prediction history with optional filters, paginated.

    Returns (items, total) where items is a list of dicts with history +
    outcome data, and total is the unpaginated count.
    """
    session = get_kernel_session()
    try:
        # Build base query with JOINs
        query = (
            session.query(KernelPredictionHistory, KernelMatchOutcome, KernelPrediction)
            .outerjoin(KernelMatchOutcome,
                       KernelPredictionHistory.match_id == KernelMatchOutcome.match_id)
            .outerjoin(KernelPrediction,
                       KernelPredictionHistory.match_id == KernelPrediction.match_id)
        )

        # Apply filters on KernelPrediction
        if sport is not None:
            query = query.filter(KernelPrediction.sport == sport)
        if competition is not None:
            query = query.filter(KernelPrediction.competition == competition)

        # Get total count (before pagination)
        total = query.count()

        # Apply pagination + ordering
        rows = (
            query
            .order_by(KernelPredictionHistory.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        items = []
        for hist, outcome, pred in rows:
            item = {
                "id": hist.id,
                "match_id": hist.match_id,
                "sport": pred.sport if pred else None,
                "competition": pred.competition if pred else None,
                "engine": hist.engine,
                "predicted_scores": hist.predicted_scores,
                "outcome_probabilities": hist.outcome_probabilities,
                "confidence": hist.confidence,
                "feature_version": hist.feature_version,
                "trigger": hist.trigger,
                "created_at": hist.created_at.isoformat() if hist.created_at else None,
                "outcome": None,
            }
            if outcome is not None:
                item["outcome"] = {
                    "home_score": outcome.home_score,
                    "away_score": outcome.away_score,
                    "outcome": outcome.outcome,
                    "outcome_correct": outcome.outcome_correct,
                    "score_mae": outcome.score_mae,
                    "brier_score": outcome.brier_score,
                    "finished_at": outcome.finished_at.isoformat() if outcome.finished_at else None,
                }
            items.append(item)

        return items, total
    except Exception:
        return [], 0
    finally:
        session.close()


def get_prediction_history_by_match(match_id: str) -> dict:
    """Get all prediction history records for a single match, time-sorted ASC.

    Returns {match_id, sport, competition, items, count}.
    Returns empty items (NOT 404) when match_id has no history.
    """
    session = get_kernel_session()
    try:
        rows = (
            session.query(KernelPredictionHistory, KernelPrediction)
            .outerjoin(KernelPrediction,
                       KernelPredictionHistory.match_id == KernelPrediction.match_id)
            .filter(KernelPredictionHistory.match_id == match_id)
            .order_by(KernelPredictionHistory.created_at.asc())
            .all()
        )

        sport = None
        competition = None
        items = []
        for hist, pred in rows:
            if pred is not None and sport is None:
                sport = pred.sport
                competition = pred.competition
            items.append({
                "id": hist.id,
                "match_id": hist.match_id,
                "sport": pred.sport if pred else None,
                "competition": pred.competition if pred else None,
                "engine": hist.engine,
                "predicted_scores": hist.predicted_scores,
                "outcome_probabilities": hist.outcome_probabilities,
                "confidence": hist.confidence,
                "feature_version": hist.feature_version,
                "trigger": hist.trigger,
                "created_at": hist.created_at.isoformat() if hist.created_at else None,
                "outcome": None,  # trajectory doesn't need outcome per record
            })

        return {
            "match_id": match_id,
            "sport": sport,
            "competition": competition,
            "items": items,
            "count": len(items),
        }
    except Exception:
        return {"match_id": match_id, "sport": None, "competition": None, "items": [], "count": 0}
    finally:
        session.close()


def get_calibrations(engine: str | None = None,
                     competition: str | None = None) -> list[KernelCalibration]:
    """Get calibration parameters, optionally filtered."""
    session = get_kernel_session()
    try:
        query = session.query(KernelCalibration)
        if engine is not None:
            query = query.filter(KernelCalibration.engine == engine)
        if competition is not None:
            query = query.filter(KernelCalibration.competition == competition)
        return query.all()
    except Exception:
        return []
    finally:
        session.close()


def compute_reliability_bins(engine: str | None = None,
                             competition: str | None = None,
                             bins: int = 10) -> dict:
    """Compute binned reliability data on-the-fly.

    Bins predictions by max(outcome_probabilities) and compares to actual
    outcome_correct frequency. Returns bins with avg_predicted,
    actual_frequency, and count per bin.

    Empty bins (count=0) return avg_predicted=null, actual_frequency=null.
    """
    session = get_kernel_session()
    try:
        query = (
            session.query(KernelPrediction, KernelMatchOutcome)
            .join(KernelMatchOutcome,
                  KernelPrediction.match_id == KernelMatchOutcome.match_id)
            .filter(KernelMatchOutcome.outcome_correct.isnot(None))
        )
        if engine is not None:
            query = query.filter(KernelPrediction.engine == engine)
        if competition is not None:
            query = query.filter(KernelPrediction.competition == competition)

        rows = query.all()

        # Initialize bins
        bin_width = 1.0 / bins
        bin_list = []
        for i in range(bins):
            lower = i * bin_width
            upper = (i + 1) * bin_width
            bin_list.append({
                "lower": round(lower, 4),
                "upper": round(upper, 4),
                "center": round((lower + upper) / 2, 4),
                "avg_predicted": None,
                "actual_frequency": None,
                "count": 0,
            })

        # Accumulate per bin
        bin_sums = [{"predicted_sum": 0.0, "actual_sum": 0.0, "count": 0} for _ in range(bins)]
        for pred, outcome in rows:
            probs = pred.outcome_probabilities or {}
            if not probs:
                continue
            predicted_prob = max(probs.values())
            actual = outcome.outcome_correct  # 1 or 0

            # Determine bin index (clamp to last bin for prob=1.0).
            # Use multiplication instead of division by bin_width to avoid
            # float truncation bugs (e.g. 0.3 / 0.1 = 2.9999... -> bin 2).
            bin_idx = min(int(predicted_prob * bins), bins - 1)
            bin_sums[bin_idx]["predicted_sum"] += predicted_prob
            bin_sums[bin_idx]["actual_sum"] += actual
            bin_sums[bin_idx]["count"] += 1

        # Finalize bin values
        for i, bs in enumerate(bin_sums):
            if bs["count"] > 0:
                bin_list[i]["avg_predicted"] = round(bs["predicted_sum"] / bs["count"], 4)
                bin_list[i]["actual_frequency"] = round(bs["actual_sum"] / bs["count"], 4)
                bin_list[i]["count"] = bs["count"]

        return {
            "engine": engine,
            "competition": competition,
            "bins": bin_list,
            "total_samples": len(rows),
        }
    except Exception:
        return {
            "engine": engine,
            "competition": competition,
            "bins": [],
            "total_samples": 0,
        }
    finally:
        session.close()
