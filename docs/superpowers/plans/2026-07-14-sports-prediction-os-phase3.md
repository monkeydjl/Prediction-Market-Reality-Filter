# Sports Prediction OS — Phase 3: Unified Learning Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the complete closed-loop learning system (outcome → error → calibration → weight update → engine score → next prediction) that drives prediction accuracy from ~67% to 72-75%+.

**Architecture:** EWMA weight update (α=0.1) for per-competition factor weights, linear regression calibration model, EngineScore DB persistence, and dynamic engine selection — all gated by `PHASE3_LEARNING_ENABLED` (default OFF). Three dormant DB tables are migrated with schema changes; one new `KernelCalibration` table is added.

**Tech Stack:** Python 3.11+, SQLAlchemy ORM, SQLite, Pydantic, FastAPI, pytest

## Global Constraints

1. `LearningService` Protocol signatures unchanged (6 methods: `record_prediction`, `record_outcome`, `compute_error`, `update_calibration`, `update_weights`, `engine_score`)
2. `PredictionEngine` Protocol signature unchanged (`predict(features, match)`)
3. `EngineRegistry.select` changes from `select(strategy, features)` to `select(engine_name, competition=None)` — existing callers must be updated
4. `PHASE3_LEARNING_ENABLED` defaults to OFF (false)
5. `EloOddsEngine` without `FactorRegistry` falls back to 0.30/0.70
6. New table `KernelCalibration` uses `kernel_` prefix
7. Weight clamp range: [0.05, 0.95]
8. Calibration slope clamp: [0.0, 2.0], intercept clamp: [-0.5, 0.5] — hardcoded constants in `learning_service.py`, not configurable env vars
9. Frontend pages must NOT be modified
10. All Phase 1 + Phase 2 tests pass (zero regression)
11. `record_prediction` history write must not affect existing `KernelPrediction` upsert
12. `FactorRegistry` constructor remains compatible with no-arg construction (`session_factory` has default `None`)
13. `ContributionItem.predicted_outcome` is a new field with default `None` — backward compatible
14. `KernelFactor` schema: auto-increment `id` PK + unique `(factor_id, competition)` — dormant table, safe to drop and recreate
15. `KernelEngineScore` adds `confidence_calibration` column — dormant table, safe to drop and recreate
16. `KernelPredictionHistory` adds `feature_version` column — dormant table, safe to drop and recreate
17. The same `KernelLearningService` instance must be shared between `EngineRegistry` and `PredictionKernel` in `_get_kernel()`
18. `predicted_scores` and `outcome_probabilities` are stored as dicts (JSON column auto-serializes), not `json.dumps()` strings
19. All test files go in `backend/tests/` directory
20. All tests use in-memory or temp SQLite DB with per-test isolation (same pattern as existing `test_kernel_learning_service.py`)

---

## File Structure

### Modified Files

| File | Responsibility | Task |
|------|---------------|------|
| `backend/app/kernel/domain.py` | Add `predicted_outcome` field to `ContributionItem` | 1 |
| `backend/app/kernel/kernel_db.py` | New `KernelCalibration` table, schema changes for 3 dormant tables, migration logic in `init_kernel_db()` | 1 |
| `backend/app/kernel/factor_registry.py` | DB persistence: `_load_from_db()`, `_init_default_factors()`, `update_weight()` DB upsert | 2 |
| `backend/app/kernel/engines/elo_odds_engine.py` | Constructor injection of `FactorRegistry`, read weights, set `predicted_outcome` in explanation | 3 |
| `backend/app/kernel/learning_service.py` | Implement `update_calibration()`, `update_weights()`, enhance `engine_score()` + `record_prediction()`, accept `FactorRegistry` | 4, 5, 6 |
| `backend/app/kernel/engine_registry.py` | `LearningService` injection, dynamic `select("auto")` | 7 |
| `backend/app/kernel/prediction_kernel.py` | Update `select()` call, complete `process_outcome()` | 7 |
| `backend/app/core/config.py` | `PHASE3_LEARNING_ENABLED` + learning parameters | 7 |
| `backend/app/api/routes/predictions.py` | `_get_kernel()` shared instances + FactorRegistry injection | 7 |
| `backend/tests/test_kernel_engine_registry.py` | Update existing tests for new `select()` signature | 7 |

### New Test Files

| File | Tests | Task |
|------|-------|------|
| `backend/tests/test_db_migration.py` | 2 | 1 |
| `backend/tests/test_factor_registry_persistence.py` | 5 | 2 |
| `backend/tests/test_elo_engine_weights.py` | 5 | 3 |
| `backend/tests/test_learning_calibration.py` | 7 | 4 |
| `backend/tests/test_learning_weights.py` | 8 | 5 |
| `backend/tests/test_engine_score_persistence.py` | 5 | 6 |
| `backend/tests/test_prediction_history.py` | 3 | 6 |
| `backend/tests/test_engine_dynamic_selection.py` | 4 | 7 |
| `backend/tests/test_process_outcome_full.py` | 5 | 7 |

**Total: 44 new tests across 9 test files**

---

### Task 1: DB Schema Migration + ContributionItem Field

**Files:**
- Modify: `backend/app/kernel/kernel_db.py`
- Modify: `backend/app/kernel/domain.py`
- Test: `backend/tests/test_db_migration.py`

**Interfaces:**
- Consumes: existing `KernelBase`, `init_kernel_db()`, `get_kernel_session()` from `kernel_db.py`
- Produces: `KernelCalibration` model class, updated `KernelFactor`/`KernelEngineScore`/`KernelPredictionHistory` models, migration logic in `init_kernel_db()`, `ContributionItem.predicted_outcome` field

- [ ] **Step 1: Write the failing test for DB migration**

```python
# backend/tests/test_db_migration.py
"""Tests for Phase 3 DB schema migration."""
import pytest
from sqlalchemy import inspect, text

from app.kernel.kernel_db import (
    init_kernel_db, close_kernel_session, get_kernel_session,
    KernelBase, KernelFactor, KernelEngineScore, KernelPredictionHistory,
    KernelCalibration,
)


@pytest.fixture
def fresh_db(tmp_path):
    """Create a fresh kernel DB."""
    db_path = str(tmp_path / "kernel_test.db")
    init_kernel_db(db_path)
    yield db_path
    close_kernel_session()


class TestDBMigration:
    def test_new_schema_has_expected_columns(self, fresh_db):
        """After init_kernel_db, all Phase 3 schema changes are present."""
        session = get_kernel_session()
        engine = session.bind

        # KernelFactor: should have 'id' PK + no single factor_id PK
        inspector = inspect(engine)
        factors_cols = {c['name']: c for c in inspector.get_columns('kernel_factors')}
        assert 'id' in factors_cols
        assert 'factor_id' in factors_cols
        assert 'competition' in factors_cols

        # KernelEngineScore: should have 'confidence_calibration'
        scores_cols = {c['name']: c for c in inspector.get_columns('kernel_engine_scores')}
        assert 'confidence_calibration' in scores_cols

        # KernelPredictionHistory: should have 'feature_version'
        history_cols = {c['name']: c for c in inspector.get_columns('kernel_prediction_history')}
        assert 'feature_version' in history_cols

        # KernelCalibration: new table exists
        cal_tables = [t for t in inspector.get_table_names() if t == 'kernel_calibration']
        assert len(cal_tables) == 1
        cal_cols = {c['name']: c for c in inspector.get_columns('kernel_calibration')}
        assert 'slope' in cal_cols
        assert 'intercept' in cal_cols
        assert 'sample_count' in cal_cols

        session.close()

    def test_calibration_upsert_roundtrip(self, fresh_db):
        """KernelCalibration rows can be inserted and queried."""
        from datetime import datetime, timezone
        session = get_kernel_session()
        try:
            cal = KernelCalibration(
                engine="elo_odds", competition="world_cup",
                slope=1.1, intercept=-0.05,
                sample_count=15, avg_confidence=0.65, avg_accuracy=0.70,
                last_updated=datetime.now(timezone.utc),
            )
            session.add(cal)
            session.commit()

            result = session.query(KernelCalibration).filter_by(
                engine="elo_odds", competition="world_cup"
            ).first()
            assert result is not None
            assert result.slope == 1.1
            assert result.intercept == -0.05
            assert result.sample_count == 15
        finally:
            session.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_db_migration.py -v`
Expected: FAIL — `KernelCalibration` not found, `confidence_calibration` column missing, `feature_version` column missing

- [ ] **Step 3: Add `predicted_outcome` to `ContributionItem` in domain.py**

In `backend/app/kernel/domain.py`, add `predicted_outcome` field to `ContributionItem`:

```python
@dataclass(frozen=True)
class ContributionItem:
    """A single factor contribution in a prediction explanation."""
    factor: str
    direction: str
    weight: float
    available: bool
    detail: str | None
    predicted_outcome: str | None = None
```

The field goes after `detail` with default `None` to maintain backward compatibility.

- [ ] **Step 4: Update `kernel_db.py` — add `KernelCalibration`, change 3 dormant table schemas, add migration logic**

Add `UniqueConstraint` to imports:
```python
from sqlalchemy import Column, String, Float, Integer, Text, DateTime, JSON, UniqueConstraint
```

Replace the `KernelFactor` class:
```python
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
```

Replace the `KernelEngineScore` class — add `confidence_calibration`:
```python
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
```

Replace the `KernelPredictionHistory` class — add `feature_version`:
```python
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
```

Add `KernelCalibration` class (after `KernelFactor`):
```python
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
```

Add migration logic inside `init_kernel_db()`, before `KernelBase.metadata.create_all(_engine)`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_db_migration.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Run existing kernel tests to verify no regression**

Run: `cd backend && python -m pytest tests/test_kernel_domain.py tests/test_kernel_db_fixtures.py tests/test_kernel_learning_service.py tests/test_kernel_factor_registry.py tests/test_kernel_engine_registry.py -v`
Expected: All existing tests PASS

- [ ] **Step 7: Commit**

```bash
cd backend
git add app/kernel/kernel_db.py app/kernel/domain.py tests/test_db_migration.py
git commit -m "feat(kernel): Phase 3 DB schema — KernelCalibration table, dormant table migration, ContributionItem.predicted_outcome"
```

---

### Task 2: FactorRegistry DB Persistence

**Files:**
- Modify: `backend/app/kernel/factor_registry.py`
- Test: `backend/tests/test_factor_registry_persistence.py`

