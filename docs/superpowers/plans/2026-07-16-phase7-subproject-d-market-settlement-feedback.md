# Market Settlement Feedback Loop (Phase 7 Subproject D) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the market-settlement feedback channel that closes the Phase 7 learning loop — when a match finishes, capture the settlement price (last market snapshot before match end), compute Brier-style error against B's persisted `model_prob`, and aggregate into a new `kernel_market_calibrations` table (parallel to Phase 3's `KernelCalibration`).

**Architecture:** Parallel-channel learning service. D reads B's persisted edges + A's market snapshots + Phase 3's match outcomes (all read-only), computes settlement error signals, and writes only to its own 2 new tables (`kernel_market_settlements`, `kernel_market_calibrations`). A scheduler job scans for finished matches without settlements and processes them in batch. Zero modifications to A/B/C/Phase 3 code.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy ORM, SQLite (kernel_predictions.db), pytest, Next.js 14, TypeScript, Vitest, React.

## Global Constraints

1. `PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED` flag defaults to OFF (`false`); all 4 endpoints return 503 when false.
2. `PHASE7_MARKET_SETTLEMENT_SCHEDULER_ENABLED` flag defaults to OFF; scheduler job not registered when false.
3. Do NOT modify Subproject A's code (`sport_market_link_store.py`, `market_snapshot_store.py`, `sport_markets.py`, `polymarket_sports_source`).
4. Do NOT modify Subproject B's code (`edge_detector_service.py`, `edge_store.py`, `sport_edges.py`, `kernel_sport_edges` table).
5. Do NOT modify Subproject C's code (`sport_recommendation_service.py`, `sport_recommendations.py`).
6. Do NOT modify Phase 3 learning loop (`learning_service.py`, `prediction_kernel.py`, `diagnosis_service.py`, `decision_quality_service.py`).
7. Do NOT structurally modify the 3 learning tables (`KernelPredictionHistory`, `KernelCalibration`, `KernelEngineScore`).
8. Do NOT modify the learning dashboard components, event pipeline code, or `ActionableRecommendation`.
9. D writes only to its own 2 tables (`kernel_market_settlements`, `kernel_market_calibrations`); never modifies A/B/C/Phase 3 data.
10. Settlement price = last `kernel_market_snapshots` row where `captured_at <= KernelMatchOutcome.finished_at` (proxy, not real Polymarket resolution API).
11. Error signals: `brier_score = (model_prob - settlement_implied_prob)^2`, `signed_error = model_prob - settlement_implied_prob`, `direction_correct = 1 if sign(raw_edge) == sign(settlement_implied_prob - market_prob) else 0`.
12. Calibration regression: `settlement_implied_prob ~ slope * model_prob + intercept`, slope clamped `[0.0, 2.0]`, intercept clamped `[-0.5, 0.5]` (same bounds as Phase 3).
13. Only consume `verified=1` market links (fail-closed, consistent with Subproject A/B).
14. Route order: static paths (`/calibrations`, `/history`) before dynamic `/{match_id}` (lesson from Subproject C).
15. Idempotency: unique constraint `(match_id, mapped_outcome)` prevents duplicate settlements; `process_settlement` checks existence first.
16. POST `/process/{match_id}` requires `require_write_key` (consistent with `POST /api/predictions/outcomes/{match_id}/process`); 3 GET endpoints are read-only.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/app/core/config.py` | Modify | Add 6 config flags before `settings = Settings()` |
| `backend/app/kernel/kernel_db.py` | Modify | Append 2 new table classes (`KernelMarketSettlement`, `KernelMarketCalibration`) |
| `backend/app/kernel/market_settlement_store.py` | Create | Persistence for 2 new tables |
| `backend/app/kernel/market_settlement_service.py` | Create | Settlement computation + calibration regression |
| `backend/app/api/routes/sport_settlements.py` | Create | 4 API endpoints (3 GET + 1 POST) |
| `backend/app/api/router.py` | Modify | Register `sport_settlements` router |
| `backend/app/core/scheduler.py` | Modify | Add `_job_process_market_settlements` + registration |
| `backend/scripts/sport_settlement_cli.py` | Create | CLI tool (process/scan/calibrations/history) |
| `backend/tests/test_market_settlement_service.py` | Create | ~25 service tests |
| `backend/tests/test_market_settlement_routes.py` | Create | ~10 route tests |
| `backend/tests/test_market_settlement_cli.py` | Create | ~4 CLI tests |
| `frontend/src/lib/sport-settlements-api.ts` | Create | API client |
| `frontend/src/app/sports/settlements/page.tsx` | Create | Settlements page |
| `frontend/src/components/sports/settlements/SettlementHistoryTable.tsx` | Create | History table component |
| `frontend/src/components/sports/settlements/MarketCalibrationPanel.tsx` | Create | Calibration panel component |
| `frontend/src/components/sports/settlements/SettlementHistoryTable.test.tsx` | Create | 4 frontend tests |
| `frontend/src/components/sports/settlements/MarketCalibrationPanel.test.tsx` | Create | 4 frontend tests |
| `frontend/src/components/app-nav.tsx` | Modify | Add nav entry |

---

## Task 1: Config + DB Tables + Store + Service

**Files:**
- Modify: `backend/app/core/config.py` (before line 1089 `settings = Settings()`)
- Modify: `backend/app/kernel/kernel_db.py` (append after `KernelSportEdge` class, ~line 255)
- Create: `backend/app/kernel/market_settlement_store.py`
- Create: `backend/app/kernel/market_settlement_service.py`
- Test: `backend/tests/test_market_settlement_service.py`

**Interfaces:**
- Consumes: `EdgeStore.get_latest_edges(match_id)` (Subproject B), `get_kernel_session()` (kernel_db), `KernelMatchOutcome` / `KernelSportMarketLink` / `KernelMarketSnapshot` / `KernelPrediction` / `get_latest_prediction` (kernel_db), `config.settings` (config)
- Produces: `MarketSettlementService` with `process_settlement(match_id) -> SettlementResult`, `scan_and_process(limit) -> ScanResult`, `get_settlement(match_id)`, `get_calibrations(engine, competition)`, `get_history(limit, engine)`; `MarketSettlementStore` with `append_settlement(...)`, `get_settlement(match_id)`, `get_settlements_for_calibration(engine, competition, limit)`, `upsert_calibration(...)`, `get_calibrations(engine, competition)`, `get_history(limit, engine)`, `get_processed_match_ids()`

- [ ] **Step 1: Write the failing test file — pure function tests**

Create `backend/tests/test_market_settlement_service.py`:

```python
"""Tests for MarketSettlementService and pure helper functions.

Covers: _compute_brier, _compute_signed_error, _compute_direction_correct,
_update_market_calibration (regression fitting), and DB-integrated
process_settlement / scan_and_process / read methods.
"""
import pytest
from datetime import datetime, timezone, timedelta

from app.kernel.market_settlement_service import (
    _compute_brier,
    _compute_signed_error,
    _compute_direction_correct,
    _update_market_calibration,
    MarketSettlementService,
    SettlementResult,
    ScanResult,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------

def test_compute_brier_zero_when_equal():
    assert _compute_brier(0.65, 0.65) == 0.0


def test_compute_brier_positive_when_different():
    # (0.7 - 0.5)^2 = 0.04
    assert _compute_brier(0.7, 0.5) == pytest.approx(0.04)


def test_compute_brier_max_when_extremes():
    # (1.0 - 0.0)^2 = 1.0
    assert _compute_brier(1.0, 0.0) == pytest.approx(1.0)


def test_compute_signed_error_positive():
    assert _compute_signed_error(0.7, 0.5) == pytest.approx(0.2)


def test_compute_signed_error_negative():
    assert _compute_signed_error(0.3, 0.6) == pytest.approx(-0.3)


def test_compute_signed_error_zero_when_equal():
    assert _compute_signed_error(0.5, 0.5) == pytest.approx(0.0)


def test_direction_correct_both_positive():
    # raw_edge > 0 (model > market), settlement > market → market moved up → correct
    assert _compute_direction_correct(raw_edge=0.1, market_prob=0.5, settlement_implied_prob=0.9) == 1


def test_direction_correct_both_negative():
    # raw_edge < 0 (model < market), settlement < market → market moved down → correct
    assert _compute_direction_correct(raw_edge=-0.1, market_prob=0.6, settlement_implied_prob=0.3) == 1


def test_direction_correct_mismatched():
    # raw_edge > 0 (model > market), settlement < market → market moved down → wrong
    assert _compute_direction_correct(raw_edge=0.1, market_prob=0.5, settlement_implied_prob=0.3) == 0


def test_direction_correct_zero_edge():
    # raw_edge == 0 → edge_sign == 0 → not correct (no directional bet)
    assert _compute_direction_correct(raw_edge=0.0, market_prob=0.5, settlement_implied_prob=0.9) == 0


def test_direction_correct_zero_market_move():
    # settlement == market → market_sign == 0 → not correct
    assert _compute_direction_correct(raw_edge=0.1, market_prob=0.5, settlement_implied_prob=0.5) == 0


def test_update_market_calibration_insufficient_samples(tmp_path, monkeypatch):
    """When < MIN_SAMPLES_FOR_MARKET_CALIBRATION settlements, no calibration row written."""
    from app.core import config
    from app.kernel.kernel_db import init_kernel_db, close_kernel_db
    close_kernel_db()
    init_kernel_db(str(tmp_path / "test_cal.db"))
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 10)
    monkeypatch.setattr(config.settings, "MARKET_CALIBRATION_WINDOW_SIZE", 30)
    from app.kernel.market_settlement_store import MarketSettlementStore
    store = MarketSettlementStore()
    # Insert only 3 settlements — below threshold
    for i in range(3):
        store.append_settlement(
            match_id=f"m{i}", mapped_outcome="home_win", engine="BasketballEngine",
            competition="nba", settlement_implied_prob=0.7, settlement_captured_at=_utcnow(),
            link_id=1, model_prob=0.65, market_prob_at_detection=0.6, raw_edge=0.05,
            adjusted_edge=0.04, brier_score=0.0025, signed_error=0.05, direction_correct=1,
            status="processed", skip_reason=None, match_finished_at=_utcnow(), processed_at=_utcnow(),
        )
    _update_market_calibration(store, "BasketballEngine", "nba")
    cals = store.get_calibrations(engine="BasketballEngine", competition="nba")
    assert len(cals) == 0  # no calibration written
    close_kernel_db()


