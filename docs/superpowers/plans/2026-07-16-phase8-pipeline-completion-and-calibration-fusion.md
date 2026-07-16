# Phase 8: Pipeline Completion + Calibration Fusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the Phase 7 data pipeline (2 stub scheduler jobs) and fuse Subproject D's market calibration into Subproject B's trust computation, forming a closed "predict → align → recommend → settle → calibrate → improve" loop.

**Architecture:** Two independent subprojects. Subproject A implements 2 placeholder scheduler jobs (`_job_capture_market_snapshots` + `_job_fetch_traditional_odds`) with a new `kernel_traditional_odds_snapshots` table for traditional sportsbook odds. Subproject B adds `CalibrationFusionService` that reads both Phase 3's `KernelCalibration` and Phase 7 D's `KernelMarketCalibration`, computing a sample-count-weighted composite trust that replaces B's Phase-3-only `_compute_trust` when `PHASE8_CALIBRATION_FUSION_ENABLED=true`.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.x (DeclarativeBase), httpx (async), pytest, React/Next.js, TypeScript, Vitest

## Global Constraints

- `PHASE8_CALIBRATION_FUSION_ENABLED` feature flag must default to OFF — when false, `EdgeDetectorService._compute_trust` falls back to Phase 7 Phase-3-only behavior (zero-invasion)
- `PHASE7_SPORT_MARKET_BRIDGE_ENABLED` must gate both scheduler jobs (503/no-op when false)
- `ODDS_API_ENABLED` must gate `_job_fetch_traditional_odds` (no-op when false, in addition to PHASE7 gate)
- New `kernel_traditional_odds_snapshots` table uses `kernel_` prefix; existing `kernel_market_snapshots` table structure is NOT modified
- `PredictionKernel`, `LearningService`, `MarketSettlementService`, `SportRecommendationService`, and `domain.py` must NOT be modified
- `ODDS_API_FETCH_INTERVAL_HOURS` must be renamed to `ODDS_FETCH_INTERVAL_MIN` (unit: hours → minutes, default: 6 → 10), updating 3 references: `config.py:1064-1065` definition + `scheduler.py:877` IntervalTrigger
- `PHASE7_SPORT_MARKET_SNAPSHOT_INTERVAL_SECONDS` config must be removed (redundant with `MARKET_SNAPSHOT_INTERVAL_MIN`)
- All `PHASE7_*` and `PHASE8_*` settings must be documented in `.env.example` with bilingual comments
- Route order: static paths (`/calibrations`, `/history`) before dynamic `/{match_id}` to avoid FastAPI catch-all (lesson from Subproject C)
- Traditional odds snapshots use unique constraint `(match_id, mapped_outcome, captured_at)` for idempotent scheduler retries
- `TraditionalOddsStore` follows the existing Store pattern: keyword-only args, session-per-call, `_row_to_dict` converter, fail-closed reads (return `[]`/`None` on exception)
- `CalibrationFusionService.compute_trust` must read both calibration tables at call time (not module load), consistent with Phase 4/5 pattern
- Composite trust weight: `w1 = phase3_count / (phase3_count + market_count)`, `w2 = market_count / (phase3_count + market_count)` — sample-count proportional
- TDD must be strictly followed for all backend DB functions (RED → GREEN)
- Subagent-driven task execution must be used for Phase 8 implementation, with independent sub-agents per task and inter-task reviews
- All 183+ existing tests must pass with zero modifications when `PHASE8_CALIBRATION_FUSION_ENABLED=false` (default)

---

## File Structure

### New Files (8)

| File | Responsibility |
|------|---------------|
| `backend/app/kernel/traditional_odds_store.py` | CRUD for `kernel_traditional_odds_snapshots` table |
| `backend/app/kernel/calibration_fusion_service.py` | `CalibrationFusionService` — reads both calibration tables, computes composite trust |
| `backend/app/api/routes/sport_odds.py` | 2 GET endpoints for traditional odds (latest + history) |
| `backend/tests/test_traditional_odds_store.py` | Tests for `TraditionalOddsStore` |
| `backend/tests/test_calibration_fusion_service.py` | Tests for `CalibrationFusionService` |
| `backend/tests/test_sport_odds_routes.py` | Tests for sport-odds API routes |
| `frontend/src/lib/sport-odds-api.ts` | Frontend API client for traditional odds |
| `frontend/src/components/sports/markets/TraditionalOddsChart.tsx` | Line chart comparing traditional vs Polymarket odds |

### Modified Files (7)

| File | Changes |
|------|---------|
| `backend/app/core/config.py` | Remove `PHASE7_SPORT_MARKET_SNAPSHOT_INTERVAL_SECONDS`; rename `ODDS_API_FETCH_INTERVAL_HOURS` → `ODDS_FETCH_INTERVAL_MIN`; add `PHASE8_CALIBRATION_FUSION_ENABLED` |
| `backend/app/core/scheduler.py` | Update `IntervalTrigger` reference; fill 2 stub job bodies (`_job_capture_market_snapshots` + `_job_fetch_traditional_odds`) |
| `backend/app/kernel/kernel_db.py` | Append `KernelTraditionalOddsSnapshot` table class |
| `backend/app/kernel/edge_detector_service.py` | Modify `_compute_trust` to delegate to `CalibrationFusionService` when `PHASE8_CALIBRATION_FUSION_ENABLED=true`; extract original logic to `_compute_trust_phase3` |
| `backend/app/services/odds_api_service.py` | Add `fetch_all_sports_odds()` function for dynamic all-sports fetch |
| `backend/app/kernel/sport_market_bridge_service.py` | Add `fetch_current_price(contract_id)` method |
| `backend/app/api/router.py` | Import + include `sport_odds` router |
| `backend/.env.example` | Add Phase 7 + Phase 8 settings documentation block |
| `frontend/src/app/sports/markets/[match_id]/page.tsx` | Add TraditionalOddsChart tab |

---

## Task 1: Config + DB Table + TraditionalOddsStore

**Files:**
- Modify: `backend/app/core/config.py` (lines 1047-1048 remove, 1064-1065 rename, ~1108 add)
- Modify: `backend/app/kernel/kernel_db.py` (append after `KernelMarketSettlement` class)
- Create: `backend/app/kernel/traditional_odds_store.py`
- Test: `backend/tests/test_traditional_odds_store.py`

**Interfaces:**
- Consumes: `get_kernel_session` from `kernel_db.py`
- Produces: `TraditionalOddsStore` class with `append_snapshot`, `get_latest_snapshot`, `get_snapshots` methods; `KernelTraditionalOddsSnapshot` ORM model

- [ ] **Step 1: Write the failing test for KernelTraditionalOddsSnapshot table + TraditionalOddsStore**

Create `backend/tests/test_traditional_odds_store.py`:

```python
"""Tests for TraditionalOddsStore — CRUD for kernel_traditional_odds_snapshots."""
from datetime import datetime, timedelta, timezone

import pytest

from app.kernel.kernel_db import init_kernel_db, close_kernel_db
from app.kernel.traditional_odds_store import TraditionalOddsStore


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "traditional_odds_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def test_append_snapshot_returns_dict(kernel_db):
    """append_snapshot inserts a row and returns it as dict."""
    store = TraditionalOddsStore()
    now = _utcnow()
    result = store.append_snapshot(
        match_id="nba-2026-g1",
        mapped_outcome="home_win",
        competition="nba",
        implied_prob=0.65,
        decimal_odds=1.538,
        bookmaker="pinnacle",
        bookmakers_count=12,
        captured_at=now,
    )
    assert result["id"] is not None
    assert result["match_id"] == "nba-2026-g1"
    assert result["mapped_outcome"] == "home_win"
    assert result["competition"] == "nba"
    assert result["implied_prob"] == pytest.approx(0.65)
    assert result["decimal_odds"] == pytest.approx(1.538)
    assert result["bookmaker"] == "pinnacle"
    assert result["bookmakers_count"] == 12
    assert result["captured_at"] == now


def test_get_latest_snapshot_returns_most_recent(kernel_db):
    """get_latest_snapshot returns the most recent snapshot."""
    store = TraditionalOddsStore()
    t1 = _utcnow() - timedelta(minutes=10)
    t2 = _utcnow()
    store.append_snapshot(
        match_id="m1", mapped_outcome="home_win", competition="nba",
        implied_prob=0.60, decimal_odds=1.667, captured_at=t1,
    )
    store.append_snapshot(
        match_id="m1", mapped_outcome="home_win", competition="nba",
        implied_prob=0.65, decimal_odds=1.538, captured_at=t2,
    )
    latest = store.get_latest_snapshot(match_id="m1")
    assert latest is not None
    assert latest["implied_prob"] == pytest.approx(0.65)
    assert latest["captured_at"] == t2


def test_get_latest_snapshot_filtered_by_outcome(kernel_db):
    """get_latest_snapshot filters by mapped_outcome when provided."""
    store = TraditionalOddsStore()
    now = _utcnow()
    store.append_snapshot(
        match_id="m1", mapped_outcome="home_win", competition="epl",
        implied_prob=0.45, decimal_odds=2.222, captured_at=now,
    )
    store.append_snapshot(
        match_id="m1", mapped_outcome="away_win", competition="epl",
        implied_prob=0.30, decimal_odds=3.333, captured_at=now,
    )
    latest = store.get_latest_snapshot(match_id="m1", mapped_outcome="away_win")
    assert latest is not None
    assert latest["mapped_outcome"] == "away_win"
    assert latest["implied_prob"] == pytest.approx(0.30)


def test_get_latest_snapshot_returns_none_when_no_data(kernel_db):
    """get_latest_snapshot returns None for non-existent match."""
    store = TraditionalOddsStore()
    result = store.get_latest_snapshot(match_id="nonexistent")
    assert result is None


def test_get_snapshots_returns_all_oldest_first(kernel_db):
    """get_snapshots returns all snapshots ordered by captured_at ascending."""
    store = TraditionalOddsStore()
    t1 = _utcnow() - timedelta(minutes=20)
    t2 = _utcnow() - timedelta(minutes=10)
    t3 = _utcnow()
    for t, prob in [(t1, 0.55), (t2, 0.60), (t3, 0.65)]:
        store.append_snapshot(
            match_id="m1", mapped_outcome="home_win", competition="nba",
            implied_prob=prob, decimal_odds=1.0 / prob, captured_at=t,
        )
    snapshots = store.get_snapshots(match_id="m1")
    assert len(snapshots) == 3
    assert snapshots[0]["captured_at"] == t1
    assert snapshots[2]["captured_at"] == t3
    assert snapshots[0]["implied_prob"] == pytest.approx(0.55)
    assert snapshots[2]["implied_prob"] == pytest.approx(0.65)


def test_get_snapshots_filtered_by_outcome(kernel_db):
    """get_snapshots filters by mapped_outcome when provided."""
    store = TraditionalOddsStore()
    now = _utcnow()
    store.append_snapshot(
        match_id="m1", mapped_outcome="home_win", competition="epl",
        implied_prob=0.45, decimal_odds=2.222, captured_at=now,
    )
    store.append_snapshot(
        match_id="m1", mapped_outcome="draw", competition="epl",
        implied_prob=0.28, decimal_odds=3.571, captured_at=now,
    )
    store.append_snapshot(
        match_id="m1", mapped_outcome="away_win", competition="epl",
        implied_prob=0.27, decimal_odds=3.704, captured_at=now,
    )
    home_only = store.get_snapshots(match_id="m1", mapped_outcome="home_win")
    assert len(home_only) == 1
    assert home_only[0]["mapped_outcome"] == "home_win"


def test_get_snapshots_returns_empty_when_no_data(kernel_db):
    """get_snapshots returns empty list for non-existent match."""
    store = TraditionalOddsStore()
    result = store.get_snapshots(match_id="nonexistent")
    assert result == []


def test_append_snapshot_idempotent_via_unique_constraint(kernel_db):
    """Duplicate (match_id, mapped_outcome, captured_at) raises and does not insert."""
    store = TraditionalOddsStore()
    now = _utcnow()
    store.append_snapshot(
        match_id="m1", mapped_outcome="home_win", competition="nba",
        implied_prob=0.65, decimal_odds=1.538, captured_at=now,
    )
    # Second insert with same (match_id, mapped_outcome, captured_at) should raise
    with pytest.raises(Exception):
        store.append_snapshot(
            match_id="m1", mapped_outcome="home_win", competition="nba",
            implied_prob=0.70, decimal_odds=1.429, captured_at=now,
        )
    # Verify only 1 row exists
    snapshots = store.get_snapshots(match_id="m1")
    assert len(snapshots) == 1
    assert snapshots[0]["implied_prob"] == pytest.approx(0.65)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; python -m pytest tests/test_traditional_odds_store.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.kernel.traditional_odds_store'`

- [ ] **Step 3: Add `KernelTraditionalOddsSnapshot` table to `kernel_db.py`**

Append the following class after the `KernelMarketCalibration` class in `backend/app/kernel/kernel_db.py`:

```python
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
```

- [ ] **Step 4: Create `TraditionalOddsStore`**

Create `backend/app/kernel/traditional_odds_store.py`:

```python
"""Persistence for traditional sportsbook odds snapshots (append-only time-series).

Separate from MarketSnapshotStore because the field semantics differ:
- Polymarket snapshots have link_id, liquidity, volume
- Traditional odds have decimal_odds, bookmaker, bookmakers_count

Follows the existing Store pattern: keyword-only args, session-per-call,
_row_to_dict converter, fail-closed reads.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.kernel.kernel_db import (
    KernelTraditionalOddsSnapshot,
    get_kernel_session,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(row: KernelTraditionalOddsSnapshot) -> dict[str, Any]:
    return {
        "id": row.id,
        "match_id": row.match_id,
        "mapped_outcome": row.mapped_outcome,
        "competition": row.competition,
        "implied_prob": row.implied_prob,
        "decimal_odds": row.decimal_odds,
        "bookmaker": row.bookmaker,
        "bookmakers_count": row.bookmakers_count,
        "captured_at": row.captured_at,
    }


class TraditionalOddsStore:
    """Append-only traditional odds snapshot store."""

    def append_snapshot(
        self,
        *,
        match_id: str,
        mapped_outcome: str,
        competition: str,
        implied_prob: float,
        decimal_odds: float,
        bookmaker: str | None = None,
        bookmakers_count: int = 0,
        captured_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Insert a snapshot. Returns the inserted row as dict.

        Idempotent via unique constraint (match_id, mapped_outcome, captured_at).
        Raises IntegrityError on duplicate.
        """
        when = captured_at or _utcnow()
        session = get_kernel_session()
        try:
            row = KernelTraditionalOddsSnapshot(
                match_id=match_id,
                mapped_outcome=mapped_outcome,
                competition=competition,
                implied_prob=implied_prob,
                decimal_odds=decimal_odds,
                bookmaker=bookmaker,
                bookmakers_count=bookmakers_count,
                captured_at=when,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _row_to_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_latest_snapshot(
        self, *, match_id: str, mapped_outcome: str | None = None
    ) -> dict[str, Any] | None:
        """Most recent snapshot for a match, optionally filtered by outcome."""
        session = get_kernel_session()
        try:
            q = session.query(KernelTraditionalOddsSnapshot).filter_by(match_id=match_id)
            if mapped_outcome is not None:
                q = q.filter(KernelTraditionalOddsSnapshot.mapped_outcome == mapped_outcome)
            row = q.order_by(KernelTraditionalOddsSnapshot.captured_at.desc()).first()
            return _row_to_dict(row) if row is not None else None
        except Exception:
            return None
        finally:
            session.close()

    def get_snapshots(
        self, *, match_id: str, mapped_outcome: str | None = None
    ) -> list[dict[str, Any]]:
        """All snapshots for a match (oldest first), optionally filtered by outcome."""
        session = get_kernel_session()
        try:
            q = session.query(KernelTraditionalOddsSnapshot).filter_by(match_id=match_id)
            if mapped_outcome is not None:
                q = q.filter(KernelTraditionalOddsSnapshot.mapped_outcome == mapped_outcome)
            rows = q.order_by(KernelTraditionalOddsSnapshot.captured_at.asc()).all()
            return [_row_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()
```

- [ ] **Step 5: Update `config.py` — remove redundant config, rename interval, add Phase 8 flag**

In `backend/app/core/config.py`:

1. **Delete** lines 1047-1049 (the `PHASE7_SPORT_MARKET_SNAPSHOT_INTERVAL_SECONDS` setting):
```python
# DELETE these 3 lines:
    PHASE7_SPORT_MARKET_SNAPSHOT_INTERVAL_SECONDS: int = int(
        os.getenv("PHASE7_SPORT_MARKET_SNAPSHOT_INTERVAL_SECONDS", "300")
    )
```

2. **Replace** lines 1064-1065 (rename `ODDS_API_FETCH_INTERVAL_HOURS` to `ODDS_FETCH_INTERVAL_MIN`):
```python
# OLD:
    ODDS_API_FETCH_INTERVAL_HOURS: int = int(
        os.getenv("ODDS_API_FETCH_INTERVAL_HOURS", "6")
    )
# NEW:
    ODDS_FETCH_INTERVAL_MIN: int = int(
        os.getenv("ODDS_FETCH_INTERVAL_MIN", "10")
    )
```

3. **Add** before `settings = Settings()` (after the Phase 7 Subproject D block):
```python
    # Phase 8 — Calibration Fusion (default OFF). When true,
    # EdgeDetectorService._compute_trust delegates to
    # CalibrationFusionService which reads both Phase 3's
    # KernelCalibration and Phase 7 D's KernelMarketCalibration to
    # compute a sample-count-weighted composite trust. When false
    # (default), _compute_trust falls back to Phase 7 Phase-3-only
    # behavior — zero-invasion.
    PHASE8_CALIBRATION_FUSION_ENABLED: bool = _env_bool(
        "PHASE8_CALIBRATION_FUSION_ENABLED", "false"
    )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend; python -m pytest tests/test_traditional_odds_store.py -v --tb=short`
Expected: 9 PASS

- [ ] **Step 7: Run regression tests to verify config changes don't break anything**