**Interfaces:**
- Consumes: `KernelFactor` model and `get_kernel_session` from `kernel_db.py` (Task 1), `FactorConfig` dataclass (existing)
- Produces: `FactorRegistry` with DB-backed `__init__(session_factory=None)`, `_load_from_db()`, `_init_default_factors()`, `update_weight()` with DB upsert — later tasks (3, 5, 7) rely on `FactorRegistry()` working with no args and `get_weight()`/`update_weight()` persisting to DB

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_factor_registry_persistence.py
"""Tests for FactorRegistry DB persistence (Phase 3)."""
from datetime import datetime, timezone
import pytest

from app.kernel.kernel_db import init_kernel_db, close_kernel_session, get_kernel_session, KernelFactor
from app.kernel.factor_registry import FactorRegistry, FactorConfig


@pytest.fixture
def db_registry(tmp_path):
    """Create a FactorRegistry backed by a temp DB."""
    db_path = str(tmp_path / "kernel_test.db")
    init_kernel_db(db_path)
    registry = FactorRegistry()
    yield registry
    close_kernel_session()


class TestFactorRegistryPersistence:
    def test_init_default_factors_on_empty_db(self, db_registry):
        """FactorRegistry() on empty DB initializes elo=0.30, odds=0.70."""
        assert db_registry.get_weight("elo", "world_cup") == 0.30
        assert db_registry.get_weight("odds", "world_cup") == 0.70

    def test_load_from_db_on_construction(self, tmp_path):
        """FactorRegistry loads existing factors from DB on construction."""
    def test_load_from_db_on_construction(self, tmp_path):
        """FactorRegistry loads existing factors from DB on construction."""
        db_path = str(tmp_path / "kernel_test.db")
        init_kernel_db(db_path)

        # Write a factor directly to DB
        session = get_kernel_session()
        session.add(KernelFactor(
            factor_id="elo", category="elo_rating", version="1.0",
            weight=0.45, competition="epl", enabled=1,
            source="manual", updated_at=datetime.now(timezone.utc),
        ))
        session.commit()
        session.close()

        # New FactorRegistry should load it
        registry = FactorRegistry()
        assert registry.get_weight("elo", "epl") == 0.45
        close_kernel_session()

    def test_update_weight_persists_to_db(self, db_registry):
        """update_weight writes to KernelFactor table."""
        db_registry.update_weight("elo", "epl", 0.40, "ewma")

        session = get_kernel_session()
        row = session.query(KernelFactor).filter_by(
            factor_id="elo", competition="epl"
        ).first()
        assert row is not None
        assert row.weight == 0.40
        assert row.source == "ewma"
        session.close()

        # New registry instance sees the persisted weight
        close_kernel_session()
        init_kernel_db()  # Re-init with same DB path (already set)
        registry2 = FactorRegistry()
        assert registry2.get_weight("elo", "epl") == 0.40

    def test_competition_fallback_to_global(self, db_registry):
        """get_weight falls back to global (competition=None) when no competition-specific weight."""
        # Default factors are global (competition=None)
        assert db_registry.get_weight("elo", "unknown_comp") == 0.30

    def test_update_weight_upsert(self, db_registry):
        """update_weight on existing factor updates it, doesn't create duplicate."""
        db_registry.update_weight("elo", "epl", 0.35, "ewma")
        db_registry.update_weight("elo", "epl", 0.40, "ewma")

        session = get_kernel_session()
        rows = session.query(KernelFactor).filter_by(
            factor_id="elo", competition="epl"
        ).all()
        assert len(rows) == 1
        assert rows[0].weight == 0.40
        session.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_factor_registry_persistence.py -v`
Expected: FAIL — `FactorRegistry()` doesn't accept DB-backed initialization, `_load_from_db` doesn't exist

- [ ] **Step 3: Implement DB-backed FactorRegistry**

Replace the entire `FactorRegistry` class in `backend/app/kernel/factor_registry.py`:

```python
# backend/app/kernel/factor_registry.py
"""Factor weight and lifecycle management."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable

from app.kernel.kernel_db import get_kernel_session, KernelFactor


@dataclass(frozen=True)
class FactorConfig:
    factor_id: str
    category: str
    version: str
    weight: float
    competition: str | None
    enabled: bool
    source: str
    updated_at: datetime


class FactorRegistry:
    """Manages factor weights per competition with DB persistence.

    Supports differentiated weights: e.g., the 'elo' factor can have
    weight 0.30 globally but 0.40 for EPL. Weights are persisted to the
    KernelFactor table and loaded on construction.
    """

    def __init__(self, session_factory: Callable | None = None) -> None:
        self._session_factory = session_factory or get_kernel_session
        # Key: (factor_id, competition | None) -> FactorConfig
        # competition=None means global default
        self._factors: dict[tuple[str, str | None], FactorConfig] = {}
        self._load_from_db()
        if not self._factors:
            self._init_default_factors()

    def _load_from_db(self) -> None:
        """Load all factors from KernelFactor table."""
        session = self._session_factory()
        try:
            rows = session.query(KernelFactor).all()
            for row in rows:
                key = (row.factor_id, row.competition)
                self._factors[key] = FactorConfig(
                    factor_id=row.factor_id,
                    category=row.category,
                    version=row.version,
                    weight=row.weight,
                    competition=row.competition,
                    enabled=bool(row.enabled),
                    source=row.source,
                    updated_at=row.updated_at or datetime.now(timezone.utc),
                )
        finally:
            session.close()

    def _init_default_factors(self) -> None:
        """Register elo (0.30) and odds (0.70) as global defaults if DB is empty."""
        now = datetime.now(timezone.utc)
        defaults = [
            FactorConfig("elo", "elo_rating", "1.0", 0.30, None, True, "default", now),
            FactorConfig("odds", "market_odds", "1.0", 0.70, None, True, "default", now),
        ]
        session = self._session_factory()
        try:
            for fc in defaults:
                row = KernelFactor(
                    factor_id=fc.factor_id, category=fc.category,
                    version=fc.version, weight=fc.weight,
                    competition=fc.competition, enabled=1,
                    source=fc.source, updated_at=now,
                )
                session.add(row)
                self._factors[(fc.factor_id, fc.competition)] = fc
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def register_factor(self, factor: FactorConfig) -> None:
        """Register a factor in memory (does not persist to DB)."""
        key = (factor.factor_id, factor.competition)
        self._factors[key] = factor

    def get_weight(self, factor_id: str, competition: str) -> float:
        """Get weight for a factor in a competition.

        Falls back to global (competition=None) if no competition-specific
        weight exists. Returns 1.0 as default if factor is unknown.
        """
        comp_factor = self._factors.get((factor_id, competition))
        if comp_factor is not None and comp_factor.enabled:
            return comp_factor.weight
        global_factor = self._factors.get((factor_id, None))
        if global_factor is not None and global_factor.enabled:
            return global_factor.weight
        return 1.0

    def update_weight(
        self, factor_id: str, competition: str,
        new_weight: float, source: str,
    ) -> None:
        """Update weight in memory and persist to KernelFactor table."""
        key = (factor_id, competition)
        existing = self._factors.get(key)
        now = datetime.now(timezone.utc)

        if existing is not None:
            updated = replace(
                existing, weight=new_weight, source=source, updated_at=now,
            )
            self._factors[key] = updated
        else:
            # Try to get category from global default
            global_fc = self._factors.get((factor_id, None))
            category = global_fc.category if global_fc else "unknown"
            version = global_fc.version if global_fc else "1.0"
            self._factors[key] = FactorConfig(
                factor_id=factor_id, category=category, version=version,
                weight=new_weight, competition=competition,
                enabled=True, source=source, updated_at=now,
            )

        # Persist to DB
        session = self._session_factory()
        try:
            row = session.query(KernelFactor).filter_by(
                factor_id=factor_id, competition=competition,
            ).first()
            if row is not None:
                row.weight = new_weight
                row.source = source
                row.updated_at = now
            else:
                fc = self._factors[key]
                row = KernelFactor(
                    factor_id=fc.factor_id, category=fc.category,
                    version=fc.version, weight=new_weight,
                    competition=competition, enabled=1,
                    source=source, updated_at=now,
                )
                session.add(row)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_active(self, competition: str) -> list[FactorConfig]:
        """List active factors for a competition (global + competition-specific)."""
        result: dict[str, FactorConfig] = {}
        for (fid, comp), factor in self._factors.items():
            if not factor.enabled:
                continue
            if comp == competition:
                result[fid] = factor
            elif comp is None and fid not in result:
                result[fid] = factor
        return list(result.values())
```

- [ ] **Step 4: Run new tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_factor_registry_persistence.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run existing factor registry tests to verify no regression**

Run: `cd backend && python -m pytest tests/test_kernel_factor_registry.py -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/kernel/factor_registry.py tests/test_factor_registry_persistence.py
git commit -m "feat(kernel): FactorRegistry DB persistence — load from/init/write to KernelFactor table"
```

---

### Task 3: EloOddsEngine Weight Reading + predicted_outcome

**Files:**
- Modify: `backend/app/kernel/engines/elo_odds_engine.py`
- Test: `backend/tests/test_elo_engine_weights.py`

**Interfaces:**
- Consumes: `FactorRegistry` from Task 2, `ContributionItem.predicted_outcome` from Task 1
- Produces: `EloOddsEngine(factor_registry=None)` constructor, `predict()` reads weights from registry and sets `predicted_outcome` in explanation — Tasks 5 and 7 rely on this

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_elo_engine_weights.py
"""Tests for EloOddsEngine FactorRegistry integration (Phase 3)."""
from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures,
)
from app.kernel.engines.elo_odds_engine import EloOddsEngine


def _make_match(competition_code="world_cup") -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code=competition_code, name="Test", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id="m1", season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


def _make_features() -> FeatureSet:
    return FeatureSet(
        match=_make_match(),
        general=GeneralFeatures(None, None, None, None),
        team=TeamFeatures(1900, 1800, None, None, None, None, None, None),
        market=MarketFeatures(2.0, 3.0, 4.0, "test", True),
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures(None, None, None, False),
        custom={}, data_quality="real", quality_notes=[], feature_version="1.0",
    )


class TestEloOddsEngineWeights:
    def test_reads_weights_from_registry(self):
        """Engine uses weights from FactorRegistry instead of hardcoded 0.30/0.70."""
        mock_reg = MagicMock()
        mock_reg.get_weight.side_effect = lambda fid, comp: 0.40 if fid == "elo" else 0.60
        engine = EloOddsEngine(factor_registry=mock_reg)
        result = engine.predict(_make_features(), _make_match())
        # Check explanation records the registry weights
        elo_item = next(e for e in result.explanation if e.factor == "elo")
        odds_item = next(e for e in result.explanation if e.factor == "odds")
        assert elo_item.weight == 0.40
        assert odds_item.weight == 0.60

    def test_fallback_to_hardcoded_without_registry(self):
        """Without FactorRegistry, engine uses 0.30/0.70."""
        engine = EloOddsEngine()
        result = engine.predict(_make_features(), _make_match())
        elo_item = next(e for e in result.explanation if e.factor == "elo")
        odds_item = next(e for e in result.explanation if e.factor == "odds")
        assert elo_item.weight == 0.30
        assert odds_item.weight == 0.70

    def test_competition_specific_weights(self):
        """Engine reads competition-specific weights from registry."""
        mock_reg = MagicMock()
        def get_weight(fid, comp):
            if comp == "epl":
                return 0.45 if fid == "elo" else 0.55
            return 0.30 if fid == "elo" else 0.70
        mock_reg.get_weight.side_effect = get_weight
        engine = EloOddsEngine(factor_registry=mock_reg)

        features = FeatureSet(
            match=_make_match("epl"),
            general=GeneralFeatures(None, None, None, None),
            team=TeamFeatures(1900, 1800, None, None, None, None, None, None),
            market=MarketFeatures(2.0, 3.0, 4.0, "test", True),
            player=PlayerFeatures(None, None, None, None),
            environment=EnvironmentFeatures(None, None, None, False),
            custom={}, data_quality="real", quality_notes=[], feature_version="1.0",
        )
        result = engine.predict(features, _make_match("epl"))
        elo_item = next(e for e in result.explanation if e.factor == "elo")
        assert elo_item.weight == 0.45

    def test_predicted_outcome_set_when_elo_available(self):
        """Explanation includes predicted_outcome for available factors."""
        engine = EloOddsEngine()
        result = engine.predict(_make_features(), _make_match())
        elo_item = next(e for e in result.explanation if e.factor == "elo")
        odds_item = next(e for e in result.explanation if e.factor == "odds")
        assert elo_item.predicted_outcome is not None
        assert elo_item.predicted_outcome in ("home_win", "draw", "away_win")
        assert odds_item.predicted_outcome is not None
        assert odds_item.predicted_outcome in ("home_win", "draw", "away_win")

    def test_predicted_outcome_none_when_factor_unavailable(self):
        """predicted_outcome is None when a factor is unavailable."""
        engine = EloOddsEngine()
        # Features with no Elo ratings and no odds
        features = FeatureSet(
            match=_make_match(),
            general=GeneralFeatures(None, None, None, None),
            team=TeamFeatures(None, None, None, None, None, None, None, None),
            market=MarketFeatures(None, None, None, None, False),
            player=PlayerFeatures(None, None, None, None),
            environment=EnvironmentFeatures(None, None, None, False),
            custom={}, data_quality="low", quality_notes=[], feature_version="1.0",
        )
        result = engine.predict(features, _make_match())
        elo_item = next(e for e in result.explanation if e.factor == "elo")
        odds_item = next(e for e in result.explanation if e.factor == "odds")
        assert elo_item.predicted_outcome is None
        assert odds_item.predicted_outcome is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_elo_engine_weights.py -v`
Expected: FAIL — `EloOddsEngine` doesn't accept `factor_registry` param, no `predicted_outcome` in explanation

- [ ] **Step 3: Implement EloOddsEngine changes**

In `backend/app/kernel/engines/elo_odds_engine.py`, modify the class:

Add `FactorRegistry` type import at top (after existing imports):
```python
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.kernel.domain import (
    FeatureSet, MatchIdentity, PredictionResult, ContributionItem,
)
from app.kernel.engines.btd_model import calculate_btd_probabilities

if TYPE_CHECKING:
    from app.kernel.factor_registry import FactorRegistry
```

Modify the `EloOddsEngine` class — add constructor and update `predict()`:

```python
class EloOddsEngine:
    """Elo + Odds fusion engine. Implements PredictionEngine Protocol."""

    def __init__(self, factor_registry: FactorRegistry | None = None) -> None:
        self._factor_registry = factor_registry

    def name(self) -> str:
        return "elo_odds"

    def supported_sports(self) -> list[str]:
        return ["*"]

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult:
        elo_home = features.team.elo_rating_home
        elo_away = features.team.elo_rating_away
        is_knockout = (match.stage or "").lower().strip() in _KNOCKOUT_STAGES

        # Get weights from FactorRegistry or fall back to defaults
        if self._factor_registry:
            elo_w = self._factor_registry.get_weight("elo", match.season.competition.code)
            odds_w = self._factor_registry.get_weight("odds", match.season.competition.code)
        else:
            elo_w, odds_w = 0.30, 0.70

        # Elo probabilities via BTD
        if elo_home is not None and elo_away is not None:
            elo_probs = calculate_btd_probabilities(
                elo_home, elo_away, is_neutral=True, is_knockout=is_knockout,
            )
            elo_available = True
        else:
            elo_probs = {"home_win": 0.4, "draw": 0.3, "away_win": 0.3}
            elo_available = False

        # Market probabilities
        odds_h = features.market.odds_home
        odds_d = features.market.odds_draw
        odds_a = features.market.odds_away
        if odds_h and odds_d and odds_a and odds_h > 1.0 and odds_d > 1.0 and odds_a > 1.0:
            market_probs = _odds_to_probabilities(odds_h, odds_d, odds_a)
            odds_available = True
        else:
            market_probs = None
            odds_available = False

        # Fuse
        fused = _fuse_elo_and_odds(elo_probs, market_probs, elo_w, odds_w)
        scores = _probabilities_to_scores(fused)
        confidence = _calculate_confidence(fused)

        # Explanation with predicted_outcome
        elo_predicted = max(elo_probs, key=elo_probs.get) if elo_available else None
        odds_predicted = max(market_probs, key=market_probs.get) if odds_available else None

        explanation = [
            ContributionItem(
                factor="elo", direction="support" if elo_available else "neutral",
                weight=elo_w, available=elo_available,
                detail=f"Elo {elo_home} vs {elo_away}" if elo_available else "Elo unavailable",
                predicted_outcome=elo_predicted,
            ),
            ContributionItem(
                factor="odds", direction="support" if odds_available else "neutral",
                weight=odds_w, available=odds_available,
                detail=f"Odds {odds_h}/{odds_d}/{odds_a}" if odds_available else "Odds unavailable",
                predicted_outcome=odds_predicted,
            ),
        ]

        return PredictionResult(
            predicted_scores=scores,
            outcome_probabilities=fused,
            confidence=confidence,
            engine_name="elo_odds",
            explanation=explanation,
            betting_analysis=None,
            feature_version=features.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )
```

- [ ] **Step 4: Run new tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_elo_engine_weights.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run existing engine tests to verify no regression**

Run: `cd backend && python -m pytest tests/test_kernel_elo_odds_engine.py -v`
Expected: All existing tests PASS (equivalence tests still pass because `_fuse_elo_and_odds` still normalizes weights)

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/kernel/engines/elo_odds_engine.py tests/test_elo_engine_weights.py
git commit -m "feat(kernel): EloOddsEngine reads weights from FactorRegistry, sets predicted_outcome in explanation"
```

---

### Task 4: Calibration Model — Linear Regression

**Files:**
- Modify: `backend/app/kernel/learning_service.py`
- Test: `backend/tests/test_learning_calibration.py`

**Interfaces:**
- Consumes: `KernelCalibration` model from Task 1, `get_kernel_session` from `kernel_db.py`, `config.settings` for `LEARNING_WINDOW_SIZE` and `MIN_SAMPLES_FOR_CALIBRATION` (note: these config values will be added in Task 7 — until then, use hardcoded defaults matching the spec: `LEARNING_WINDOW_SIZE=30`, `MIN_SAMPLES_FOR_CALIBRATION=10`)
- Produces: `update_calibration(competition, engine)` implementation — Task 6 relies on calibration data being in the `KernelCalibration` table

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_learning_calibration.py
"""Tests for update_calibration (Phase 3)."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome, PredictionResult,
    ContributionItem,
)
from app.kernel.kernel_db import (
    init_kernel_db, close_kernel_session, get_kernel_session,
    KernelPrediction, KernelMatchOutcome, KernelCalibration,
)
from app.kernel.learning_service import KernelLearningService