def test_update_market_calibration_sufficient_samples(tmp_path, monkeypatch):
    """When >= MIN_SAMPLES settlements, calibration row is written with regression."""
    from app.core import config
    from app.kernel.kernel_db import init_kernel_db, close_kernel_db
    close_kernel_db()
    init_kernel_db(str(tmp_path / "test_cal2.db"))
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 5)
    monkeypatch.setattr(config.settings, "MARKET_CALIBRATION_WINDOW_SIZE", 30)
    from app.kernel.market_settlement_store import MarketSettlementStore
    store = MarketSettlementStore()
    # Insert 5 settlements with perfect linear relationship: y = x (slope=1, intercept=0)
    for i in range(5):
        model_p = 0.4 + i * 0.1  # 0.4, 0.5, 0.6, 0.7, 0.8
        settlement_p = model_p  # perfect calibration
        store.append_settlement(
            match_id=f"m{i}", mapped_outcome="home_win", engine="BasketballEngine",
            competition="nba", settlement_implied_prob=settlement_p,
            settlement_captured_at=_utcnow(), link_id=1, model_prob=model_p,
            market_prob_at_detection=0.5, raw_edge=model_p - 0.5,
            adjusted_edge=model_p - 0.5, brier_score=0.0, signed_error=0.0,
            direction_correct=1, status="processed", skip_reason=None,
            match_finished_at=_utcnow(), processed_at=_utcnow(),
        )
    _update_market_calibration(store, "BasketballEngine", "nba")
    cals = store.get_calibrations(engine="BasketballEngine", competition="nba")
    assert len(cals) == 1
    cal = cals[0]
    assert cal["sample_count"] == 5
    assert cal["slope"] == pytest.approx(1.0, abs=0.01)
    assert cal["intercept"] == pytest.approx(0.0, abs=0.01)
    assert cal["avg_brier"] == pytest.approx(0.0, abs=0.001)
    assert cal["direction_accuracy"] == pytest.approx(1.0, abs=0.01)
    close_kernel_db()


def test_update_market_calibration_slope_clamped(tmp_path, monkeypatch):
    """Slope is clamped to [0.0, 2.0]."""
    from app.core import config
    from app.kernel.kernel_db import init_kernel_db, close_kernel_db
    close_kernel_db()
    init_kernel_db(str(tmp_path / "test_cal3.db"))
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 3)
    monkeypatch.setattr(config.settings, "MARKET_CALIBRATION_WINDOW_SIZE", 30)
    from app.kernel.market_settlement_store import MarketSettlementStore
    store = MarketSettlementStore()
    # Steep relationship: y = 5x → slope=5, should be clamped to 2.0
    for i in range(3):
        model_p = 0.1 + i * 0.1
        settlement_p = 5 * model_p
        store.append_settlement(
            match_id=f"m{i}", mapped_outcome="home_win", engine="TestEngine",
            competition="test", settlement_implied_prob=settlement_p,
            settlement_captured_at=_utcnow(), link_id=1, model_prob=model_p,
            market_prob_at_detection=0.5, raw_edge=model_p - 0.5,
            adjusted_edge=model_p - 0.5, brier_score=0.0, signed_error=0.0,
            direction_correct=1, status="processed", skip_reason=None,
            match_finished_at=_utcnow(), processed_at=_utcnow(),
        )
    _update_market_calibration(store, "TestEngine", "test")
    cals = store.get_calibrations(engine="TestEngine", competition="test")
    assert len(cals) == 1
    assert cals[0]["slope"] == pytest.approx(2.0, abs=0.01)  # clamped
    close_kernel_db()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend ; python -m pytest tests/test_market_settlement_service.py -v --no-header -k "compute_brier or compute_signed_error or direction_correct or update_market_calibration" 2>&1 | tail -20`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.kernel.market_settlement_service'`

- [ ] **Step 3: Add config flags to `backend/app/core/config.py`**

Insert before line 1089 (`settings = Settings()`), after the Subproject C block (line 1086):

```python
    # Phase 7 Subproject D — Market Settlement Feedback (default OFF).
    # When false, all /api/sport-settlements/* endpoints return 503 and the
    # scheduler job is not registered.
    PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED: bool = _env_bool(
        "PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED", "false"
    )
    PHASE7_MARKET_SETTLEMENT_SCHEDULER_ENABLED: bool = _env_bool(
        "PHASE7_MARKET_SETTLEMENT_SCHEDULER_ENABLED", "false"
    )
    MARKET_SETTLEMENT_INTERVAL_MIN: int = int(
        os.getenv("MARKET_SETTLEMENT_INTERVAL_MIN", "10")
    )
    MARKET_SETTLEMENT_BATCH_LIMIT: int = int(
        os.getenv("MARKET_SETTLEMENT_BATCH_LIMIT", "50")
    )
    MIN_SAMPLES_FOR_MARKET_CALIBRATION: int = int(
        os.getenv("MIN_SAMPLES_FOR_MARKET_CALIBRATION", "10")
    )
    MARKET_CALIBRATION_WINDOW_SIZE: int = int(
        os.getenv("MARKET_CALIBRATION_WINDOW_SIZE", "30")
    )
```

- [ ] **Step 4: Add 2 new table classes to `backend/app/kernel/kernel_db.py`**

Append after the `KernelSportEdge` class (find the last column of `KernelSportEdge`, which ends around line 255, and add these classes before the `init_kernel_db` function):

```python
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
```

- [ ] **Step 5: Create `backend/app/kernel/market_settlement_store.py`**

```python
"""Persistence for kernel_market_settlements + kernel_market_calibrations tables.

Mirrors the edge_store / sport_market_link_store pattern: append-only writes,
dict returns, session-per-call. D writes only to these 2 tables.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.kernel.kernel_db import (
    KernelMarketSettlement,
    KernelMarketCalibration,
    get_kernel_session,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _settlement_to_dict(row: KernelMarketSettlement) -> dict[str, Any]:
    return {
        "id": row.id,
        "match_id": row.match_id,
        "mapped_outcome": row.mapped_outcome,
        "engine": row.engine,
        "competition": row.competition,
        "settlement_implied_prob": row.settlement_implied_prob,
        "settlement_captured_at": row.settlement_captured_at,
        "link_id": row.link_id,
        "model_prob": row.model_prob,
        "market_prob_at_detection": row.market_prob_at_detection,
        "raw_edge": row.raw_edge,
        "adjusted_edge": row.adjusted_edge,
        "brier_score": row.brier_score,
        "signed_error": row.signed_error,
        "direction_correct": row.direction_correct,
        "status": row.status,
        "skip_reason": row.skip_reason,
        "match_finished_at": row.match_finished_at,
        "processed_at": row.processed_at,
    }


def _calibration_to_dict(row: KernelMarketCalibration) -> dict[str, Any]:
    return {
        "id": row.id,
        "engine": row.engine,
        "competition": row.competition,
        "slope": row.slope,
        "intercept": row.intercept,
        "sample_count": row.sample_count,
        "avg_brier": row.avg_brier,
        "avg_signed_error": row.avg_signed_error,
        "direction_accuracy": row.direction_accuracy,
        "last_updated": row.last_updated,
    }


class MarketSettlementStore:
    """Persistence for settlement records and market calibrations."""

    def append_settlement(
        self,
        *,
        match_id: str,
        mapped_outcome: str,
        engine: str,
        competition: str,
        settlement_implied_prob: float | None,
        settlement_captured_at: datetime | None,
        link_id: int | None,
        model_prob: float | None,
        market_prob_at_detection: float | None,
        raw_edge: float | None,
        adjusted_edge: float | None,
        brier_score: float | None,
        signed_error: float | None,
        direction_correct: int | None,
        status: str,
        skip_reason: str | None,
        match_finished_at: datetime,
        processed_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Insert a settlement row. Returns the inserted row as dict.

        Caller is responsible for idempotency check (get_settlement before append).
        Unique constraint on (match_id, mapped_outcome) provides DB-level safety.
        """
        when = processed_at or _utcnow()
        session = get_kernel_session()
        try:
            row = KernelMarketSettlement(
                match_id=match_id, mapped_outcome=mapped_outcome, engine=engine,
                competition=competition,
                settlement_implied_prob=settlement_implied_prob,
                settlement_captured_at=settlement_captured_at, link_id=link_id,
                model_prob=model_prob, market_prob_at_detection=market_prob_at_detection,
                raw_edge=raw_edge, adjusted_edge=adjusted_edge, brier_score=brier_score,
                signed_error=signed_error, direction_correct=direction_correct,
                status=status, skip_reason=skip_reason,
                match_finished_at=match_finished_at, processed_at=when,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _settlement_to_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_settlement(self, match_id: str) -> list[dict[str, Any]]:
        """All settlement rows for a match."""
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelMarketSettlement)
                .filter_by(match_id=match_id)
                .order_by(KernelMarketSettlement.mapped_outcome.asc())
                .all()
            )
            return [_settlement_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def get_settlements_for_calibration(
        self, engine: str, competition: str, limit: int
    ) -> list[dict[str, Any]]:
        """Recent processed settlements for (engine, competition), most recent first.

        Only returns rows where status='processed' and brier_score IS NOT NULL.
        """
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelMarketSettlement)
                .filter(
                    KernelMarketSettlement.engine == engine,
                    KernelMarketSettlement.competition == competition,
                    KernelMarketSettlement.status == "processed",
                    KernelMarketSettlement.brier_score.isnot(None),
                )
                .order_by(KernelMarketSettlement.processed_at.desc())
                .limit(limit)
                .all()
            )
            return [_settlement_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def upsert_calibration(
        self,
        *,
        engine: str,
        competition: str,
        slope: float,
        intercept: float,
        sample_count: int,
        avg_brier: float,
        avg_signed_error: float,
        direction_accuracy: float,
        last_updated: datetime,
    ) -> dict[str, Any]:
        """Upsert a market calibration row keyed by (engine, competition)."""
        session = get_kernel_session()
        try:
            row = (
                session.query(KernelMarketCalibration)
                .filter_by(engine=engine, competition=competition)
                .one_or_none()
            )
            if row is not None:
                row.slope = slope
                row.intercept = intercept
                row.sample_count = sample_count
                row.avg_brier = avg_brier
                row.avg_signed_error = avg_signed_error
                row.direction_accuracy = direction_accuracy
                row.last_updated = last_updated
            else:
                row = KernelMarketCalibration(
                    engine=engine, competition=competition, slope=slope,
                    intercept=intercept, sample_count=sample_count, avg_brier=avg_brier,
                    avg_signed_error=avg_signed_error, direction_accuracy=direction_accuracy,
                    last_updated=last_updated,
                )
                session.add(row)
            session.commit()
            session.refresh(row)
            return _calibration_to_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_calibrations(
        self, engine: str | None = None, competition: str | None = None
    ) -> list[dict[str, Any]]:
        """List calibrations, optionally filtered."""
        session = get_kernel_session()
        try:
            q = session.query(KernelMarketCalibration)
            if engine is not None:
                q = q.filter(KernelMarketCalibration.engine == engine)
            if competition is not None:
                q = q.filter(KernelMarketCalibration.competition == competition)
            rows = q.order_by(KernelMarketCalibration.last_updated.desc()).all()
            return [_calibration_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def get_history(self, limit: int, engine: str | None = None) -> list[dict[str, Any]]:
        """Recent settlements, most recent first."""
        session = get_kernel_session()
        try:
            q = session.query(KernelMarketSettlement)
            if engine is not None:
                q = q.filter(KernelMarketSettlement.engine == engine)
            rows = (
                q.order_by(KernelMarketSettlement.processed_at.desc())
                .limit(limit)
                .all()
            )
            return [_settlement_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def get_processed_match_ids(self) -> set[str]:
        """Set of match_ids that already have settlement rows (for scan dedup)."""
        session = get_kernel_session()
        try:
            rows = session.query(KernelMarketSettlement.match_id).distinct().all()
            return {r[0] for r in rows}
        except Exception:
            return set()
        finally:
            session.close()
```