Run: `cd backend; python -m pytest tests/test_edge_detector_service.py tests/test_sport_edge_routes.py tests/test_phase7_e2e_integration.py -v --tb=short`
Expected: All PASS (the renamed config is not yet referenced by scheduler — that's Task 3)

- [ ] **Step 8: Commit**

```bash
git add backend/app/core/config.py backend/app/kernel/kernel_db.py backend/app/kernel/traditional_odds_store.py backend/tests/test_traditional_odds_store.py
git commit -m "feat(phase8): add KernelTraditionalOddsSnapshot table + TraditionalOddsStore + config cleanup"
```

---

## Task 2: CalibrationFusionService

**Files:**
- Create: `backend/app/kernel/calibration_fusion_service.py`
- Test: `backend/tests/test_calibration_fusion_service.py`

**Interfaces:**
- Consumes: `get_calibration(engine, competition)` from `kernel_db.py` (returns `KernelCalibration` or None); `MarketSettlementStore.get_calibrations(engine, competition)` from `market_settlement_store.py` (returns `list[dict]`)
- Produces: `CalibrationFusionService` class with `compute_trust(engine, competition) -> CompositeTrust`; `CompositeTrust` dataclass with `trust`, `phase3_trust`, `market_trust`, `phase3_weight`, `market_weight`, `phase3_sample_count`, `market_sample_count`, `source` fields

- [ ] **Step 1: Write the failing test for CalibrationFusionService**

Create `backend/tests/test_calibration_fusion_service.py`:

```python
"""Tests for CalibrationFusionService — sample-count-weighted trust fusion."""
from datetime import datetime, timezone

import pytest

from app.kernel.kernel_db import (
    init_kernel_db, close_kernel_db,
    KernelCalibration, KernelMarketCalibration,
    get_kernel_session,
)
from app.kernel.calibration_fusion_service import (
    CalibrationFusionService, CompositeTrust,
)


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "fusion_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _seed_phase3_calibration(
    engine="BasketballEngine", competition="nba",
    avg_accuracy=0.72, sample_count=20,
):
    """Insert a KernelCalibration row."""
    session = get_kernel_session()
    try:
        session.add(KernelCalibration(
            engine=engine, competition=competition, slope=1.0, intercept=0.0,
            sample_count=sample_count, avg_confidence=0.65,
            avg_accuracy=avg_accuracy, last_updated=_utcnow(),
        ))
        session.commit()
    finally:
        session.close()


def _seed_market_calibration(
    engine="BasketballEngine", competition="nba",
    direction_accuracy=0.80, sample_count=30,
):
    """Insert a KernelMarketCalibration row."""
    session = get_kernel_session()
    try:
        session.add(KernelMarketCalibration(
            engine=engine, competition=competition, slope=0.95, intercept=0.02,
            sample_count=sample_count, avg_brier=0.15, avg_signed_error=-0.01,
            direction_accuracy=direction_accuracy, last_updated=_utcnow(),
        ))
        session.commit()
    finally:
        session.close()


# --- Pure trust computation tests ---

def test_compute_trust_dormant_when_both_tables_empty(kernel_db):
    """Both tables empty → dormant trust (0.5), source='dormant'."""
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    assert result.source == "dormant"
    assert result.trust == pytest.approx(0.5)
    assert result.phase3_trust == pytest.approx(0.5)
    assert result.market_trust == pytest.approx(0.5)
    assert result.phase3_weight == pytest.approx(0.0)
    assert result.market_weight == pytest.approx(0.0)
    assert result.phase3_sample_count == 0
    assert result.market_sample_count == 0


def test_compute_trust_phase3_only(kernel_db):
    """Only Phase 3 has data → phase3_trust, source='phase3_only'."""
    _seed_phase3_calibration(avg_accuracy=0.75, sample_count=25)
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    assert result.source == "phase3_only"
    assert result.phase3_trust == pytest.approx(0.75)
    assert result.trust == pytest.approx(0.75)
    assert result.phase3_weight == pytest.approx(1.0)
    assert result.market_weight == pytest.approx(0.0)
    assert result.phase3_sample_count == 25
    assert result.market_sample_count == 0


def test_compute_trust_market_only(kernel_db):
    """Only market calibration has data → market_trust, source='market_only'."""
    _seed_market_calibration(direction_accuracy=0.82, sample_count=35)
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    assert result.source == "market_only"
    assert result.market_trust == pytest.approx(0.82)
    assert result.trust == pytest.approx(0.82)
    assert result.phase3_weight == pytest.approx(0.0)
    assert result.market_weight == pytest.approx(1.0)
    assert result.phase3_sample_count == 0
    assert result.market_sample_count == 35


def test_compute_trust_fusion_weighted_by_sample_count(kernel_db):
    """Both tables have data → weighted fusion, source='fusion'.

    phase3: avg_accuracy=0.72, sample_count=20
    market: direction_accuracy=0.80, sample_count=30
    w1 = 20 / (20 + 30) = 0.4
    w2 = 30 / (20 + 30) = 0.6
    composite = 0.4 * 0.72 + 0.6 * 0.80 = 0.288 + 0.480 = 0.768
    """
    _seed_phase3_calibration(avg_accuracy=0.72, sample_count=20)
    _seed_market_calibration(direction_accuracy=0.80, sample_count=30)
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    assert result.source == "fusion"
    assert result.phase3_trust == pytest.approx(0.72)
    assert result.market_trust == pytest.approx(0.80)
    assert result.phase3_weight == pytest.approx(0.4)
    assert result.market_weight == pytest.approx(0.6)
    assert result.trust == pytest.approx(0.768)
    assert result.phase3_sample_count == 20
    assert result.market_sample_count == 30


def test_compute_trust_phase3_dormant_when_sample_count_below_min(kernel_db):
    """Phase 3 with sample_count < CALIBRATION_FEEDBACK_MIN_SAMPLES → dormant (0.5)."""
    _seed_phase3_calibration(avg_accuracy=0.90, sample_count=3)  # below MIN (10)
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    # Only Phase 3 with low sample_count → phase3 dormant, no market data
    assert result.source == "phase3_only"  # has a row, just dormant
    assert result.phase3_trust == pytest.approx(0.5)  # dormant
    assert result.trust == pytest.approx(0.5)


def test_compute_trust_market_dormant_when_sample_count_below_min(kernel_db):
    """Market calibration with sample_count < MIN_SAMPLES_FOR_MARKET_CALIBRATION → dormant (0.5)."""
    _seed_market_calibration(direction_accuracy=0.95, sample_count=3)  # below MIN (10)
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    assert result.source == "market_only"
    assert result.market_trust == pytest.approx(0.5)  # dormant
    assert result.trust == pytest.approx(0.5)


def test_compute_trust_clamped_to_floor(kernel_db):
    """Trust values below DIAGNOSIS_TRUST_FLOOR are clamped."""
    _seed_phase3_calibration(avg_accuracy=0.10, sample_count=20)  # very low accuracy
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    # DIAGNOSIS_TRUST_FLOOR is 0.3 (from config)
    from app.core import config
    expected_floor = config.settings.DIAGNOSIS_TRUST_FLOOR
    assert result.phase3_trust == pytest.approx(expected_floor)
    assert result.trust == pytest.approx(expected_floor)


def test_compute_trust_fusion_with_one_dormant_source(kernel_db):
    """Fusion where one source is dormant (low sample_count).

    phase3: qualified (sample_count=20, accuracy=0.72)
    market: dormant (sample_count=3, direction_accuracy=0.95)
    → fusion, but market_trust = 0.5 (dormant)
    w1 = 20 / (20 + 3) = 0.8696
    w2 = 3 / (20 + 3) = 0.1304
    composite = 0.8696 * 0.72 + 0.1304 * 0.5 = 0.6261 + 0.0652 = 0.6913
    """
    _seed_phase3_calibration(avg_accuracy=0.72, sample_count=20)
    _seed_market_calibration(direction_accuracy=0.95, sample_count=3)
    svc = CalibrationFusionService()
    result = svc.compute_trust("BasketballEngine", "nba")
    assert result.source == "fusion"
    assert result.phase3_trust == pytest.approx(0.72)
    assert result.market_trust == pytest.approx(0.5)  # dormant
    assert result.trust == pytest.approx(0.6913, abs=0.01)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; python -m pytest tests/test_calibration_fusion_service.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.kernel.calibration_fusion_service'`

- [ ] **Step 3: Create `CalibrationFusionService`**

Create `backend/app/kernel/calibration_fusion_service.py`:

```python
"""Calibration Fusion Service — combines Phase 3 and Phase 7 D calibration signals.

Reads both calibration tables:
- KernelCalibration (Phase 3 match-outcome calibration): avg_accuracy
- KernelMarketCalibration (Phase 7 D market-settlement calibration): direction_accuracy

Computes a sample-count-weighted composite trust. When PHASE8_CALIBRATION_FUSION_ENABLED
is false, EdgeDetectorService._compute_trust bypasses this service entirely (zero-invasion).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core import config
from app.kernel.kernel_db import get_calibration
from app.kernel.market_settlement_store import MarketSettlementStore


@dataclass(frozen=True)
class CompositeTrust:
    """Result of fusing Phase 3 and market calibration signals."""
    trust: float                     # fused trust value (the one B uses)
    phase3_trust: float              # Phase 3 calibration trust
    market_trust: float              # D market calibration trust
    phase3_weight: float             # w1 (0.0 to 1.0)
    market_weight: float             # w2 (0.0 to 1.0)
    phase3_sample_count: int
    market_sample_count: int
    source: str                      # "dormant" / "phase3_only" / "market_only" / "fusion"


def _clamp_trust(value: float) -> float:
    """Clamp trust to [DIAGNOSIS_TRUST_FLOOR, 1.0]."""
    return max(
        config.settings.DIAGNOSIS_TRUST_FLOOR,
        min(value, 1.0),
    )


def _compute_phase3_trust(avg_accuracy: float, sample_count: int) -> float:
    """Phase 3 trust: dormant if below MIN, else clamped avg_accuracy."""
    if sample_count < config.settings.CALIBRATION_FEEDBACK_MIN_SAMPLES:
        return config.settings.DIAGNOSIS_DORMANT_TRUST
    return _clamp_trust(avg_accuracy)


def _compute_market_trust(direction_accuracy: float, sample_count: int) -> float:
    """Market trust: dormant if below MIN, else clamped direction_accuracy."""
    if sample_count < config.settings.MIN_SAMPLES_FOR_MARKET_CALIBRATION:
        return config.settings.DIAGNOSIS_DORMANT_TRUST
    return _clamp_trust(direction_accuracy)


class CalibrationFusionService:
    """Fuses Phase 3 and Phase 7 D calibration signals into a composite trust."""

    def __init__(self) -> None:
        self._settlement_store = MarketSettlementStore()

    def compute_trust(self, engine: str, competition: str) -> CompositeTrust:
        """Compute composite trust by sample-count-weighted fusion.

        Rules:
        1. Both tables have no data → DIAGNOSIS_DORMANT_TRUST (0.5), source="dormant"
        2. Only Phase 3 has data → phase3_trust, source="phase3_only"
        3. Only market has data → market_trust, source="market_only"
        4. Both have data → weighted fusion, source="fusion"
           w1 = phase3_count / (phase3_count + market_count)
           w2 = market_count / (phase3_count + market_count)
           composite = w1 * phase3_trust + w2 * market_trust
        """
        # Read Phase 3 calibration
        phase3_cal = get_calibration(engine, competition)
        phase3_has_data = phase3_cal is not None

        # Read market calibration (D)
        market_cals = self._settlement_store.get_calibrations(
            engine=engine, competition=competition
        )
        market_cal = market_cals[0] if market_cals else None
        market_has_data = market_cal is not None

        dormant = config.settings.DIAGNOSIS_DORMANT_TRUST

        # Case 1: both empty
        if not phase3_has_data and not market_has_data:
            return CompositeTrust(
                trust=dormant,
                phase3_trust=dormant,
                market_trust=dormant,
                phase3_weight=0.0,
                market_weight=0.0,
                phase3_sample_count=0,
                market_sample_count=0,
                source="dormant",
            )

        # Compute per-source trust values
        if phase3_has_data:
            phase3_trust = _compute_phase3_trust(
                phase3_cal.avg_accuracy, phase3_cal.sample_count
            )
            phase3_count = phase3_cal.sample_count
        else:
            phase3_trust = dormant
            phase3_count = 0

        if market_has_data:
            market_trust = _compute_market_trust(
                market_cal["direction_accuracy"], market_cal["sample_count"]
            )
            market_count = market_cal["sample_count"]
        else:
            market_trust = dormant
            market_count = 0

        # Case 2: only Phase 3
        if phase3_has_data and not market_has_data:
            return CompositeTrust(
                trust=phase3_trust,
                phase3_trust=phase3_trust,
                market_trust=dormant,
                phase3_weight=1.0,
                market_weight=0.0,
                phase3_sample_count=phase3_count,
                market_sample_count=0,
                source="phase3_only",
            )

        # Case 3: only market
        if market_has_data and not phase3_has_data:
            return CompositeTrust(
                trust=market_trust,
                phase3_trust=dormant,
                market_trust=market_trust,
                phase3_weight=0.0,
                market_weight=1.0,
                phase3_sample_count=0,
                market_sample_count=market_count,
                source="market_only",
            )

        # Case 4: fusion (both have data)
        total = phase3_count + market_count
        if total == 0:
            # Both have rows but zero sample_count — treat as dormant
            return CompositeTrust(
                trust=dormant,
                phase3_trust=phase3_trust,
                market_trust=market_trust,
                phase3_weight=0.0,
                market_weight=0.0,
                phase3_sample_count=phase3_count,
                market_sample_count=market_count,
                source="dormant",
            )

        w1 = phase3_count / total
        w2 = market_count / total
        composite = w1 * phase3_trust + w2 * market_trust

        return CompositeTrust(
            trust=composite,
            phase3_trust=phase3_trust,
            market_trust=market_trust,
            phase3_weight=w1,
            market_weight=w2,
            phase3_sample_count=phase3_count,
            market_sample_count=market_count,
            source="fusion",
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; python -m pytest tests/test_calibration_fusion_service.py -v --tb=short`
Expected: 9 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/kernel/calibration_fusion_service.py backend/tests/test_calibration_fusion_service.py
git commit -m "feat(phase8): add CalibrationFusionService with sample-count-weighted trust fusion"
```

---

## Task 3: EdgeDetectorService._compute_trust Modification + Scheduler Jobs + fetch_all_sports_odds + fetch_current_price

**Files:**
- Modify: `backend/app/kernel/edge_detector_service.py` (lines 281-300 — `_compute_trust`)
- Modify: `backend/app/services/odds_api_service.py` (append `fetch_all_sports_odds`)
- Modify: `backend/app/kernel/sport_market_bridge_service.py` (append `fetch_current_price`)
- Modify: `backend/app/core/scheduler.py` (lines 577-605 fill 2 stubs, line 877 update IntervalTrigger)
- Test: `backend/tests/test_calibration_fusion_service.py` (add integration test for EdgeDetectorService)

**Interfaces:**
- Consumes: `CalibrationFusionService` from Task 2; `TraditionalOddsStore` from Task 1; `fetch_all_sports_odds` (new); `fetch_current_price` (new)
- Produces: Modified `EdgeDetectorService._compute_trust` that delegates to fusion when `PHASE8_CALIBRATION_FUSION_ENABLED=true`; 2 complete scheduler jobs

- [ ] **Step 1: Write the failing test for EdgeDetectorService trust delegation**

Append to `backend/tests/test_calibration_fusion_service.py`:

```python
def test_edge_detector_delegates_to_fusion_when_enabled(kernel_db, monkeypatch):
    """When PHASE8_CALIBRATION_FUSION_ENABLED=true, EdgeDetectorService uses fusion."""
    from app.core import config
    monkeypatch.setattr(config.settings, "PHASE8_CALIBRATION_FUSION_ENABLED", True)
    _seed_phase3_calibration(avg_accuracy=0.72, sample_count=20)
    _seed_market_calibration(direction_accuracy=0.80, sample_count=30)
    # Expected: 0.4 * 0.72 + 0.6 * 0.80 = 0.768
    from app.kernel.edge_detector_service import EdgeDetectorService
    svc = EdgeDetectorService()
    trust = svc._compute_trust("BasketballEngine", "nba")
    assert trust == pytest.approx(0.768)


def test_edge_detector_falls_back_to_phase3_when_disabled(kernel_db, monkeypatch):
    """When PHASE8_CALIBRATION_FUSION_ENABLED=false, EdgeDetectorService uses Phase 3 only."""
    from app.core import config
    monkeypatch.setattr(config.settings, "PHASE8_CALIBRATION_FUSION_ENABLED", False)
    _seed_phase3_calibration(avg_accuracy=0.72, sample_count=20)
    _seed_market_calibration(direction_accuracy=0.80, sample_count=30)
    from app.kernel.edge_detector_service import EdgeDetectorService
    svc = EdgeDetectorService()
    trust = svc._compute_trust("BasketballEngine", "nba")
    # Phase 3 only: 0.72 (qualified, not dormant)
    assert trust == pytest.approx(0.72)
    # Should NOT be the fusion value 0.768
    assert trust != pytest.approx(0.768)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; python -m pytest tests/test_calibration_fusion_service.py::test_edge_detector_delegates_to_fusion_when_enabled -v --tb=short`
Expected: FAIL (trust is 0.72, not 0.768 — because `_compute_trust` still reads Phase 3 only)

- [ ] **Step 3: Modify `EdgeDetectorService._compute_trust`**

In `backend/app/kernel/edge_detector_service.py`, replace the `_compute_trust` method (lines 281-300) with:

```python
    def _compute_trust(self, engine_name: str, competition: str) -> float:
        """Trust computation — Phase 8 adds calibration fusion.

        When PHASE8_CALIBRATION_FUSION_ENABLED is true, delegates to
        CalibrationFusionService which reads both Phase 3's
        KernelCalibration and Phase 7 D's KernelMarketCalibration to
        compute a sample-count-weighted composite trust. When false
        (default), falls back to Phase 7 Phase-3-only behavior —
        zero-invasion.
        """
        if not config.settings.PHASE8_CALIBRATION_FUSION_ENABLED:
            return self._compute_trust_phase3(engine_name, competition)

        from app.kernel.calibration_fusion_service import CalibrationFusionService
        fusion = CalibrationFusionService()
        composite = fusion.compute_trust(engine_name, competition)
        return composite.trust

    def _compute_trust_phase3(self, engine_name: str, competition: str) -> float:
        """Phase 7 behavior — Phase 3 KernelCalibration only.

        - No calibration row (cold start) -> DIAGNOSIS_DORMANT_TRUST (0.5)
        - sample_count < CALIBRATION_FEEDBACK_MIN_SAMPLES (dormant) -> 0.5
        - Qualified -> clamp(avg_accuracy, DIAGNOSIS_TRUST_FLOOR, 1.0)
        """
        calibration = get_calibration(engine_name, competition)
        if calibration is None:
            return config.settings.DIAGNOSIS_DORMANT_TRUST

        if calibration.sample_count < config.settings.CALIBRATION_FEEDBACK_MIN_SAMPLES:
            return config.settings.DIAGNOSIS_DORMANT_TRUST

        trust = max(
            config.settings.DIAGNOSIS_TRUST_FLOOR,
            min(calibration.avg_accuracy, 1.0),
        )
        return trust
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; python -m pytest tests/test_calibration_fusion_service.py -v --tb=short`
Expected: 11 PASS (9 original + 2 new)

- [ ] **Step 5: Add `fetch_all_sports_odds` to `odds_api_service.py`**

Append the following to `backend/app/services/odds_api_service.py`:

```python
async def fetch_all_sports_odds() -> dict[str, list[dict]]:
    """Fetch odds for all available sports from The Odds API.

    1. GET /sports to discover all active sport keys.
    2. For each sport key, GET /sports/{key}/odds.
    3. Respect quota: stop early if x-requests-remaining <= 0.
    4. Skip sports with no upcoming fixtures.

    Returns:
        {sport_key: [fixture_dict, ...], ...}
        Each fixture_dict has: home_team, away_team, commence_time, bookmakers.
    """
    if not ODDS_API_KEY:
        logger.debug("Odds API key not configured, skipping fetch_all_sports_odds")
        return {}

    if hasattr(settings, 'ODDS_API_ENABLED') and not settings.ODDS_API_ENABLED:
        logger.debug("Odds API disabled in config, skipping fetch_all_sports_odds")
        return {}

    if _quota_remaining is not None and _quota_remaining <= 0:
        logger.debug("Odds API quota exhausted, skipping fetch_all_sports_odds")
        return {}

    result: dict[str, list[dict]] = {}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Step 1: discover all active sport keys
            sports_response = await client.get(
                f"{ODDS_API_BASE}/sports",
                params={"apiKey": ODDS_API_KEY},
            )
            _update_quota_from_headers(sports_response.headers)

            if sports_response.status_code != 200:
                logger.warning(
                    "Odds API /sports returned %d: %s",
                    sports_response.status_code, sports_response.text[:200],
                )
                return result

            sports = sports_response.json()
            active_sport_keys = [
                s["key"] for s in sports
                if s.get("active") and not s.get("has_outright", False)
            ]

            # Step 2: fetch odds for each sport key
            for sport_key in active_sport_keys:
                # Check quota before each request
                if _quota_remaining is not None and _quota_remaining <= 0:
                    logger.warning(
                        "Odds API quota exhausted, stopping at sport_key=%s",
                        sport_key,
                    )
                    break

                try:
                    odds_response = await client.get(
                        f"{ODDS_API_BASE}/sports/{sport_key}/odds",
                        params={
                            "apiKey": ODDS_API_KEY,
                            "regions": REGIONS,
                            "markets": MARKETS,
                            "oddsFormat": ODDS_FORMAT,
                        },
                    )
                    _update_quota_from_headers(odds_response.headers)

                    if odds_response.status_code == 422:
                        # Sport key invalid or no upcoming events
                        continue

                    if odds_response.status_code != 200:
                        logger.debug(
                            "Odds API /sports/%s/odds returned %d",
                            sport_key, odds_response.status_code,
                        )
                        continue

                    fixtures = odds_response.json()
                    if fixtures:
                        result[sport_key] = fixtures

                except Exception as exc:
                    logger.debug(
                        "Odds API fetch error for sport_key=%s: %s",
                        sport_key, exc,
                    )
                    continue

    except Exception as e:
        logger.debug("Odds API fetch_all_sports_odds error: %s", e)

    return result
```

- [ ] **Step 6: Add `fetch_current_price` to `SportMarketBridgeService`**

Append the following method to the `SportMarketBridgeService` class in `backend/app/kernel/sport_market_bridge_service.py`:

```python
    async def fetch_current_price(self, contract_id: str) -> dict | None:
        """Fetch the current price and implied prob for a Polymarket contract.

        Uses the Polymarket gamma API to get the latest market data for a
        single contract by ID.

        Returns:
            {"price": float, "implied_prob": float, "liquidity": float | None, "volume": float | None}
            None if the contract is unavailable or API error.
        """
        import httpx
        from app.utils.implied_prob import polymarket_to_implied

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    "https://gamma-api.polymarket.com/markets",
                    params={"id": contract_id, "limit": "1"},
                )
                if response.status_code != 200:
                    logger.warning(
                        "Polymarket price fetch got %d for contract %s",
                        response.status_code, contract_id,
                    )
                    return None

                data = response.json()
                if not data:
                    return None

                item = data[0]
                # Parse outcomePrices JSON string
                import json
                prices_field = item.get("outcomePrices")
                if not prices_field:
                    return None

                try:
                    prices = json.loads(prices_field)
                except (ValueError, TypeError):
                    return None

                if not isinstance(prices, list) or len(prices) < 2:
                    return None

                yes_price = float(prices[0])
                no_price = float(prices[1])
                yes_implied, _, _ = polymarket_to_implied(yes_price, no_price)

                liquidity = item.get("liquidity")
                volume = item.get("volume")

                return {
                    "price": yes_price,
                    "implied_prob": yes_implied,
                    "liquidity": float(liquidity) if liquidity is not None else None,
                    "volume": float(volume) if volume is not None else None,
                }

        except Exception as exc:
            logger.debug(
                "Polymarket price fetch error for contract %s: %s",
                contract_id, exc,
            )
            return None