def _make_match(match_id="m1", competition="world_cup") -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code=competition, name="Test", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id=match_id, season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


def _make_prediction_with_probs(home_prob, engine="elo_odds") -> PredictionResult:
    return PredictionResult(
        predicted_scores={"home": 2.0, "away": 1.0},
        outcome_probabilities={"home_win": home_prob, "draw": 0.25, "away_win": 1 - home_prob - 0.25},
        confidence=0.72, engine_name=engine,
        explanation=[
            ContributionItem(factor="elo", direction="support", weight=0.30, available=True,
                             detail="Elo", predicted_outcome="home_win"),
            ContributionItem(factor="odds", direction="support", weight=0.70, available=True,
                             detail="Odds", predicted_outcome="home_win"),
        ],
        betting_analysis=None, feature_version="1.0",
        prediction_timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )


@pytest.fixture
def svc(tmp_path):
    db_path = str(tmp_path / "kernel_test.db")
    init_kernel_db(db_path)
    yield KernelLearningService()
    close_kernel_session()


def _seed_predictions_with_outcomes(service, count, competition="world_cup"):
    """Seed N predictions + outcomes where home_win probability varies."""
    for i in range(count):
        match = _make_match(f"m{i}", competition)
        home_prob = 0.4 + (i % 5) * 0.1  # 0.4, 0.5, 0.6, 0.7, 0.8 cycling
        pred = _make_prediction_with_probs(home_prob)
        service.record_prediction(match, pred)

        outcome = MatchOutcome(
            match_id=f"m{i}", home_score=2 if i % 3 != 2 else 1,
            away_score=1 if i % 3 != 2 else 2,
            outcome="home_win" if i % 3 != 2 else "away_win",
            finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
        )
        service.record_outcome(outcome)
        service.compute_error(f"m{i}")