- [ ] **Step 6: Create `backend/app/kernel/market_settlement_service.py`**

```python
"""Market settlement feedback service.

Reads B's persisted edges + A's market snapshots + Phase 3's match outcomes
(all read-only), computes market-settlement-based error signals, and writes to
kernel_market_settlements + kernel_market_calibrations (D's own tables only).

Parallel channel: Phase 3's match-outcome learning continues unchanged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core import config
from app.kernel.kernel_db import (
    KernelMatchOutcome,
    KernelSportMarketLink,
    KernelMarketSnapshot,
    KernelPrediction,
    get_kernel_session,
    get_latest_prediction,
)
from app.kernel.edge_store import EdgeStore
from app.kernel.market_settlement_store import MarketSettlementStore

logger = logging.getLogger(__name__)

_CALIBRATION_SLOPE_MIN = 0.0
_CALIBRATION_SLOPE_MAX = 2.0
_CALIBRATION_INTERCEPT_MIN = -0.5
_CALIBRATION_INTERCEPT_MAX = 0.5


@dataclass(frozen=True)
class SettlementResult:
    """Result of processing a single match's settlement."""
    match_id: str
    status: str
    settlements_count: int
    skip_reason: str | None


@dataclass(frozen=True)
class ScanResult:
    """Result of a batch scan."""
    scanned: int
    processed: int
    skipped: int
    already_processed: int
    errors: int
    error_details: list[str]


def _compute_brier(model_prob: float, settlement_implied_prob: float) -> float:
    """Brier-style score: (model_prob - settlement_implied_prob)^2."""
    return round((model_prob - settlement_implied_prob) ** 2, 6)


def _compute_signed_error(model_prob: float, settlement_implied_prob: float) -> float:
    """Signed error: model_prob - settlement_implied_prob."""
    return round(model_prob - settlement_implied_prob, 6)


def _compute_direction_correct(
    raw_edge: float, market_prob: float, settlement_implied_prob: float
) -> int:
    """Did the edge direction match the market resolution?

    Edge direction: sign(raw_edge). Market resolution direction:
    sign(settlement_implied_prob - market_prob). Correct if both non-zero and match.
    """
    edge_sign = 1 if raw_edge > 0 else (-1 if raw_edge < 0 else 0)
    market_move = settlement_implied_prob - market_prob
    market_sign = 1 if market_move > 0 else (-1 if market_move < 0 else 0)
    return 1 if edge_sign == market_sign and edge_sign != 0 else 0


def _update_market_calibration(
    store: MarketSettlementStore, engine: str, competition: str
) -> None:
    """Fit linear regression on recent settlements and upsert calibration.

    x = model_prob, y = settlement_implied_prob
    slope clamped to [0.0, 2.0], intercept clamped to [-0.5, 0.5].
    """
    settlements = store.get_settlements_for_calibration(
        engine, competition, limit=config.settings.MARKET_CALIBRATION_WINDOW_SIZE
    )
    if len(settlements) < config.settings.MIN_SAMPLES_FOR_MARKET_CALIBRATION:
        return

    xs = [s["model_prob"] for s in settlements]
    ys = [s["settlement_implied_prob"] for s in settlements]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    slope = num / den if den != 0 else 1.0
    intercept = mean_y - slope * mean_x

    slope = max(_CALIBRATION_SLOPE_MIN, min(_CALIBRATION_SLOPE_MAX, slope))
    intercept = max(_CALIBRATION_INTERCEPT_MIN, min(_CALIBRATION_INTERCEPT_MAX, intercept))

    avg_brier = sum(s["brier_score"] for s in settlements) / n
    avg_signed_error = sum(s["signed_error"] for s in settlements) / n
    direction_accuracy = sum(s["direction_correct"] for s in settlements) / n

    store.upsert_calibration(
        engine=engine, competition=competition, slope=round(slope, 4),
        intercept=round(intercept, 4), sample_count=n,
        avg_brier=round(avg_brier, 6), avg_signed_error=round(avg_signed_error, 6),
        direction_accuracy=round(direction_accuracy, 4),
        last_updated=datetime.now(timezone.utc),
    )


def _find_settlement_snapshot(link_id: int, finished_at: datetime) -> dict[str, Any] | None:
    """Find the last market snapshot before the match finished.

    Queries kernel_market_snapshots directly (read-only).
    """
    session = get_kernel_session()
    try:
        row = (
            session.query(KernelMarketSnapshot)
            .filter(
                KernelMarketSnapshot.link_id == link_id,
                KernelMarketSnapshot.captured_at <= finished_at,
            )
            .order_by(KernelMarketSnapshot.captured_at.desc())
            .first()
        )
        if row is None:
            return None
        return {
            "id": row.id, "link_id": row.link_id, "implied_prob": row.implied_prob,
            "price": row.price, "liquidity": row.liquidity, "volume": row.volume,
            "captured_at": row.captured_at,
        }
    except Exception:
        return None
    finally:
        session.close()


def _find_verified_link_for_outcome(
    match_id: str, mapped_outcome: str
) -> dict[str, Any] | None:
    """Find the best verified market link for a match's outcome.

    Picks the link with highest link_confidence among verified links.
    """
    session = get_kernel_session()
    try:
        row = (
            session.query(KernelSportMarketLink)
            .filter(
                KernelSportMarketLink.match_id == match_id,
                KernelSportMarketLink.mapped_outcome == mapped_outcome,
                KernelSportMarketLink.verified == 1,
            )
            .order_by(KernelSportMarketLink.link_confidence.desc())
            .first()
        )
        if row is None:
            return None
        return {
            "id": row.id, "match_id": row.match_id, "contract_id": row.contract_id,
            "source": row.source, "outcome_label": row.outcome_label,
            "mapped_outcome": row.mapped_outcome, "link_method": row.link_method,
            "link_confidence": row.link_confidence, "verified": row.verified,
            "market_question": row.market_question, "implied_prob": row.implied_prob,
            "created_at": row.created_at, "updated_at": row.updated_at,
        }
    except Exception:
        return None
    finally:
        session.close()


def _find_finished_matches_without_settlements(limit: int) -> list[dict[str, Any]]:
    """Find finished matches that don't have settlement rows yet."""
    session = get_kernel_session()
    try:
        processed_ids = (
            session.query(KernelMarketSettlement.match_id).distinct().all()
            if False else []  # Use store.get_processed_match_ids() instead
        )
        # Query finished matches not in processed set
        from app.kernel.kernel_db import KernelMarketSettlement as _KMS
        processed_subquery = (
            session.query(_KMS.match_id).distinct().subquery()
        )
        rows = (
            session.query(KernelMatchOutcome)
            .filter(
                KernelMatchOutcome.finished_at.isnot(None),
                ~KernelMatchOutcome.match_id.in_(processed_subquery),
            )
            .order_by(KernelMatchOutcome.finished_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {"match_id": r.match_id, "outcome": r.outcome, "finished_at": r.finished_at}
            for r in rows
        ]
    except Exception:
        return []
    finally:
        session.close()


class MarketSettlementService:
    """Market settlement feedback service."""

    def __init__(self) -> None:
        self._edge_store = EdgeStore()
        self._store = MarketSettlementStore()

    def process_settlement(self, match_id: str) -> SettlementResult:
        """Process a single match's market settlement. Idempotent."""
        # Check if already processed
        existing = self._store.get_settlement(match_id)
        if existing:
            return SettlementResult(
                match_id=match_id, status="already_processed",
                settlements_count=0, skip_reason=None,
            )

        # Read match outcome
        session = get_kernel_session()
        try:
            outcome_row = (
                session.query(KernelMatchOutcome)
                .filter_by(match_id=match_id)
                .one_or_none()
            )
        finally:
            session.close()

        if outcome_row is None or outcome_row.finished_at is None:
            return SettlementResult(
                match_id=match_id, status="skipped_not_finished",
                settlements_count=0, skip_reason="Match not finished or no outcome recorded.",
            )

        finished_at = outcome_row.finished_at

        # Read prediction for engine/competition metadata
        prediction = get_latest_prediction(match_id)
        if prediction is None:
            return SettlementResult(
                match_id=match_id, status="skipped_no_edges",
                settlements_count=0, skip_reason="No prediction found for match.",
            )
        engine = prediction.engine
        competition = prediction.competition

        # Read B's edges
        edges = self._edge_store.get_latest_edges(match_id)
        if not edges:
            return SettlementResult(
                match_id=match_id, status="skipped_no_edges",
                settlements_count=0, skip_reason="No edges found for match.",
            )

        # Process each edge's mapped_outcome
        settlements_count = 0
        for edge in edges:
            mapped_outcome = edge["mapped_outcome"]
            # Find verified link for this outcome
            link = _find_verified_link_for_outcome(match_id, mapped_outcome)
            if link is None:
                # Skip: no verified link — insert a skipped settlement row
                self._store.append_settlement(
                    match_id=match_id, mapped_outcome=mapped_outcome, engine=engine,
                    competition=competition, settlement_implied_prob=None,
                    settlement_captured_at=None, link_id=None,
                    model_prob=edge["model_prob"], market_prob_at_detection=edge["market_prob"],
                    raw_edge=edge["raw_edge"], adjusted_edge=edge["adjusted_edge"],
                    brier_score=None, signed_error=None, direction_correct=None,
                    status="skipped_no_links",
                    skip_reason=f"No verified link for outcome {mapped_outcome}.",
                    match_finished_at=finished_at,
                )
                settlements_count += 1
                continue

            # Find last snapshot before finished_at
            snapshot = _find_settlement_snapshot(link["id"], finished_at)
            if snapshot is None:
                self._store.append_settlement(
                    match_id=match_id, mapped_outcome=mapped_outcome, engine=engine,
                    competition=competition, settlement_implied_prob=None,
                    settlement_captured_at=None, link_id=link["id"],
                    model_prob=edge["model_prob"], market_prob_at_detection=edge["market_prob"],
                    raw_edge=edge["raw_edge"], adjusted_edge=edge["adjusted_edge"],
                    brier_score=None, signed_error=None, direction_correct=None,
                    status="skipped_no_snapshot",
                    skip_reason=f"No snapshot before {finished_at} for link {link['id']}.",
                    match_finished_at=finished_at,
                )
                settlements_count += 1
                continue

            # Compute error signals
            settlement_prob = snapshot["implied_prob"]
            model_prob = edge["model_prob"]
            market_prob = edge["market_prob"]
            raw_edge = edge["raw_edge"]
            adjusted_edge = edge["adjusted_edge"]

            brier = _compute_brier(model_prob, settlement_prob)
            signed_err = _compute_signed_error(model_prob, settlement_prob)
            dir_correct = _compute_direction_correct(raw_edge, market_prob, settlement_prob)

            self._store.append_settlement(
                match_id=match_id, mapped_outcome=mapped_outcome, engine=engine,
                competition=competition, settlement_implied_prob=settlement_prob,
                settlement_captured_at=snapshot["captured_at"], link_id=link["id"],
                model_prob=model_prob, market_prob_at_detection=market_prob,
                raw_edge=raw_edge, adjusted_edge=adjusted_edge,
                brier_score=brier, signed_error=signed_err, direction_correct=dir_correct,
                status="processed", skip_reason=None,
                match_finished_at=finished_at,
            )
            settlements_count += 1

        # Update calibration for this engine/competition
        _update_market_calibration(self._store, engine, competition)

        return SettlementResult(
            match_id=match_id, status="processed",
            settlements_count=settlements_count, skip_reason=None,
        )

    def scan_and_process(self, limit: int = 50) -> ScanResult:
        """Scan for finished matches without settlements, process them in batch."""
        matches = _find_finished_matches_without_settlements(limit)
        scanned = len(matches)
        processed = 0
        skipped = 0
        already = 0
        errors = 0
        error_details: list[str] = []

        for m in matches:
            try:
                result = self.process_settlement(m["match_id"])
                if result.status == "processed":
                    processed += 1
                elif result.status == "already_processed":
                    already += 1
                else:
                    skipped += 1
            except Exception as exc:
                errors += 1
                if len(error_details) < 10:
                    error_details.append(f"{m['match_id']}: {exc}")
                logger.error(f"Settlement processing failed for {m['match_id']}: {exc}")

        return ScanResult(
            scanned=scanned, processed=processed, skipped=skipped,
            already_processed=already, errors=errors, error_details=error_details,
        )

    def get_settlement(self, match_id: str) -> list[dict[str, Any]]:
        """Get all settlement records for a match."""
        return self._store.get_settlement(match_id)

    def get_calibrations(
        self, engine: str | None = None, competition: str | None = None
    ) -> list[dict[str, Any]]:
        """Get market calibrations, optionally filtered."""
        return self._store.get_calibrations(engine=engine, competition=competition)

    def get_history(
        self, limit: int = 20, engine: str | None = None
    ) -> list[dict[str, Any]]:
        """Get recent settlements (most recent first)."""
        return self._store.get_history(limit=limit, engine=engine)
```