```

- [ ] **Step 7: Fill `_job_capture_market_snapshots` stub in `scheduler.py`**

In `backend/app/core/scheduler.py`, replace the `_job_capture_market_snapshots` function (lines 592-605) with:

```python
async def _job_capture_market_snapshots():
    """Every MARKET_SNAPSHOT_INTERVAL_MIN: capture Polymarket price snapshots."""
    if not settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED:
        return
    run_id = _start_run("sport_market_snapshots")
    try:
        from app.kernel.kernel_db import init_kernel_db
        from app.kernel.sport_market_bridge_service import SportMarketBridgeService
        from app.kernel.sport_market_link_store import SportMarketLinkStore
        from app.kernel.market_snapshot_store import MarketSnapshotStore

        init_kernel_db()
        bridge = SportMarketBridgeService()
        link_store = SportMarketLinkStore()
        snap_store = MarketSnapshotStore()

        # Get all verified links across all matches
        matches = link_store.get_matches_with_verified_links()
        captured = 0
        errors = 0
        for match_id in matches:
            try:
                links = link_store.get_verified_links(match_id=match_id)
                for link in links:
                    price = await bridge.fetch_current_price(link["contract_id"])
                    if price is not None:
                        snap_store.append_snapshot(
                            link_id=link["id"],
                            implied_prob=price["implied_prob"],
                            price=price["price"],
                            liquidity=price.get("liquidity"),
                            volume=price.get("volume"),
                            captured_at=_utcnow(),
                        )
                        captured += 1
            except Exception as exc:
                errors += 1
                logger.warning(
                    f"Snapshot capture failed for match {match_id}: {exc}"
                )

        _finish_run(run_id, "success", result={
            "matches_total": len(matches),
            "captured": captured,
            "errors": errors,
        })
    except Exception as exc:
        logger.exception("[Scheduler] Market snapshot capture failed")
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
```

- [ ] **Step 8: Fill `_job_fetch_traditional_odds` stub in `scheduler.py`**

In `backend/app/core/scheduler.py`, replace the `_job_fetch_traditional_odds` function (lines 577-589) with:

```python
async def _job_fetch_traditional_odds():
    """Every ODDS_FETCH_INTERVAL_MIN: fetch traditional sportsbook odds."""
    if not settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED:
        return
    if not settings.ODDS_API_ENABLED:
        return
    run_id = _start_run("sport_market_odds_fetch")
    try:
        from app.kernel.kernel_db import init_kernel_db
        from app.kernel.traditional_odds_store import TraditionalOddsStore
        from app.kernel.sport_market_link_store import SportMarketLinkStore
        from app.services.odds_api_service import fetch_all_sports_odds

        init_kernel_db()
        odds_store = TraditionalOddsStore()
        link_store = SportMarketLinkStore()

        # Get all matches with verified links (need odds)
        matches = link_store.get_matches_with_verified_links()
        if not matches:
            _finish_run(run_id, "success", result={
                "matches_total": 0, "captured": 0, "errors": 0,
            })
            return

        # Fetch all available sports odds from The Odds API
        all_odds = await fetch_all_sports_odds()

        # Match odds to matches and store
        captured = 0
        errors = 0
        for match_id in matches:
            try:
                odds_list = _match_odds_to_match(match_id, all_odds)
                if odds_list:
                    now = _utcnow()
                    competition = match_id.split("-")[0]
                    for outcome, implied_prob, decimal_odds, bookmaker, book_count in odds_list:
                        odds_store.append_snapshot(
                            match_id=match_id,
                            mapped_outcome=outcome,
                            competition=competition,
                            implied_prob=implied_prob,
                            decimal_odds=decimal_odds,
                            bookmaker=bookmaker,
                            bookmakers_count=book_count,
                            captured_at=now,
                        )
                        captured += 1
            except Exception as exc:
                errors += 1
                logger.warning(f"Odds fetch failed for {match_id}: {exc}")

        _finish_run(run_id, "success", result={
            "matches_total": len(matches),
            "captured": captured,
            "errors": errors,
        })
    except Exception as exc:
        logger.exception("[Scheduler] Traditional odds fetch failed")
        _finish_run(run_id, "failed", error=str(exc), exc=exc)