class TestUpdateCalibration:
    def test_insufficient_samples_skips(self, svc):
        """With < 10 samples, update_calibration does nothing."""
        _seed_predictions_with_outcomes(svc, 5)
        svc.update_calibration("world_cup", "elo_odds")

        session = get_kernel_session()
        cals = session.query(KernelCalibration).all()
        assert len(cals) == 0
        session.close()

    def test_sufficient_samples_creates_calibration(self, svc):
        """With >= 10 samples, calibration row is created."""
        _seed_predictions_with_outcomes(svc, 12)
        svc.update_calibration("world_cup", "elo_odds")

        session = get_kernel_session()
        cal = session.query(KernelCalibration).filter_by(
            engine="elo_odds", competition="world_cup"
        ).first()
        assert cal is not None
        assert 0.0 <= cal.slope <= 2.0
        assert -0.5 <= cal.intercept <= 0.5
        assert cal.sample_count == 12
        session.close()

    def test_slope_clamped_to_max(self, svc):
        """Slope is clamped to [0.0, 2.0]."""
        _seed_predictions_with_outcomes(svc, 12)
        svc.update_calibration("world_cup", "elo_odds")

        session = get_kernel_session()
        cal = session.query(KernelCalibration).first()
        assert cal.slope <= 2.0
        assert cal.slope >= 0.0
        session.close()

    def test_intercept_clamped(self, svc):
        """Intercept is clamped to [-0.5, 0.5]."""
        _seed_predictions_with_outcomes(svc, 12)
        svc.update_calibration("world_cup", "elo_odds")

        session = get_kernel_session()
        cal = session.query(KernelCalibration).first()
        assert -0.5 <= cal.intercept <= 0.5
        session.close()

    def test_upsert_updates_existing(self, svc):
        """Running update_calibration twice updates the same row."""
        _seed_predictions_with_outcomes(svc, 12)
        svc.update_calibration("world_cup", "elo_odds")
        svc.update_calibration("world_cup", "elo_odds")

        session = get_kernel_session()
        cals = session.query(KernelCalibration).filter_by(
            engine="elo_odds", competition="world_cup"
        ).all()
        assert len(cals) == 1
        session.close()

    def test_per_competition_isolation(self, svc):
        """Calibration for different competitions are separate rows."""
        _seed_predictions_with_outcomes(svc, 12, "world_cup")
        _seed_predictions_with_outcomes(svc, 12, "epl")
        svc.update_calibration("world_cup", "elo_odds")
        svc.update_calibration("epl", "elo_odds")

        session = get_kernel_session()
        cals = session.query(KernelCalibration).all()
        assert len(cals) == 2
        comps = {c.competition for c in cals}
        assert comps == {"world_cup", "epl"}
        session.close()

    def test_avg_confidence_and_accuracy_stored(self, svc):
        """avg_confidence and avg_accuracy are stored in calibration row."""
        _seed_predictions_with_outcomes(svc, 12)
        svc.update_calibration("world_cup", "elo_odds")

        session = get_kernel_session()
        cal = session.query(KernelCalibration).first()
        assert cal.avg_confidence > 0
        assert cal.avg_accuracy >= 0
        session.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_learning_calibration.py -v`
Expected: FAIL — `update_calibration` is still a stub

- [ ] **Step 3: Implement `update_calibration` in `learning_service.py`**

Add constants at the top of `backend/app/kernel/learning_service.py` (after the imports, before the class):

```python
# Phase 3 hardcoded constants (not configurable — see spec Section 3.4)
_CALIBRATION_SLOPE_MIN = 0.0
_CALIBRATION_SLOPE_MAX = 2.0
_CALIBRATION_INTERCEPT_MIN = -0.5
_CALIBRATION_INTERCEPT_MAX = 0.5

# Defaults used until config.py has the Phase 3 settings (added in Task 7)
_DEFAULT_LEARNING_WINDOW_SIZE = 30
_DEFAULT_MIN_SAMPLES_FOR_CALIBRATION = 10
```

Add `KernelCalibration` and `KernelPrediction` to the import from `kernel_db`:
```python
from app.kernel.kernel_db import (
    get_kernel_session,
    KernelPrediction, KernelMatchOutcome, KernelEngineScore,
    KernelCalibration,
)
```

Replace the `update_calibration` stub:

```python
    def update_calibration(self, competition: str, engine: str) -> None:
        """Fit linear regression calibration model and persist to DB."""
        session = get_kernel_session()
        try:
            # Query recent predictions with outcomes for this competition+engine
            query = (
                select(KernelPrediction, KernelMatchOutcome)
                .join(KernelMatchOutcome, KernelPrediction.match_id == KernelMatchOutcome.match_id)
                .where(
                    KernelPrediction.competition == competition,
                    KernelPrediction.engine == engine,
                    KernelMatchOutcome.outcome.isnot(None),
                )
                .order_by(KernelMatchOutcome.finished_at.desc())
                .limit(_DEFAULT_LEARNING_WINDOW_SIZE)
            )
            results = session.execute(query).all()
            if len(results) < _DEFAULT_MIN_SAMPLES_FOR_CALIBRATION:
                return

            x = [r[0].outcome_probabilities.get("home_win", 0) for r in results]
            y = [1.0 if r[1].outcome == "home_win" else 0.0 for r in results]

            n = len(x)
            sum_x = sum(x)
            sum_y = sum(y)
            sum_xx = sum(xi * xi for xi in x)
            sum_xy = sum(xi * yi for xi, yi in zip(x, y))
            denominator = n * sum_xx - sum_x * sum_x
            if abs(denominator) < 1e-10:
                return

            slope = (n * sum_xy - sum_x * sum_y) / denominator
            intercept = (sum_y - slope * sum_x) / n

            slope = max(_CALIBRATION_SLOPE_MIN, min(_CALIBRATION_SLOPE_MAX, slope))
            intercept = max(_CALIBRATION_INTERCEPT_MIN, min(_CALIBRATION_INTERCEPT_MAX, intercept))

            avg_confidence = sum_x / n
            avg_accuracy = sum_y / n

            # Upsert calibration
            existing = session.query(KernelCalibration).filter_by(
                engine=engine, competition=competition,
            ).first()
            now = datetime.now(timezone.utc)
            if existing:
                existing.slope = slope
                existing.intercept = intercept
                existing.sample_count = n
                existing.avg_confidence = avg_confidence
                existing.avg_accuracy = avg_accuracy
                existing.last_updated = now
            else:
                cal = KernelCalibration(
                    engine=engine, competition=competition,
                    slope=slope, intercept=intercept,
                    sample_count=n, avg_confidence=avg_confidence,
                    avg_accuracy=avg_accuracy, last_updated=now,
                )
                session.add(cal)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_learning_calibration.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run existing learning service tests to verify no regression**

Run: `cd backend && python -m pytest tests/test_kernel_learning_service.py -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/kernel/learning_service.py tests/test_learning_calibration.py
git commit -m "feat(kernel): implement update_calibration — linear regression model persisted to KernelCalibration table"
```

---

### Task 5: Weight Update — EWMA

**Files:**
- Modify: `backend/app/kernel/learning_service.py`
- Test: `backend/tests/test_learning_weights.py`

