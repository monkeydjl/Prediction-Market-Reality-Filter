# Phase 7 Subproject B — Edge Detector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Edge Detector that computes model-vs-market divergence per outcome for sports matches, persists it as a time-series, and exposes 3 read-only API endpoints.

**Architecture:** `EdgeDetectorService` (domain service) reads verified market links (Subproject A) and kernel predictions via existing read accessors, aligns by `mapped_outcome`, computes `raw_edge` + trust-weighted `adjusted_edge`, and delegates persistence to `EdgeStore`. Read-only on the kernel — never calls `PredictionKernel.predict()`. Zero-invasion: no modifications to `PredictionKernel`, `LearningService`, `domain.py`, the 3 learning tables, or Subproject A files (except one additive method).

**Tech Stack:** Python 3.11+, SQLAlchemy 2.0 ORM (KernelBase), FastAPI, pytest, APScheduler (IntervalTrigger).

## Global Constraints

1. `PHASE7_EDGE_DETECTOR_ENABLED` feature flag must default to OFF — when false, all 3 endpoints return 503 and the scheduler job is not registered.
2. Zero-invasion: `PredictionKernel`, `PredictionEngine`, `FeatureSet`, `domain.py`, `LearningService`, the 3 learning tables (`KernelPredictionHistory`, `KernelCalibration`, `KernelEngineScore`), learning dashboard components, `event_market_link_store`, `polymarket_event_source`, and all Subproject A files (except minimal additive appends to `sport_market_link_store.py` and `kernel_db.py`) must NOT be modified.
3. `KernelCalibration` table must have zero structural modifications — B only reads via a new `get_calibration()` accessor function.
4. New table `kernel_sport_edges` must subclass `KernelBase` and use the `kernel_` prefix.
5. New `get_matches_with_verified_links` method on `SportMarketLinkStore` must be additive — must NOT modify existing `upsert_link`, `get_links`, `get_verified_links`, `get_pending_links`, `set_verified`, or `list_links` methods.
6. `EdgeDetectorService` must NOT call `PredictionKernel.predict()` — only `get_latest_prediction(match_id)` (read-only).
7. Edge values are 0-1 scale (NOT 0-100). `raw_edge` can be negative (model predicts lower than market).
8. Polymarket spread is NOT adjusted — use raw `implied_prob`. `spread` field on `EdgeResult` is `None` for now (known limitation).
9. All 3 API endpoints are GET (read-only) — no `require_write_key` auth.
10. Standing instruction "不推送" — commits must not be pushed to origin.
11. B must NOT produce act/watch/skip decisions — that is Subproject C.
12. B must NOT extend `ActionableRecommendation` — that is Subproject C.
13. B must NOT feed market settlement prices back into the learning loop — that is Subproject D.
14. Subagent-driven task execution must be used for implementation, with independent sub-agents per task and inter-task reviews.

**Critical field-name note:** `KernelPrediction` ORM has `engine` (NOT `engine_name`) and `created_at` (NOT `prediction_timestamp`). The `EdgeDetectionSummary` dataclass uses `engine_name` / `prediction_timestamp` as its public attribute names, but the service must read `pred.engine` / `pred.created_at` when populating them.

---

## File Structure

### New files

| File | Responsibility |
|------|----------------|
| `backend/app/kernel/edge_store.py` | `EdgeStore` — persistence for `kernel_sport_edges` |
| `backend/app/kernel/edge_detector_service.py` | `EdgeDetectorService` + `EdgeResult`/`EdgeSource`/`EdgeDetectionSummary` value objects |
| `backend/app/api/routes/sport_edges.py` | 3 GET endpoints, 503-gated |
| `backend/scripts/sport_edge_cli.py` | CLI for manual edge computation and inspection |
| `backend/tests/test_edge_store.py` | Unit tests for `EdgeStore` (4 tests) |
| `backend/tests/test_edge_detector_service.py` | Unit tests for `EdgeDetectorService` (15 tests) |
| `backend/tests/test_sport_edge_routes.py` | API integration tests (8 tests) |
| `backend/tests/test_sport_edge_cli.py` | CLI tests (3 tests) |

### Modified files (minimal additive changes only)

| File | Change |
|------|--------|
| `backend/app/core/config.py` | Add `PHASE7_EDGE_DETECTOR_ENABLED` + `EDGE_DETECTION_INTERVAL_MIN` before `settings = Settings()` |
| `backend/app/kernel/kernel_db.py` | Add `KernelSportEdge` table class + `get_calibration()` function (additive) |
| `backend/app/kernel/sport_market_link_store.py` | Add `get_matches_with_verified_links()` method (additive) |
| `backend/app/api/router.py` | Register `sport_edges` router (additive) |
| `backend/app/core/scheduler.py` | Add `_job_detect_sport_edges` + register in `start_scheduler` (additive) |

---

## Task 1: Config flags + `KernelSportEdge` table + `get_calibration()` accessor

**Files:**
- Modify: `backend/app/core/config.py:1069-1072` (add 2 flags before `settings = Settings()`)
- Modify: `backend/app/kernel/kernel_db.py:223` (add `KernelSportEdge` class after `KernelMarketSnapshot`) and `kernel_db.py:320` (add `get_calibration()` after `get_latest_prediction`)
- Test: `backend/tests/test_edge_db.py` (new — 3 tests)

**Interfaces:**
- Consumes: `KernelBase` (line 21), `KernelCalibration` (line 107), `_env_bool` (config.py line 34)
- Produces: `KernelSportEdge` ORM class, `get_calibration(engine_name, competition) -> KernelCalibration | None`, config flags `PHASE7_EDGE_DETECTOR_ENABLED` + `EDGE_DETECTION_INTERVAL_MIN`

- [ ] **Step 1: Write failing tests for config flags and `get_calibration`**

Create `backend/tests/test_edge_db.py`:

```python
"""Tests for KernelSportEdge table and get_calibration accessor."""
import pytest

from app.kernel.kernel_db import (
    init_kernel_db,
    close_kernel_db,
    KernelCalibration,
    KernelSportEdge,
    get_calibration,
    get_kernel_session,
)
from datetime import datetime


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "edge_db_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def test_phased_edge_detector_enabled_defaults_off():
    """Global constraint 1: flag must default to OFF."""
    from app.core import config
    # Re-read settings (already loaded at module import, but we check the value)
    assert config.settings.PHASE7_EDGE_DETECTOR_ENABLED is False
    assert config.settings.EDGE_DETECTION_INTERVAL_MIN == 5


def test_get_calibration_returns_none_when_no_row(kernel_db):
    """Cold start: no calibration row -> None."""
    result = get_calibration("BasketballEngine", "nba")
    assert result is None


def test_get_calibration_returns_row_when_present(kernel_db):
    """Qualified: calibration row exists -> returned."""
    from datetime import timezone
    session = get_kernel_session()
    try:
        row = KernelCalibration(
            engine="BasketballEngine",
            competition="nba",
            slope=1.0,
            intercept=0.0,
            sample_count=20,
            avg_confidence=0.65,
            avg_accuracy=0.72,
            last_updated=datetime.now(timezone.utc),
        )
        session.add(row)
        session.commit()
    finally:
        session.close()

    result = get_calibration("BasketballEngine", "nba")
    assert result is not None
    assert result.avg_accuracy == pytest.approx(0.72)
    assert result.sample_count == 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_edge_db.py -v`
Expected: FAIL with `ImportError` (cannot import `KernelSportEdge`, `get_calibration`) and `AttributeError` (config flags missing).

- [ ] **Step 3: Add config flags to `backend/app/core/config.py`**

In `backend/app/core/config.py`, find the block ending at line 1069 (`MARKET_SNAPSHOT_INTERVAL_MIN` assignment). Insert after line 1069, before the blank line at 1070-1071 and `settings = Settings()` at 1072:

```python
    # Phase 7 Subproject B — Edge Detector (default OFF). Computes
    # model-vs-market divergence per outcome for sports matches. When false,
    # all /api/sport-edges/* endpoints return 503 and the scheduler job is
    # not registered.
    PHASE7_EDGE_DETECTOR_ENABLED: bool = _env_bool(
        "PHASE7_EDGE_DETECTOR_ENABLED", "false"
    )
    EDGE_DETECTION_INTERVAL_MIN: int = int(
        os.getenv("EDGE_DETECTION_INTERVAL_MIN", "5")
    )
```

- [ ] **Step 4: Add `KernelSportEdge` table class to `backend/app/kernel/kernel_db.py`**

In `backend/app/kernel/kernel_db.py`, after the `KernelMarketSnapshot` class (ends at line 223) and before `def init_kernel_db` (line 225), insert:

```python
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
    captured_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_kernel_sport_edges_match_outcome_captured", "match_id", "mapped_outcome", "captured_at"),
    )
```

Also, ensure `Boolean` and `Index` are imported. At line 26, the existing import is:
```python
from sqlalchemy import Column, String, Float, Integer, Text, DateTime, JSON, UniqueConstraint
```
Update it to:
```python
from sqlalchemy import Column, String, Float, Integer, Text, DateTime, JSON, UniqueConstraint, Boolean, Index
```

And ensure `datetime` is imported. At the top of the file (after `from __future__ import annotations`), check for `from datetime import datetime`. If not present, add:
```python
from datetime import datetime
```

- [ ] **Step 5: Add `get_calibration()` accessor to `backend/app/kernel/kernel_db.py`**

After `get_latest_prediction` (ends at line 321) and before `get_match_ids_with_predictions` (line 323), insert:

```python
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_edge_db.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Run regression — Subproject A tests must still pass**

Run: `cd backend && python -m pytest tests/test_sport_market_routes.py tests/test_sport_market_bridge_service.py -v`
Expected: PASS (no regressions — changes are additive).

- [ ] **Step 8: Commit**

```bash
git add backend/app/core/config.py backend/app/kernel/kernel_db.py backend/tests/test_edge_db.py
git commit -m "feat(phase7-b): add KernelSportEdge table, get_calibration accessor, config flags"
```

---

## Task 2: `EdgeStore` (persistence)

**Files:**
- Create: `backend/app/kernel/edge_store.py`
- Test: `backend/tests/test_edge_store.py` (new — 4 tests)

**Interfaces:**
- Consumes: `KernelSportEdge` (Task 1), `get_kernel_session` (kernel_db.py:290)
- Produces: `EdgeStore` class with methods `append_edge`, `get_latest_edges`, `get_edge_history`, `get_top_discrepancies`

- [ ] **Step 1: Write failing tests for `EdgeStore`**

Create `backend/tests/test_edge_store.py`:

```python
"""Tests for EdgeStore persistence (kernel_sport_edges table)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.kernel.kernel_db import init_kernel_db, close_kernel_db
from app.kernel.edge_store import EdgeStore


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "edge_store_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def _utcnow():
    return datetime.now(timezone.utc)


def test_append_edge_and_get_latest(kernel_db):
    """Append 2 edges for same outcome at different times -> get_latest returns only newest."""
    store = EdgeStore()
    old_ts = _utcnow() - timedelta(hours=2)
    new_ts = _utcnow()
    store.append_edge(
        match_id="m1", mapped_outcome="home_win", model_prob=0.6, market_prob=0.55,
        raw_edge=0.05, trust=0.7, liquidity_factor=0.8, adjusted_edge=0.028,
        spread=None, sources_count=1, stale=False, captured_at=old_ts,
    )
    store.append_edge(
        match_id="m1", mapped_outcome="home_win", model_prob=0.65, market_prob=0.58,
        raw_edge=0.07, trust=0.72, liquidity_factor=0.85, adjusted_edge=0.0428,
        spread=None, sources_count=2, stale=False, captured_at=new_ts,
    )
    latest = store.get_latest_edges(match_id="m1")
    assert len(latest) == 1
    assert latest[0]["model_prob"] == pytest.approx(0.65)
    assert latest[0]["raw_edge"] == pytest.approx(0.07)


def test_get_latest_edges_multiple_outcomes(kernel_db):
    """3 outcomes -> returns 3 latest edges."""
    store = EdgeStore()
    ts = _utcnow()
    for outcome in ("home_win", "draw", "away_win"):
        store.append_edge(
            match_id="m1", mapped_outcome=outcome, model_prob=0.4, market_prob=0.35,
            raw_edge=0.05, trust=0.7, liquidity_factor=0.8, adjusted_edge=0.028,
            spread=None, sources_count=1, stale=False, captured_at=ts,
        )
    latest = store.get_latest_edges(match_id="m1")
    assert len(latest) == 3
    outcomes = {e["mapped_outcome"] for e in latest}
    assert outcomes == {"home_win", "draw", "away_win"}


def test_get_edge_history_filtered_by_outcome(kernel_db):
    """History with mapped_outcome filter returns only that outcome's series."""
    store = EdgeStore()
    ts = _utcnow()
    store.append_edge(
        match_id="m1", mapped_outcome="home_win", model_prob=0.6, market_prob=0.55,
        raw_edge=0.05, trust=0.7, liquidity_factor=0.8, adjusted_edge=0.028,
        spread=None, sources_count=1, stale=False, captured_at=ts,
    )
    store.append_edge(
        match_id="m1", mapped_outcome="away_win", model_prob=0.4, market_prob=0.45,
        raw_edge=-0.05, trust=0.7, liquidity_factor=0.8, adjusted_edge=-0.028,
        spread=None, sources_count=1, stale=False, captured_at=ts,
    )
    history = store.get_edge_history(match_id="m1", mapped_outcome="home_win")
    assert len(history) == 1
    assert history[0]["mapped_outcome"] == "home_win"
    # Unfiltered history returns all
    all_history = store.get_edge_history(match_id="m1")
    assert len(all_history) == 2