Note: The `_find_finished_matches_without_settlements` function needs to import `KernelMarketSettlement` from `kernel_db`. Add this import at the top of the function or at the module level. To keep the module-level imports clean (D reads `KernelMarketSettlement` from `kernel_db`), add `KernelMarketSettlement` to the import list at the top of the file:

```python
from app.kernel.kernel_db import (
    KernelMatchOutcome,
    KernelSportMarketLink,
    KernelMarketSnapshot,
    KernelMarketSettlement,
    KernelPrediction,
    get_kernel_session,
    get_latest_prediction,
)
```

And simplify `_find_finished_matches_without_settlements` to use the module-level import:

```python
def _find_finished_matches_without_settlements(limit: int) -> list[dict[str, Any]]:
    """Find finished matches that don't have settlement rows yet."""
    session = get_kernel_session()
    try:
        processed_subquery = (
            session.query(KernelMarketSettlement.match_id).distinct().subquery()
        )
        rows = (
            session.query(KernelMatchOutcome)
            .filter(
                KernelMatchOutcome.finished_at.isnot(None),
                ~KernelMatchOutcome.match_id.in_(processed_subquery),
            )
            .order_by(KernelMatchOutcome.finished_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {"match_id": r.match_id, "outcome": r.outcome, "finished_at": r.finished_at}
            for r in rows
        ]
    except Exception:
        return []
    finally:
        session.close()
```

- [ ] **Step 7: Run pure function tests**

Run: `cd backend ; python -m pytest tests/test_market_settlement_service.py -v --no-header -k "compute_brier or compute_signed_error or direction_correct or update_market_calibration" 2>&1 | tail -25`
Expected: All 14 pure function tests PASS.

- [ ] **Step 8: Append DB-integrated tests to `backend/tests/test_market_settlement_service.py`**

Append these tests at the end of the file:

```python
# ---------------------------------------------------------------------------
# DB-integrated tests
# ---------------------------------------------------------------------------

@pytest.fixture
def kernel_db(tmp_path):
    from app.kernel.kernel_db import init_kernel_db, close_kernel_db
    db_path = tmp_path / "settlement_service_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def _seed_prediction(match_id="m1", engine="BasketballEngine", competition="nba", probs=None):
    """Seed a KernelPrediction row."""
    from app.kernel.kernel_db import KernelPrediction, get_kernel_session
    if probs is None:
        probs = {"home_win": 0.65, "away_win": 0.35}
    now = _utcnow()
    session = get_kernel_session()
    try:
        session.add(KernelPrediction(
            match_id=match_id, sport="basketball", competition=competition,
            season="2025-26", engine=engine, predicted_scores={},
            outcome_probabilities=probs, confidence=0.7, feature_version="nba-1.0",
            explanation={}, created_at=now, updated_at=now,
        ))
        session.commit()
    finally:
        session.close()


def _seed_outcome(match_id="m1", finished_at=None, outcome="home_win"):
    """Seed a KernelMatchOutcome row."""
    from app.kernel.kernel_db import KernelMatchOutcome, get_kernel_session
    now = finished_at or _utcnow()
    session = get_kernel_session()
    try:
        session.add(KernelMatchOutcome(
            match_id=match_id, home_score=2, away_score=1, outcome=outcome,
            engine=None, score_mae=None, outcome_correct=None, brier_score=None,
            finished_at=now, created_at=now,
        ))
        session.commit()
    finally:
        session.close()


def _seed_verified_link(match_id="m1", mapped_outcome="home_win", implied_prob=0.6):
    """Seed a verified market link + snapshot via A's public API."""
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    now = _utcnow()
    link_store = SportMarketLinkStore()
    link = link_store.upsert_link(
        match_id=match_id, contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome=mapped_outcome, link_method="rule",
        link_confidence=0.95, verified=True, market_question="q", implied_prob=implied_prob,
    )
    snap_store = MarketSnapshotStore()
    snap_store.append_snapshot(
        link_id=link["id"], implied_prob=implied_prob, price=implied_prob,
        liquidity=None, volume=None, captured_at=now,
    )
    return link


def _seed_edge(match_id="m1", mapped_outcome="home_win", model_prob=0.65, market_prob=0.6):
    """Seed a B edge via EdgeStore.append_edge."""
    from app.kernel.edge_store import EdgeStore
    raw_edge = model_prob - market_prob
    adjusted_edge = raw_edge * 0.8
    store = EdgeStore()
    store.append_edge(
        match_id=match_id, mapped_outcome=mapped_outcome,
        model_prob=model_prob, market_prob=market_prob, raw_edge=raw_edge,
        trust=0.8, liquidity_factor=1.0, adjusted_edge=adjusted_edge,
        spread=None, sources_count=1, stale=False,
    )


def test_process_settlement_happy_path(kernel_db, monkeypatch):
    """Finished match + verified link + snapshot + edge → settlement row written."""
    from app.core import config
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 10)
    finished = _utcnow()
    _seed_prediction(match_id="m1")
    _seed_outcome(match_id="m1", finished_at=finished)
    _seed_verified_link(match_id="m1", mapped_outcome="home_win", implied_prob=0.9)
    _seed_edge(match_id="m1", mapped_outcome="home_win", model_prob=0.65, market_prob=0.6)

    svc = MarketSettlementService()
    result = svc.process_settlement("m1")
    assert result.status == "processed"
    assert result.settlements_count == 1

    settlements = svc.get_settlement("m1")
    assert len(settlements) == 1
    s = settlements[0]
    assert s["mapped_outcome"] == "home_win"
    assert s["engine"] == "BasketballEngine"
    assert s["competition"] == "nba"
    assert s["settlement_implied_prob"] == pytest.approx(0.9)
    assert s["model_prob"] == pytest.approx(0.65)
    assert s["brier_score"] == pytest.approx((0.65 - 0.9) ** 2)
    assert s["signed_error"] == pytest.approx(0.65 - 0.9)
    assert s["direction_correct"] == 1  # raw_edge=0.05>0, settlement(0.9)>market(0.6) → both positive
    assert s["status"] == "processed"


def test_process_settlement_idempotent(kernel_db):
    """Re-processing returns already_processed without writing duplicates."""
    finished = _utcnow()
    _seed_prediction(match_id="m1")
    _seed_outcome(match_id="m1", finished_at=finished)
    _seed_verified_link(match_id="m1", mapped_outcome="home_win", implied_prob=0.9)
    _seed_edge(match_id="m1", mapped_outcome="home_win")

    svc = MarketSettlementService()
    result1 = svc.process_settlement("m1")
    assert result1.status == "processed"
    result2 = svc.process_settlement("m1")
    assert result2.status == "already_processed"
    assert result2.settlements_count == 0

    settlements = svc.get_settlement("m1")
    assert len(settlements) == 1  # no duplicate


def test_process_settlement_skipped_not_finished(kernel_db):
    """Match without outcome → skipped_not_finished."""
    _seed_prediction(match_id="m2")
    # No outcome seeded
    svc = MarketSettlementService()
    result = svc.process_settlement("m2")
    assert result.status == "skipped_not_finished"
    assert result.settlements_count == 0


def test_process_settlement_skipped_no_edges(kernel_db):
    """Finished match but no B edges → skipped_no_edges."""
    finished = _utcnow()
    _seed_prediction(match_id="m3")
    _seed_outcome(match_id="m3", finished_at=finished)
    # No edges seeded
    svc = MarketSettlementService()
    result = svc.process_settlement("m3")
    assert result.status == "skipped_no_edges"


def test_process_settlement_skipped_no_links(kernel_db):
    """Finished match + edges but no verified links → skipped_no_links settlement row."""
    finished = _utcnow()
    _seed_prediction(match_id="m4")
    _seed_outcome(match_id="m4", finished_at=finished)
    _seed_edge(match_id="m4", mapped_outcome="home_win")
    # No verified link seeded
    svc = MarketSettlementService()
    result = svc.process_settlement("m4")
    assert result.status == "processed"  # process_settlement itself succeeds
    assert result.settlements_count == 1  # one skipped settlement row written
    settlements = svc.get_settlement("m4")
    assert settlements[0]["status"] == "skipped_no_links"
    assert settlements[0]["brier_score"] is None


def test_process_settlement_skipped_no_snapshot(kernel_db):
    """Finished match + edges + verified link but no snapshot before finished_at → skipped."""
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    finished = _utcnow() - timedelta(hours=2)  # finished 2 hours ago
    _seed_prediction(match_id="m5")
    _seed_outcome(match_id="m5", finished_at=finished)
    _seed_edge(match_id="m5", mapped_outcome="home_win")
    # Seed link but snapshot is AFTER finished_at
    link_store = SportMarketLinkStore()
    link = link_store.upsert_link(
        match_id="m5", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=True, market_question="q", implied_prob=0.6,
    )
    snap_store = MarketSnapshotStore()
    snap_store.append_snapshot(
        link_id=link["id"], implied_prob=0.6, price=0.6,
        liquidity=None, volume=None, captured_at=_utcnow(),  # NOW, after finished_at
    )
    svc = MarketSettlementService()
    result = svc.process_settlement("m5")
    assert result.status == "processed"
    settlements = svc.get_settlement("m5")
    assert settlements[0]["status"] == "skipped_no_snapshot"
    assert settlements[0]["brier_score"] is None


def test_scan_and_process_batch(kernel_db, monkeypatch):
    """scan_and_process processes multiple finished matches."""
    from app.core import config
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 10)
    for i in range(3):
        mid = f"batch_{i}"
        _seed_prediction(match_id=mid)
        _seed_outcome(match_id=mid)
        _seed_verified_link(match_id=mid, mapped_outcome="home_win", implied_prob=0.7)
        _seed_edge(match_id=mid, mapped_outcome="home_win")
    svc = MarketSettlementService()
    result = svc.scan_and_process(limit=10)
    assert result.scanned == 3
    assert result.processed == 3
    assert result.errors == 0


def test_scan_and_process_skips_already_processed(kernel_db, monkeypatch):
    """scan_and_process doesn't re-process already-settled matches."""
    from app.core import config
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 10)
    _seed_prediction(match_id="m1")
    _seed_outcome(match_id="m1")
    _seed_verified_link(match_id="m1", mapped_outcome="home_win", implied_prob=0.7)
    _seed_edge(match_id="m1", mapped_outcome="home_win")
    svc = MarketSettlementService()
    # First scan processes it
    result1 = svc.scan_and_process(limit=10)
    assert result1.processed == 1
    # Second scan finds nothing new
    result2 = svc.scan_and_process(limit=10)
    assert result2.scanned == 0
    assert result2.processed == 0


def test_get_calibrations_empty(kernel_db):
    """get_calibrations returns empty list when no calibrations exist."""
    svc = MarketSettlementService()
    cals = svc.get_calibrations()
    assert cals == []


def test_get_calibrations_after_processing(kernel_db, monkeypatch):
    """After processing enough settlements, calibration row appears."""
    from app.core import config
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 2)
    monkeypatch.setattr(config.settings, "MARKET_CALIBRATION_WINDOW_SIZE", 30)
    for i in range(2):
        mid = f"cal_{i}"
        _seed_prediction(match_id=mid)
        _seed_outcome(match_id=mid)
        _seed_verified_link(match_id=mid, mapped_outcome="home_win", implied_prob=0.7)
        _seed_edge(match_id=mid, mapped_outcome="home_win", model_prob=0.65, market_prob=0.6)
    svc = MarketSettlementService()
    svc.scan_and_process(limit=10)
    cals = svc.get_calibrations(engine="BasketballEngine", competition="nba")
    assert len(cals) == 1
    assert cals[0]["sample_count"] == 2


def test_get_history(kernel_db, monkeypatch):
    """get_history returns recent settlements."""
    from app.core import config
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 10)
    _seed_prediction(match_id="m1")
    _seed_outcome(match_id="m1")
    _seed_verified_link(match_id="m1", mapped_outcome="home_win", implied_prob=0.7)
    _seed_edge(match_id="m1", mapped_outcome="home_win")
    svc = MarketSettlementService()
    svc.process_settlement("m1")
    history = svc.get_history(limit=10)
    assert len(history) == 1
    assert history[0]["match_id"] == "m1"


def test_get_history_filtered_by_engine(kernel_db, monkeypatch):
    """get_history filters by engine."""
    from app.core import config
    monkeypatch.setattr(config.settings, "MIN_SAMPLES_FOR_MARKET_CALIBRATION", 10)
    _seed_prediction(match_id="m1", engine="BasketballEngine")
    _seed_outcome(match_id="m1")
    _seed_verified_link(match_id="m1", mapped_outcome="home_win", implied_prob=0.7)
    _seed_edge(match_id="m1", mapped_outcome="home_win")
    svc = MarketSettlementService()
    svc.process_settlement("m1")
    history = svc.get_history(limit=10, engine="BasketballEngine")
    assert len(history) == 1
    history_other = svc.get_history(limit=10, engine="OtherEngine")
    assert len(history_other) == 0
```

- [ ] **Step 9: Run all service tests**

Run: `cd backend ; python -m pytest tests/test_market_settlement_service.py -v --no-header 2>&1 | tail -40`
Expected: All ~25 tests PASS.

- [ ] **Step 10: Run regression tests**

Run: `cd backend ; python -m pytest tests/test_edge_detector_service.py tests/test_edge_store.py tests/test_sport_edge_routes.py tests/test_sport_recommendation_service.py tests/test_sport_recommendation_routes.py tests/test_diagnosis_service.py -v --no-header 2>&1 | tail -20`
Expected: All existing tests PASS (zero regression on B/C/Phase 3).

- [ ] **Step 11: Commit**

```bash
git add backend/app/core/config.py backend/app/kernel/kernel_db.py backend/app/kernel/market_settlement_store.py backend/app/kernel/market_settlement_service.py backend/tests/test_market_settlement_service.py
git commit -m "feat(phase7-d): add MarketSettlementService with settlement computation and calibration regression"
```
IMPORTANT: Use `;` not `&&` to chain commands in PowerShell.

---

## Task 2: API Routes + Router Registration + Scheduler Job

**Files:**
- Create: `backend/app/api/routes/sport_settlements.py`
- Modify: `backend/app/api/router.py` (line 3 import + after line 15)
- Modify: `backend/app/core/scheduler.py` (add job function + registration)
- Test: `backend/tests/test_market_settlement_routes.py`

**Interfaces:**
- Consumes: `MarketSettlementService` from Task 1, `config.settings`, `require_write_key` from `app.api.security`
- Produces: 4 API endpoints under `/api/sport-settlements/*`, scheduler job `_job_process_market_settlements`

- [ ] **Step 1: Write the failing route test file**

Create `backend/tests/test_market_settlement_routes.py`:

```python
"""Tests for sport settlement API routes.

All endpoints gated by PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED (503 when false).
3 GET endpoints are read-only. 1 POST endpoint requires require_write_key.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config
from app.kernel.kernel_db import init_kernel_db, close_kernel_db


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "settlement_routes_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


@pytest.fixture
def client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED", True)
    monkeypatch.setattr(config.settings, "OPERATOR_WRITE_KEY", "test-key")
    from app.api.routes import sport_settlements
    app = FastAPI()
    app.include_router(sport_settlements.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def disabled_client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED", False)
    from app.api.routes import sport_settlements
    app = FastAPI()
    app.include_router(sport_settlements.router, prefix="/api")
    return TestClient(app)


def _seed_full_scenario(match_id="m1"):
    """Seed prediction + outcome + link + snapshot + edge for a complete settlement."""
    from datetime import datetime, timezone
    from app.kernel.kernel_db import KernelPrediction, KernelMatchOutcome, get_kernel_session
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    from app.kernel.edge_store import EdgeStore
    now = datetime.now(timezone.utc)
    session = get_kernel_session()
    try:
        session.add(KernelPrediction(
            match_id=match_id, sport="basketball", competition="nba",
            season="2025-26", engine="BasketballEngine", predicted_scores={},
            outcome_probabilities={"home_win": 0.65, "away_win": 0.35}, confidence=0.7,
            feature_version="nba-1.0", explanation={}, created_at=now, updated_at=now,
        ))
        session.add(KernelMatchOutcome(
            match_id=match_id, home_score=2, away_score=1, outcome="home_win",
            engine=None, score_mae=None, outcome_correct=None, brier_score=None,
            finished_at=now, created_at=now,
        ))
        session.commit()
    finally:
        session.close()
    link_store = SportMarketLinkStore()
    link = link_store.upsert_link(
        match_id=match_id, contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=True, market_question="q", implied_prob=0.6,
    )
    snap_store = MarketSnapshotStore()
    snap_store.append_snapshot(
        link_id=link["id"], implied_prob=0.9, price=0.9,
        liquidity=None, volume=None, captured_at=now,
    )
    edge_store = EdgeStore()
    edge_store.append_edge(
        match_id=match_id, mapped_outcome="home_win",
        model_prob=0.65, market_prob=0.6, raw_edge=0.05,
        trust=0.8, liquidity_factor=1.0, adjusted_edge=0.04,
        spread=None, sources_count=1, stale=False,
    )


def test_get_settlement_returns_503_when_disabled(disabled_client):
    res = disabled_client.get("/api/sport-settlements/m1")
    assert res.status_code == 503


def test_get_settlement_returns_404_when_no_settlements(client):
    res = client.get("/api/sport-settlements/m1")
    assert res.status_code == 404


def test_get_settlement_returns_settlement(client):
    _seed_full_scenario("m1")
    # Process settlement first
    res = client.post("/api/sport-settlements/process/m1", headers={"X-Write-Key": "test-key"})
    assert res.status_code == 200
    # Now GET should return the settlement
    res = client.get("/api/sport-settlements/m1")
    assert res.status_code == 200
    data = res.json()
    assert data["match_id"] == "m1"
    assert data["total"] == 1
    assert data["items"][0]["mapped_outcome"] == "home_win"
    assert data["items"][0]["status"] == "processed"


def test_get_calibrations_returns_503_when_disabled(disabled_client):
    res = disabled_client.get("/api/sport-settlements/calibrations")
    assert res.status_code == 503


def test_get_calibrations_returns_empty(client):
    res = client.get("/api/sport-settlements/calibrations")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_get_calibrations_with_filter(client):
    _seed_full_scenario("m1")
    client.post("/api/sport-settlements/process/m1", headers={"X-Write-Key": "test-key"})
    res = client.get("/api/sport-settlements/calibrations?engine=BasketballEngine")
    assert res.status_code == 200


def test_get_history_returns_503_when_disabled(disabled_client):
    res = disabled_client.get("/api/sport-settlements/history")
    assert res.status_code == 503


def test_get_history_returns_history(client):
    _seed_full_scenario("m1")
    client.post("/api/sport-settlements/process/m1", headers={"X-Write-Key": "test-key"})
    res = client.get("/api/sport-settlements/history?limit=10")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["match_id"] == "m1"


def test_process_returns_503_when_disabled(disabled_client):
    res = disabled_client.post("/api/sport-settlements/process/m1")
    assert res.status_code == 503


def test_process_requires_write_key(client):
    """POST /process without write key → 401/403."""
    _seed_full_scenario("m1")
    res = client.post("/api/sport-settlements/process/m1")  # no X-Write-Key header
    assert res.status_code in (401, 403)


def test_process_with_write_key_succeeds(client):
    _seed_full_scenario("m1")
    res = client.post(
        "/api/sport-settlements/process/m1", headers={"X-Write-Key": "test-key"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["match_id"] == "m1"
    assert data["status"] == "processed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend ; python -m pytest tests/test_market_settlement_routes.py -v --no-header 2>&1 | tail -15`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.routes.sport_settlements'`

- [ ] **Step 3: Create `backend/app/api/routes/sport_settlements.py`**

```python
"""Sport settlement API routes.

All endpoints gated by PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED (503 when false).
3 GET endpoints are read-only. 1 POST endpoint requires require_write_key.
Route order: static paths (/calibrations, /history) before dynamic /{match_id}
to avoid FastAPI catch-all routing (lesson from Subproject C).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.security import require_write_key
from app.core import config

router = APIRouter(prefix="/sport-settlements", tags=["Sport Settlements"])


def _ensure_enabled() -> None:
    if not config.settings.PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED:
        raise HTTPException(
            status_code=503, detail="Market settlement feedback is disabled."
        )


def _service():
    from app.kernel.market_settlement_service import MarketSettlementService
    return MarketSettlementService()


@router.get("/calibrations")
def get_calibrations(
    engine: str | None = Query(None),
    competition: str | None = Query(None),
) -> dict:
    """Market calibration list. Static path before /{match_id}."""
    _ensure_enabled()
    svc = _service()
    items = svc.get_calibrations(engine=engine, competition=competition)
    return {"items": items, "total": len(items)}


@router.get("/history")
def get_history(
    limit: int = Query(20, ge=1, le=100),
    engine: str | None = Query(None),
) -> dict:
    """Settlement history. Static path before /{match_id}."""
    _ensure_enabled()
    svc = _service()
    items = svc.get_history(limit=limit, engine=engine)
    return {"items": items, "total": len(items)}


@router.get("/{match_id}")
def get_settlement(match_id: str) -> dict:
    """Single match settlement. Returns 404 when no settlements exist."""
    _ensure_enabled()
    svc = _service()
    items = svc.get_settlement(match_id)
    if not items:
        raise HTTPException(
            status_code=404, detail="No settlements found for match."
        )
    return {"match_id": match_id, "items": items, "total": len(items)}


@router.post("/process/{match_id}")
def process_settlement(
    match_id: str, _auth: None = Depends(require_write_key)
) -> dict:
    """Manually trigger settlement processing for a match."""
    _ensure_enabled()
    svc = _service()
    result = svc.process_settlement(match_id)
    return {
        "match_id": match_id,
        "status": result.status,
        "settlements_count": result.settlements_count,
    }
```

- [ ] **Step 4: Register router in `backend/app/api/router.py`**

On line 3, add `sport_settlements` to the import:
```python
from app.api.routes import events, llm, quality_metrics, world_cup_predictions, world_cup_analytics, predictions, sport_markets, sport_edges, sport_recommendations, sport_settlements
```

After line 15, add:
```python
api_router.include_router(sport_settlements.router, tags=["Sport Settlements"])
```

- [ ] **Step 5: Add scheduler job to `backend/app/core/scheduler.py`**

Add the job function after the existing `_job_detect_sport_edges` function (find it around line 608-625):

```python
async def _job_process_market_settlements():
    """Scan for finished matches without settlements, process them."""
    if not settings.PHASE7_MARKET_SETTLEMENT_SCHEDULER_ENABLED:
        return
    from app.kernel.market_settlement_service import MarketSettlementService
    try:
        svc = MarketSettlementService()
        result = svc.scan_and_process(limit=settings.MARKET_SETTLEMENT_BATCH_LIMIT)
        logger.info(
            f"[Scheduler] Market settlements: scanned={result.scanned} "
            f"processed={result.processed} skipped={result.skipped} "
            f"already={result.already_processed} errors={result.errors}"
        )
    except Exception as exc:
        logger.error(f"[Scheduler] Market settlement job failed: {exc}")
```

Add the registration after B's edge detection job registration (find the `if settings.PHASE7_EDGE_DETECTOR_ENABLED:` block, ~line 861, and add after its `scheduler.add_job(...)` call):

```python
        if settings.PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED:
            scheduler.add_job(
                _job_process_market_settlements,
                IntervalTrigger(minutes=settings.MARKET_SETTLEMENT_INTERVAL_MIN),
                id="market_settlement_feedback",
                replace_existing=True,
                max_instances=1,
            )
```

- [ ] **Step 6: Run route tests**

Run: `cd backend ; python -m pytest tests/test_market_settlement_routes.py -v --no-header 2>&1 | tail -20`
Expected: All ~10 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/routes/sport_settlements.py backend/app/api/router.py backend/app/core/scheduler.py backend/tests/test_market_settlement_routes.py
git commit -m "feat(phase7-d): add 4 sport-settlements API endpoints + scheduler job"
```

---

## Task 3: CLI Tool

**Files:**
- Create: `backend/scripts/sport_settlement_cli.py`
- Test: `backend/tests/test_market_settlement_cli.py`

**Interfaces:**
- Consumes: `MarketSettlementService` from Task 1, `init_kernel_db` from kernel_db
- Produces: CLI with `process`, `scan`, `calibrations`, `history` subcommands

- [ ] **Step 1: Write the failing CLI test file**

Create `backend/tests/test_market_settlement_cli.py`:

```python
"""Tests for sport_settlement_cli."""
import pytest
from app.kernel.kernel_db import init_kernel_db, close_kernel_db


@pytest.fixture
def kernel_db(tmp_path, monkeypatch):
    db_path = tmp_path / "settlement_cli_test.db"
    monkeypatch.setenv("KERNEL_DB_PATH", str(db_path))
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def _seed_data(match_id="m1"):
    from datetime import datetime, timezone
    from app.kernel.kernel_db import KernelPrediction, KernelMatchOutcome, get_kernel_session
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    from app.kernel.edge_store import EdgeStore
    now = datetime.now(timezone.utc)
    session = get_kernel_session()
    try:
        session.add(KernelPrediction(
            match_id=match_id, sport="basketball", competition="nba",
            season="2025-26", engine="BasketballEngine", predicted_scores={},
            outcome_probabilities={"home_win": 0.65, "away_win": 0.35}, confidence=0.7,
            feature_version="nba-1.0", explanation={}, created_at=now, updated_at=now,
        ))
        session.add(KernelMatchOutcome(
            match_id=match_id, home_score=2, away_score=1, outcome="home_win",
            engine=None, score_mae=None, outcome_correct=None, brier_score=None,
            finished_at=now, created_at=now,
        ))
        session.commit()
    finally:
        session.close()
    link_store = SportMarketLinkStore()
    link = link_store.upsert_link(
        match_id=match_id, contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=True, market_question="q", implied_prob=0.6,
    )
    snap_store = MarketSnapshotStore()
    snap_store.append_snapshot(
        link_id=link["id"], implied_prob=0.9, price=0.9,
        liquidity=None, volume=None, captured_at=now,
    )
    edge_store = EdgeStore()
    edge_store.append_edge(
        match_id=match_id, mapped_outcome="home_win",
        model_prob=0.65, market_prob=0.6, raw_edge=0.05,
        trust=0.8, liquidity_factor=1.0, adjusted_edge=0.04,
        spread=None, sources_count=1, stale=False,
    )