**Interfaces:**
- Consumes: `FactorRegistry` from Task 2 (injected via constructor), `ContributionItem.predicted_outcome` from Task 1, `KernelPrediction.explanation` JSON from existing `record_prediction`
- Produces: `update_weights(competition)` implementation that persists weights via `FactorRegistry.update_weight()` — Task 7 relies on this for `process_outcome` loop

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_learning_weights.py
"""Tests for update_weights EWMA (Phase 3)."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome, PredictionResult,
    ContributionItem,
)
from app.kernel.kernel_db import (
    init_kernel_db, close_kernel_session, get_kernel_session,
    KernelPrediction, KernelMatchOutcome,
)
from app.kernel.learning_service import KernelLearningService
from app.kernel.factor_registry import FactorRegistry


def _make_match(match_id="m1", competition="world_cup") -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code=competition, name="Test", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id=match_id, season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


def _make_prediction(elo_outcome="home_win", odds_outcome="home_win",
                     engine="elo_odds") -> PredictionResult:
    return PredictionResult(
        predicted_scores={"home": 2.0, "away": 1.0},
        outcome_probabilities={"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
        confidence=0.72, engine_name=engine,
        explanation=[
            ContributionItem(factor="elo", direction="support", weight=0.30,
                             available=True, detail="Elo", predicted_outcome=elo_outcome),
            ContributionItem(factor="odds", direction="support", weight=0.70,
                             available=True, detail="Odds", predicted_outcome=odds_outcome),
        ],
        betting_analysis=None, feature_version="1.0",
        prediction_timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )


@pytest.fixture
def svc_with_registry(tmp_path):
    db_path = str(tmp_path / "kernel_test.db")
    init_kernel_db(db_path)
    registry = FactorRegistry()
    service = KernelLearningService(factor_registry=registry)
    yield service, registry
    close_kernel_session()


def _seed_data(service, count, elo_correct=True, odds_correct=True,
               actual_outcome="home_win", competition="world_cup"):
    """Seed N predictions + outcomes with controlled per-factor accuracy."""
    for i in range(count):
        match = _make_match(f"m{i}", competition)
        elo_out = actual_outcome if elo_correct else "away_win"
        odds_out = actual_outcome if odds_correct else "away_win"
        pred = _make_prediction(elo_out, odds_out)
        service.record_prediction(match, pred)
        outcome = MatchOutcome(
            match_id=f"m{i}", home_score=2, away_score=1,
            outcome=actual_outcome,
            finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
        )
        service.record_outcome(outcome)
        service.compute_error(f"m{i}")


class TestUpdateWeights:
    def test_insufficient_samples_skips(self, svc_with_registry):
        """With < 10 samples, update_weights does nothing."""
        svc, reg = svc_with_registry
        _seed_data(svc, 5)
        old_elo = reg.get_weight("elo", "world_cup")
        svc.update_weights("world_cup")
        assert reg.get_weight("elo", "world_cup") == old_elo

    def test_ewma_updates_weights(self, svc_with_registry):
        """With >= 10 samples, weights are updated via EWMA."""
        svc, reg = svc_with_registry
        _seed_data(svc, 12)
        old_elo = reg.get_weight("elo", "world_cup")  # 0.30

        svc.update_weights("world_cup")
        new_elo = reg.get_weight("elo", "world_cup")
        # Weight should change (both factors predicted correctly, so target ≈ 0.5/0.5)
        assert new_elo != old_elo

    def test_weight_clamped_to_floor(self, svc_with_registry):
        """Weight cannot go below 0.05."""
        svc, reg = svc_with_registry
        # Elo always wrong, odds always right → elo target ≈ 0
        _seed_data(svc, 12, elo_correct=False, odds_correct=True)
        svc.update_weights("world_cup")
        assert reg.get_weight("elo", "world_cup") >= 0.05

    def test_weight_clamped_to_ceiling(self, svc_with_registry):
        """Weight cannot go above 0.95."""
        svc, reg = svc_with_registry
        # Elo always right, odds always wrong → elo target ≈ 1.0
        _seed_data(svc, 12, elo_correct=True, odds_correct=False)
        svc.update_weights("world_cup")
        assert reg.get_weight("elo", "world_cup") <= 0.95

    def test_both_factors_wrong_no_change(self, svc_with_registry):
        """When both factors are always wrong (total_acc=0), no update."""
        svc, reg = svc_with_registry
        _seed_data(svc, 12, elo_correct=False, odds_correct=False)
        old_elo = reg.get_weight("elo", "world_cup")
        svc.update_weights("world_cup")
        assert reg.get_weight("elo", "world_cup") == old_elo

    def test_per_competition_isolation(self, svc_with_registry):
        """Weight update for one competition doesn't affect another."""
        svc, reg = svc_with_registry
        _seed_data(svc, 12, competition="world_cup")
        _seed_data(svc, 12, competition="epl")
        svc.update_weights("world_cup")

        # EPL weight should still be default
        assert reg.get_weight("elo", "epl") == 0.30

    def test_weights_persisted_to_db(self, svc_with_registry):
        """Updated weights are persisted to KernelFactor table."""
        svc, reg = svc_with_registry
        _seed_data(svc, 12)
        svc.update_weights("world_cup")

        from app.kernel.kernel_db import KernelFactor
        session = get_kernel_session()
        row = session.query(KernelFactor).filter_by(
            factor_id="elo", competition="world_cup"
        ).first()
        assert row is not None
        assert row.source == "ewma"
        session.close()

    def test_missing_predicted_outcome_skipped(self, svc_with_registry):
        """Predictions without predicted_outcome are skipped in per-factor accuracy."""
        svc, reg = svc_with_registry
        # Seed predictions WITHOUT predicted_outcome (simulating pre-Phase 3 data)
        for i in range(12):
            match = _make_match(f"old_m{i}")
            pred = PredictionResult(
                predicted_scores={"home": 2.0, "away": 1.0},
                outcome_probabilities={"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
                confidence=0.72, engine_name="elo_odds",
                explanation=[
                    ContributionItem(factor="elo", direction="support", weight=0.30,
                                     available=True, detail="Elo", predicted_outcome=None),
                    ContributionItem(factor="odds", direction="support", weight=0.70,
                                     available=True, detail="Odds", predicted_outcome=None),
                ],
                betting_analysis=None, feature_version="1.0",
                prediction_timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
            )
            svc.record_prediction(match, pred)
            outcome = MatchOutcome(
                match_id=f"old_m{i}", home_score=2, away_score=1,
                outcome="home_win",
                finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
            )
            svc.record_outcome(outcome)
            svc.compute_error(f"old_m{i}")

        old_elo = reg.get_weight("elo", "world_cup")
        svc.update_weights("world_cup")
        # No per-factor data → no update
        assert reg.get_weight("elo", "world_cup") == old_elo
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_learning_weights.py -v`
Expected: FAIL — `KernelLearningService` doesn't accept `factor_registry`, `update_weights` is still a stub

- [ ] **Step 3: Add `FactorRegistry` injection to `KernelLearningService` constructor**

In `backend/app/kernel/learning_service.py`, modify the class:

Add import at top:
```python
from app.kernel.factor_registry import FactorRegistry
```

Add `__init__` to the class (before `record_prediction`):
```python
class KernelLearningService:
    """Implements LearningService Protocol."""

    def __init__(self, factor_registry: FactorRegistry | None = None) -> None:
        self._factor_registry = factor_registry or FactorRegistry()
```

- [ ] **Step 4: Implement `update_weights`**

Replace the `update_weights` stub:

```python
    def update_weights(self, competition: str) -> None:
        """EWMA weight adjustment per competition using per-factor accuracy."""
        if self._factor_registry is None:
            return

        session = get_kernel_session()
        try:
            # Query recent outcomes with predictions for this competition
            query = (
                select(KernelPrediction, KernelMatchOutcome)
                .join(KernelMatchOutcome, KernelPrediction.match_id == KernelMatchOutcome.match_id)
                .where(
                    KernelPrediction.competition == competition,
                    KernelMatchOutcome.outcome.isnot(None),
                )
                .order_by(KernelMatchOutcome.finished_at.desc())
                .limit(_DEFAULT_LEARNING_WINDOW_SIZE)
            )
            results = session.execute(query).all()
            if len(results) < _DEFAULT_MIN_SAMPLES_FOR_CALIBRATION:
                return

            elo_correct = 0
            elo_total = 0
            odds_correct = 0
            odds_total = 0

            for pred, outcome in results:
                actual = outcome.outcome
                explanation = pred.explanation or []
                for item in explanation:
                    if not isinstance(item, dict):
                        continue
                    factor = item.get("factor")
                    predicted = item.get("predicted_outcome")
                    if not predicted:
                        continue
                    if factor == "elo":
                        elo_total += 1
                        if predicted == actual:
                            elo_correct += 1
                    elif factor == "odds":
                        odds_total += 1
                        if predicted == actual:
                            odds_correct += 1

            if elo_total == 0 or odds_total == 0:
                return

            elo_acc = elo_correct / elo_total
            odds_acc = odds_correct / odds_total

            total_acc = elo_acc + odds_acc
            if total_acc == 0:
                return

            w_elo_target = elo_acc / total_acc
            w_odds_target = odds_acc / total_acc

            w_elo_old = self._factor_registry.get_weight("elo", competition)
            w_odds_old = self._factor_registry.get_weight("odds", competition)

            alpha = _DEFAULT_EWMA_ALPHA
            w_elo_new = max(_WEIGHT_FLOOR, min(_WEIGHT_CEILING,
                          alpha * w_elo_target + (1 - alpha) * w_elo_old))
            w_odds_new = max(_WEIGHT_FLOOR, min(_WEIGHT_CEILING,
                           1.0 - w_elo_new))

            self._factor_registry.update_weight("elo", competition, w_elo_new, source="ewma")
            self._factor_registry.update_weight("odds", competition, w_odds_new, source="ewma")
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
```

Add the additional constants at the top of the file (next to the Task 4 constants):
```python
_DEFAULT_EWMA_ALPHA = 0.1
_WEIGHT_FLOOR = 0.05
_WEIGHT_CEILING = 0.95
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_learning_weights.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Run existing + calibration tests to verify no regression**

Run: `cd backend && python -m pytest tests/test_kernel_learning_service.py tests/test_learning_calibration.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
cd backend
git add app/kernel/learning_service.py tests/test_learning_weights.py
git commit -m "feat(kernel): implement update_weights — EWMA per-factor accuracy weight adjustment"
```

---

### Task 6: EngineScore Persistence + Prediction History

**Files:**
- Modify: `backend/app/kernel/learning_service.py`
- Test: `backend/tests/test_engine_score_persistence.py`
- Test: `backend/tests/test_prediction_history.py`

**Interfaces:**
- Consumes: `KernelEngineScore` model (with new `confidence_calibration` column from Task 1), `KernelCalibration` from Task 4, `KernelPredictionHistory` model (with new `feature_version` column from Task 1)
- Produces: `engine_score()` persists to DB with `confidence_calibration`, `record_prediction()` writes to `KernelPredictionHistory` — Task 7 relies on EngineScore being in DB for dynamic selection

- [ ] **Step 1: Write failing tests for EngineScore persistence**

```python
# backend/tests/test_engine_score_persistence.py
"""Tests for EngineScore DB persistence (Phase 3)."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome, PredictionResult,
    ContributionItem,
)
from app.kernel.kernel_db import (
    init_kernel_db, close_kernel_session, get_kernel_session,
    KernelPrediction, KernelMatchOutcome, KernelEngineScore, KernelCalibration,
)
from app.kernel.learning_service import KernelLearningService


def _make_match(match_id="m1", competition="world_cup") -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code=competition, name="Test", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id=match_id, season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


def _make_prediction(engine="elo_odds") -> PredictionResult:
    return PredictionResult(
        predicted_scores={"home": 2.0, "away": 1.0},
        outcome_probabilities={"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
        confidence=0.72, engine_name=engine,
        explanation=[
            ContributionItem(factor="elo", direction="support", weight=0.30,
                             available=True, detail="Elo", predicted_outcome="home_win"),
            ContributionItem(factor="odds", direction="support", weight=0.70,
                             available=True, detail="Odds", predicted_outcome="home_win"),
        ],
        betting_analysis=None, feature_version="1.0",
        prediction_timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )


@pytest.fixture
def svc(tmp_path):
    db_path = str(tmp_path / "kernel_test.db")
    init_kernel_db(db_path)
    yield KernelLearningService()
    close_kernel_session()


def _seed_and_process(service, count, competition="world_cup"):
    for i in range(count):
        match = _make_match(f"m{i}", competition)
        pred = _make_prediction()
        service.record_prediction(match, pred)
        outcome = MatchOutcome(
            match_id=f"m{i}", home_score=2, away_score=1,
            outcome="home_win",
            finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
        )
        service.record_outcome(outcome)
        service.compute_error(f"m{i}")


class TestEngineScorePersistence:
    def test_engine_score_persists_to_db(self, svc):
        """engine_score writes to KernelEngineScore table."""
        _seed_and_process(svc, 3)
        score = svc.engine_score("elo_odds", "world_cup")

        session = get_kernel_session()
        row = session.query(KernelEngineScore).filter_by(
            engine="elo_odds", competition="world_cup"
        ).first()
        assert row is not None
        assert row.sample_count == 3
        assert row.accuracy is not None
        session.close()

    def test_confidence_calibration_from_calibration_table(self, svc):
        """confidence_calibration reads from KernelCalibration if available."""
        _seed_and_process(svc, 3)

        # Insert a calibration row
        session = get_kernel_session()
        session.add(KernelCalibration(
            engine="elo_odds", competition="world_cup",
            slope=1.0, intercept=0.0,
            sample_count=15, avg_confidence=0.60, avg_accuracy=0.72,
            last_updated=datetime.now(timezone.utc),
        ))
        session.commit()
        session.close()

        score = svc.engine_score("elo_odds", "world_cup")
        # confidence_calibration = avg_accuracy / avg_confidence = 0.72 / 0.60 = 1.2
        assert abs(score.confidence_calibration - 1.2) < 0.01

    def test_confidence_calibration_zero_when_no_calibration(self, svc):
        """confidence_calibration is 0.0 when no calibration row exists."""
        _seed_and_process(svc, 3)
        score = svc.engine_score("elo_odds", "world_cup")
        assert score.confidence_calibration == 0.0

    def test_engine_score_upsert(self, svc):
        """Running engine_score twice updates the same row."""
        _seed_and_process(svc, 3)
        svc.engine_score("elo_odds", "world_cup")
        svc.engine_score("elo_odds", "world_cup")

        session = get_kernel_session()
        rows = session.query(KernelEngineScore).filter_by(
            engine="elo_odds", competition="world_cup"
        ).all()
        assert len(rows) == 1
        session.close()

    def test_engine_score_returns_correct_values(self, svc):
        """engine_score returns EngineScore with correct aggregated values."""
        _seed_and_process(svc, 5)
        score = svc.engine_score("elo_odds", "world_cup")
        assert score.engine == "elo_odds"
        assert score.competition == "world_cup"
        assert score.sample_count == 5
        assert 0.0 <= score.accuracy <= 1.0
```

- [ ] **Step 2: Write failing tests for Prediction History**

```python
# backend/tests/test_prediction_history.py
"""Tests for KernelPredictionHistory writing (Phase 3)."""
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, PredictionResult, ContributionItem,
)
from app.kernel.kernel_db import (
    init_kernel_db, close_kernel_session, get_kernel_session,
    KernelPrediction, KernelPredictionHistory,
)
from app.kernel.learning_service import KernelLearningService


def _make_match(match_id="m1") -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id=match_id, season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


def _make_prediction(engine="elo_odds") -> PredictionResult:
    return PredictionResult(
        predicted_scores={"home": 2.0, "away": 1.0},
        outcome_probabilities={"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
        confidence=0.72, engine_name=engine,
        explanation=[
            ContributionItem(factor="elo", direction="support", weight=0.30,
                             available=True, detail="Elo", predicted_outcome="home_win"),
        ],
        betting_analysis=None, feature_version="1.0",
        prediction_timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )


@pytest.fixture
def svc(tmp_path):
    db_path = str(tmp_path / "kernel_test.db")
    init_kernel_db(db_path)
    yield KernelLearningService()
    close_kernel_session()


class TestPredictionHistory:
    def test_record_prediction_writes_history(self, svc):
        """record_prediction writes a row to KernelPredictionHistory."""
        match = _make_match("m1")
        pred = _make_prediction()
        svc.record_prediction(match, pred)

        session = get_kernel_session()
        history = session.query(KernelPredictionHistory).filter_by(match_id="m1").first()
        assert history is not None
        assert history.engine == "elo_odds"
        assert history.feature_version == "1.0"
        assert history.trigger == "initial"
        session.close()

    def test_record_prediction_still_upserts_prediction(self, svc):
        """record_prediction still upserts KernelPrediction (existing behavior)."""
        match = _make_match("m1")
        pred = _make_prediction()
        svc.record_prediction(match, pred)

        session = get_kernel_session()
        kp = session.get(KernelPrediction, "m1")
        assert kp is not None
        assert kp.engine == "elo_odds"
        session.close()

    def test_multiple_predictions_create_multiple_history_rows(self, svc):
        """Each record_prediction call creates a new history row."""
        match = _make_match("m1")
        pred1 = _make_prediction()
        pred2 = _make_prediction()
        svc.record_prediction(match, pred1)
        svc.record_prediction(match, pred2)

        session = get_kernel_session()
        rows = session.query(KernelPredictionHistory).filter_by(match_id="m1").all()
        assert len(rows) == 2
        session.close()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_engine_score_persistence.py tests/test_prediction_history.py -v`
Expected: FAIL — `engine_score` doesn't persist to DB, `record_prediction` doesn't write history

- [ ] **Step 4: Enhance `engine_score()` to persist to DB**

In `backend/app/kernel/learning_service.py`, replace the existing `engine_score` method:

```python
    def engine_score(self, engine: str,
                     competition: str | None = None) -> EngineScore | None:
        session = get_kernel_session()
        try:
            query = select(
                KernelMatchOutcome,
            ).where(
                KernelMatchOutcome.engine == engine,
                KernelMatchOutcome.outcome_correct.isnot(None),
            )
            if competition is not None:
                query = query.join(
                    KernelPrediction,
                    KernelMatchOutcome.match_id == KernelPrediction.match_id,
                ).where(KernelPrediction.competition == competition)

            results = session.execute(query).scalars().all()
            if not results:
                return None

            count = len(results)
            correct = sum(1 for r in results if r.outcome_correct)
            accuracy = correct / count if count > 0 else 0.0
            avg_mae = sum(r.score_mae or 0 for r in results) / count
            avg_brier = sum(r.brier_score or 0 for r in results) / count

            # Read confidence_calibration from KernelCalibration
            confidence_calibration = 0.0
            if competition is not None:
                cal = session.query(KernelCalibration).filter_by(
                    engine=engine, competition=competition,
                ).first()
                if cal:
                    confidence_calibration = cal.avg_accuracy / max(cal.avg_confidence, 1e-6)

            score = EngineScore(
                engine=engine, competition=competition,
                accuracy=round(accuracy, 4),
                avg_mae=round(avg_mae, 4),
                brier_score=round(avg_brier, 4),
                sample_count=count,
                confidence_calibration=round(confidence_calibration, 4),
                last_updated=datetime.now(timezone.utc),
            )

            # Persist to KernelEngineScore table
            existing = session.query(KernelEngineScore).filter_by(
                engine=engine, competition=competition,
            ).first()
            now = datetime.now(timezone.utc)
            if existing:
                existing.accuracy = score.accuracy
                existing.avg_mae = score.avg_mae
                existing.brier_score = score.brier_score
                existing.sample_count = count
                existing.confidence_calibration = score.confidence_calibration
                existing.last_updated = now
            else:
                row = KernelEngineScore(
                    engine=engine, competition=competition,
                    accuracy=score.accuracy, avg_mae=score.avg_mae,
                    brier_score=score.brier_score, sample_count=count,
                    confidence_calibration=score.confidence_calibration,
                    last_updated=now,
                )
                session.add(row)
            session.commit()

            return score
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
```

- [ ] **Step 5: Enhance `record_prediction()` to write to KernelPredictionHistory**

Add `KernelPredictionHistory` to the import from `kernel_db`:
```python
from app.kernel.kernel_db import (
    get_kernel_session,
    KernelPrediction, KernelMatchOutcome, KernelEngineScore,
    KernelCalibration, KernelPredictionHistory,
)
```

In the `record_prediction` method, add history writing after the existing `session.commit()` (but before the `finally`):

```python
    def record_prediction(self, match: MatchIdentity,
                          prediction: PredictionResult) -> None:
        session = get_kernel_session()
        try:
            existing = session.get(KernelPrediction, match.match_id)
            now = datetime.now(timezone.utc)
            if existing:
                existing.engine = prediction.engine_name
                existing.predicted_scores = prediction.predicted_scores
                existing.outcome_probabilities = prediction.outcome_probabilities
                existing.confidence = prediction.confidence
                existing.feature_version = prediction.feature_version
                existing.explanation = [c.__dict__ for c in prediction.explanation]
                existing.updated_at = now
            else:
                record = KernelPrediction(
                    match_id=match.match_id,
                    sport=match.season.competition.sport.code,
                    competition=match.season.competition.code,
                    season=match.season.season_key,
                    engine=prediction.engine_name,
                    predicted_scores=prediction.predicted_scores,
                    outcome_probabilities=prediction.outcome_probabilities,
                    confidence=prediction.confidence,
                    feature_version=prediction.feature_version,
                    explanation=[c.__dict__ for c in prediction.explanation],
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            session.commit()

            # Write to KernelPredictionHistory (Phase 3)
            history = KernelPredictionHistory(
                match_id=match.match_id,
                engine=prediction.engine_name,
                predicted_scores=prediction.predicted_scores,
                outcome_probabilities=prediction.outcome_probabilities,
                confidence=prediction.confidence,
                feature_version=prediction.feature_version,
                trigger="initial",
                created_at=now,
            )
            session.add(history)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
```

- [ ] **Step 6: Run all new tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_engine_score_persistence.py tests/test_prediction_history.py -v`
Expected: PASS (8 tests)

- [ ] **Step 7: Run all previous Phase 3 + existing tests for regression**

Run: `cd backend && python -m pytest tests/test_kernel_learning_service.py tests/test_learning_calibration.py tests/test_learning_weights.py -v`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
cd backend
git add app/kernel/learning_service.py tests/test_engine_score_persistence.py tests/test_prediction_history.py
git commit -m "feat(kernel): EngineScore DB persistence + prediction history writing"
```

---

### Task 7: Dynamic Engine Selection + process_outcome + Config + API Integration

**Files:**
- Modify: `backend/app/kernel/engine_registry.py`
- Modify: `backend/app/kernel/prediction_kernel.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/api/routes/predictions.py`
- Modify: `backend/tests/test_kernel_engine_registry.py` (update existing tests)
- Test: `backend/tests/test_engine_dynamic_selection.py`
- Test: `backend/tests/test_process_outcome_full.py`

**Interfaces:**
- Consumes: All Phase 3 work from Tasks 1-6
- Produces: Complete `process_outcome` loop, `PHASE3_LEARNING_ENABLED` flag, `_get_kernel()` with shared instances, `EngineRegistry.select(engine_name, competition=None)` with dynamic selection

- [ ] **Step 1: Write failing tests for dynamic engine selection**

```python
# backend/tests/test_engine_dynamic_selection.py
"""Tests for dynamic engine selection (Phase 3)."""
from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, FeatureSet, PredictionResult,
    GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures, EngineScore,
)
from app.kernel.engine_registry import EngineRegistry
from app.kernel.engines.elo_odds_engine import EloOddsEngine


class FakeEngine:
    """Minimal engine for testing."""
    def __init__(self, name_str):
        self._name = name_str
    def name(self): return self._name
    def supported_sports(self): return ["*"]
    def predict(self, features, match):
        return PredictionResult(
            predicted_scores={"home": 1.0, "away": 0.0},
            outcome_probabilities={"home_win": 0.5, "draw": 0.3, "away_win": 0.2},
            confidence=0.5, engine_name=self._name, explanation=[],
            betting_analysis=None, feature_version="1.0",
            prediction_timestamp=datetime.now(timezone.utc),
        )


class TestDynamicEngineSelection:
    def test_auto_returns_default_without_learning_service(self):
        """Without LearningService, select('auto') returns default engine."""
        reg = EngineRegistry()
        reg.register(FakeEngine("engine_a"))
        engine = reg.select("auto", competition="world_cup")
        assert engine.name() == "engine_a"

    def test_auto_returns_default_with_insufficient_samples(self):
        """With < 5 samples, select('auto') returns default."""
        mock_ls = MagicMock()
        mock_ls.engine_score.return_value = EngineScore(
            engine="engine_a", competition="world_cup",
            accuracy=0.8, avg_mae=0.5, brier_score=0.3,
            sample_count=3, confidence_calibration=1.0,
            last_updated=datetime.now(timezone.utc),
        )
        reg = EngineRegistry(learning_service=mock_ls)
        reg.register(FakeEngine("engine_a"))
        engine = reg.select("auto", competition="world_cup")
        assert engine.name() == "engine_a"

    def test_auto_selects_best_engine(self):
        """With sufficient samples, select('auto') picks highest accuracy."""
        mock_ls = MagicMock()
        def engine_score(name, comp=None):
            if name == "engine_a":
                return EngineScore(name, comp, 0.6, 0.5, 0.3, 10, 1.0,
                                   datetime.now(timezone.utc))
            return EngineScore(name, comp, 0.8, 0.3, 0.2, 10, 1.0,
                               datetime.now(timezone.utc))
        mock_ls.engine_score.side_effect = engine_score

        reg = EngineRegistry(learning_service=mock_ls)
        reg.register(FakeEngine("engine_a"))
        reg.register(FakeEngine("engine_b"))
        engine = reg.select("auto", competition="world_cup")
        assert engine.name() == "engine_b"

    def test_select_by_name_ignores_learning(self):
        """select('engine_a') returns that engine regardless of scores."""
        mock_ls = MagicMock()
        reg = EngineRegistry(learning_service=mock_ls)
        reg.register(FakeEngine("engine_a"))
        engine = reg.select("engine_a", competition="world_cup")
        assert engine.name() == "engine_a"
        mock_ls.engine_score.assert_not_called()
```

- [ ] **Step 2: Write failing tests for process_outcome full loop**

```python
# backend/tests/test_process_outcome_full.py
"""Tests for process_outcome full loop (Phase 3)."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome, PredictionResult,
    FeatureSet, GeneralFeatures, TeamFeatures, MarketFeatures,
    PlayerFeatures, EnvironmentFeatures, PredictionError, ContributionItem,
)
from app.kernel.kernel_db import init_kernel_db, close_kernel_session
from app.kernel.learning_service import KernelLearningService
from app.kernel.factor_registry import FactorRegistry
from app.kernel.prediction_kernel import PredictionKernel
from app.kernel.engine_registry import EngineRegistry
from app.kernel.engines.elo_odds_engine import EloOddsEngine


def _make_match(match_id="m1", competition="world_cup") -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code=competition, name="Test", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2026")
    home = TeamIdentity(code="BRA", name="Brazil", competition=comp)
    away = TeamIdentity(code="ARG", name="Argentina", competition=comp)
    return MatchIdentity(
        match_id=match_id, season=season, stage="group", round=None,
        home=home, away=away,
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


def _make_outcome(match_id="m1") -> MatchOutcome:
    return MatchOutcome(
        match_id=match_id, home_score=2, away_score=1,
        outcome="home_win",
        finished_at=datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
    )


def _make_prediction() -> PredictionResult:
    return PredictionResult(
        predicted_scores={"home": 2.0, "away": 1.0},
        outcome_probabilities={"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
        confidence=0.72, engine_name="elo_odds",
        explanation=[
            ContributionItem(factor="elo", direction="support", weight=0.30,
                             available=True, detail="Elo", predicted_outcome="home_win"),
            ContributionItem(factor="odds", direction="support", weight=0.70,
                             available=True, detail="Odds", predicted_outcome="home_win"),
        ],
        betting_analysis=None, feature_version="1.0",
        prediction_timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )


@pytest.fixture
def kernel_setup(tmp_path):
    """Set up a full kernel with temp DB."""
    db_path = str(tmp_path / "kernel_test.db")
    init_kernel_db(db_path)

    factor_reg = FactorRegistry()
    learning = KernelLearningService(factor_registry=factor_reg)
    engine = EloOddsEngine(factor_registry=factor_reg)
    reg = EngineRegistry(learning_service=learning)
    reg.register(engine)

    # Mock adapter
    adapter = MagicMock()
    adapter.get_match_identity.return_value = _make_match()
    adapter.fetch_outcome.return_value = _make_outcome()

    # Mock feature builder
    feature_builder = MagicMock()
    feature_builder.build.return_value = FeatureSet(
        match=_make_match(),
        general=GeneralFeatures(None, None, None, None),
        team=TeamFeatures(1900, 1800, None, None, None, None, None, None),
        market=MarketFeatures(2.0, 3.0, 4.0, "test", True),
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures(None, None, None, False),
        custom={}, data_quality="real", quality_notes=[], feature_version="1.0",
    )

    from app.kernel.feature_registry import FeatureRegistry
    kernel = PredictionKernel(
        adapter=adapter, feature_builder=feature_builder,
        engine_registry=reg, factor_registry=factor_reg,
        feature_registry=FeatureRegistry(), learning=learning,
    )
    yield kernel, adapter, learning
    close_kernel_session()


class TestProcessOutcomeFull:
    def test_outcome_none_skips(self, kernel_setup):
        """When fetch_outcome returns None, process_outcome does nothing."""
        kernel, adapter, learning = kernel_setup
        adapter.fetch_outcome.return_value = None
        kernel.process_outcome("m1")
        # record_outcome should not have been called
        # (verify by checking no outcome in DB)
        error = learning.compute_error("m1")
        assert error is None

    @patch("app.kernel.prediction_kernel.config")
    def test_phase3_off_only_records_and_computes(self, mock_config, kernel_setup):
        """When PHASE3_LEARNING_ENABLED=false, only record_outcome + compute_error run."""
        mock_config.settings.PHASE3_LEARNING_ENABLED = False
        kernel, adapter, learning = kernel_setup

        # Seed a prediction first
        learning.record_prediction(_make_match("m1"), _make_prediction())

        kernel.process_outcome("m1")

        # compute_error should have run (outcome recorded + error computed)
        error = learning.compute_error("m1")
        assert error is not None

    @patch("app.kernel.prediction_kernel.config")
    def test_phase3_on_runs_full_loop(self, mock_config, kernel_setup):
        """When PHASE3_LEARNING_ENABLED=true, all 5 steps run."""
        mock_config.settings.PHASE3_LEARNING_ENABLED = True
        kernel, adapter, learning = kernel_setup

        # Seed a prediction first
        learning.record_prediction(_make_match("m1"), _make_prediction())

        # Patch learning methods to track calls
        with patch.object(learning, "update_calibration") as mock_cal, \
             patch.object(learning, "update_weights") as mock_weights, \
             patch.object(learning, "engine_score") as mock_score:
            kernel.process_outcome("m1")
            mock_cal.assert_called_once_with("world_cup", "elo_odds")
            mock_weights.assert_called_once_with("world_cup")
            mock_score.assert_called_once_with("elo_odds", "world_cup")

    @patch("app.kernel.prediction_kernel.config")
    def test_phase3_on_no_prediction_skips_learning(self, mock_config, kernel_setup):
        """When no prediction exists, compute_error returns None and learning skips."""
        mock_config.settings.PHASE3_LEARNING_ENABLED = True
        kernel, adapter, learning = kernel_setup
        # No prediction seeded → compute_error returns None
        kernel.process_outcome("m1")
        # Learning methods should not crash

    @patch("app.kernel.prediction_kernel.config")
    def test_phase3_on_calls_engine_score_last(self, mock_config, kernel_setup):
        """engine_score is called after calibration and weights."""
        mock_config.settings.PHASE3_LEARNING_ENABLED = True
        kernel, adapter, learning = kernel_setup

        learning.record_prediction(_make_match("m1"), _make_prediction())

        call_order = []
        with patch.object(learning, "update_calibration",
                          side_effect=lambda *a: call_order.append("cal")), \
             patch.object(learning, "update_weights",
                          side_effect=lambda *a: call_order.append("weights")), \
             patch.object(learning, "engine_score",
                          side_effect=lambda *a: call_order.append("score")):
            kernel.process_outcome("m1")
            assert call_order == ["cal", "weights", "score"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_engine_dynamic_selection.py tests/test_process_outcome_full.py -v`
Expected: FAIL — `EngineRegistry` doesn't accept `learning_service`, `select` signature wrong, `process_outcome` incomplete

- [ ] **Step 4: Update `EngineRegistry` for dynamic selection**

Replace the entire `backend/app/kernel/engine_registry.py`:

```python
# backend/app/kernel/engine_registry.py
"""Engine registration and selection."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.kernel.protocols import PredictionEngine

if TYPE_CHECKING:
    from app.kernel.protocols import LearningService

# Minimum samples for dynamic engine selection (hardcoded — see spec Section 3.4)
_MIN_SAMPLES_FOR_ENGINE_SELECT = 5


class EngineRegistry:
    """Registers engines and selects them by name or strategy."""

    def __init__(self, learning_service: LearningService | None = None) -> None:
        self._engines: dict[str, PredictionEngine] = {}
        self._default_name: str | None = None
        self._learning_service = learning_service

    def register(self, engine: PredictionEngine) -> None:
        name = engine.name()
        self._engines[name] = engine
        if self._default_name is None:
            self._default_name = name

    def get(self, name: str) -> PredictionEngine:
        if name not in self._engines:
            available = list(self._engines.keys())
            raise KeyError(f"Unknown engine: {name}. Available: {available}")
        return self._engines[name]

    def list_engines(self) -> list[str]:
        return list(self._engines.keys())

    def select(self, engine_name: str,
               competition: str | None = None) -> PredictionEngine:
        """Select an engine by name or 'auto' for dynamic selection.

        When engine_name is 'auto' and a LearningService is available,
        selects the engine with the highest accuracy that has at least
        _MIN_SAMPLES_FOR_ENGINE_SELECT samples. Falls back to default
        engine if no engine has enough samples.
        """
        if engine_name != "auto":
            return self.get(engine_name)

        if self._default_name is None:
            raise KeyError("No engines registered")

        # Dynamic selection via LearningService
        if self._learning_service is not None:
            best_engine = None
            best_accuracy = -1.0
            for name, engine in self._engines.items():
                score = self._learning_service.engine_score(name, competition)
                if score and score.sample_count >= _MIN_SAMPLES_FOR_ENGINE_SELECT:
                    if score.accuracy > best_accuracy:
                        best_accuracy = score.accuracy
                        best_engine = engine
            if best_engine is not None:
                return best_engine

        return self._engines[self._default_name]
```

- [ ] **Step 5: Update existing `test_kernel_engine_registry.py` tests**

In `backend/tests/test_kernel_engine_registry.py`, update tests to use new `select` signature. Replace `features` argument with `competition=None` or omit it:

```python
class TestEngineRegistry:
    def test_register_and_get(self):
        reg = EngineRegistry()
        engine = EloOddsEngine()
        reg.register(engine)
        assert reg.get("elo_odds") is engine

    def test_list_engines(self):
        reg = EngineRegistry()
        reg.register(EloOddsEngine())
        names = reg.list_engines()
        assert "elo_odds" in names

    def test_get_unknown_raises(self):
        reg = EngineRegistry()
        with pytest.raises(KeyError):
            reg.get("nonexistent")

    def test_select_auto_returns_default(self):
        reg = EngineRegistry()
        reg.register(EloOddsEngine())
        engine = reg.select("auto", competition="world_cup")
        assert engine.name() == "elo_odds"

    def test_select_by_name(self):
        reg = EngineRegistry()
        reg.register(EloOddsEngine())
        engine = reg.select("elo_odds", competition="world_cup")
        assert engine.name() == "elo_odds"

    def test_select_unknown_strategy_raises(self):
        reg = EngineRegistry()
        reg.register(EloOddsEngine())
        with pytest.raises(KeyError):
            reg.select("nonexistent", competition="world_cup")
```

Remove the `_make_features()` function and unused imports since `select` no longer takes `FeatureSet`.

- [ ] **Step 6: Update `prediction_kernel.py` — `select()` call and `process_outcome`**

In `backend/app/kernel/prediction_kernel.py`, update the `predict` method's `select` call:

```python
    def predict(self, match_id: str, engine: str = "auto") -> PredictionResult:
        """Run a prediction for a single match."""
        # 1. Get match identity
        match = self._adapter.get_match_identity(match_id)
        # 2. Fetch raw data
        raw = self._adapter.fetch_all_data(match)
        # 3. Build features
        features = self._feature_builder.build(match, raw)
        # 4. Select engine
        engine_impl = self._engine_registry.select(engine, competition=match.season.competition.code)
        # 5. Run prediction
        prediction = engine_impl.predict(features, match)
        # 6. Record for learning
        self._learning.record_prediction(match, prediction)
        # 7. Return result
        return prediction
```

Replace the `process_outcome` method:

```python
    def process_outcome(self, match_id: str) -> None:
        """Process a match outcome — triggers the learning loop."""
        from app.core import config

        outcome = self._adapter.fetch_outcome(match_id)
        if outcome is None:
            logger.warning("No outcome found for match %s", match_id)
            return
        self._learning.record_outcome(outcome)
        error = self._learning.compute_error(match_id)
        if error is None:
            return

        match = self._adapter.get_match_identity(match_id)
        competition = match.season.competition.code
        engine = error.engine

        if config.settings.PHASE3_LEARNING_ENABLED:
            self._learning.update_calibration(competition, engine)
            self._learning.update_weights(competition)
            self._learning.engine_score(engine, competition)
```

- [ ] **Step 7: Add Phase 3 config settings**

In `backend/app/core/config.py`, after the `PHASE2_LEAGUES_ENABLED` block (around line 963), add:

```python
    # Phase 3 — Unified learning loop (default OFF). When false,
    # process_outcome only records outcomes and computes errors
    # (existing Phase 1 behavior). Set to true to enable calibration,
    # weight updates, and engine score persistence.
    PHASE3_LEARNING_ENABLED: bool = _env_bool(
        "PHASE3_LEARNING_ENABLED", "false"
    )
    LEARNING_WINDOW_SIZE: int = int(
        os.getenv("LEARNING_WINDOW_SIZE", "30")
    )
    EWMA_ALPHA: float = float(
        os.getenv("EWMA_ALPHA", "0.1")
    )
    MIN_SAMPLES_FOR_CALIBRATION: int = int(
        os.getenv("MIN_SAMPLES_FOR_CALIBRATION", "10")
    )
    MIN_SAMPLES_FOR_ENGINE_SELECT: int = int(
        os.getenv("MIN_SAMPLES_FOR_ENGINE_SELECT", "5")
    )
    WEIGHT_FLOOR: float = float(
        os.getenv("WEIGHT_FLOOR", "0.05")
    )
    WEIGHT_CEILING: float = float(
        os.getenv("WEIGHT_CEILING", "0.95")
    )
```

- [ ] **Step 8: Update `_get_kernel()` in `predictions.py`**

In `backend/app/api/routes/predictions.py`, update the `_get_kernel()` function (around line 43-76):

```python
    if not hasattr(_get_kernel, "_instance"):
        init_kernel_db()
        factor_registry = FactorRegistry()
        engine = EloOddsEngine(factor_registry=factor_registry)
        learning = KernelLearningService(factor_registry=factor_registry)
        reg = EngineRegistry(learning_service=learning)
        reg.register(engine)

        # Build adapter registry — always includes WorldCupAdapter
        adapters: dict[str, object] = {
            "wc-": WorldCupAdapter(),
        }

        # Phase 2: register UCL and EPL adapters when enabled
        if config.settings.PHASE2_LEAGUES_ENABLED:
            from app.sports.football.adapters.ucl_adapter import UCLAdapter
            from app.sports.football.adapters.epl_adapter import EPLAdapter
            adapters["ucl-"] = UCLAdapter()
            adapters["epl-"] = EPLAdapter()

            # Phase 2b: register league-format adapters from LEAGUE_REGISTRY
            from app.sports.football.adapters.league_adapter import LEAGUE_REGISTRY, LeagueAdapter
            for prefix, cfg in LEAGUE_REGISTRY.items():
                adapters[prefix] = LeagueAdapter(cfg)

        from app.sports.football.adapters.multi_adapter import MultiAdapter
        multi = MultiAdapter(adapters)

        _get_kernel._instance = PredictionKernel(
            adapter=multi,
            feature_builder=FootballFeatureBuilder(),
            engine_registry=reg,
            factor_registry=factor_registry,
            feature_registry=FeatureRegistry(),
            learning=learning,
        )
    return _get_kernel._instance
```

- [ ] **Step 9: Run new tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_engine_dynamic_selection.py tests/test_process_outcome_full.py -v`
Expected: PASS (9 tests)

- [ ] **Step 10: Run updated existing engine registry tests**

Run: `cd backend && python -m pytest tests/test_kernel_engine_registry.py -v`
Expected: All tests PASS

- [ ] **Step 11: Run full Phase 1 + Phase 2 regression**

Run: `cd backend && python -m pytest tests/test_kernel_*.py tests/test_league_adapter.py tests/test_ucl_adapter.py tests/test_epl_adapter.py tests/test_multi_adapter.py tests/test_football_data_client.py tests/test_club_elo_service.py -v`
Expected: All tests PASS (zero regression)

- [ ] **Step 12: Run all Phase 3 tests together**

Run: `cd backend && python -m pytest tests/test_db_migration.py tests/test_factor_registry_persistence.py tests/test_elo_engine_weights.py tests/test_learning_calibration.py tests/test_learning_weights.py tests/test_engine_score_persistence.py tests/test_prediction_history.py tests/test_engine_dynamic_selection.py tests/test_process_outcome_full.py -v`
Expected: All 44 tests PASS

- [ ] **Step 13: Commit**

```bash
cd backend
git add app/kernel/engine_registry.py app/kernel/prediction_kernel.py app/core/config.py app/api/routes/predictions.py tests/test_kernel_engine_registry.py tests/test_engine_dynamic_selection.py tests/test_process_outcome_full.py
git commit -m "feat(kernel): Phase 3 integration — dynamic engine selection, process_outcome full loop, PHASE3_LEARNING_ENABLED flag, _get_kernel shared instances"
```

---

## Self-Review

### Spec Coverage

| Spec Section | Task(s) | Covered |
|-------------|---------|---------|
| §1 Goal — 5-step loop | 7 | ✅ `process_outcome` full loop |
| §3.2.1 DB Migration | 1 | ✅ `_migrate_dormant_tables()` |
| §3.3 KernelCalibration table | 1 | ✅ New model + test |
| §4 Weight Update EWMA | 5 | ✅ `update_weights` + 8 tests |
| §4.2 predicted_outcome | 1, 3 | ✅ domain.py field + engine sets it |
| §5 FactorRegistry Persistence | 2 | ✅ `_load_from_db`, `_init_default_factors`, DB upsert |
| §5.1.1 KernelFactor schema | 1 | ✅ Composite key |
| §6 EloOddsEngine Weight Reading | 3 | ✅ Constructor injection + `predicted_outcome` |
| §7 Calibration Model | 4 | ✅ Linear regression + 7 tests |
| §8 EngineScore Persistence | 6 | ✅ DB write + `confidence_calibration` |
| §8.2 KernelEngineScore schema | 1 | ✅ New column |
| §9 Dynamic Engine Selection | 7 | ✅ `select(engine_name, competition)` |
| §10 Prediction History | 6 | ✅ `record_prediction` writes history |
| §10.1.1 KernelPredictionHistory schema | 1 | ✅ New column |
| §11 process_outcome Full Loop | 7 | ✅ 5-step loop with flag gating |
| §12 Configuration | 7 | ✅ `PHASE3_LEARNING_ENABLED` + 6 params |
| §12.2 _get_kernel() Changes | 7 | ✅ Shared instances |
| §13 Testing — 44 tests | 1-7 | ✅ 9 test files, 44 tests |
| §14 Constraints 1-18 | All | ✅ Each constraint addressed |

### Placeholder Scan

No TBD, TODO, or placeholder text found. All code blocks contain complete implementations.

### Type Consistency

- `FactorRegistry.__init__(session_factory=None)` — consistent across Tasks 2, 3, 5, 7
- `EloOddsEngine(factor_registry=None)` — consistent across Tasks 3, 7
- `KernelLearningService(factor_registry=None)` — consistent across Tasks 5, 6, 7
- `EngineRegistry(learning_service=None)` — consistent across Tasks 7
- `EngineRegistry.select(engine_name, competition=None)` — consistent across Tasks 7
- `ContributionItem.predicted_outcome` — consistent across Tasks 1, 3, 4, 5
- `KernelCalibration` columns — consistent across Tasks 1, 4, 6