def _match_odds_to_match(
    match_id: str, all_odds: dict[str, list[dict]]
) -> list[tuple[str, float, float, str, int]]:
    """Match The Odds API fixtures to a match_id.

    Parses match_id to extract competition, date, and team tokens, then
    finds the matching fixture in all_odds by team-name normalization.

    Returns:
        [(mapped_outcome, implied_prob, decimal_odds, bookmaker, bookmakers_count), ...]
        Empty list if no match found.
    """
    from app.services.odds_api_service import (
        COMPETITION_TO_ODDS_API_SPORT, normalize_team_name, extract_best_odds,
    )
    from app.utils.implied_prob import odds_api_to_implied
    from app.kernel.sport_market_bridge_service import SportMarketBridgeService

    # Parse match_id using the bridge service's parser
    competition, date_str, team_tokens = SportMarketBridgeService._parse_match_id_static(match_id)
    if not team_tokens or len(team_tokens) < 2:
        return []

    sport_key = COMPETITION_TO_ODDS_API_SPORT.get(competition)
    if not sport_key:
        return []

    fixtures = all_odds.get(sport_key, [])
    if not fixtures:
        return []

    home_token = team_tokens[0]
    away_token = team_tokens[1]
    home_normalized = normalize_team_name(home_token)
    away_normalized = normalize_team_name(away_token)

    # Find matching fixture
    for fixture in fixtures:
        fixture_home = normalize_team_name(fixture.get("home_team", ""))
        fixture_away = normalize_team_name(fixture.get("away_team", ""))
        if fixture_home == home_normalized and fixture_away == away_normalized:
            # Extract best odds
            odds = extract_best_odds(fixture)
            if not odds:
                continue

            home_decimal = odds.get("home")
            away_decimal = odds.get("away")
            draw_decimal = odds.get("draw")
            bookmaker = odds.get("source", "average")
            book_count = odds.get("bookmakers_count", 0)

            # Convert to implied probabilities
            decimals = []
            mapping = []
            if home_decimal is not None:
                decimals.append(home_decimal)
                mapping.append("home_win")
            if draw_decimal is not None:
                decimals.append(draw_decimal)
                mapping.append("draw")
            if away_decimal is not None:
                decimals.append(away_decimal)
                mapping.append("away_win")

            if not decimals:
                continue

            implied = odds_api_to_implied(decimals)

            return [
                (mapping[i], implied[i], decimals[i], bookmaker, book_count)
                for i in range(len(mapping))
            ]

    return []