def test_cli_process_command(kernel_db, capsys):
    _seed_data("m1")
    from scripts.sport_settlement_cli import main
    rc = main(["process", "--match-id", "m1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[OK]" in out
    assert "m1" in out


def test_cli_scan_command(kernel_db, capsys):
    from app.core import config
    # Lower threshold so calibration writes
    _seed_data("m1")
    from scripts.sport_settlement_cli import main
    rc = main(["scan", "--limit", "10"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "scanned" in out or "processed" in out


def test_cli_calibrations_command(kernel_db, capsys):
    from scripts.sport_settlement_cli import main
    rc = main(["calibrations"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "calibration" in out.lower() or "no calibration" in out.lower()


def test_cli_history_command(kernel_db, capsys):
    from scripts.sport_settlement_cli import main
    rc = main(["history", "--limit", "5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "settlement" in out.lower() or "no settlement" in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend ; python -m pytest tests/test_market_settlement_cli.py -v --no-header 2>&1 | tail -10`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.sport_settlement_cli'`

- [ ] **Step 3: Create `backend/scripts/sport_settlement_cli.py`**

```python
"""Sport settlement CLI.

Usage:
    python -m scripts.sport_settlement_cli process --match-id ID
    python -m scripts.sport_settlement_cli scan [--limit N]
    python -m scripts.sport_settlement_cli calibrations [--engine E] [--competition C]
    python -m scripts.sport_settlement_cli history [--limit N] [--engine E]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))


def _print(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def _cmd_process(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.market_settlement_service import MarketSettlementService
    init_kernel_db()
    svc = MarketSettlementService()
    result = svc.process_settlement(args.match_id)
    if result.status == "already_processed":
        _print(f"[INFO] match={args.match_id} already processed")
        return 0
    if result.status.startswith("skipped"):
        _print(f"[SKIP] match={args.match_id} status={result.status} reason={result.skip_reason}")
        return 0
    _print(f"[OK] match={args.match_id} status={result.status} settlements={result.settlements_count}")
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.market_settlement_service import MarketSettlementService
    init_kernel_db()
    svc = MarketSettlementService()
    result = svc.scan_and_process(limit=args.limit)
    _print(
        f"[OK] scanned={result.scanned} processed={result.processed} "
        f"skipped={result.skipped} already={result.already_processed} errors={result.errors}"
    )
    for detail in result.error_details:
        _print(f"  ERROR: {detail}")
    return 0


def _cmd_calibrations(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.market_settlement_service import MarketSettlementService
    init_kernel_db()
    svc = MarketSettlementService()
    cals = svc.get_calibrations(engine=args.engine, competition=args.competition)
    if not cals:
        _print("[INFO] no market calibrations found")
        return 0
    _print(f"[OK] {len(cals)} calibrations:")
    for cal in cals:
        _print(
            f"  engine={cal['engine']:<20} competition={cal['competition']:<10} "
            f"slope={cal['slope']:.3f} intercept={cal['intercept']:+.3f} "
            f"samples={cal['sample_count']} avg_brier={cal['avg_brier']:.4f} "
            f"dir_acc={cal['direction_accuracy']:.2%}"
        )
    return 0


def _cmd_history(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.market_settlement_service import MarketSettlementService
    init_kernel_db()
    svc = MarketSettlementService()
    items = svc.get_history(limit=args.limit, engine=args.engine)
    if not items:
        _print("[INFO] no settlements found")
        return 0
    _print(f"[OK] {len(items)} settlements (limit={args.limit}):")
    for s in items:
        _print(
            f"  match={s['match_id']:<20} outcome={s['mapped_outcome']:<10} "
            f"engine={s['engine']:<20} model={s['model_prob']:.3f} "
            f"settlement={s['settlement_implied_prob']:.3f if s['settlement_implied_prob'] is not None else 'N/A'} "
            f"brier={s['brier_score']:.4f if s['brier_score'] is not None else 'N/A'} "
            f"status={s['status']}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sport settlement CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_process = sub.add_parser("process", help="process settlement for a single match")
    p_process.add_argument("--match-id", required=True)
    p_process.set_defaults(func=_cmd_process)

    p_scan = sub.add_parser("scan", help="scan and process finished matches")
    p_scan.add_argument("--limit", type=int, default=50)
    p_scan.set_defaults(func=_cmd_scan)

    p_cal = sub.add_parser("calibrations", help="show market calibrations")
    p_cal.add_argument("--engine", default=None)
    p_cal.add_argument("--competition", default=None)
    p_cal.set_defaults(func=_cmd_calibrations)

    p_hist = sub.add_parser("history", help="show settlement history")
    p_hist.add_argument("--limit", type=int, default=20)
    p_hist.add_argument("--engine", default=None)
    p_hist.set_defaults(func=_cmd_history)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

Note: The f-string in `_cmd_history` uses conditional expressions inside `{}`. For Python 3.12+ this works, but to be safe with older formatting, use this alternative for the history print loop:

```python
    for s in items:
        settlement_str = f"{s['settlement_implied_prob']:.3f}" if s['settlement_implied_prob'] is not None else "N/A"
        brier_str = f"{s['brier_score']:.4f}" if s['brier_score'] is not None else "N/A"
        _print(
            f"  match={s['match_id']:<20} outcome={s['mapped_outcome']:<10} "
            f"engine={s['engine']:<20} model={s['model_prob']:.3f} "
            f"settlement={settlement_str} brier={brier_str} "
            f"status={s['status']}"
        )
```

- [ ] **Step 4: Run CLI tests**

Run: `cd backend ; python -m pytest tests/test_market_settlement_cli.py -v --no-header 2>&1 | tail -15`
Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/sport_settlement_cli.py backend/tests/test_market_settlement_cli.py
git commit -m "feat(phase7-d): add sport_settlement_cli with process/scan/calibrations/history commands"
```

---

## Task 4: Frontend (API Client + Page + Components + Nav)

**Files:**
- Create: `frontend/src/lib/sport-settlements-api.ts`
- Create: `frontend/src/components/sports/settlements/SettlementHistoryTable.tsx`
- Create: `frontend/src/components/sports/settlements/MarketCalibrationPanel.tsx`
- Create: `frontend/src/app/sports/settlements/page.tsx`
- Modify: `frontend/src/components/app-nav.tsx` (add nav entry after `/sports/recommendations`)
- Create: `frontend/src/components/sports/settlements/SettlementHistoryTable.test.tsx`
- Create: `frontend/src/components/sports/settlements/MarketCalibrationPanel.test.tsx`

**Interfaces:**
- Consumes: `/api/sport-settlements/*` endpoints from Task 2
- Produces: Frontend page at `/sports/settlements` with history table + calibration panel

- [ ] **Step 1: Create `frontend/src/lib/sport-settlements-api.ts`**

```typescript
import { getWorldCupApiBase } from "./env";

const API_BASE = getWorldCupApiBase();

export interface MarketSettlement {
  id: number;
  match_id: string;
  mapped_outcome: string;
  engine: string;
  competition: string;
  settlement_implied_prob: number | null;
  settlement_captured_at: string | null;
  link_id: number | null;
  model_prob: number | null;
  market_prob_at_detection: number | null;
  raw_edge: number | null;
  adjusted_edge: number | null;
  brier_score: number | null;
  signed_error: number | null;
  direction_correct: number | null;
  status: string;
  skip_reason: string | null;
  match_finished_at: string;
  processed_at: string;
}

export interface MarketCalibration {
  id: number;
  engine: string;
  competition: string;
  slope: number;
  intercept: number;
  sample_count: number;
  avg_brier: number;
  avg_signed_error: number;
  direction_accuracy: number;
  last_updated: string;
}

export interface SettlementList {
  items: MarketSettlement[];
  total: number;
}

export interface CalibrationList {
  items: MarketCalibration[];
  total: number;
}

function buildQuery(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== "");
  if (entries.length === 0) return "";
  const usp = new URLSearchParams();
  for (const [k, v] of entries) usp.set(k, String(v));
  return `?${usp.toString()}`;
}

export async function fetchSettlement(matchId: string): Promise<SettlementList> {
  const res = await fetch(`${API_BASE}/api/sport-settlements/${matchId}`);
  if (!res.ok) throw new Error(`Failed to fetch settlement: ${res.status}`);
  return res.json();
}

export async function fetchSettlementHistory(
  limit: number = 20,
  engine?: string,
): Promise<SettlementList> {
  const q = buildQuery({ limit, engine });
  const res = await fetch(`${API_BASE}/api/sport-settlements/history${q}`);
  if (!res.ok) throw new Error(`Failed to fetch history: ${res.status}`);
  return res.json();
}

export async function fetchCalibrations(
  engine?: string,
  competition?: string,
): Promise<CalibrationList> {
  const q = buildQuery({ engine, competition });
  const res = await fetch(`${API_BASE}/api/sport-settlements/calibrations${q}`);
  if (!res.ok) throw new Error(`Failed to fetch calibrations: ${res.status}`);
  return res.json();
}
```

- [ ] **Step 2: Create `frontend/src/components/sports/settlements/SettlementHistoryTable.tsx`**

```tsx
"use client";
import { useEffect, useState } from "react";
import { fetchSettlementHistory, type MarketSettlement } from "@/lib/sport-settlements-api";

export function SettlementHistoryTable() {
  const [items, setItems] = useState<MarketSettlement[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [engineFilter, setEngineFilter] = useState<string>("");

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchSettlementHistory(50, engineFilter || undefined)
      .then((data) => {
        setItems(data.items);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [engineFilter]);

  if (loading) return <div data-testid="loading">加载中...</div>;
  if (error) return <div data-testid="error">错误: {error}</div>;
  if (items.length === 0) return <div data-testid="empty">暂无结算记录</div>;

  return (
    <div>
      <div className="mb-2 flex gap-2">
        <input
          value={engineFilter}
          onChange={(e) => setEngineFilter(e.target.value)}
          placeholder="按引擎过滤"
          data-testid="engine-filter"
          className="border px-2 py-1"
        />
      </div>
      <table data-testid="settlements-table" className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b">
            <th className="text-left p-1">比赛</th>
            <th className="text-left p-1">引擎</th>
            <th className="text-left p-1">赛事</th>
            <th className="text-left p-1">结果</th>
            <th className="text-right p-1">模型概率</th>
            <th className="text-right p-1">结算概率</th>
            <th className="text-right p-1">Brier</th>
            <th className="text-center p-1">方向</th>
            <th className="text-left p-1">状态</th>
          </tr>
        </thead>
        <tbody>
          {items.map((s) => (
            <tr key={s.id} className="border-b">
              <td className="p-1">{s.match_id}</td>
              <td className="p-1">{s.engine}</td>
              <td className="p-1">{s.competition}</td>
              <td className="p-1">{s.mapped_outcome}</td>
              <td className="text-right p-1">
                {s.model_prob !== null ? s.model_prob.toFixed(3) : "—"}
              </td>
              <td className="text-right p-1">
                {s.settlement_implied_prob !== null ? s.settlement_implied_prob.toFixed(3) : "—"}
              </td>
              <td className="text-right p-1">
                {s.brier_score !== null ? s.brier_score.toFixed(4) : "—"}
              </td>
              <td className="text-center p-1" data-testid={`dir-${s.id}`}>
                {s.direction_correct === 1 ? "✓" : s.direction_correct === 0 ? "✗" : "—"}
              </td>
              <td className="p-1">{s.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 3: Create `frontend/src/components/sports/settlements/MarketCalibrationPanel.tsx`**

```tsx
"use client";
import { useEffect, useState } from "react";
import { fetchCalibrations, type MarketCalibration } from "@/lib/sport-settlements-api";

export function MarketCalibrationPanel() {
  const [items, setItems] = useState<MarketCalibration[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchCalibrations()
      .then((data) => {
        setItems(data.items);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  if (loading) return <div data-testid="loading">加载中...</div>;
  if (error) return <div data-testid="error">错误: {error}</div>;
  if (items.length === 0) return <div data-testid="empty">暂无市场校准数据</div>;

  return (
    <div data-testid="calibration-panel" className="grid gap-2">
      {items.map((cal) => {
        const isWellCalibrated = Math.abs(cal.slope - 1.0) < 0.2;
        return (
          <div
            key={cal.id}
            data-testid={`cal-card-${cal.id}`}
            className={`border p-3 rounded ${isWellCalibrated ? "border-green-500" : "border-yellow-500"}`}
          >
            <div className="flex justify-between">
              <span className="font-mono text-sm">{cal.engine}</span>
              <span className="text-xs text-muted-foreground">{cal.competition}</span>
            </div>
            <div className="mt-2 grid grid-cols-2 gap-1 text-xs">
              <span>斜率: {cal.slope.toFixed(3)}</span>
              <span>截距: {cal.intercept.toFixed(3)}</span>
              <span>样本数: {cal.sample_count}</span>
              <span>方向准确率: {(cal.direction_accuracy * 100).toFixed(1)}%</span>
              <span>平均 Brier: {cal.avg_brier.toFixed(4)}</span>
              <span>平均误差: {cal.avg_signed_error.toFixed(4)}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: Create `frontend/src/app/sports/settlements/page.tsx`**

```tsx
"use client";
import { useState } from "react";
import { SettlementHistoryTable } from "@/components/sports/settlements/SettlementHistoryTable";
import { MarketCalibrationPanel } from "@/components/sports/settlements/MarketCalibrationPanel";

type Tab = "history" | "calibrations";

export default function SportSettlementsPage() {
  const [tab, setTab] = useState<Tab>("history");

  return (
    <main className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="text-xl font-semibold">市场结算反馈</h1>
      <div className="mt-4 flex gap-2">
        <button
          onClick={() => setTab("history")}
          className={tab === "history" ? "bg-secondary" : ""}
        >
          结算历史
        </button>
        <button
          onClick={() => setTab("calibrations")}
          className={tab === "calibrations" ? "bg-secondary" : ""}
        >
          市场校准
        </button>
      </div>
      <div className="mt-4">
        {tab === "history" && <SettlementHistoryTable />}
        {tab === "calibrations" && <MarketCalibrationPanel />}
      </div>
    </main>
  );
}
```

- [ ] **Step 5: Add nav entry to `frontend/src/components/app-nav.tsx`**

After line 22 (`/sports/recommendations` entry), add:

```typescript
  { href: "/sports/settlements", label: "体育结算", icon: Target, match: ["/sports/settlements"] },
```

Note: `Target` is already imported on line 5 (used by `/decisions`). Reusing the same icon is acceptable — or use `CheckCircle` from lucide-react if available. Check the import line first; if `Target` is already there, reuse it.

- [ ] **Step 6: Create `frontend/src/components/sports/settlements/SettlementHistoryTable.test.tsx`**

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { SettlementHistoryTable } from "./SettlementHistoryTable";
import type { SettlementList } from "@/lib/sport-settlements-api";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const apiMocks = vi.hoisted(() => ({
  fetchSettlementHistory: vi.fn(),
}));
vi.mock("@/lib/sport-settlements-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sport-settlements-api")>()),
  fetchSettlementHistory: apiMocks.fetchSettlementHistory,
}));

const historyData: SettlementList = {
  items: [
    {
      id: 1, match_id: "m1", mapped_outcome: "home_win", engine: "BasketballEngine",
      competition: "nba", settlement_implied_prob: 0.9, settlement_captured_at: "2026-01-01T00:00:00Z",
      link_id: 1, model_prob: 0.65, market_prob_at_detection: 0.6, raw_edge: 0.05,
      adjusted_edge: 0.04, brier_score: 0.0625, signed_error: -0.25, direction_correct: 1,
      status: "processed", skip_reason: null, match_finished_at: "2026-01-01T00:00:00Z",
      processed_at: "2026-01-01T00:00:00Z",
    },
  ],
  total: 1,
};

describe("SettlementHistoryTable", () => {
  it("renders rows after load", async () => {
    apiMocks.fetchSettlementHistory.mockResolvedValue(historyData);
    render(<SettlementHistoryTable />);
    await waitFor(() =>
      expect(screen.getByTestId("settlements-table")).toBeInTheDocument(),
    );
    expect(screen.getByText("m1")).toBeInTheDocument();
    expect(screen.getByText("BasketballEngine")).toBeInTheDocument();
  });

  it("renders empty state", async () => {
    apiMocks.fetchSettlementHistory.mockResolvedValue({ items: [], total: 0 });
    render(<SettlementHistoryTable />);
    await waitFor(() => expect(screen.getByTestId("empty")).toBeInTheDocument());
  });

  it("renders error state", async () => {
    apiMocks.fetchSettlementHistory.mockRejectedValue(new Error("boom"));
    render(<SettlementHistoryTable />);
    await waitFor(() => expect(screen.getByTestId("error")).toBeInTheDocument());
  });

  it("renders direction correct checkmark", async () => {
    apiMocks.fetchSettlementHistory.mockResolvedValue(historyData);
    render(<SettlementHistoryTable />);
    await waitFor(() => expect(screen.getByTestId("dir-1")).toBeInTheDocument());
    expect(screen.getByTestId("dir-1").textContent).toBe("✓");
  });
});
```

- [ ] **Step 7: Create `frontend/src/components/sports/settlements/MarketCalibrationPanel.test.tsx`**

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MarketCalibrationPanel } from "./MarketCalibrationPanel";
import type { CalibrationList } from "@/lib/sport-settlements-api";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const apiMocks = vi.hoisted(() => ({
  fetchCalibrations: vi.fn(),
}));
vi.mock("@/lib/sport-settlements-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sport-settlements-api")>()),
  fetchCalibrations: apiMocks.fetchCalibrations,
}));

const calData: CalibrationList = {
  items: [
    {
      id: 1, engine: "BasketballEngine", competition: "nba",
      slope: 0.95, intercept: 0.02, sample_count: 15,
      avg_brier: 0.034, avg_signed_error: -0.01, direction_accuracy: 0.73,
      last_updated: "2026-01-01T00:00:00Z",
    },
  ],
  total: 1,
};

describe("MarketCalibrationPanel", () => {
  it("renders cards after load", async () => {
    apiMocks.fetchCalibrations.mockResolvedValue(calData);
    render(<MarketCalibrationPanel />);
    await waitFor(() =>
      expect(screen.getByTestId("calibration-panel")).toBeInTheDocument(),
    );
    expect(screen.getByText("BasketballEngine")).toBeInTheDocument();
  });

  it("renders empty state", async () => {
    apiMocks.fetchCalibrations.mockResolvedValue({ items: [], total: 0 });
    render(<MarketCalibrationPanel />);
    await waitFor(() => expect(screen.getByTestId("empty")).toBeInTheDocument());
  });

  it("renders error state", async () => {
    apiMocks.fetchCalibrations.mockRejectedValue(new Error("boom"));
    render(<MarketCalibrationPanel />);
    await waitFor(() => expect(screen.getByTestId("error")).toBeInTheDocument());
  });

  it("shows calibration metrics", async () => {
    apiMocks.fetchCalibrations.mockResolvedValue(calData);
    render(<MarketCalibrationPanel />);
    await waitFor(() => expect(screen.getByTestId("cal-card-1")).toBeInTheDocument());
    const card = screen.getByTestId("cal-card-1");
    expect(card.textContent).toContain("0.950");  // slope
    expect(card.textContent).toContain("15");  // sample_count
  });
});
```

- [ ] **Step 8: Run frontend tests**

Run: `cd frontend ; npx vitest run src/components/sports/settlements/ 2>&1 | tail -20`
Expected: All 8 tests PASS (4 + 4).

- [ ] **Step 9: Commit**

```bash
git add frontend/src/lib/sport-settlements-api.ts frontend/src/app/sports/settlements/page.tsx frontend/src/components/sports/settlements/ frontend/src/components/app-nav.tsx
git commit -m "feat(phase7-d): add sport settlements frontend (API client + page + components + nav)"
```

---

## Final Verification

After completing all 4 tasks, run these verification commands:

1. **Full backend test suite for D**:
   ```
   cd backend ; python -m pytest tests/test_market_settlement_service.py tests/test_market_settlement_routes.py tests/test_market_settlement_cli.py -v --no-header 2>&1 | tail -40
   ```

2. **Regression — B/C/Phase 3**:
   ```
   cd backend ; python -m pytest tests/test_edge_detector_service.py tests/test_edge_store.py tests/test_sport_edge_routes.py tests/test_sport_recommendation_service.py tests/test_sport_recommendation_routes.py tests/test_sport_recommendation_cli.py tests/test_diagnosis_service.py -v --no-header 2>&1 | tail -20
   ```

3. **Frontend D tests**:
   ```
   cd frontend ; npx vitest run src/components/sports/settlements/ 2>&1 | tail -15
   ```

4. **Git status** — verify clean working tree:
   ```
   git status --short
   ```

## Summary

| Task | Files | Tests | Commit Message |
|------|-------|-------|-----------------|
| 1. Config + DB + Store + Service | 5 | ~25 | `feat(phase7-d): add MarketSettlementService with settlement computation and calibration regression` |
| 2. API Routes + Router + Scheduler | 4 | ~10 | `feat(phase7-d): add 4 sport-settlements API endpoints + scheduler job` |
| 3. CLI Tool | 2 | 4 | `feat(phase7-d): add sport_settlement_cli with process/scan/calibrations/history commands` |
| 4. Frontend | 7 | 8 | `feat(phase7-d): add sport settlements frontend (API client + page + components + nav)` |
| **Total** | **18** | **~47** | 4 commits |