def test_get_top_discrepancies_min_abs_edge_filter(kernel_db):
    """min_abs_edge filters out small edges; orders by |adjusted_edge| DESC."""
    store = EdgeStore()
    ts = _utcnow()
    # small edge
    store.append_edge(
        match_id="m1", mapped_outcome="home_win", model_prob=0.55, market_prob=0.54,
        raw_edge=0.01, trust=0.7, liquidity_factor=0.8, adjusted_edge=0.0056,
        spread=None, sources_count=1, stale=False, captured_at=ts,
    )
    # large edge
    store.append_edge(
        match_id="m2", mapped_outcome="home_win", model_prob=0.70, market_prob=0.50,
        raw_edge=0.20, trust=0.8, liquidity_factor=0.9, adjusted_edge=0.144,
        spread=None, sources_count=1, stale=False, captured_at=ts,
    )
    # min_abs_edge=0.05 filters out m1
    top = store.get_top_discrepancies(limit=20, min_abs_edge=0.05)
    assert len(top) == 1
    assert top[0]["match_id"] == "m2"
    assert top[0]["adjusted_edge"] == pytest.approx(0.144)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_edge_store.py -v`
Expected: FAIL with `ImportError: No module named 'app.kernel.edge_store'`.

- [ ] **Step 3: Create `backend/app/kernel/edge_store.py`**

```python
"""Persistence for kernel_sport_edges table (append-only time-series).

Each detect_edges() call appends one row per outcome. Read methods support
latest-per-outcome and full history queries. Mirrors the
sport_market_link_store / market_snapshot_store pattern.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, desc

from app.kernel.kernel_db import (
    KernelSportEdge,
    get_kernel_session,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(row: KernelSportEdge) -> dict[str, Any]:
    return {
        "id": row.id,
        "match_id": row.match_id,
        "mapped_outcome": row.mapped_outcome,
        "model_prob": row.model_prob,
        "market_prob": row.market_prob,
        "raw_edge": row.raw_edge,
        "trust": row.trust,
        "liquidity_factor": row.liquidity_factor,
        "adjusted_edge": row.adjusted_edge,
        "spread": row.spread,
        "sources_count": row.sources_count,
        "stale": bool(row.stale),
        "captured_at": row.captured_at,
    }


class EdgeStore:
    """Append-only persistence for edge snapshots.

    Writes one row per (match_id, mapped_outcome, captured_at). Reads support
    latest-per-outcome, full history, and top-discrepancy queries.
    """

    def append_edge(
        self,
        *,
        match_id: str,
        mapped_outcome: str,
        model_prob: float,
        market_prob: float,
        raw_edge: float,
        trust: float,
        liquidity_factor: float,
        adjusted_edge: float,
        spread: float | None,
        sources_count: int,
        stale: bool,
        captured_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Append one edge snapshot row. Returns the inserted row as dict."""
        when = captured_at or _utcnow()
        session = get_kernel_session()
        try:
            row = KernelSportEdge(
                match_id=match_id,
                mapped_outcome=mapped_outcome,
                model_prob=model_prob,
                market_prob=market_prob,
                raw_edge=raw_edge,
                trust=trust,
                liquidity_factor=liquidity_factor,
                adjusted_edge=adjusted_edge,
                spread=spread,
                sources_count=sources_count,
                stale=1 if stale else 0,
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

    def get_latest_edges(self, match_id: str) -> list[dict[str, Any]]:
        """Latest edge per mapped_outcome for a match.

        Uses a subquery to find max(captured_at) per (match_id, mapped_outcome),
        then joins back to get the full row.
        """
        session = get_kernel_session()
        try:
            subq = (
                session.query(
                    KernelSportEdge.mapped_outcome,
                    func.max(KernelSportEdge.captured_at).label("max_ts"),
                )
                .filter(KernelSportEdge.match_id == match_id)
                .group_by(KernelSportEdge.mapped_outcome)
                .subquery()
            )
            rows = (
                session.query(KernelSportEdge)
                .join(
                    subq,
                    (KernelSportEdge.mapped_outcome == subq.c.mapped_outcome)
                    & (KernelSportEdge.captured_at == subq.c.max_ts),
                )
                .filter(KernelSportEdge.match_id == match_id)
                .all()
            )
            return [_row_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def get_edge_history(
        self, match_id: str, mapped_outcome: str | None = None
    ) -> list[dict[str, Any]]:
        """Full time-series, optionally filtered by outcome. Ordered by captured_at ASC."""
        session = get_kernel_session()
        try:
            q = session.query(KernelSportEdge).filter(
                KernelSportEdge.match_id == match_id
            )
            if mapped_outcome is not None:
                q = q.filter(KernelSportEdge.mapped_outcome == mapped_outcome)
            rows = q.order_by(KernelSportEdge.captured_at.asc()).all()
            return [_row_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def get_top_discrepancies(
        self, limit: int = 20, min_abs_edge: float = 0.0
    ) -> list[dict[str, Any]]:
        """Top matches by |adjusted_edge| (latest snapshot per match+outcome).

        Ordered by |adjusted_edge| DESC. Filters out edges where
        |adjusted_edge| < min_abs_edge.
        """
        session = get_kernel_session()
        try:
            # Subquery: latest edge per (match_id, mapped_outcome)
            subq = (
                session.query(
                    KernelSportEdge.match_id,
                    KernelSportEdge.mapped_outcome,
                    func.max(KernelSportEdge.captured_at).label("max_ts"),
                )
                .group_by(
                    KernelSportEdge.match_id, KernelSportEdge.mapped_outcome
                )
                .subquery()
            )
            rows = (
                session.query(KernelSportEdge)
                .join(
                    subq,
                    (KernelSportEdge.match_id == subq.c.match_id)
                    & (KernelSportEdge.mapped_outcome == subq.c.mapped_outcome)
                    & (KernelSportEdge.captured_at == subq.c.max_ts),
                )
                .filter(
                    func.abs(KernelSportEdge.adjusted_edge) >= min_abs_edge
                )
                .order_by(desc(func.abs(KernelSportEdge.adjusted_edge)))
                .limit(limit)
                .all()
            )
            return [_row_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_edge_store.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/kernel/edge_store.py backend/tests/test_edge_store.py
git commit -m "feat(phase7-b): add EdgeStore persistence with append/latest/history/discrepancies"
```

---

## Task 3: `SportMarketLinkStore.get_matches_with_verified_links()` (additive)

**Files:**
- Modify: `backend/app/kernel/sport_market_link_store.py:215` (append new method after `list_links`)
- Test: `backend/tests/test_link_store_matches.py` (new — 2 tests)

**Interfaces:**
- Consumes: `KernelSportMarketLink` (kernel_db.py:185), `get_kernel_session`
- Produces: `SportMarketLinkStore.get_matches_with_verified_links() -> list[str]`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_link_store_matches.py`:

```python
"""Tests for SportMarketLinkStore.get_matches_with_verified_links (additive)."""
import pytest

from app.kernel.kernel_db import init_kernel_db, close_kernel_db
from app.kernel.sport_market_link_store import SportMarketLinkStore


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "link_matches_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def _seed(store, match_id, contract_id, verified):
    return store.upsert_link(
        match_id=match_id, contract_id=contract_id, source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=verified, market_question="q", implied_prob=0.6,
    )


def test_get_matches_with_verified_links_returns_distinct_match_ids(kernel_db):
    """Returns distinct match_ids that have at least one verified=True link."""
    store = SportMarketLinkStore()
    _seed(store, "m1", "c1", verified=True)
    _seed(store, "m1", "c2", verified=True)  # second verified link for m1
    _seed(store, "m2", "c3", verified=False)  # m2 has only unverified
    _seed(store, "m3", "c4", verified=True)
    matches = store.get_matches_with_verified_links()
    assert sorted(matches) == ["m1", "m3"]


def test_get_matches_with_verified_links_empty_when_none_verified(kernel_db):
    """Returns empty list when no verified links exist."""
    store = SportMarketLinkStore()
    _seed(store, "m1", "c1", verified=False)
    matches = store.get_matches_with_verified_links()
    assert matches == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_link_store_matches.py -v`
Expected: FAIL with `AttributeError: 'SportMarketLinkStore' object has no attribute 'get_matches_with_verified_links'`.

- [ ] **Step 3: Add `get_matches_with_verified_links` method to `backend/app/kernel/sport_market_link_store.py`**

Append after the `list_links` method (ends at line 215):

```python
    def get_matches_with_verified_links(self) -> list[str]:
        """Return distinct match_ids that have at least one verified=True link.

        Used by the edge detector scheduler job to enumerate matches that
        need edge recomputation. Additive — does not modify existing methods.
        """
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelSportMarketLink.match_id)
                .filter(KernelSportMarketLink.verified == 1)
                .distinct()
                .all()
            )
            return [r[0] for r in rows]
        except Exception:
            return []
        finally:
            session.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_link_store_matches.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run regression — Subproject A link store tests must still pass**

Run: `cd backend && python -m pytest tests/test_sport_market_routes.py tests/test_sport_market_bridge_service.py -v`
Expected: PASS (no regressions — additive change).

- [ ] **Step 6: Commit**

```bash
git add backend/app/kernel/sport_market_link_store.py backend/tests/test_link_store_matches.py
git commit -m "feat(phase7-b): add get_matches_with_verified_links to SportMarketLinkStore"
```

---

## Task 4: `EdgeDetectorService` (domain service + value objects)

**Files:**
- Create: `backend/app/kernel/edge_detector_service.py`
- Test: `backend/tests/test_edge_detector_service.py` (new — 15 tests)

**Interfaces:**
- Consumes: `get_latest_prediction(match_id)` (kernel_db.py:310), `get_calibration(engine_name, competition)` (Task 1), `SportMarketLinkStore.get_verified_links(match_id=)` (Subproject A), `MarketSnapshotStore.get_latest_snapshot(link_id=)` (Subproject A), `EdgeStore` (Task 2), config flags `DIAGNOSIS_DORMANT_TRUST` / `DIAGNOSIS_TRUST_FLOOR` / `DIAGNOSIS_LIQUIDITY_FLOOR` / `CALIBRATION_FEEDBACK_MIN_SAMPLES` / `EDGE_STALE_HOURS`
- Produces: `EdgeDetectorService` with `detect_edges`, `get_latest_edges`, `get_edge_history`, `get_top_discrepancies`; value objects `EdgeSource`, `EdgeResult`, `EdgeDetectionSummary`

**Critical field-name mapping:** `KernelPrediction.engine` → `EdgeDetectionSummary.engine_name`; `KernelPrediction.created_at` → `EdgeDetectionSummary.prediction_timestamp`.

- [ ] **Step 1: Write failing tests (part 1 — skip cases + single source)**

Create `backend/tests/test_edge_detector_service.py`:

```python
"""Tests for EdgeDetectorService (domain service).

Covers: skip cases, single/multi source aggregation, trust computation,
liquidity factor ramp, staleness, binary-sport skip, persistence, top
discrepancies ordering.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.kernel.kernel_db import (
    init_kernel_db,
    close_kernel_db,
    KernelPrediction,
    KernelCalibration,
    get_kernel_session,
)
from app.kernel.sport_market_link_store import SportMarketLinkStore
from app.kernel.market_snapshot_store import MarketSnapshotStore
from app.kernel.edge_detector_service import (
    EdgeDetectorService,
    EdgeResult,
    EdgeDetectionSummary,
)


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "edge_service_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


@pytest.fixture
def service(kernel_db):
    return EdgeDetectorService()


def _utcnow():
    return datetime.now(timezone.utc)


def _seed_prediction(
    match_id="m1",
    engine="BasketballEngine",
    competition="nba",
    probs=None,
    ts=None,
):
    """Insert a KernelPrediction row."""
    if probs is None:
        probs = {"home_win": 0.6, "away_win": 0.4}
    if ts is None:
        ts = _utcnow()
    session = get_kernel_session()
    try:
        row = KernelPrediction(
            match_id=match_id,
            sport="basketball",
            competition=competition,
            season="2025-26",
            engine=engine,
            predicted_scores={},
            outcome_probabilities=probs,
            confidence=0.7,
            feature_version="nba-1.0",
            explanation={},
            created_at=ts,
            updated_at=ts,
        )
        session.add(row)
        session.commit()
    finally:
        session.close()


def _seed_link_and_snapshot(
    match_id="m1",
    contract_id="c1",
    source="polymarket",
    mapped_outcome="home_win",
    implied_prob=0.55,
    liquidity=None,
    verified=True,
    snap_ts=None,
):
    """Insert a verified link + its latest snapshot. Returns the link dict."""
    store = SportMarketLinkStore()
    link = store.upsert_link(
        match_id=match_id, contract_id=contract_id, source=source,
        outcome_label="YES", mapped_outcome=mapped_outcome, link_method="rule",
        link_confidence=0.95, verified=verified, market_question="q",
        implied_prob=implied_prob,
    )
    if verified and snap_ts is not None:
        snap_store = MarketSnapshotStore()
        snap_store.append_snapshot(
            link_id=link["id"], implied_prob=implied_prob, price=implied_prob,
            liquidity=liquidity, volume=None, captured_at=snap_ts,
        )
    elif verified:
        # Default: snapshot with same implied_prob, no liquidity
        snap_store = MarketSnapshotStore()
        snap_store.append_snapshot(
            link_id=link["id"], implied_prob=implied_prob, price=implied_prob,
            liquidity=liquidity, volume=None, captured_at=_utcnow(),
        )
    return link


def _seed_calibration(engine="BasketballEngine", competition="nba",
                      avg_accuracy=0.72, sample_count=20):
    session = get_kernel_session()
    try:
        row = KernelCalibration(
            engine=engine, competition=competition, slope=1.0, intercept=0.0,
            sample_count=sample_count, avg_confidence=0.65,
            avg_accuracy=avg_accuracy, last_updated=_utcnow(),
        )
        session.add(row)
        session.commit()
    finally:
        session.close()


def test_detect_edges_no_prediction_returns_skipped(service):
    """Match with no KernelPrediction -> skipped=True, skip_reason='no_prediction'."""
    _seed_link_and_snapshot(match_id="m1")
    result = service.detect_edges("m1")
    assert isinstance(result, EdgeDetectionSummary)
    assert result.skipped is True
    assert result.skip_reason == "no_prediction"
    assert result.outcomes == []


def test_detect_edges_no_verified_links_returns_skipped(service):
    """Match with prediction but no verified links -> skipped='no_verified_links'."""
    _seed_prediction(match_id="m1")
    # Unverified link only
    _seed_link_and_snapshot(match_id="m1", verified=False)
    result = service.detect_edges("m1")
    assert result.skipped is True
    assert result.skip_reason == "no_verified_links"
    assert result.outcomes == []


def test_detect_edges_single_outcome_single_source(service):
    """One outcome, one link -> correct raw_edge, market_prob, adjusted_edge."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.65, "away_win": 0.35})
    _seed_link_and_snapshot(match_id="m1", mapped_outcome="home_win", implied_prob=0.58)
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    result = service.detect_edges("m1")
    assert result.skipped is False
    # Only home_win has a verified link; away_win is skipped
    assert len(result.outcomes) == 1
    edge = result.outcomes[0]
    assert edge.mapped_outcome == "home_win"
    assert edge.model_prob == pytest.approx(0.65)
    assert edge.market_prob == pytest.approx(0.58)
    assert edge.raw_edge == pytest.approx(0.07)
    assert edge.trust == pytest.approx(0.72)
    # liquidity=None -> liquidity_factor=1.0
    assert edge.liquidity_factor == pytest.approx(1.0)
    # adjusted_edge = 0.07 * 0.72 * 1.0
    assert edge.adjusted_edge == pytest.approx(0.0504, abs=1e-4)
    assert edge.sources_count == 1
    assert edge.spread is None
    assert edge.stale is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_edge_detector_service.py::test_detect_edges_no_prediction_returns_skipped tests/test_edge_detector_service.py::test_detect_edges_no_verified_links_returns_skipped tests/test_edge_detector_service.py::test_detect_edges_single_outcome_single_source -v`
Expected: FAIL with `ImportError: No module named 'app.kernel.edge_detector_service'`.

- [ ] **Step 3: Create `backend/app/kernel/edge_detector_service.py` (initial version — skip cases + single source)**

```python
"""EdgeDetectorService — computes model-vs-market edge for sports matches.

Read-only on the Prediction Kernel (uses get_latest_prediction, never
PredictionKernel.predict). Consumes verified market links (Subproject A)
and persisted kernel predictions. Produces per-outcome edge snapshots
persisted to kernel_sport_edges via EdgeStore.

Trust from KernelCalibration.avg_accuracy (sports calibration, not event
segment_skill). Liquidity-weighted multi-source market probability
aggregation. Staleness based on EDGE_STALE_HOURS.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core import config
from app.kernel.edge_store import EdgeStore
from app.kernel.kernel_db import get_calibration, get_latest_prediction
from app.kernel.market_snapshot_store import MarketSnapshotStore
from app.kernel.sport_market_link_store import SportMarketLinkStore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class EdgeSource:
    """Contribution from one verified link to an aggregated edge."""
    link_id: int
    source: str
    contract_id: str
    implied_prob: float
    liquidity: float | None
    volume: float | None
    weight: float
    link_confidence: float


@dataclass(frozen=True)
class EdgeResult:
    """Per-outcome edge computation result."""
    match_id: str
    mapped_outcome: str
    model_prob: float
    market_prob: float
    raw_edge: float
    trust: float
    liquidity_factor: float
    adjusted_edge: float
    spread: float | None
    sources: list[EdgeSource]
    sources_count: int
    stale: bool
    captured_at: datetime


@dataclass(frozen=True)
class EdgeDetectionSummary:
    """Result of detect_edges(match_id) — all outcomes for one match."""
    match_id: str
    outcomes: list[EdgeResult]
    engine_name: str | None
    competition: str | None
    prediction_timestamp: datetime | None
    skipped: bool
    skip_reason: str | None


class EdgeDetectorService:
    """Computes model-vs-market edge for sports matches.

    Read-only on the kernel. Produces per-outcome edge snapshots persisted
    to kernel_sport_edges. Trust from KernelCalibration. Liquidity-weighted
    multi-source aggregation.
    """

    def __init__(self) -> None:
        self._link_store = SportMarketLinkStore()
        self._snap_store = MarketSnapshotStore()
        self._edge_store = EdgeStore()

    def detect_edges(self, match_id: str) -> EdgeDetectionSummary:
        """Compute and persist edge snapshots for all outcomes of a match.

        Steps:
        1. Fetch KernelPrediction. If None -> skipped (no_prediction).
        2. Fetch verified links. If empty -> skipped (no_verified_links).
        3. Fetch latest snapshot for each link.
        4. Group links by mapped_outcome.
        5. For each outcome in prediction.outcome_probabilities:
           a. Aggregate market_prob (liquidity-weighted average).
           b. raw_edge = model_prob - market_prob.
           c. trust = _compute_trust(engine, competition).
           d. liquidity_factor = _compute_liquidity_factor(links).
           e. adjusted_edge = raw_edge * trust * liquidity_factor.
           f. stale = _is_stale(prediction_ts, snapshot timestamps).
           g. Build EdgeResult, persist to kernel_sport_edges.
        6. Return EdgeDetectionSummary.
        """
        pred = get_latest_prediction(match_id)
        if pred is None:
            return EdgeDetectionSummary(
                match_id=match_id, outcomes=[],
                engine_name=None, competition=None,
                prediction_timestamp=None,
                skipped=True, skip_reason="no_prediction",
            )

        verified_links = self._link_store.get_verified_links(match_id=match_id)
        if not verified_links:
            return EdgeDetectionSummary(
                match_id=match_id, outcomes=[],
                engine_name=pred.engine, competition=pred.competition,
                prediction_timestamp=pred.created_at,
                skipped=True, skip_reason="no_verified_links",
            )

        # Fetch latest snapshot for each link
        links_with_snaps: list[tuple[dict, dict | None]] = []
        for link in verified_links:
            snap = self._snap_store.get_latest_snapshot(link_id=link["id"])
            links_with_snaps.append((link, snap))

        # Group by mapped_outcome
        by_outcome: dict[str, list[tuple[dict, dict | None]]] = {}
        for link, snap in links_with_snaps:
            outcome = link["mapped_outcome"]
            by_outcome.setdefault(outcome, []).append((link, snap))

        # Compute trust once per match (engine + competition are match-level)
        trust = self._compute_trust(pred.engine, pred.competition)

        outcomes: list[EdgeResult] = []
        now = _utcnow()
        for outcome, model_prob in pred.outcome_probabilities.items():
            group = by_outcome.get(outcome)
            if not group:
                continue  # no market data for this outcome — skip

            market_prob, spread, sources = self._aggregate_market_prob(group)
            liquidity_factor = self._compute_liquidity_factor(group)
            raw_edge = model_prob - market_prob
            adjusted_edge = raw_edge * trust * liquidity_factor
            snap_timestamps = [
                snap["captured_at"] if snap else None
                for _, snap in group
            ]
            stale = self._is_stale(pred.created_at, snap_timestamps)

            edge_result = EdgeResult(
                match_id=match_id,
                mapped_outcome=outcome,
                model_prob=model_prob,
                market_prob=market_prob,
                raw_edge=raw_edge,
                trust=trust,
                liquidity_factor=liquidity_factor,
                adjusted_edge=adjusted_edge,
                spread=spread,
                sources=sources,
                sources_count=len(sources),
                stale=stale,
                captured_at=now,
            )
            outcomes.append(edge_result)

            # Persist to kernel_sport_edges
            self._edge_store.append_edge(
                match_id=match_id,
                mapped_outcome=outcome,
                model_prob=model_prob,
                market_prob=market_prob,
                raw_edge=raw_edge,
                trust=trust,
                liquidity_factor=liquidity_factor,
                adjusted_edge=adjusted_edge,
                spread=spread,
                sources_count=len(sources),
                stale=stale,
                captured_at=now,
            )

        return EdgeDetectionSummary(
            match_id=match_id,
            outcomes=outcomes,
            engine_name=pred.engine,
            competition=pred.competition,
            prediction_timestamp=pred.created_at,
            skipped=False,
            skip_reason=None,
        )

    def get_latest_edges(self, match_id: str) -> list[EdgeResult]:
        """Read the most recent edge snapshot per outcome for a match."""
        rows = self._edge_store.get_latest_edges(match_id)
        return [self._row_to_edge_result(r) for r in rows]

    def get_edge_history(
        self, match_id: str, mapped_outcome: str | None = None
    ) -> list[EdgeResult]:
        """Read full edge time-series for a match, optionally filtered by outcome."""
        rows = self._edge_store.get_edge_history(match_id, mapped_outcome)
        return [self._row_to_edge_result(r) for r in rows]

    def get_top_discrepancies(
        self, limit: int = 20, min_abs_edge: float = 0.0
    ) -> list[EdgeResult]:
        """Read matches with the largest |adjusted_edge| across all matches."""
        rows = self._edge_store.get_top_discrepancies(limit=limit, min_abs_edge=min_abs_edge)
        return [self._row_to_edge_result(r) for r in rows]

    def _row_to_edge_result(self, row: dict) -> EdgeResult:
        """Convert a persisted edge row dict back to EdgeResult.

        Note: sources list is not persisted (it's per-link metadata); we
        return an empty list here since the persisted row only stores
        sources_count. This is acceptable for read endpoints that don't
        need per-link breakdown.
        """
        return EdgeResult(
            match_id=row["match_id"],
            mapped_outcome=row["mapped_outcome"],
            model_prob=row["model_prob"],
            market_prob=row["market_prob"],
            raw_edge=row["raw_edge"],
            trust=row["trust"],
            liquidity_factor=row["liquidity_factor"],
            adjusted_edge=row["adjusted_edge"],
            spread=row["spread"],
            sources=[],  # not persisted per-row
            sources_count=row["sources_count"],
            stale=row["stale"],
            captured_at=row["captured_at"],
        )

    def _aggregate_market_prob(
        self, links_with_snaps: list[tuple[dict, dict | None]]
    ) -> tuple[float, float | None, list[EdgeSource]]:
        """Returns (market_prob, spread, sources).

        market_prob = Σ(implied_prob × weight) / Σ(weight)
        where weight = max(latest_snapshot.liquidity, 1) if liquidity present else 1

        spread is always None (known limitation: requires both YES and NO
        prices on separate links).
        """
        total_weight = 0.0
        weighted_sum = 0.0
        sources: list[EdgeSource] = []

        for link, snap in links_with_snaps:
            implied = snap["implied_prob"] if snap else link["implied_prob"]
            liquidity = snap["liquidity"] if snap else None
            volume = snap["volume"] if snap else None
            if liquidity is not None and liquidity > 0:
                weight = max(liquidity, 1.0)
            else:
                weight = 1.0

            weighted_sum += implied * weight
            total_weight += weight

            sources.append(EdgeSource(
                link_id=link["id"],
                source=link["source"],
                contract_id=link["contract_id"],
                implied_prob=implied,
                liquidity=liquidity,
                volume=volume,
                weight=weight,
                link_confidence=link["link_confidence"],
            ))

        market_prob = weighted_sum / total_weight if total_weight > 0 else 0.0
        # Spread is None — known limitation (requires both YES and NO prices)
        spread = None
        return market_prob, spread, sources

    def _compute_trust(self, engine_name: str, competition: str) -> float:
        """Trust from KernelCalibration (sports), mirroring
        diagnosis_service.calibration_trust.

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

    def _compute_liquidity_factor(
        self, links_with_snaps: list[tuple[dict, dict | None]]
    ) -> float:
        """Liquidity factor from the max liquidity among all links.

        Uses the latest snapshot's liquidity. If all links have None
        liquidity (traditional sportsbook), returns 1.0 (no penalty).
        Mirrors diagnosis_service.liquidity_factor but uses max (most
        liquid source dominates).
        """
        liquidities = [
            snap["liquidity"]
            for _, snap in links_with_snaps
            if snap and snap.get("liquidity") is not None and snap["liquidity"] > 0
        ]
        if not liquidities:
            return 1.0

        max_liq = max(liquidities)
        floor = config.settings.DIAGNOSIS_LIQUIDITY_FLOOR
        if floor <= 0:
            return 1.0
        return min(max_liq / floor, 1.0)

    def _is_stale(
        self,
        prediction_ts: datetime | None,
        snapshot_timestamps: list[datetime | None],
    ) -> bool:
        """True if prediction is stale OR ALL market snapshots are stale.

        A fresh snapshot (captured_at within EDGE_STALE_HOURS) is enough
        to mark the edge as not stale — even if other snapshots are old.
        Uses the NEWEST snapshot (max timestamp).
        """
        threshold = config.settings.EDGE_STALE_HOURS  # 72.0 hours
        now = _utcnow()

        if prediction_ts is not None:
            # Handle both tz-aware and tz-naive datetimes
            pred_age = (now - prediction_ts).total_seconds() / 3600
            if pred_age > threshold:
                return True

        valid_snaps = [ts for ts in snapshot_timestamps if ts is not None]
        if not valid_snaps:
            return True  # no snapshots at all — definitely stale

        # Use the NEWEST snapshot (max timestamp). If the newest is still
        # old, then ALL snapshots are old -> stale. One fresh is enough.
        newest_snap = max(valid_snaps)
        snap_age = (now - newest_snap).total_seconds() / 3600
        return snap_age > threshold
```

- [ ] **Step 4: Run the 3 initial tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_edge_detector_service.py::test_detect_edges_no_prediction_returns_skipped tests/test_edge_detector_service.py::test_detect_edges_no_verified_links_returns_skipped tests/test_edge_detector_service.py::test_detect_edges_single_outcome_single_source -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Add remaining 12 tests to `backend/tests/test_edge_detector_service.py`**

Append to the test file (after `test_detect_edges_single_outcome_single_source`):

```python
def test_detect_edges_multi_source_liquidity_weighted(service):
    """Two links for same outcome with different liquidities -> weighted avg."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.65})
    ts = _utcnow()
    # Link 1: implied=0.55, liquidity=1000 -> weight=1000
    _seed_link_and_snapshot(
        match_id="m1", contract_id="c1", mapped_outcome="home_win",
        implied_prob=0.55, liquidity=1000.0, snap_ts=ts,
    )
    # Link 2: implied=0.60, liquidity=3000 -> weight=3000
    _seed_link_and_snapshot(
        match_id="m1", contract_id="c2", mapped_outcome="home_win",
        implied_prob=0.60, liquidity=3000.0, snap_ts=ts,
    )
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    result = service.detect_edges("m1")
    edge = result.outcomes[0]
    # market_prob = (0.55*1000 + 0.60*3000) / (1000+3000) = (550+1800)/4000 = 0.5875
    assert edge.market_prob == pytest.approx(0.5875, abs=1e-4)
    assert edge.sources_count == 2
    # liquidity_factor = min(max(1000,3000)/5000, 1.0) = 3000/5000 = 0.6
    assert edge.liquidity_factor == pytest.approx(0.6)


def test_detect_edges_traditional_odds_no_liquidity_uses_weight_1(service):
    """liquidity=None -> weight=1, liquidity_factor=1.0."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.6})
    _seed_link_and_snapshot(
        match_id="m1", contract_id="c1", source="the_odds_api",
        mapped_outcome="home_win", implied_prob=0.58, liquidity=None,
    )
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    result = service.detect_edges("m1")
    edge = result.outcomes[0]
    assert edge.market_prob == pytest.approx(0.58)
    assert edge.liquidity_factor == pytest.approx(1.0)


def test_detect_edges_trust_cold_start(service):
    """No KernelCalibration row -> trust=0.5 (DIAGNOSIS_DORMANT_TRUST)."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.6})
    _seed_link_and_snapshot(match_id="m1", mapped_outcome="home_win", implied_prob=0.55)
    # No _seed_calibration call -> cold start
    result = service.detect_edges("m1")
    edge = result.outcomes[0]
    assert edge.trust == pytest.approx(0.5)


def test_detect_edges_trust_dormant(service):
    """sample_count < 8 -> trust=0.5 (dormant)."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.6})
    _seed_link_and_snapshot(match_id="m1", mapped_outcome="home_win", implied_prob=0.55)
    _seed_calibration(avg_accuracy=0.72, sample_count=5)  # < 8 -> dormant
    result = service.detect_edges("m1")
    edge = result.outcomes[0]
    assert edge.trust == pytest.approx(0.5)


def test_detect_edges_trust_qualified(service):
    """sample_count >= 8 -> trust=clamp(avg_accuracy, 0.1, 1.0)."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.6})
    _seed_link_and_snapshot(match_id="m1", mapped_outcome="home_win", implied_prob=0.55)
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    result = service.detect_edges("m1")
    edge = result.outcomes[0]
    assert edge.trust == pytest.approx(0.72)


def test_detect_edges_trust_qualified_floor(service):
    """avg_accuracy below floor -> trust=floor (0.1)."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.6})
    _seed_link_and_snapshot(match_id="m1", mapped_outcome="home_win", implied_prob=0.55)
    _seed_calibration(avg_accuracy=0.05, sample_count=20)
    result = service.detect_edges("m1")
    edge = result.outcomes[0]
    assert edge.trust == pytest.approx(0.1)


def test_detect_edges_liquidity_factor_ramp(service):
    """liquidity=2500 -> factor=0.5; 5000 -> 1.0; 10000 -> 1.0 (clamped).

    Uses distinct match_ids in the same fixture DB (no DB reset needed).
    """
    # 2500 -> 0.5 (2500/5000)
    _seed_prediction(match_id="m1", probs={"home_win": 0.6})
    _seed_link_and_snapshot(
        match_id="m1", mapped_outcome="home_win", implied_prob=0.55, liquidity=2500.0,
    )
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    result = service.detect_edges("m1")
    assert result.outcomes[0].liquidity_factor == pytest.approx(0.5)

    # 5000 -> 1.0 (5000/5000, clamped to 1.0)
    _seed_prediction(match_id="m2", probs={"home_win": 0.6})
    _seed_link_and_snapshot(
        match_id="m2", mapped_outcome="home_win", implied_prob=0.55, liquidity=5000.0,
    )
    result2 = service.detect_edges("m2")
    assert result2.outcomes[0].liquidity_factor == pytest.approx(1.0)

    # 10000 -> 1.0 (clamped, > floor)
    _seed_prediction(match_id="m3", probs={"home_win": 0.6})
    _seed_link_and_snapshot(
        match_id="m3", mapped_outcome="home_win", implied_prob=0.55, liquidity=10000.0,
    )
    result3 = service.detect_edges("m3")
    assert result3.outcomes[0].liquidity_factor == pytest.approx(1.0)


def test_detect_edges_stale_when_prediction_old(service):
    """prediction_timestamp 100h old -> stale=True."""
    old_ts = _utcnow() - timedelta(hours=100)
    _seed_prediction(match_id="m1", probs={"home_win": 0.6}, ts=old_ts)
    _seed_link_and_snapshot(match_id="m1", mapped_outcome="home_win", implied_prob=0.55)
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    result = service.detect_edges("m1")
    assert result.outcomes[0].stale is True


def test_detect_edges_stale_when_all_snapshots_old(service):
    """All snapshots 100h old -> stale=True (prediction is fresh)."""
    fresh_ts = _utcnow()
    _seed_prediction(match_id="m1", probs={"home_win": 0.6}, ts=fresh_ts)
    old_snap_ts = _utcnow() - timedelta(hours=100)
    _seed_link_and_snapshot(
        match_id="m1", mapped_outcome="home_win", implied_prob=0.55, snap_ts=old_snap_ts,
    )
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    result = service.detect_edges("m1")
    assert result.outcomes[0].stale is True


def test_detect_edges_not_stale_when_one_snapshot_fresh(service):
    """One snapshot 1h old, another 100h old -> stale=False (newest is fresh)."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.6})
    # Link 1: old snapshot
    old_ts = _utcnow() - timedelta(hours=100)
    _seed_link_and_snapshot(
        match_id="m1", contract_id="c1", mapped_outcome="home_win",
        implied_prob=0.55, snap_ts=old_ts,
    )
    # Link 2: fresh snapshot
    fresh_ts = _utcnow() - timedelta(hours=1)
    _seed_link_and_snapshot(
        match_id="m1", contract_id="c2", mapped_outcome="home_win",
        implied_prob=0.57, snap_ts=fresh_ts,
    )
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    result = service.detect_edges("m1")
    assert result.outcomes[0].stale is False


def test_detect_edges_binary_sport_skips_missing_outcome(service):
    """outcome_probabilities has away_win but no verified link for it -> skipped."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.6, "away_win": 0.4})
    # Only home_win has a link
    _seed_link_and_snapshot(match_id="m1", mapped_outcome="home_win", implied_prob=0.55)
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    result = service.detect_edges("m1")
    # Only home_win edge computed; away_win skipped
    assert len(result.outcomes) == 1
    assert result.outcomes[0].mapped_outcome == "home_win"


def test_detect_edges_persists_to_edge_store(service):
    """After detect_edges, get_latest_edges returns the computed edges."""
    _seed_prediction(match_id="m1", probs={"home_win": 0.65})
    _seed_link_and_snapshot(match_id="m1", mapped_outcome="home_win", implied_prob=0.58)
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    service.detect_edges("m1")
    latest = service.get_latest_edges("m1")
    assert len(latest) == 1
    assert latest[0].model_prob == pytest.approx(0.65)
    assert latest[0].raw_edge == pytest.approx(0.07)


def test_get_top_discrepancies_orders_by_abs_adjusted_edge_desc(service):
    """Multiple matches -> ordered by |adjusted_edge| DESC."""
    # m1: small edge
    _seed_prediction(match_id="m1", probs={"home_win": 0.55})
    _seed_link_and_snapshot(match_id="m1", mapped_outcome="home_win", implied_prob=0.54)
    _seed_calibration(avg_accuracy=0.72, sample_count=20)
    service.detect_edges("m1")

    # m2: large edge
    _seed_prediction(match_id="m2", probs={"home_win": 0.70})
    _seed_link_and_snapshot(match_id="m2", mapped_outcome="home_win", implied_prob=0.50)
    service.detect_edges("m2")

    top = service.get_top_discrepancies(limit=20)
    assert len(top) == 2
    # m2 has larger |adjusted_edge|
    assert abs(top[0].adjusted_edge) >= abs(top[1].adjusted_edge)
    assert top[0].match_id == "m2"
```

- [ ] **Step 6: Run all 15 tests**

Run: `cd backend && python -m pytest tests/test_edge_detector_service.py -v`
Expected: PASS (15 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/kernel/edge_detector_service.py backend/tests/test_edge_detector_service.py
git commit -m "feat(phase7-b): add EdgeDetectorService with trust/liquidity/staleness computation"
```

---

## Task 5: API endpoints + router registration

**Files:**
- Create: `backend/app/api/routes/sport_edges.py`
- Modify: `backend/app/api/router.py` (register new router)
- Test: `backend/tests/test_sport_edge_routes.py` (new — 8 tests)

**Interfaces:**
- Consumes: `EdgeDetectorService` (Task 4), config flag `PHASE7_EDGE_DETECTOR_ENABLED`
- Produces: 3 GET endpoints: `/api/sport-edges/{match_id}/latest`, `/api/sport-edges/{match_id}/history`, `/api/sport-edges/discrepancies`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_sport_edge_routes.py`:

```python
"""Tests for sport edge API routes.

All endpoints gated by PHASE7_EDGE_DETECTOR_ENABLED (503 when false).
All are GET (read-only) — no require_write_key auth.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config
from app.kernel.kernel_db import init_kernel_db, close_kernel_db


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "edge_routes_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


@pytest.fixture
def client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_EDGE_DETECTOR_ENABLED", True)
    from app.api.routes import sport_edges
    app = FastAPI()
    app.include_router(sport_edges.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def disabled_client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_EDGE_DETECTOR_ENABLED", False)
    from app.api.routes import sport_edges
    app = FastAPI()
    app.include_router(sport_edges.router, prefix="/api")
    return TestClient(app)


def _seed_prediction_and_link(match_id="m1", probs=None, implied=0.55):
    """Helper: seed prediction + verified link + snapshot + calibration."""
    from datetime import datetime, timezone
    from app.kernel.kernel_db import KernelPrediction, KernelCalibration, get_kernel_session
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    if probs is None:
        probs = {"home_win": 0.65, "away_win": 0.35}
    now = datetime.now(timezone.utc)
    session = get_kernel_session()
    try:
        session.add(KernelPrediction(
            match_id=match_id, sport="basketball", competition="nba",
            season="2025-26", engine="BasketballEngine", predicted_scores={},
            outcome_probabilities=probs, confidence=0.7, feature_version="nba-1.0",
            explanation={}, created_at=now, updated_at=now,
        ))
        session.add(KernelCalibration(
            engine="BasketballEngine", competition="nba", slope=1.0, intercept=0.0,
            sample_count=20, avg_confidence=0.65, avg_accuracy=0.72, last_updated=now,
        ))
        session.commit()
    finally:
        session.close()
    store = SportMarketLinkStore()
    link = store.upsert_link(
        match_id=match_id, contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=True, market_question="q", implied_prob=implied,
    )
    snap_store = MarketSnapshotStore()
    snap_store.append_snapshot(
        link_id=link["id"], implied_prob=implied, price=implied,
        liquidity=None, volume=None, captured_at=now,
    )


def test_latest_returns_503_when_disabled(disabled_client):
    res = disabled_client.get("/api/sport-edges/m1/latest")
    assert res.status_code == 503


def test_latest_returns_edges(client):
    _seed_prediction_and_link(match_id="m1", implied=0.58)
    # Trigger edge computation
    from app.kernel.edge_detector_service import EdgeDetectorService
    EdgeDetectorService().detect_edges("m1")
    res = client.get("/api/sport-edges/m1/latest")
    assert res.status_code == 200
    data = res.json()
    assert data["match_id"] == "m1"
    assert data["skipped"] is False
    assert len(data["outcomes"]) == 1
    edge = data["outcomes"][0]
    assert edge["mapped_outcome"] == "home_win"
    assert edge["model_prob"] == pytest.approx(0.65)
    assert edge["market_prob"] == pytest.approx(0.58)
    assert edge["raw_edge"] == pytest.approx(0.07)


def test_latest_returns_skipped_summary(client):
    """Match with no prediction -> skipped=true."""
    res = client.get("/api/sport-edges/m1/latest")
    assert res.status_code == 200
    data = res.json()
    assert data["skipped"] is True
    assert data["skip_reason"] == "no_prediction"
    assert data["outcomes"] == []


def test_history_returns_timeseries(client):
    _seed_prediction_and_link(match_id="m1", implied=0.58)
    from app.kernel.edge_detector_service import EdgeDetectorService
    svc = EdgeDetectorService()
    # Compute twice to create 2 snapshots
    svc.detect_edges("m1")
    svc.detect_edges("m1")
    res = client.get("/api/sport-edges/m1/history")
    assert res.status_code == 200
    data = res.json()
    assert data["match_id"] == "m1"
    assert len(data["series"]) >= 1
    home_series = next(s for s in data["series"] if s["mapped_outcome"] == "home_win")
    assert len(home_series["snapshots"]) == 2


def test_history_filtered_by_outcome(client):
    _seed_prediction_and_link(match_id="m1", implied=0.58)
    from app.kernel.edge_detector_service import EdgeDetectorService
    EdgeDetectorService().detect_edges("m1")
    res = client.get("/api/sport-edges/m1/history", params={"mapped_outcome": "home_win"})
    assert res.status_code == 200
    data = res.json()
    assert len(data["series"]) == 1
    assert data["series"][0]["mapped_outcome"] == "home_win"


def test_discrepancies_returns_top_edges(client):
    _seed_prediction_and_link(match_id="m1", implied=0.50)  # large edge
    from app.kernel.edge_detector_service import EdgeDetectorService
    EdgeDetectorService().detect_edges("m1")
    res = client.get("/api/sport-edges/discrepancies")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 1
    assert len(data["items"]) >= 1
    assert data["items"][0]["match_id"] == "m1"


def test_discrepancies_respects_limit(client):
    _seed_prediction_and_link(match_id="m1", implied=0.50)
    _seed_prediction_and_link(match_id="m2", implied=0.45)
    from app.kernel.edge_detector_service import EdgeDetectorService
    EdgeDetectorService().detect_edges("m1")
    EdgeDetectorService().detect_edges("m2")
    res = client.get("/api/sport-edges/discrepancies", params={"limit": 1})
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 1


def test_discrepancies_respects_min_abs_edge(client):
    _seed_prediction_and_link(match_id="m1", implied=0.64)  # small edge (0.01)
    from app.kernel.edge_detector_service import EdgeDetectorService
    EdgeDetectorService().detect_edges("m1")
    res = client.get("/api/sport-edges/discrepancies", params={"min_abs_edge": 0.05})
    assert res.status_code == 200
    data = res.json()
    # edge is 0.01 * 0.72 * 1.0 = 0.0072, below 0.05 threshold
    assert data["total"] == 0
    assert data["items"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_sport_edge_routes.py -v`
Expected: FAIL with `ImportError: No module named 'app.api.routes.sport_edges'`.

- [ ] **Step 3: Create `backend/app/api/routes/sport_edges.py`**

```python
"""Sport edge detector API routes.

When PHASE7_EDGE_DETECTOR_ENABLED is false, all routes return 503.
All endpoints are GET (read-only) — no require_write_key auth (consistent
with Subproject A's GET endpoints).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.core import config

router = APIRouter(prefix="/sport-edges", tags=["Sport Edges"])


def _ensure_enabled() -> None:
    if not config.settings.PHASE7_EDGE_DETECTOR_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Edge detector is disabled. Set PHASE7_EDGE_DETECTOR_ENABLED=true to enable.",
        )


def _service():
    from app.kernel.edge_detector_service import EdgeDetectorService
    return EdgeDetectorService()


def _edge_to_dict(edge) -> dict[str, Any]:
    """Serialize an EdgeResult to a JSON-friendly dict."""
    return {
        "mapped_outcome": edge.mapped_outcome,
        "model_prob": edge.model_prob,
        "market_prob": edge.market_prob,
        "raw_edge": edge.raw_edge,
        "trust": edge.trust,
        "liquidity_factor": edge.liquidity_factor,
        "adjusted_edge": edge.adjusted_edge,
        "spread": edge.spread,
        "sources_count": edge.sources_count,
        "stale": edge.stale,
        "captured_at": edge.captured_at.isoformat() if edge.captured_at else None,
        "sources": [
            {
                "link_id": s.link_id,
                "source": s.source,
                "contract_id": s.contract_id,
                "implied_prob": s.implied_prob,
                "liquidity": s.liquidity,
                "volume": s.volume,
                "weight": s.weight,
                "link_confidence": s.link_confidence,
            }
            for s in edge.sources
        ],
    }


@router.get("/{match_id}/latest")
def get_latest_edges(match_id: str) -> dict[str, Any]:
    """Latest edge snapshot per outcome for a match.

    If the match has no prediction or no verified links, returns skipped=true.
    Note: this reads persisted edges. To trigger computation, use the CLI
    or scheduler — this endpoint does not compute on demand.
    """
    _ensure_enabled()
    svc = _service()
    edges = svc.get_latest_edges(match_id)
    if not edges:
        # No persisted edges — check why (no prediction or no verified links)
        from app.kernel.kernel_db import get_latest_prediction
        pred = get_latest_prediction(match_id)
        if pred is None:
            return {
                "match_id": match_id, "outcomes": [],
                "engine_name": None, "competition": None,
                "prediction_timestamp": None,
                "skipped": True, "skip_reason": "no_prediction",
            }
        # Has prediction but no persisted edges -> either no verified links
        # or edges not yet computed
        return {
            "match_id": match_id, "outcomes": [],
            "engine_name": pred.engine, "competition": pred.competition,
            "prediction_timestamp": pred.created_at.isoformat() if pred.created_at else None,
            "skipped": True, "skip_reason": "no_verified_links",
        }
    # Use the first edge's match-level metadata (trust is per-match)
    first = edges[0]
    return {
        "match_id": match_id,
        "outcomes": [_edge_to_dict(e) for e in edges],
        "engine_name": None,  # not persisted per-edge; populated only on detect
        "competition": None,
        "prediction_timestamp": None,
        "skipped": False,
        "skip_reason": None,
    }


@router.get("/{match_id}/history")
def get_edge_history(
    match_id: str,
    mapped_outcome: str | None = Query(None),
) -> dict[str, Any]:
    """Full edge time-series for a match, optionally filtered by outcome."""
    _ensure_enabled()
    svc = _service()
    edges = svc.get_edge_history(match_id, mapped_outcome=mapped_outcome)
    # Group by mapped_outcome
    by_outcome: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        by_outcome.setdefault(edge.mapped_outcome, []).append({
            "captured_at": edge.captured_at.isoformat() if edge.captured_at else None,
            "model_prob": edge.model_prob,
            "market_prob": edge.market_prob,
            "raw_edge": edge.raw_edge,
            "adjusted_edge": edge.adjusted_edge,
            "stale": edge.stale,
        })
    series = [
        {"mapped_outcome": outcome, "snapshots": snaps}
        for outcome, snaps in by_outcome.items()
    ]
    return {"match_id": match_id, "series": series}


@router.get("/discrepancies")
def get_discrepancies(
    limit: int = Query(20, ge=1, le=100),
    min_abs_edge: float = Query(0.0, ge=0.0, le=1.0),
) -> dict[str, Any]:
    """Top matches by |adjusted_edge| across all matches with edge data."""
    _ensure_enabled()
    svc = _service()
    edges = svc.get_top_discrepancies(limit=limit, min_abs_edge=min_abs_edge)
    items = [
        {
            "match_id": e.match_id,
            "mapped_outcome": e.mapped_outcome,
            "model_prob": e.model_prob,
            "market_prob": e.market_prob,
            "raw_edge": e.raw_edge,
            "adjusted_edge": e.adjusted_edge,
            "stale": e.stale,
            "captured_at": e.captured_at.isoformat() if e.captured_at else None,
        }
        for e in edges
    ]
    return {"items": items, "total": len(items)}
```

- [ ] **Step 4: Register router in `backend/app/api/router.py`**

In `backend/app/api/router.py`, update the import line (line 3) and add the router registration. The file becomes:

```python
# app/api/router.py — v0.3.0
from fastapi import APIRouter
from app.api.routes import events, llm, quality_metrics, world_cup_predictions, world_cup_analytics, predictions, sport_markets, sport_edges

api_router = APIRouter()

api_router.include_router(events.router, prefix="/events", tags=["Events"])
api_router.include_router(llm.router, prefix="/llm", tags=["LLM"])
api_router.include_router(quality_metrics.router, tags=["Quality Metrics"])
api_router.include_router(world_cup_predictions.router, tags=["World Cup Predictions"])
api_router.include_router(world_cup_analytics.router, tags=["World Cup Analytics"])
api_router.include_router(predictions.router, tags=["Predictions"])
api_router.include_router(sport_markets.router, tags=["Sport Markets"])
api_router.include_router(sport_edges.router, tags=["Sport Edges"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_sport_edge_routes.py -v`
Expected: PASS (8 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/sport_edges.py backend/app/api/router.py backend/tests/test_sport_edge_routes.py
git commit -m "feat(phase7-b): add 3 sport-edges API endpoints (latest/history/discrepancies)"
```

---

## Task 6: Scheduler job + CLI

**Files:**
- Modify: `backend/app/core/scheduler.py` (add `_job_detect_sport_edges` + register in `start_scheduler`)
- Create: `backend/scripts/sport_edge_cli.py`
- Test: `backend/tests/test_sport_edge_cli.py` (new — 3 tests)

**Interfaces:**
- Consumes: `EdgeDetectorService` (Task 4), `SportMarketLinkStore.get_matches_with_verified_links` (Task 3), config flags `PHASE7_EDGE_DETECTOR_ENABLED` + `EDGE_DETECTION_INTERVAL_MIN`, scheduler helpers `_start_run` / `_finish_run`
- Produces: scheduler job `sport_edge_detect`, CLI with `detect` / `latest` / `discrepancies` subcommands

- [ ] **Step 1: Write failing CLI tests**

Create `backend/tests/test_sport_edge_cli.py`:

```python
"""Tests for sport_edge_cli (manual edge computation and inspection)."""
import pytest

from app.kernel.kernel_db import init_kernel_db, close_kernel_db
from app.kernel.kernel_db import KernelPrediction, KernelCalibration, get_kernel_session
from app.kernel.sport_market_link_store import SportMarketLinkStore
from app.kernel.market_snapshot_store import MarketSnapshotStore


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "edge_cli_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def _seed(match_id="m1", implied=0.58):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    session = get_kernel_session()
    try:
        session.add(KernelPrediction(
            match_id=match_id, sport="basketball", competition="nba",
            season="2025-26", engine="BasketballEngine", predicted_scores={},
            outcome_probabilities={"home_win": 0.65, "away_win": 0.35},
            confidence=0.7, feature_version="nba-1.0", explanation={},
            created_at=now, updated_at=now,
        ))
        session.add(KernelCalibration(
            engine="BasketballEngine", competition="nba", slope=1.0, intercept=0.0,
            sample_count=20, avg_confidence=0.65, avg_accuracy=0.72, last_updated=now,
        ))
        session.commit()
    finally:
        session.close()
    store = SportMarketLinkStore()
    link = store.upsert_link(
        match_id=match_id, contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=True, market_question="q", implied_prob=implied,
    )
    snap_store = MarketSnapshotStore()
    snap_store.append_snapshot(
        link_id=link["id"], implied_prob=implied, price=implied,
        liquidity=None, volume=None, captured_at=now,
    )


def test_cli_detect(kernel_db, capsys):
    """detect subcommand -> exit 0, edge persisted."""
    _seed(match_id="m1")
    from scripts.sport_edge_cli import main
    rc = main(["detect", "--match-id", "m1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "m1" in out


def test_cli_latest(kernel_db, capsys):
    """latest subcommand -> exit 0, output contains edge data."""
    _seed(match_id="m1")
    from scripts.sport_edge_cli import main
    main(["detect", "--match-id", "m1"])  # compute first
    capsys.readouterr()  # clear
    rc = main(["latest", "--match-id", "m1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "home_win" in out


def test_cli_discrepancies(kernel_db, capsys):
    """discrepancies subcommand -> exit 0."""
    _seed(match_id="m1")
    from scripts.sport_edge_cli import main
    main(["detect", "--match-id", "m1"])  # compute first
    capsys.readouterr()  # clear
    rc = main(["discrepancies"])
    assert rc == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_sport_edge_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.sport_edge_cli'`.

- [ ] **Step 3: Create `backend/scripts/sport_edge_cli.py`**

```python
"""Sport edge detector CLI.

Usage:
    python -m scripts.sport_edge_cli detect --match-id ID
    python -m scripts.sport_edge_cli latest --match-id ID
    python -m scripts.sport_edge_cli discrepancies [--limit N] [--min-abs-edge F]
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


def _cmd_detect(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.edge_detector_service import EdgeDetectorService
    init_kernel_db()
    svc = EdgeDetectorService()
    summary = svc.detect_edges(args.match_id)
    if summary.skipped:
        _print(f"[SKIP] match={args.match_id} reason={summary.skip_reason}")
        return 0
    _print(f"[OK] match={args.match_id} engine={summary.engine_name} outcomes={len(summary.outcomes)}")
    for edge in summary.outcomes:
        _print(
            f"  outcome={edge.mapped_outcome:<10} "
            f"model={edge.model_prob:.3f} market={edge.market_prob:.3f} "
            f"raw={edge.raw_edge:+.3f} adj={edge.adjusted_edge:+.3f} "
            f"trust={edge.trust:.2f} liq={edge.liquidity_factor:.2f} "
            f"stale={edge.stale}"
        )
    return 0


def _cmd_latest(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.edge_detector_service import EdgeDetectorService
    init_kernel_db()
    svc = EdgeDetectorService()
    edges = svc.get_latest_edges(args.match_id)
    if not edges:
        _print(f"[INFO] no edges found for match={args.match_id}")
        return 0
    _print(f"[OK] {len(edges)} edges for match={args.match_id}:")
    for edge in edges:
        _print(
            f"  outcome={edge.mapped_outcome:<10} "
            f"model={edge.model_prob:.3f} market={edge.market_prob:.3f} "
            f"raw={edge.raw_edge:+.3f} adj={edge.adjusted_edge:+.3f} "
            f"stale={edge.stale} captured={edge.captured_at}"
        )
    return 0


def _cmd_discrepancies(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.edge_detector_service import EdgeDetectorService
    init_kernel_db()
    svc = EdgeDetectorService()
    edges = svc.get_top_discrepancies(limit=args.limit, min_abs_edge=args.min_abs_edge)
    if not edges:
        _print("[INFO] no discrepancies found")
        return 0
    _print(f"[OK] {len(edges)} discrepancies (limit={args.limit}, min_abs_edge={args.min_abs_edge}):")
    for edge in edges:
        _print(
            f"  match={edge.match_id:<24} outcome={edge.mapped_outcome:<10} "
            f"adj={edge.adjusted_edge:+.3f} raw={edge.raw_edge:+.3f} "
            f"stale={edge.stale}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sport edge detector CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_detect = sub.add_parser("detect", help="compute and persist edges for a match")
    p_detect.add_argument("--match-id", required=True)
    p_detect.set_defaults(func=_cmd_detect)

    p_latest = sub.add_parser("latest", help="show latest edge per outcome for a match")
    p_latest.add_argument("--match-id", required=True)
    p_latest.set_defaults(func=_cmd_latest)

    p_disc = sub.add_parser("discrepancies", help="show top edge discrepancies")
    p_disc.add_argument("--limit", type=int, default=20)
    p_disc.add_argument("--min-abs-edge", type=float, default=0.0, dest="min_abs_edge")
    p_disc.set_defaults(func=_cmd_discrepancies)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Add scheduler job to `backend/app/core/scheduler.py`**

First, add the job function. Find `_job_capture_market_snapshots` (ends at line 605) and insert after it (before `_summarize_prediction_update` at line 608):

```python
async def _job_detect_sport_edges():
    """Every EDGE_DETECTION_INTERVAL_MIN: compute edges for matches with verified links."""
    if not settings.PHASE7_EDGE_DETECTOR_ENABLED:
        return
    run_id = _start_run("sport_edge_detect")
    try:
        from app.kernel.kernel_db import init_kernel_db
        from app.kernel.edge_detector_service import EdgeDetectorService
        from app.kernel.sport_market_link_store import SportMarketLinkStore
        init_kernel_db()
        store = SportMarketLinkStore()
        matches = store.get_matches_with_verified_links()
        service = EdgeDetectorService()
        processed = 0
        for match_id in matches:
            try:
                summary = service.detect_edges(match_id)
                if not summary.skipped:
                    processed += 1
            except Exception as exc:
                logger.warning(f"[Scheduler] Edge detection failed for {match_id}: {exc}")
        _finish_run(run_id, "success", result={
            "matches_total": len(matches),
            "matches_processed": processed,
        })
    except Exception as exc:
        logger.exception("[Scheduler] Sport edge detection failed")
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
```

Then, register the job in `start_scheduler`. Find the block at lines 809-830 (the `if settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED:` block) and add after it (before `scheduler.start()` at line 831):

```python
        if settings.PHASE7_EDGE_DETECTOR_ENABLED:
            scheduler.add_job(
                _job_detect_sport_edges,
                IntervalTrigger(minutes=settings.EDGE_DETECTION_INTERVAL_MIN),
                id="sport_edge_detect",
                replace_existing=True,
                max_instances=1,
            )
```

- [ ] **Step 5: Run CLI tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_sport_edge_cli.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run full Subproject B test suite**

Run: `cd backend && python -m pytest tests/test_edge_db.py tests/test_edge_store.py tests/test_link_store_matches.py tests/test_edge_detector_service.py tests/test_sport_edge_routes.py tests/test_sport_edge_cli.py -v`
Expected: PASS (3 + 4 + 2 + 15 + 8 + 3 = 35 tests).

- [ ] **Step 7: Run regression — Subproject A + learning dashboard tests must still pass**

Run: `cd backend && python -m pytest tests/test_sport_market_routes.py tests/test_sport_market_bridge_service.py tests/test_sport_market_link_store.py tests/test_market_snapshot_store.py -v`
Expected: PASS (no regressions).

- [ ] **Step 8: Commit**

```bash
git add backend/app/core/scheduler.py backend/scripts/sport_edge_cli.py backend/tests/test_sport_edge_cli.py
git commit -m "feat(phase7-b): add sport_edge_detect scheduler job + sport_edge_cli"
```

---

## Final Verification (after all 6 tasks)

- [ ] **Step 1: Run the complete test suite**

Run: `cd backend && python -m pytest tests/ -v -k "edge or sport_market or market_snapshot or link_store"`
Expected: All Subproject B + Subproject A regression tests PASS.

- [ ] **Step 2: Verify global constraints**

```bash
# Constraint 1: flag defaults OFF
grep -n "PHASE7_EDGE_DETECTOR_ENABLED.*false" backend/app/core/config.py

# Constraint 4: kernel_ prefix + KernelBase subclass
grep -n "class KernelSportEdge(KernelBase)" backend/app/kernel/kernel_db.py

# Constraint 6: no PredictionKernel.predict() call
grep -rn "\.predict(" backend/app/kernel/edge_detector_service.py
# Expected: no matches

# Constraint 9: no require_write_key in sport_edges.py
grep -n "require_write_key" backend/app/api/routes/sport_edges.py
# Expected: no matches
```

- [ ] **Step 3: Verify no Subproject A files were modified (except additive appends)**

```bash
git diff 71ebedc..HEAD -- backend/app/kernel/sport_market_link_store.py
# Expected: only the new get_matches_with_verified_links method added

git diff 71ebedc..HEAD -- backend/app/kernel/kernel_db.py
# Expected: only KernelSportEdge class + get_calibration function added
```

- [ ] **Step 4: Final commit (if any cleanup needed)**

If all checks pass, no additional commit needed. The work is complete.

---

## Summary

| Task | Files | Tests | Commit message prefix |
|------|-------|-------|----------------------|
| 1 | config.py, kernel_db.py, test_edge_db.py | 3 | `feat(phase7-b): add KernelSportEdge table, get_calibration accessor, config flags` |
| 2 | edge_store.py, test_edge_store.py | 4 | `feat(phase7-b): add EdgeStore persistence with append/latest/history/discrepancies` |
| 3 | sport_market_link_store.py, test_link_store_matches.py | 2 | `feat(phase7-b): add get_matches_with_verified_links to SportMarketLinkStore` |
| 4 | edge_detector_service.py, test_edge_detector_service.py | 15 | `feat(phase7-b): add EdgeDetectorService with trust/liquidity/staleness computation` |
| 5 | sport_edges.py, router.py, test_sport_edge_routes.py | 8 | `feat(phase7-b): add 3 sport-edges API endpoints (latest/history/discrepancies)` |
| 6 | scheduler.py, sport_edge_cli.py, test_sport_edge_cli.py | 3 | `feat(phase7-b): add sport_edge_detect scheduler job + sport_edge_cli` |

**Total:** 8 new files + 5 modified files, 35 tests (spec called for 30; 5 extra for robustness).