```

- [ ] **Step 9: Update IntervalTrigger reference in `scheduler.py`**

In `backend/app/core/scheduler.py` line 877, change:

```python
# OLD:
                IntervalTrigger(hours=settings.ODDS_API_FETCH_INTERVAL_HOURS),
# NEW:
                IntervalTrigger(minutes=settings.ODDS_FETCH_INTERVAL_MIN),
```

- [ ] **Step 10: Add `_parse_match_id_static` helper to `SportMarketBridgeService`**

In `backend/app/kernel/sport_market_bridge_service.py`, add a static method wrapper for `_parse_match_id`:

```python
    @staticmethod
    def _parse_match_id_static(match_id: str) -> tuple[str | None, str | None, list[str]]:
        """Static wrapper for _parse_match_id (used by scheduler helper)."""
        return _parse_match_id(match_id)
```

- [ ] **Step 11: Run all tests**

Run: `cd backend; python -m pytest tests/test_calibration_fusion_service.py tests/test_traditional_odds_store.py tests/test_edge_detector_service.py tests/test_sport_edge_routes.py tests/test_phase7_e2e_integration.py -v --tb=short`
Expected: All PASS (existing tests pass because `PHASE8_CALIBRATION_FUSION_ENABLED` defaults to false)

- [ ] **Step 12: Commit**

```bash
git add backend/app/kernel/edge_detector_service.py backend/app/services/odds_api_service.py backend/app/kernel/sport_market_bridge_service.py backend/app/core/scheduler.py backend/tests/test_calibration_fusion_service.py
git commit -m "feat(phase8): wire CalibrationFusionService into EdgeDetectorService + complete 2 scheduler jobs"
```

---

## Task 4: Sport Odds API Routes + Router

**Files:**
- Create: `backend/app/api/routes/sport_odds.py`
- Modify: `backend/app/api/router.py` (line 3 add import, line 16 add include_router)
- Test: `backend/tests/test_sport_odds_routes.py`

**Interfaces:**
- Consumes: `TraditionalOddsStore` from Task 1
- Produces: 2 GET endpoints (`/latest`, `/history`) gated by `PHASE7_SPORT_MARKET_BRIDGE_ENABLED`

- [ ] **Step 1: Write the failing test for sport-odds routes**

Create `backend/tests/test_sport_odds_routes.py`:

```python
"""Tests for sport-odds API routes.

All endpoints gated by PHASE7_SPORT_MARKET_BRIDGE_ENABLED (503 when false).
Both are GET (read-only) — no require_write_key auth.
"""
import pytest
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config
from app.kernel.kernel_db import init_kernel_db, close_kernel_db
from app.kernel.traditional_odds_store import TraditionalOddsStore


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "sport_odds_routes_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


@pytest.fixture
def client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", True)
    from app.api.routes import sport_odds
    app = FastAPI()
    app.include_router(sport_odds.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def disabled_client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", False)
    from app.api.routes import sport_odds
    app = FastAPI()
    app.include_router(sport_odds.router, prefix="/api")
    return TestClient(app)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _seed_odds(match_id="m1", outcomes=None):
    """Helper: seed traditional odds snapshots for a match."""
    store = TraditionalOddsStore()
    now = _utcnow()
    if outcomes is None:
        outcomes = [
            ("home_win", 0.65, 1.538),
            ("away_win", 0.35, 2.857),
        ]
    for outcome, prob, decimal in outcomes:
        store.append_snapshot(
            match_id=match_id, mapped_outcome=outcome, competition="nba",
            implied_prob=prob, decimal_odds=decimal,
            bookmaker="pinnacle", bookmakers_count=12, captured_at=now,
        )


def test_latest_returns_503_when_disabled(disabled_client):
    res = disabled_client.get("/api/sport-odds/m1/latest")
    assert res.status_code == 503


def test_latest_returns_odds(client):
    _seed_odds(match_id="m1")
    res = client.get("/api/sport-odds/m1/latest")
    assert res.status_code == 200
    data = res.json()
    assert data["match_id"] == "m1"
    assert data["skipped"] is False
    assert len(data["outcomes"]) == 2
    home = next(o for o in data["outcomes"] if o["mapped_outcome"] == "home_win")
    assert home["implied_prob"] == pytest.approx(0.65)
    assert home["decimal_odds"] == pytest.approx(1.538)
    assert home["bookmaker"] == "pinnacle"
    assert home["bookmakers_count"] == 12


def test_latest_returns_empty_when_no_data(client):
    """Match with no odds → skipped=true, skip_reason='no_odds'."""
    res = client.get("/api/sport-odds/nonexistent/latest")
    assert res.status_code == 200
    data = res.json()
    assert data["skipped"] is True
    assert data["skip_reason"] == "no_odds"
    assert data["outcomes"] == []


def test_history_returns_timeseries(client):
    _seed_odds(match_id="m1")
    # Add a second snapshot 10 minutes later
    store = TraditionalOddsStore()
    store.append_snapshot(
        match_id="m1", mapped_outcome="home_win", competition="nba",
        implied_prob=0.70, decimal_odds=1.429,
        bookmaker="pinnacle", bookmakers_count=12,
        captured_at=_utcnow() + timedelta(minutes=10),
    )
    res = client.get("/api/sport-odds/m1/history")
    assert res.status_code == 200
    data = res.json()
    assert data["match_id"] == "m1"
    assert len(data["series"]) >= 1
    home_series = next(s for s in data["series"] if s["mapped_outcome"] == "home_win")
    assert len(home_series["snapshots"]) == 2


def test_history_filtered_by_outcome(client):
    _seed_odds(match_id="m1")
    res = client.get("/api/sport-odds/m1/history", params={"mapped_outcome": "home_win"})
    assert res.status_code == 200
    data = res.json()
    assert len(data["series"]) == 1
    assert data["series"][0]["mapped_outcome"] == "home_win"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; python -m pytest tests/test_sport_odds_routes.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.routes.sport_odds'`

- [ ] **Step 3: Create `sport_odds.py` route file**

Create `backend/app/api/routes/sport_odds.py`:

```python
"""Sport odds API routes — traditional sportsbook odds snapshots.

All endpoints gated by PHASE7_SPORT_MARKET_BRIDGE_ENABLED (503 when false).
Both are GET (read-only) — no require_write_key auth.
Route order: static paths (/history) before dynamic /{match_id}/latest.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.core import config

router = APIRouter(prefix="/sport-odds", tags=["Sport Odds"])


def _ensure_enabled() -> None:
    if not config.settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED:
        raise HTTPException(
            status_code=503, detail="Sport market bridge is disabled."
        )


def _store() -> "TraditionalOddsStore":
    from app.kernel.traditional_odds_store import TraditionalOddsStore
    return TraditionalOddsStore()


@router.get("/{match_id}/history")
def get_history(
    match_id: str,
    mapped_outcome: str | None = Query(None),
) -> dict:
    """Historical traditional odds time-series for a match.

    Returns one series per mapped_outcome, each with all snapshots ordered
    oldest-first (chart x-axis order).
    """
    _ensure_enabled()
    store = _store()
    snapshots = store.get_snapshots(match_id=match_id, mapped_outcome=mapped_outcome)
    if not snapshots:
        return {"match_id": match_id, "series": [], "skipped": True, "skip_reason": "no_odds"}

    # Group by mapped_outcome
    by_outcome: dict[str, list[dict]] = {}
    for snap in snapshots:
        outcome = snap["mapped_outcome"]
        by_outcome.setdefault(outcome, []).append({
            "implied_prob": snap["implied_prob"],
            "decimal_odds": snap["decimal_odds"],
            "bookmaker": snap["bookmaker"],
            "bookmakers_count": snap["bookmakers_count"],
            "captured_at": snap["captured_at"].isoformat() if snap["captured_at"] else None,
        })

    series = [
        {"mapped_outcome": outcome, "snapshots": snaps}
        for outcome, snaps in by_outcome.items()
    ]
    return {"match_id": match_id, "series": series, "skipped": False, "skip_reason": None}


@router.get("/{match_id}/latest")
def get_latest(match_id: str) -> dict:
    """Latest traditional odds snapshot for each outcome of a match."""
    _ensure_enabled()
    store = _store()
    snapshots = store.get_snapshots(match_id=match_id)
    if not snapshots:
        return {
            "match_id": match_id,
            "outcomes": [],
            "skipped": True,
            "skip_reason": "no_odds",
        }

    # Get the latest snapshot per outcome
    latest_by_outcome: dict[str, dict] = {}
    for snap in snapshots:
        outcome = snap["mapped_outcome"]
        # snapshots are ordered oldest-first, so last one wins
        latest_by_outcome[outcome] = {
            "mapped_outcome": outcome,
            "implied_prob": snap["implied_prob"],
            "decimal_odds": snap["decimal_odds"],
            "bookmaker": snap["bookmaker"],
            "bookmakers_count": snap["bookmakers_count"],
            "captured_at": snap["captured_at"].isoformat() if snap["captured_at"] else None,
        }

    return {
        "match_id": match_id,
        "outcomes": list(latest_by_outcome.values()),
        "skipped": False,
        "skip_reason": None,
    }
```

- [ ] **Step 4: Register router in `router.py`**

In `backend/app/api/router.py`, modify line 3 to add `sport_odds`:

```python
# OLD:
from app.api.routes import events, llm, quality_metrics, world_cup_predictions, world_cup_analytics, predictions, sport_markets, sport_edges, sport_recommendations, sport_settlements
# NEW:
from app.api.routes import events, llm, quality_metrics, world_cup_predictions, world_cup_analytics, predictions, sport_markets, sport_edges, sport_recommendations, sport_settlements, sport_odds
```

Add after line 16:
```python
api_router.include_router(sport_odds.router, tags=["Sport Odds"])
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend; python -m pytest tests/test_sport_odds_routes.py -v --tb=short`
Expected: 6 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/sport_odds.py backend/app/api/router.py backend/tests/test_sport_odds_routes.py
git commit -m "feat(phase8): add sport-odds API routes (latest + history) with PHASE7 gating"
```

---

## Task 5: Frontend — API Client + TraditionalOddsChart Component

**Files:**
- Create: `frontend/src/lib/sport-odds-api.ts`
- Create: `frontend/src/components/sports/markets/TraditionalOddsChart.tsx`
- Create: `frontend/src/components/sports/markets/TraditionalOddsChart.test.tsx`

**Interfaces:**
- Consumes: `getWorldCupApiBase` from `frontend/src/lib/env.ts`
- Produces: `fetchTraditionalOddsHistory` + `fetchTraditionalOddsLatest` functions; `TraditionalOddsChart` React component

- [ ] **Step 1: Create `sport-odds-api.ts`**

Create `frontend/src/lib/sport-odds-api.ts`:

```typescript
import { getWorldCupApiBase } from "./env";

const API_BASE = getWorldCupApiBase();

export interface TraditionalOddsSnapshot {
  implied_prob: number;
  decimal_odds: number;
  bookmaker: string | null;
  bookmakers_count: number;
  captured_at: string | null;
}

export interface TraditionalOddsSeries {
  mapped_outcome: string;
  snapshots: TraditionalOddsSnapshot[];
}

export interface TraditionalOddsHistory {
  match_id: string;
  series: TraditionalOddsSeries[];
  skipped: boolean;
  skip_reason: string | null;
}

export interface TraditionalOddsLatest {
  match_id: string;
  outcomes: TraditionalOddsSnapshot[];
  skipped: boolean;
  skip_reason: string | null;
}

export async function fetchTraditionalOddsLatest(
  matchId: string,
): Promise<TraditionalOddsLatest> {
  const res = await fetch(`${API_BASE}/api/sport-odds/${matchId}/latest`);
  if (!res.ok) throw new Error(`Failed to fetch odds: ${res.status}`);
  return res.json();
}

export async function fetchTraditionalOddsHistory(
  matchId: string,
  mappedOutcome?: string,
): Promise<TraditionalOddsHistory> {
  const usp = new URLSearchParams();
  if (mappedOutcome) usp.set("mapped_outcome", mappedOutcome);
  const q = usp.toString() ? `?${usp.toString()}` : "";
  const res = await fetch(`${API_BASE}/api/sport-odds/${matchId}/history${q}`);
  if (!res.ok) throw new Error(`Failed to fetch odds history: ${res.status}`);
  return res.json();
}
```

- [ ] **Step 2: Write the failing test for `TraditionalOddsChart`**

Create `frontend/src/components/sports/markets/TraditionalOddsChart.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("@/lib/sport-odds-api", () => ({
  fetchTraditionalOddsHistory: vi.fn(),
}));

import { TraditionalOddsChart } from "./TraditionalOddsChart";
import { fetchTraditionalOddsHistory } from "@/lib/sport-odds-api";

describe("TraditionalOddsChart", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows loading state initially", () => {
    vi.mocked(fetchTraditionalOddsHistory).mockReturnValue(new Promise(() => {}));
    render(<TraditionalOddsChart matchId="nba-2026-g1" />);
    expect(screen.getByTestId("loading")).toBeTruthy();
  });

  it("shows empty state when no data", async () => {
    vi.mocked(fetchTraditionalOddsHistory).mockResolvedValue({
      match_id: "m1",
      series: [],
      skipped: true,
      skip_reason: "no_odds",
    });
    render(<TraditionalOddsChart matchId="m1" />);
    await waitFor(() => {
      expect(screen.getByTestId("empty")).toBeTruthy();
    });
  });

  it("renders chart with odds data", async () => {
    vi.mocked(fetchTraditionalOddsHistory).mockResolvedValue({
      match_id: "nba-2026-g1",
      series: [
        {
          mapped_outcome: "home_win",
          snapshots: [
            { implied_prob: 0.60, decimal_odds: 1.667, bookmaker: "pinnacle", bookmakers_count: 12, captured_at: "2026-07-16T10:00:00Z" },
            { implied_prob: 0.65, decimal_odds: 1.538, bookmaker: "pinnacle", bookmakers_count: 12, captured_at: "2026-07-16T11:00:00Z" },
          ],
        },
      ],
      skipped: false,
      skip_reason: null,
    });
    render(<TraditionalOddsChart matchId="nba-2026-g1" />);
    await waitFor(() => {
      expect(screen.getByTestId("odds-chart")).toBeTruthy();
    });
    expect(screen.getByText("home_win")).toBeTruthy();
  });

  it("shows error state on fetch failure", async () => {
    vi.mocked(fetchTraditionalOddsHistory).mockRejectedValue(new Error("404"));
    render(<TraditionalOddsChart matchId="m1" />);
    await waitFor(() => {
      expect(screen.getByTestId("error")).toBeTruthy();
    });
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend; npx vitest run src/components/sports/markets/TraditionalOddsChart.test.tsx`
Expected: FAIL (component doesn't exist)

- [ ] **Step 4: Create `TraditionalOddsChart.tsx`**

Create `frontend/src/components/sports/markets/TraditionalOddsChart.tsx`:

```tsx
"use client";
import { useEffect, useState } from "react";
import {
  fetchTraditionalOddsHistory,
  type TraditionalOddsHistory,
} from "@/lib/sport-odds-api";

interface TraditionalOddsChartProps {
  matchId: string;
}

export function TraditionalOddsChart({ matchId }: TraditionalOddsChartProps) {
  const [data, setData] = useState<TraditionalOddsHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchTraditionalOddsHistory(matchId)
      .then((d) => {
        setData(d);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [matchId]);

  if (loading) return <div data-testid="loading">加载中...</div>;
  if (error) return <div data-testid="error">错误: {error}</div>;
  if (!data || data.skipped || data.series.length === 0)
    return <div data-testid="empty">暂无传统赔率数据</div>;

  return (
    <div data-testid="odds-chart" className="w-full">
      <h3 className="text-lg font-semibold mb-2">传统赔率 vs Polymarket</h3>
      <div className="space-y-4">
        {data.series.map((s) => (
          <div key={s.mapped_outcome} className="border-b pb-2">
            <div className="font-medium mb-1">{s.mapped_outcome}</div>
            <div className="text-sm text-gray-600">
              {s.snapshots.length} 个快照
            </div>
            <table className="w-full text-sm mt-1">
              <thead>
                <tr className="border-b">
                  <th className="text-left p-1">时间</th>
                  <th className="text-right p-1">隐含概率</th>
                  <th className="text-right p-1">赔率</th>
                  <th className="text-left p-1">来源</th>
                </tr>
              </thead>
              <tbody>
                {s.snapshots.map((snap, i) => (
                  <tr key={i} className="border-b">
                    <td className="p-1">
                      {snap.captured_at
                        ? new Date(snap.captured_at).toLocaleString()
                        : "—"}
                    </td>
                    <td className="text-right p-1">
                      {snap.implied_prob.toFixed(3)}
                    </td>
                    <td className="text-right p-1">
                      {snap.decimal_odds.toFixed(3)}
                    </td>
                    <td className="p-1">{snap.bookmaker || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend; npx vitest run src/components/sports/markets/TraditionalOddsChart.test.tsx`
Expected: 4 PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/sport-odds-api.ts frontend/src/components/sports/markets/TraditionalOddsChart.tsx frontend/src/components/sports/markets/TraditionalOddsChart.test.tsx
git commit -m "feat(phase8): add TraditionalOddsChart frontend component + sport-odds-api client"
```

---

## Task 6: .env.example Documentation + Final Regression

**Files:**
- Modify: `backend/.env.example` (append Phase 7 + Phase 8 settings block)

**Interfaces:**
- Consumes: All previous tasks
- Produces: Complete `.env.example` documentation for all `PHASE7_*` and `PHASE8_*` settings

- [ ] **Step 1: Append Phase 7 + Phase 8 settings to `.env.example`**

Append the following to `backend/.env.example`:

```
# === Phase 7: Sport Market Bridge ===
# When false, all sport-market endpoints return 503 and scheduler jobs are not registered.
PHASE7_SPORT_MARKET_BRIDGE_ENABLED=false  # 中文：是否启用体育市场桥接层；默认关闭。
# Polymarket sports source — fetches candidate markets from gamma-api.polymarket.com.
PHASE7_POLYMARKET_SPORTS_SOURCE_ENABLED=false  # 中文：是否启用 Polymarket 体育市场源；默认关闭。
# The Odds API multi-league extension (10 competitions).
PHASE7_ODDS_API_MULTI_LEAGUE_ENABLED=false  # 中文：是否启用 The Odds API 多联赛扩展；默认关闭。
# Scheduler master flag for sport-market-bridge jobs.
PHASE7_SPORT_MARKET_BRIDGE_SCHEDULER_ENABLED=false  # 中文：是否注册体育市场桥接调度任务；默认关闭。
# LLM pending threshold for manual verification gate.
PHASE7_SPORT_MARKET_LINK_PENDING_THRESHOLD=0.6  # 中文：待人工审核的置信度阈值。
# Polymarket sports discovery interval (minutes).
POLYMARKET_SPORTS_DISCOVERY_INTERVAL_MIN=60  # 中文：Polymarket 市场发现间隔（分钟）。
# Traditional odds fetch interval (minutes). Was ODDS_API_FETCH_INTERVAL_HOURS=6.
ODDS_FETCH_INTERVAL_MIN=10  # 中文：传统赔率抓取间隔（分钟）。
# Polymarket snapshot capture interval (minutes).
MARKET_SNAPSHOT_INTERVAL_MIN=1  # 中文：Polymarket 价格快照间隔（分钟）。

# Phase 7 Subproject B — Edge Detector
PHASE7_EDGE_DETECTOR_ENABLED=false  # 中文：是否启用 Edge Detector；默认关闭。
EDGE_DETECTION_INTERVAL_MIN=5  # 中文：Edge 计算间隔（分钟）。

# Phase 7 Subproject C — Sport Recommendation Engine
PHASE7_SPORT_RECOMMENDATION_ENABLED=false  # 中文：是否启用体育推荐引擎；默认关闭。

# Phase 7 Subproject D — Market Settlement Feedback
PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED=false  # 中文：是否启用市场结算反馈；默认关闭。
PHASE7_MARKET_SETTLEMENT_SCHEDULER_ENABLED=false  # 中文：是否注册结算反馈调度任务；默认关闭。
MARKET_SETTLEMENT_INTERVAL_MIN=10  # 中文：结算处理间隔（分钟）。
MARKET_SETTLEMENT_BATCH_LIMIT=50  # 中文：结算批处理上限。
MIN_SAMPLES_FOR_MARKET_CALIBRATION=10  # 中文：市场校准最小样本数。
MARKET_CALIBRATION_WINDOW_SIZE=30  # 中文：市场校准窗口大小。

# === Phase 8: Calibration Fusion ===
# When true, EdgeDetectorService._compute_trust delegates to
# CalibrationFusionService which reads both Phase 3 and Phase 7 D
# calibration tables to compute a sample-count-weighted composite trust.
# When false (default), falls back to Phase 7 Phase-3-only behavior (zero-invasion).
PHASE8_CALIBRATION_FUSION_ENABLED=false  # 中文：是否启用校准融合；默认关闭。
```

- [ ] **Step 2: Run full backend regression**

Run: `cd backend; python -m pytest tests/ -v --tb=short -x`
Expected: All tests PASS (183+ existing + ~30 new Phase 8 tests)

- [ ] **Step 3: Run full frontend regression**

Run: `cd frontend; npx vitest run`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add backend/.env.example
git commit -m "docs(phase8): document all PHASE7_* and PHASE8_* settings in .env.example"
```

---

## Self-Review Checklist

After all tasks are complete, verify:

1. **Spec coverage:**
   - [x] `_job_capture_market_snapshots` implemented (Task 3, Step 7)
   - [x] `_job_fetch_traditional_odds` implemented (Task 3, Step 8)
   - [x] `kernel_traditional_odds_snapshots` table created (Task 1, Step 3)
   - [x] `TraditionalOddsStore` created (Task 1, Step 4)
   - [x] `fetch_all_sports_odds` created (Task 3, Step 5)
   - [x] `fetch_current_price` created (Task 3, Step 6)
   - [x] `CalibrationFusionService` created (Task 2, Step 3)
   - [x] `EdgeDetectorService._compute_trust` modified (Task 3, Step 3)
   - [x] `PHASE8_CALIBRATION_FUSION_ENABLED` config added (Task 1, Step 5)
   - [x] Config redundancy removed (Task 1, Step 5)
   - [x] `ODDS_API_FETCH_INTERVAL_HOURS` renamed (Task 1, Step 5 + Task 3, Step 9)
   - [x] 2 API endpoints created (Task 4, Step 3)
   - [x] `TraditionalOddsChart` frontend component created (Task 5, Step 4)
   - [x] `.env.example` documented (Task 6, Step 1)

2. **Placeholder scan:** No TBD/TODO — all steps have complete code.

3. **Type consistency:**
   - `TraditionalOddsStore.append_snapshot(*, match_id, mapped_outcome, competition, implied_prob, decimal_odds, bookmaker, bookmakers_count, captured_at)` — used consistently in Tasks 1, 3, 4
   - `CalibrationFusionService.compute_trust(engine, competition) -> CompositeTrust` — used consistently in Tasks 2, 3
   - `fetch_all_sports_odds() -> dict[str, list[dict]]` — used in Task 3
   - `fetch_current_price(contract_id) -> dict | None` — used in Task 3

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-16-phase8-pipeline-completion-and-calibration-fusion.md`. Two execution options:

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
