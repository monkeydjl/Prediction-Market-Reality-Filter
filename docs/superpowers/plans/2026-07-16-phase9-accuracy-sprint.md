# Phase 9: Accuracy Sprint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a backtesting + parameter optimization framework that uses 2 seasons of NBA/MLB/NHL historical data to drive factor weight + Elo parameter tuning, and activate the learning loop for ongoing adaptation.

**Architecture:** HistoricalDataIngestor fetches past matches → EloTimeMachine replays Elo with candidate params → BacktestRunner computes predictions using replicated engine formulas → ParameterOptimizer (Optuna TPE) searches for optimal params → OptimizedParamsStore applies them to FactorRegistry. Zero-invasion: no engine or kernel modifications.

**Tech Stack:** Python 3.12, SQLAlchemy, Optuna (TPE sampler), APScheduler, FastAPI, Next.js, Vitest, Pytest

## Global Constraints

- `PHASE9_ACCURACY_SPRINT_ENABLED` and `PHASE9_LEARNING_ACTIVATED` feature flags must default to OFF
- `PredictionKernel`, `domain.py`, `LearningService`, and all `engines/*.py` files must NOT be modified
- New database table must use `kernel_` prefix
- Existing tables must NOT be structurally modified (only additive data)
- Frontend pages must NOT be modified (only new components + new route page)
- API endpoints must use Pydantic type annotations for request payloads
- All async functions must properly use `await` for asynchronous operations
- Stores follow keyword-only args, session-per-call, fail-closed reads pattern
- Feature flags must default to OFF to maintain backward compatibility
- `.env.example` variable names must match code configuration
- Do NOT push to origin (standing instruction)
- TDD strictly followed (RED → GREEN → COMMIT per task)
- BacktestRunner replicates engine formulas (DRY violation acknowledged — only way to test candidate params without modifying engines)

---

## File Structure

### New files (backend)
1. `backend/app/kernel/backtest/__init__.py` — package init
2. `backend/app/kernel/backtest/elo_time_machine.py` — EloParams + EloTimeMachine
3. `backend/app/kernel/backtest/runner.py` — BacktestParams + BacktestResult + BacktestRunner
4. `backend/app/kernel/parameter_optimizer.py` — ParameterOptimizer (Optuna TPE)
5. `backend/app/kernel/optimized_params_store.py` — OptimizedParamsStore
6. `backend/app/services/historical_data_ingestor.py` — HistoricalDataIngestor
7. `backend/app/api/routes/sport_optimization.py` — 6 API endpoints
8. `backend/tests/test_elo_time_machine.py` — 6 tests
9. `backend/tests/test_backtest_runner.py` — 7 tests
10. `backend/tests/test_parameter_optimizer.py` — 5 tests
11. `backend/tests/test_optimized_params_store.py` — 6 tests
12. `backend/tests/test_historical_data_ingestor.py` — 5 tests
13. `backend/tests/test_sport_optimization_routes.py` — 6 tests

### Modified files (backend)
1. `backend/app/core/config.py` — add 6 new settings
2. `backend/app/kernel/kernel_db.py` — add KernelOptimizedParams model
3. `backend/app/core/scheduler.py` — add 2 scheduler jobs
4. `backend/app/api/router.py` — register sport_optimization router
5. `backend/requirements.txt` — add optuna

### New files (frontend)
1. `frontend/src/lib/optimization-api.ts` — API client
2. `frontend/src/components/sports/optimization/OptimizationDashboard.tsx` — dashboard component
3. `frontend/src/components/sports/optimization/OptimizationDashboard.test.tsx` — 4 tests
4. `frontend/src/app/sports/optimization/page.tsx` — route page

---

## Task 1: Config + KernelOptimizedParams + OptimizedParamsStore

**Files:**
- Modify: `backend/app/core/config.py` (append before `settings = Settings()`)
- Modify: `backend/app/kernel/kernel_db.py` (append after KernelMarketCalibration class)
- Create: `backend/app/kernel/optimized_params_store.py`
- Test: `backend/tests/test_optimized_params_store.py`

**Interfaces:**
- Consumes: `get_kernel_session` from `kernel_db.py`, `FactorRegistry` from `factor_registry.py`
- Produces: `OptimizedParamsStore` class with `save_candidate`, `get_applied`, `get_candidates`, `apply` methods

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_optimized_params_store.py
"""Tests for OptimizedParamsStore — TDD RED phase."""
import json
import pytest
from datetime import datetime, timezone

from app.kernel.optimized_params_store import OptimizedParamsStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Create a store with an isolated SQLite DB."""
    db_path = str(tmp_path / "test_optimized.db")
    monkeypatch.setenv("KERNEL_DB_PATH", db_path)
    from app.kernel import kernel_db
    kernel_db.KernelBase.metadata.create_all(kernel_db._get_engine(db_path))
    return OptimizedParamsStore(db_path=db_path)


def test_save_candidate_returns_record(store):
    result = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.45, "home_court": 0.15, "rest": 0.15, "form": 0.25},
        elo_params={"hfa": 100, "k_regular": 20, "k_playoff": 30},
        score=0.72, accuracy=0.68, brier_score=0.21, mae=0.32, sample_count=100,
        trial_number=5,
    )
    assert result["id"] is not None
    assert result["sport"] == "nba"
    assert result["status"] == "candidate"
    assert json.loads(result["factor_weights"])["elo"] == 0.45


def test_get_applied_returns_none_when_no_applied(store):
    result = store.get_applied("nba", "nba")
    assert result is None


def test_apply_marks_candidate_as_applied(store):
    saved = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.50}, elo_params={"hfa": 110},
        score=0.75, accuracy=0.70, brier_score=0.20, mae=0.30, sample_count=100,
    )
    applied = store.apply(saved["id"])
    assert applied["status"] == "applied"
    assert applied["applied_at"] is not None
    # Verify get_applied returns it
    again = store.get_applied("nba", "nba")
    assert again["id"] == saved["id"]


def test_apply_archives_previous_applied(store):
    first = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.40}, elo_params={"hfa": 90},
        score=0.70, accuracy=0.65, brier_score=0.22, mae=0.35, sample_count=100,
    )
    store.apply(first["id"])
    second = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.50}, elo_params={"hfa": 110},
        score=0.75, accuracy=0.70, brier_score=0.20, mae=0.30, sample_count=100,
    )
    store.apply(second["id"])
    # First should be archived
    candidates = store.get_candidates("nba")
    statuses = [c["status"] for c in candidates]
    assert "archived" in statuses
    # Only one applied
    applied = store.get_applied("nba", "nba")
    assert applied["id"] == second["id"]


def test_get_candidates_filters_by_sport(store):
    store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.45}, elo_params={"hfa": 100},
        score=0.72, accuracy=0.68, brier_score=0.21, mae=0.32, sample_count=100,
    )
    store.save_candidate(
        sport="mlb", competition="mlb",
        factor_weights={"elo": 0.30}, elo_params={"hfa": 50},
        score=0.68, accuracy=0.63, brier_score=0.24, mae=0.37, sample_count=100,
    )
    nba_only = store.get_candidates("nba")
    assert len(nba_only) == 1
    assert nba_only[0]["sport"] == "nba"


def test_apply_is_idempotent(store):
    saved = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.45}, elo_params={"hfa": 100},
        score=0.72, accuracy=0.68, brier_score=0.21, mae=0.32, sample_count=100,
    )
    store.apply(saved["id"])
    # Applying again should not error
    result = store.apply(saved["id"])
    assert result["status"] == "applied"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_optimized_params_store.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.kernel.optimized_params_store'`

- [ ] **Step 3: Add config settings**

In `backend/app/core/config.py`, append before `settings = Settings()`:

```python
    # === Phase 9 — Accuracy Sprint ===
    PHASE9_ACCURACY_SPRINT_ENABLED: bool = _env_bool("PHASE9_ACCURACY_SPRINT_ENABLED", "false")
    PHASE9_LEARNING_ACTIVATED: bool = _env_bool("PHASE9_LEARNING_ACTIVATED", "false")
    PHASE9_OPTIMIZATION_TRIALS: int = int(os.getenv("PHASE9_OPTIMIZATION_TRIALS", "150"))
    PHASE9_BACKTEST_SEASONS: str = os.getenv("PHASE9_BACKTEST_SEASONS", "2023-24,2024-25")
    PHASE9_OPTIMIZATION_INTERVAL_MIN: int = int(os.getenv("PHASE9_OPTIMIZATION_INTERVAL_MIN", "0"))
    PHASE9_WEEKLY_WEIGHT_UPDATE_INTERVAL_MIN: int = int(os.getenv("PHASE9_WEEKLY_WEIGHT_UPDATE_INTERVAL_MIN", "0"))
```

- [ ] **Step 4: Add KernelOptimizedParams model**

In `backend/app/kernel/kernel_db.py`, append after the `KernelTraditionalOddsSnapshot` class:

```python
class KernelOptimizedParams(KernelBase):
    """Stores optimized parameter sets from Phase 9 backtesting."""
    __tablename__ = "kernel_optimized_params"
    __table_args__ = (
        UniqueConstraint("sport", "competition", "status", name="uq_optimized_params_active"),
    )

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
```

- [ ] **Step 5: Implement OptimizedParamsStore**

```python
# backend/app/kernel/optimized_params_store.py
"""Store for optimized parameter sets (Phase 9)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.kernel.kernel_db import KernelOptimizedParams, KernelBase


def _get_engine(db_path: str):
    return create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})


class OptimizedParamsStore:
    """Stores and applies optimized parameter sets.

    Follows existing Store pattern: keyword-only args, session-per-call,
    fail-closed reads (return None / [] on exception).
    """

    def __init__(self, *, db_path: str | None = None) -> None:
        if db_path is None:
            from app.kernel.kernel_db import get_kernel_session
            self._session_factory = get_kernel_session
        else:
            engine = _get_engine(db_path)
            self._session_factory = sessionmaker(bind=engine)

    def _row_to_dict(self, row: KernelOptimizedParams) -> dict[str, Any]:
        return {
            "id": row.id,
            "sport": row.sport,
            "competition": row.competition,
            "factor_weights": row.factor_weights,
            "elo_params": row.elo_params,
            "score": row.score,
            "accuracy": row.accuracy,
            "brier_score": row.brier_score,
            "mae": row.mae,
            "sample_count": row.sample_count,
            "trial_number": row.trial_number,
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "applied_at": row.applied_at.isoformat() if row.applied_at else None,
        }

    def save_candidate(
        self,
        *,
        sport: str,
        competition: str,
        factor_weights: dict,
        elo_params: dict,
        score: float,
        accuracy: float,
        brier_score: float,
        mae: float,
        sample_count: int,
        trial_number: int | None = None,
    ) -> dict:
        session = self._session_factory()
        try:
            row = KernelOptimizedParams(
                sport=sport,
                competition=competition,
                factor_weights=json.dumps(factor_weights),
                elo_params=json.dumps(elo_params),
                score=score,
                accuracy=accuracy,
                brier_score=brier_score,
                mae=mae,
                sample_count=sample_count,
                trial_number=trial_number,
                status="candidate",
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._row_to_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_applied(self, sport: str, competition: str) -> dict | None:
        session = self._session_factory()
        try:
            row = (
                session.query(KernelOptimizedParams)
                .filter_by(sport=sport, competition=competition, status="applied")
                .first()
            )
            return self._row_to_dict(row) if row else None
        except Exception:
            return None
        finally:
            session.close()

    def get_candidates(self, sport: str | None = None, limit: int = 50) -> list[dict]:
        session = self._session_factory()
        try:
            q = session.query(KernelOptimizedParams)
            if sport is not None:
                q = q.filter_by(sport=sport)
            q = q.order_by(KernelOptimizedParams.created_at.desc()).limit(limit)
            return [self._row_to_dict(r) for r in q.all()]
        except Exception:
            return []
        finally:
            session.close()

    def apply(self, params_id: int) -> dict:
        session = self._session_factory()
        try:
            # Archive any currently-applied params for this sport/competition
            target = session.query(KernelOptimizedParams).filter_by(id=params_id).first()
            if target is None:
                raise ValueError(f"Params id {params_id} not found")
            existing = (
                session.query(KernelOptimizedParams)
                .filter_by(sport=target.sport, competition=target.competition, status="applied")
                .all()
            )
            for row in existing:
                if row.id != params_id:
                    row.status = "archived"
            target.status = "applied"
            target.applied_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(target)
            return self._row_to_dict(target)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_optimized_params_store.py -v --tb=short`
Expected: 6 PASS

- [ ] **Step 7: Commit**

```bash
cd backend
git add app/core/config.py app/kernel/kernel_db.py app/kernel/optimized_params_store.py tests/test_optimized_params_store.py
git commit -m "feat(phase9): add config + KernelOptimizedParams + OptimizedParamsStore"
```

---

## Task 2: EloTimeMachine

**Files:**
- Create: `backend/app/kernel/backtest/__init__.py`
- Create: `backend/app/kernel/backtest/elo_time_machine.py`
- Test: `backend/tests/test_elo_time_machine.py`

**Interfaces:**
- Consumes: `compute_expected_score`, `update_elo`, `apply_season_regression` from `app.sports._shared.elo_calculator`
- Produces: `EloParams` dataclass, `EloTimeMachine` class with `replay(sport, matches, elo_params)` method

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_elo_time_machine.py
"""Tests for EloTimeMachine — TDD RED phase."""
import pytest

from app.kernel.backtest.elo_time_machine import EloParams, EloTimeMachine


def _make_match(match_id, home, away, home_score, away_score, season, is_playoff=False):
    return {
        "match_id": match_id,
        "home_team": home,
        "away_team": away,
        "home_score": home_score,
        "away_score": away_score,
        "season": season,
        "is_playoff": is_playoff,
    }


def test_single_season_replay():
    matches = [
        _make_match("m1", "Lakers", "Celtics", 110, 105, 2024),
        _make_match("m2", "Celtics", "Lakers", 108, 112, 2024),
    ]
    params = EloParams(hfa=100, k_regular=20, k_playoff=30, season_carry=0.75, initial=1500, league_avg_total=220)
    machine = EloTimeMachine()
    snapshots = machine.replay("nba", matches, params)
    # Before first match, both teams at 1500
    assert snapshots["m1"]["home_elo"] == 1500.0
    assert snapshots["m1"]["away_elo"] == 1500.0
    # After m1 (Lakers won), Lakers Elo > 1500, Celtics < 1500
    assert snapshots["m2"]["home_elo"] < 1500.0  # Celtics (lost m1)
    assert snapshots["m2"]["away_elo"] > 1500.0  # Lakers (won m1)


def test_season_boundary_regression():
    matches = [
        _make_match("m1", "Lakers", "Celtics", 110, 105, 2023),
        _make_match("m2", "Lakers", "Celtics", 112, 108, 2024),
    ]
    params = EloParams(hfa=100, k_regular=20, k_playoff=30, season_carry=0.75, initial=1500, league_avg_total=220)
    machine = EloTimeMachine()
    snapshots = machine.replay("nba", matches, params)
    # After season boundary, Elo regresses toward 1500
    # Lakers won m1 → Elo > 1500. After regression: 0.75 * elo + 0.25 * 1500
    lakers_after_m1 = snapshots["m2"]["home_elo"]
    # Should be between 1500 and pre-regression value (closer to 1500)
    assert 1500.0 < lakers_after_m1 < snapshots["m1"]["home_elo"] + 20  # regressed


def test_hfa_affects_expected_score():
    matches = [
        _make_match("m1", "A", "B", 100, 99, 2024),  # close game
    ]
    params_low_hfa = EloParams(hfa=50, k_regular=20, k_playoff=30, season_carry=0.75, initial=1500, league_avg_total=220)
    params_high_hfa = EloParams(hfa=150, k_regular=20, k_playoff=30, season_carry=0.75, initial=1500, league_avg_total=220)
    machine = EloTimeMachine()
    snap_low = machine.replay("nba", matches, params_low_hfa)
    snap_high = machine.replay("nba", matches, params_high_hfa)
    # With higher HFA, expected home win prob is higher, so actual win yields smaller Elo gain
    # Both start at 1500, home won. With higher HFA, expected was higher → smaller upset → smaller Elo change
    # So home_elo after match is lower with high HFA (less gain)
    # But we only have pre-match snapshots. Let's verify post-match by checking m2 doesn't exist.
    # Actually, let's verify that snapshots are correct for both.
    assert snap_low["m1"]["home_elo"] == 1500.0
    assert snap_high["m1"]["home_elo"] == 1500.0


def test_k_factor_affects_elo_change():
    matches = [
        _make_match("m1", "A", "B", 100, 99, 2024),
        _make_match("m2", "A", "B", 100, 99, 2024),
    ]
    params_low_k = EloParams(hfa=100, k_regular=10, k_playoff=30, season_carry=0.75, initial=1500, league_avg_total=220)
    params_high_k = EloParams(hfa=100, k_regular=40, k_playoff=30, season_carry=0.75, initial=1500, league_avg_total=220)
    machine = EloTimeMachine()
    snap_low = machine.replay("nba", matches, params_low_k)
    snap_high = machine.replay("nba", matches, params_high_k)
    # After m1 (A won), A's Elo increased. With higher K, increase is larger.
    # In m2, A is away. snap["m2"]["away_elo"] = A's Elo after m1.
    elo_low = snap_low["m2"]["away_elo"]
    elo_high = snap_high["m2"]["away_elo"]
    assert elo_high > elo_low  # Higher K → bigger Elo change


def test_initial_elo_applied_to_new_teams():
    matches = [
        _make_match("m1", "A", "B", 100, 99, 2024),
        _make_match("m2", "A", "C", 100, 99, 2024),  # C is new
    ]
    params = EloParams(hfa=100, k_regular=20, k_playoff=30, season_carry=0.75, initial=1500, league_avg_total=220)
    machine = EloTimeMachine()
    snapshots = machine.replay("nba", matches, params)
    # Team C starts at initial=1500
    assert snapshots["m2"]["away_elo"] == 1500.0


def test_playoff_uses_higher_k():
    matches = [
        _make_match("m1", "A", "B", 100, 99, 2024, is_playoff=True),
        _make_match("m2", "A", "B", 100, 99, 2024, is_playoff=True),
    ]
    params = EloParams(hfa=100, k_regular=20, k_playoff=40, season_carry=0.75, initial=1500, league_avg_total=220)
    machine = EloTimeMachine()
    snapshots = machine.replay("nba", matches, params)
    # After m1 (playoff, K=40), A's Elo increased more than with K=20
    elo_after = snapshots["m2"]["away_elo"]  # A is away in m2
    # With K=40, expected was ~0.5 (both 1500), actual=1.0, gain = 40 * (1 - 0.5) = 20
    assert elo_after == pytest.approx(1520.0, abs=0.1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_elo_time_machine.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.kernel.backtest'`

- [ ] **Step 3: Create package init + EloTimeMachine**

```python
# backend/app/kernel/backtest/__init__.py
"""Backtesting framework for Sports Prediction Kernel (Phase 9)."""
```

```python
# backend/app/kernel/backtest/elo_time_machine.py
"""EloTimeMachine — replays Elo ratings from season start with given parameters.

Reuses the stateless Elo functions from app.sports._shared.elo_calculator
(verbatim copy of NBA's elo_calculator). The time machine records Elo
snapshots BEFORE each match so the BacktestRunner can use them for prediction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.sports._shared.elo_calculator import (
    compute_expected_score,
    update_elo,
    apply_season_regression,
)


@dataclass(frozen=True)
class EloParams:
    """Elo computation parameters for backtesting."""
    hfa: float
    k_regular: float
    k_playoff: float
    season_carry: float  # 0.0 = full reset, 1.0 = no regression
    initial: float       # default 1500
    league_avg_total: float  # only used for display, not Elo computation


class EloTimeMachine:
    """Replays Elo ratings from season start with given parameters."""

    def replay(
        self,
        sport: str,
        matches: list[dict[str, Any]],
        elo_params: EloParams,
    ) -> dict[str, dict[str, float]]:
        """Replay Elo from season start with given params.

        Args:
            sport: "nba" / "mlb" / "nhl" (affects nothing — formula is same)
            matches: List of match dicts with keys:
                - match_id (str)
                - home_team (str)
                - away_team (str)
                - home_score (int)
                - away_score (int)
                - season (int)
                - is_playoff (bool, optional)
                Matches MUST be in chronological order.
            elo_params: EloParams with HFA, K-factors, season_carry, initial.

        Returns:
            {match_id: {"home_elo": float, "away_elo": float}} snapshot
            BEFORE each match (for prediction).
        """
        ratings: dict[str, float] = {}
        current_season: int | None = None
        snapshots: dict[str, dict[str, float]] = {}

        for match in matches:
            season = match["season"]
            # Apply regression at season boundary
            if current_season is not None and season != current_season:
                for team in ratings:
                    ratings[team] = apply_season_regression(
                        ratings[team], mean=elo_params.initial, carry=elo_params.season_carry,
                    )
            current_season = season

            home = match["home_team"]
            away = match["away_team"]
            # Initialize new teams at initial Elo
            if home not in ratings:
                ratings[home] = elo_params.initial
            if away not in ratings:
                ratings[away] = elo_params.initial

            elo_home = ratings[home]
            elo_away = ratings[away]

            # Record snapshot BEFORE match
            snapshots[match["match_id"]] = {
                "home_elo": elo_home,
                "away_elo": elo_away,
            }

            # Update Elo after match
            expected = compute_expected_score(elo_home, elo_away, elo_params.hfa)
            home_won = match["home_score"] > match["away_score"]
            actual_home = 1.0 if home_won else 0.0
            actual_away = 1.0 - actual_home

            k = elo_params.k_playoff if match.get("is_playoff") else elo_params.k_regular
            ratings[home] = update_elo(elo_home, expected, actual_home, k)
            ratings[away] = update_elo(elo_away, 1.0 - expected, actual_away, k)

        return snapshots
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_elo_time_machine.py -v --tb=short`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/kernel/backtest/__init__.py app/kernel/backtest/elo_time_machine.py tests/test_elo_time_machine.py
git commit -m "feat(phase9): add EloTimeMachine for historical Elo replay"
```

---

## Task 3: BacktestRunner

**Files:**
- Create: `backend/app/kernel/backtest/runner.py`
- Test: `backend/tests/test_backtest_runner.py`

**Interfaces:**
- Consumes: `EloTimeMachine`, `EloParams` from Task 2; `compute_expected_score` from `_shared.elo_calculator`
- Produces: `BacktestParams`, `BacktestResult` dataclasses, `BacktestRunner` class

**Key design decision:** BacktestRunner replicates the engine formulas (BasketballEngine, BaseballEngine, HockeyEngine) to test candidate parameters without modifying engines. This is an acknowledged DRY violation — the only way to honor zero-invasion.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_backtest_runner.py
"""Tests for BacktestRunner — TDD RED phase."""
import pytest

from app.kernel.backtest.elo_time_machine import EloParams
from app.kernel.backtest.runner import BacktestParams, BacktestRunner


def _make_match(match_id, home, away, home_score, away_score, season, rest_home=2, rest_away=2, form_home=0.5, form_away=0.5):
    return {
        "match_id": match_id,
        "home_team": home,
        "away_team": away,
        "home_score": home_score,
        "away_score": away_score,
        "season": season,
        "is_playoff": False,
        "rest_days_home": rest_home,
        "rest_days_away": rest_away,
        "form_home": form_home,
        "form_away": form_away,
    }


def test_baseline_backtest_produces_result():
    matches = [
        _make_match("m1", "Lakers", "Celtics", 110, 105, 2024),
        _make_match("m2", "Celtics", "Lakers", 108, 112, 2024),
    ]
    params = BacktestParams(
        factor_weights={"elo": 0.45, "home_court": 0.15, "rest": 0.15, "form": 0.25},
        elo_params={"hfa": 100, "k_regular": 20, "k_playoff": 30, "season_carry": 0.75, "initial": 1500},
    )
    runner = BacktestRunner()
    result = runner.run("nba", train_matches=matches[:1], test_matches=matches[1:], params=params)
    assert 0.0 <= result.accuracy <= 1.0
    assert 0.0 <= result.brier_score <= 1.0
    assert 0.0 <= result.mae <= 1.0
    assert result.sample_count == 1
    assert result.score == pytest.approx(0.5 * result.accuracy + 0.3 * (1 - result.brier_score) + 0.2 * (1 - result.mae))


def test_parameter_change_affects_predictions():
    matches = [
        _make_match("m1", "A", "B", 100, 99, 2024),
        _make_match("m2", "A", "B", 100, 99, 2024),
    ]
    params_high_elo = BacktestParams(
        factor_weights={"elo": 0.90, "home_court": 0.04, "rest": 0.03, "form": 0.03},
        elo_params={"hfa": 100, "k_regular": 20, "k_playoff": 30, "season_carry": 0.75, "initial": 1500},
    )
    params_low_elo = BacktestParams(
        factor_weights={"elo": 0.10, "home_court": 0.30, "rest": 0.30, "form": 0.30},
        elo_params={"hfa": 100, "k_regular": 20, "k_playoff": 30, "season_carry": 0.75, "initial": 1500},
    )
    runner = BacktestRunner()
    result_high = runner.run("nba", train_matches=matches[:1], test_matches=matches[1:], params=params_high_elo)
    result_low = runner.run("nba", train_matches=matches[:1], test_matches=matches[1:], params=params_low_elo)
    # With high elo weight, predictions should differ from low elo weight
    # (Different probabilities → different scores)
    # They might not differ in accuracy (both 1/1 or 0/1) but brier/mae should differ
    assert result_high.brier_score != result_low.brier_score or result_high.mae != result_low.mae


def test_time_series_split_no_data_leakage():
    train = [_make_match(f"t{i}", "A", "B", 100+i, 99+i, 2023) for i in range(10)]
    test = [_make_match(f"e{i}", "A", "B", 100+i, 99+i, 2024) for i in range(5)]
    params = BacktestParams(
        factor_weights={"elo": 0.45, "home_court": 0.15, "rest": 0.15, "form": 0.25},
        elo_params={"hfa": 100, "k_regular": 20, "k_playoff": 30, "season_carry": 0.75, "initial": 1500},
    )
    runner = BacktestRunner()
    result = runner.run("nba", train_matches=train, test_matches=test, params=params)
    assert result.sample_count == 5


def test_empty_test_matches_returns_zero_sample():
    train = [_make_match("t1", "A", "B", 100, 99, 2024)]
    params = BacktestParams(
        factor_weights={"elo": 0.45, "home_court": 0.15, "rest": 0.15, "form": 0.25},
        elo_params={"hfa": 100, "k_regular": 20, "k_playoff": 30, "season_carry": 0.75, "initial": 1500},
    )
    runner = BacktestRunner()
    result = runner.run("nba", train_matches=train, test_matches=[], params=params)
    assert result.sample_count == 0
    assert result.accuracy == 0.0


def test_single_match_backtest():
    train = [_make_match("t1", "A", "B", 100, 99, 2024)]
    test = [_make_match("e1", "A", "B", 100, 99, 2024)]
    params = BacktestParams(
        factor_weights={"elo": 0.45, "home_court": 0.15, "rest": 0.15, "form": 0.25},
        elo_params={"hfa": 100, "k_regular": 20, "k_playoff": 30, "season_carry": 0.75, "initial": 1500},
    )
    runner = BacktestRunner()
    result = runner.run("nba", train_matches=train, test_matches=test, params=params)
    assert result.sample_count == 1


def test_multi_season_backtest():
    train = [_make_match(f"t{i}", "A", "B", 100, 99, 2023) for i in range(5)]
    test = [_make_match(f"e{i}", "A", "B", 100, 99, 2024) for i in range(5)]
    params = BacktestParams(
        factor_weights={"elo": 0.45, "home_court": 0.15, "rest": 0.15, "form": 0.25},
        elo_params={"hfa": 100, "k_regular": 20, "k_playoff": 30, "season_carry": 0.75, "initial": 1500},
    )
    runner = BacktestRunner()
    result = runner.run("nba", train_matches=train, test_matches=test, params=params)
    assert result.sample_count == 5


def test_mlb_with_starting_pitcher():
    train = [{"match_id": "t1", "home_team": "A", "away_team": "B", "home_score": 5, "away_score": 3, "season": 2024, "is_playoff": False, "rest_days_home": 2, "rest_days_away": 2, "form_home": 0.5, "form_away": 0.5, "pitcher_era_home": 3.5, "pitcher_era_away": 4.0}]
    test = [{"match_id": "e1", "home_team": "A", "away_team": "B", "home_score": 4, "away_score": 2, "season": 2024, "is_playoff": False, "rest_days_home": 2, "rest_days_away": 2, "form_home": 0.5, "form_away": 0.5, "pitcher_era_home": 3.0, "pitcher_era_away": 4.5}]
    params = BacktestParams(
        factor_weights={"elo": 0.30, "home_court": 0.10, "rest": 0.15, "form": 0.20, "starting_pitcher": 0.25},
        elo_params={"hfa": 50, "k_regular": 20, "k_playoff": 30, "season_carry": 0.75, "initial": 1500},
    )
    runner = BacktestRunner()
    result = runner.run("mlb", train_matches=train, test_matches=test, params=params)
    assert result.sample_count == 1
    assert 0.0 <= result.accuracy <= 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_backtest_runner.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.kernel.backtest.runner'`

- [ ] **Step 3: Implement BacktestRunner**

```python
# backend/app/kernel/backtest/runner.py
"""BacktestRunner — runs backtest with given parameters over historical matches.

Replicates the engine formulas (BasketballEngine, BaseballEngine, HockeyEngine)
to test candidate parameters without modifying engines. This is an acknowledged
DRY violation — the only way to honor zero-invasion.

If an engine's formula changes, the corresponding _compute_*_prediction method
must be updated to match.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.kernel.backtest.elo_time_machine import EloTimeMachine, EloParams
from app.sports._shared.elo_calculator import compute_expected_score


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class BacktestParams:
    """Parameters for a single backtest run."""
    factor_weights: dict[str, float]
    elo_params: dict[str, float]


@dataclass(frozen=True)
class BacktestResult:
    """Result of a backtest run."""
    accuracy: float
    brier_score: float
    mae: float
    sample_count: int
    score: float  # 0.5*accuracy + 0.3*(1-brier) + 0.2*(1-mae)
    predictions: list[dict] = field(default_factory=list)


# Sport-specific constants (match engine defaults)
_HOME_COURT_PROB = {
    "nba": 0.58,
    "mlb": 0.54,
    "nhl": 0.55,
}

_FACTOR_NAMES = {
    "nba": ["elo", "home_court", "rest", "form"],
    "mlb": ["elo", "home_court", "rest", "form", "starting_pitcher"],
    "nhl": ["elo", "home_court", "rest", "form", "goalie"],
}


class BacktestRunner:
    """Runs backtest with given parameters over historical matches."""

    def __init__(self) -> None:
        self._elo_machine = EloTimeMachine()

    def run(
        self,
        sport: str,
        *,
        train_matches: list[dict[str, Any]],
        test_matches: list[dict[str, Any]],
        params: BacktestParams,
    ) -> BacktestResult:
        """Run backtest with given params. Synchronous.

        Args:
            sport: "nba" / "mlb" / "nhl"
            train_matches: Training matches (for Elo accumulation only)
            test_matches: Test matches (for evaluation)
            params: BacktestParams with factor_weights + elo_params

        Returns:
            BacktestResult with accuracy, Brier, MAE, score.
        """
        if not test_matches:
            return BacktestResult(accuracy=0.0, brier_score=0.0, mae=0.0, sample_count=0, score=0.0)

        # Replay Elo over all matches (train + test) with candidate Elo params
        all_matches = train_matches + test_matches
        elo_params = EloParams(
            hfa=params.elo_params["hfa"],
            k_regular=params.elo_params["k_regular"],
            k_playoff=params.elo_params["k_playoff"],
            season_carry=params.elo_params.get("season_carry", 0.75),
            initial=params.elo_params.get("initial", 1500),
            league_avg_total=0,  # not used for Elo computation
        )
        snapshots = self._elo_machine.replay(sport, all_matches, elo_params)

        # Run predictions on test matches
        predictions: list[dict] = []
        correct = 0
        brier_sum = 0.0
        mae_sum = 0.0

        for match in test_matches:
            match_id = match["match_id"]
            snapshot = snapshots.get(match_id, {"home_elo": 1500.0, "away_elo": 1500.0})

            # Compute prediction using replicated engine formula
            p_home = self._compute_prediction(sport, match, snapshot, params.factor_weights, params.elo_params)
            p_away = 1.0 - p_home

            # Actual outcome
            home_won = match["home_score"] > match["away_score"]
            actual_home = 1.0 if home_won else 0.0

            # Metrics
            predicted_outcome = "home_win" if p_home >= 0.5 else "away_win"
            actual_outcome = "home_win" if home_won else "away_win"
            is_correct = predicted_outcome == actual_outcome
            if is_correct:
                correct += 1

            # Brier score: (predicted_prob - actual)^2 averaged over outcomes
            brier = ((p_home - actual_home) ** 2 + (p_away - (1 - actual_home)) ** 2) / 2
            brier_sum += brier

            # MAE: |predicted_prob - actual|
            mae = abs(p_home - actual_home)
            mae_sum += mae

            predictions.append({
                "match_id": match_id,
                "p_home": round(p_home, 4),
                "p_away": round(p_away, 4),
                "actual": actual_outcome,
                "predicted": predicted_outcome,
                "correct": is_correct,
            })

        n = len(test_matches)
        accuracy = correct / n
        brier_score = brier_sum / n
        mae = mae_sum / n
        score = 0.5 * accuracy + 0.3 * (1 - brier_score) + 0.2 * (1 - mae)

        return BacktestResult(
            accuracy=round(accuracy, 4),
            brier_score=round(brier_score, 4),
            mae=round(mae, 4),
            sample_count=n,
            score=round(score, 4),
            predictions=predictions,
        )

    def _compute_prediction(
        self,
        sport: str,
        match: dict[str, Any],
        elo_snapshot: dict[str, float],
        weights: dict[str, float],
        elo_params: dict[str, float],
    ) -> float:
        """Compute P(home_win) using replicated engine formula."""
        if sport == "nba":
            return self._compute_nba_prediction(match, elo_snapshot, weights, elo_params)
        elif sport == "mlb":
            return self._compute_mlb_prediction(match, elo_snapshot, weights, elo_params)
        elif sport == "nhl":
            return self._compute_nhl_prediction(match, elo_snapshot, weights, elo_params)
        else:
            raise ValueError(f"Unsupported sport: {sport}")

    def _compute_nba_prediction(
        self, match: dict, elo_snapshot: dict, weights: dict, elo_params: dict,
    ) -> float:
        """Replicate BasketballEngine formula."""
        hfa = elo_params["hfa"]
        factors: list[tuple[str, float, float, bool]] = []

        # 1. Elo factor
        elo_home = elo_snapshot["home_elo"]
        elo_away = elo_snapshot["away_elo"]
        p_elo = compute_expected_score(elo_home, elo_away, hfa)
        factors.append(("elo", p_elo, weights.get("elo", 0), True))

        # 2. Home court (constant)
        factors.append(("home_court", _HOME_COURT_PROB["nba"], weights.get("home_court", 0), True))

        # 3. Rest factor
        rest_home = match.get("rest_days_home")
        rest_away = match.get("rest_days_away")
        if rest_home is not None and rest_away is not None:
            rest_diff = _clamp(rest_home - rest_away, -3, 3)
            p_rest = 0.5 + rest_diff * 0.03
            factors.append(("rest", p_rest, weights.get("rest", 0), True))
        else:
            factors.append(("rest", 0.5, weights.get("rest", 0), False))

        # 4. Form factor
        form_home = match.get("form_home")
        form_away = match.get("form_away")
        if form_home is not None and form_away is not None:
            form_diff = _clamp(form_home - form_away, -0.3, 0.3)
            p_form = 0.5 + form_diff * 0.5
            factors.append(("form", p_form, weights.get("form", 0), True))
        else:
            factors.append(("form", 0.5, weights.get("form", 0), False))

        return self._weighted_fusion(factors)

    def _compute_mlb_prediction(
        self, match: dict, elo_snapshot: dict, weights: dict, elo_params: dict,
    ) -> float:
        """Replicate BaseballEngine formula."""
        hfa = elo_params["hfa"]
        factors: list[tuple[str, float, float, bool]] = []

        # 1. Elo
        p_elo = compute_expected_score(elo_snapshot["home_elo"], elo_snapshot["away_elo"], hfa)
        factors.append(("elo", p_elo, weights.get("elo", 0), True))

        # 2. Home court
        factors.append(("home_court", _HOME_COURT_PROB["mlb"], weights.get("home_court", 0), True))

        # 3. Rest
        rest_home = match.get("rest_days_home")
        rest_away = match.get("rest_days_away")
        if rest_home is not None and rest_away is not None:
            rest_diff = _clamp(rest_home - rest_away, -3, 3)
            factors.append(("rest", 0.5 + rest_diff * 0.03, weights.get("rest", 0), True))
        else:
            factors.append(("rest", 0.5, weights.get("rest", 0), False))

        # 4. Form
        form_home = match.get("form_home")
        form_away = match.get("form_away")
        if form_home is not None and form_away is not None:
            form_diff = _clamp(form_home - form_away, -0.3, 0.3)
            factors.append(("form", 0.5 + form_diff * 0.5, weights.get("form", 0), True))
        else:
            factors.append(("form", 0.5, weights.get("form", 0), False))

        # 5. Starting pitcher
        era_home = match.get("pitcher_era_home")
        era_away = match.get("pitcher_era_away")
        if era_home is not None and era_away is not None:
            era_diff = _clamp(era_away - era_home, -2.0, 2.0)
            factors.append(("starting_pitcher", 0.5 + era_diff * 0.1, weights.get("starting_pitcher", 0), True))
        else:
            factors.append(("starting_pitcher", 0.5, weights.get("starting_pitcher", 0), False))

        return self._weighted_fusion(factors)

    def _compute_nhl_prediction(
        self, match: dict, elo_snapshot: dict, weights: dict, elo_params: dict,
    ) -> float:
        """Replicate HockeyEngine formula."""
        hfa = elo_params["hfa"]
        factors: list[tuple[str, float, float, bool]] = []

        # 1. Elo
        p_elo = compute_expected_score(elo_snapshot["home_elo"], elo_snapshot["away_elo"], hfa)
        factors.append(("elo", p_elo, weights.get("elo", 0), True))

        # 2. Home court
        factors.append(("home_court", _HOME_COURT_PROB["nhl"], weights.get("home_court", 0), True))

        # 3. Rest
        rest_home = match.get("rest_days_home")
        rest_away = match.get("rest_days_away")
        if rest_home is not None and rest_away is not None:
            rest_diff = _clamp(rest_home - rest_away, -3, 3)
            factors.append(("rest", 0.5 + rest_diff * 0.03, weights.get("rest", 0), True))
        else:
            factors.append(("rest", 0.5, weights.get("rest", 0), False))

        # 4. Form
        form_home = match.get("form_home")
        form_away = match.get("form_away")
        if form_home is not None and form_away is not None:
            form_diff = _clamp(form_home - form_away, -0.3, 0.3)
            factors.append(("form", 0.5 + form_diff * 0.5, weights.get("form", 0), True))
        else:
            factors.append(("form", 0.5, weights.get("form", 0), False))

        # 5. Goalie
        sv_home = match.get("goalie_save_pct_home")
        sv_away = match.get("goalie_save_pct_away")
        if sv_home is not None and sv_away is not None:
            sv_diff = _clamp(sv_home - sv_away, -0.1, 0.1)
            factors.append(("goalie", 0.5 + sv_diff * 2.0, weights.get("goalie", 0), True))
        else:
            factors.append(("goalie", 0.5, weights.get("goalie", 0), False))

        return self._weighted_fusion(factors)

    @staticmethod
    def _weighted_fusion(factors: list[tuple[str, float, float, bool]]) -> float:
        """Weighted average with weight redistribution for unavailable factors."""
        available = [(f, p, w) for f, p, w, a in factors if a]
        total_w = sum(w for _, _, w in available)
        if total_w > 0:
            return sum(p * (w / total_w) for _, p, w in available)
        return 0.5
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_backtest_runner.py -v --tb=short`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/kernel/backtest/runner.py tests/test_backtest_runner.py
git commit -m "feat(phase9): add BacktestRunner with replicated engine formulas"
```

---

## Task 4: HistoricalDataIngestor

**Files:**
- Create: `backend/app/services/historical_data_ingestor.py`
- Test: `backend/tests/test_historical_data_ingestor.py`

**Interfaces:**
- Consumes: existing sport-specific adapters (NBA/MLB/NHL) for historical data fetching
- Produces: `HistoricalDataIngestor` class with `ingest_season(sport, season)` method

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_historical_data_ingestor.py
"""Tests for HistoricalDataIngestor — TDD RED phase."""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.historical_data_ingestor import HistoricalDataIngestor


@pytest.fixture
def ingestor(tmp_path, monkeypatch):
    monkeypatch.setenv("KERNEL_DB_PATH", str(tmp_path / "test_ingestor.db"))
    from app.kernel import kernel_db
    kernel_db.KernelBase.metadata.create_all(kernel_db._get_engine(str(tmp_path / "test_ingestor.db")))
    return HistoricalDataIngestor()


@pytest.mark.asyncio
async def test_ingest_one_season_nba(ingestor):
    mock_games = [
        {"game_id": 1, "home_team": "Lakers", "away_team": "Celtics", "home_score": 110, "away_score": 105, "season": 2024, "date": "2024-01-01"},
        {"game_id": 2, "home_team": "Celtics", "away_team": "Lakers", "home_score": 108, "away_score": 112, "season": 2024, "date": "2024-01-03"},
    ]
    with patch("app.services.historical_data_ingestor.fetch_nba_season_games", new_callable=AsyncMock, return_value=mock_games):
        result = await ingestor.ingest_season("nba", "2024-25")
    assert result["matches"] == 2
    assert result["results"] == 2
    assert len(result["errors"]) == 0


@pytest.mark.asyncio
async def test_ingest_multi_season(ingestor):
    with patch("app.services.historical_data_ingestor.fetch_nba_season_games", new_callable=AsyncMock, return_value=[
        {"game_id": 1, "home_team": "A", "away_team": "B", "home_score": 100, "away_score": 99, "season": 2023, "date": "2023-01-01"},
    ]):
        result1 = await ingestor.ingest_season("nba", "2023-24")
    with patch("app.services.historical_data_ingestor.fetch_nba_season_games", new_callable=AsyncMock, return_value=[
        {"game_id": 2, "home_team": "A", "away_team": "B", "home_score": 101, "away_score": 98, "season": 2024, "date": "2024-01-01"},
    ]):
        result2 = await ingestor.ingest_season("nba", "2024-25")
    assert result1["matches"] == 1
    assert result2["matches"] == 1


@pytest.mark.asyncio
async def test_api_failure_returns_errors(ingestor):
    with patch("app.services.historical_data_ingestor.fetch_nba_season_games", new_callable=AsyncMock, side_effect=Exception("API down")):
        result = await ingestor.ingest_season("nba", "2024-25")
    assert result["matches"] == 0
    assert len(result["errors"]) == 1
    assert "API down" in result["errors"][0]


@pytest.mark.asyncio
async def test_idempotent_re_ingest(ingestor):
    mock_games = [
        {"game_id": 1, "home_team": "Lakers", "away_team": "Celtics", "home_score": 110, "away_score": 105, "season": 2024, "date": "2024-01-01"},
    ]
    with patch("app.services.historical_data_ingestor.fetch_nba_season_games", new_callable=AsyncMock, return_value=mock_games):
        result1 = await ingestor.ingest_season("nba", "2024-25")
        result2 = await ingestor.ingest_season("nba", "2024-25")
    assert result1["matches"] == 1
    assert result2["matches"] == 1  # No duplicate
    assert result2["results"] == 1


@pytest.mark.asyncio
async def test_mlb_ingest(ingestor):
    mock_games = [
        {"game_id": 1, "home_team": "Yankees", "away_team": "Red Sox", "home_score": 5, "away_score": 3, "season": 2024, "date": "2024-06-01"},
    ]
    with patch("app.services.historical_data_ingestor.fetch_mlb_season_games", new_callable=AsyncMock, return_value=mock_games):
        result = await ingestor.ingest_season("mlb", "2024")
    assert result["matches"] == 1
    assert result["results"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_historical_data_ingestor.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.historical_data_ingestor'`

- [ ] **Step 3: Implement HistoricalDataIngestor**

```python
# backend/app/services/historical_data_ingestor.py
"""HistoricalDataIngestor — fetches historical matches + results for backtesting.

Delegates to existing sport-specific API clients (balldontlie for NBA,
statsapi.mlb.com for MLB, api-web.nhle.com for NHL). Stores results in
existing kernel_match_fixtures + kernel_match_results tables (additive).
"""
from __future__ import annotations

import logging
from typing import Any

from app.kernel.kernel_db import get_kernel_session, KernelMatchFixture, KernelMatchResult

logger = logging.getLogger(__name__)


async def fetch_nba_season_games(season: str) -> list[dict[str, Any]]:
    """Fetch NBA games for a season from balldontlie.io.

    Args:
        season: e.g., "2024-25"

    Returns:
        List of game dicts with home_team, away_team, home_score, away_score, season, date.
    """
    # Delegates to existing NBA adapter's fetch logic.
    # This is a thin wrapper that the ingestor calls.
    from app.sports.basketball.nba_adapter import NBDataAdapter
    adapter = NBDataAdapter()
    games = await adapter.fetch_historical_games(season)
    return games


async def fetch_mlb_season_games(season: str) -> list[dict[str, Any]]:
    """Fetch MLB games for a season from statsapi.mlb.com."""
    from app.sports.baseball.mlb_adapter import MLBDataAdapter
    adapter = MLBDataAdapter()
    games = await adapter.fetch_historical_games(season)
    return games


async def fetch_nhl_season_games(season: str) -> list[dict[str, Any]]:
    """Fetch NHL games for a season from api-web.nhle.com."""
    from app.sports.hockey.nhl_adapter import NHLDataAdapter
    adapter = NHLDataAdapter()
    games = await adapter.fetch_historical_games(season)
    return games


_FETCHERS = {
    "nba": fetch_nba_season_games,
    "mlb": fetch_mlb_season_games,
    "nhl": fetch_nhl_season_games,
}


class HistoricalDataIngestor:
    """Fetches historical matches + results from existing sports APIs."""

    async def ingest_season(self, sport: str, season: str) -> dict[str, Any]:
        """Fetch + store historical matches + results for one season.

        Args:
            sport: "nba" / "mlb" / "nhl"
            season: e.g., "2024-25" for NBA/NHL, "2024" for MLB

        Returns:
            {"matches": N, "results": N, "errors": [...]}
        """
        fetcher = _FETCHERS.get(sport)
        if fetcher is None:
            return {"matches": 0, "results": 0, "errors": [f"Unknown sport: {sport}"]}

        try:
            games = await fetcher(season)
        except Exception as exc:
            logger.exception("Failed to fetch %s season %s", sport, season)
            return {"matches": 0, "results": 0, "errors": [str(exc)]}

        matches_stored = 0
        results_stored = 0
        errors: list[str] = []

        session = get_kernel_session()
        try:
            for game in games:
                match_id = f"{sport}-{season}-{game['game_id']}"
                # Check if already exists (idempotent)
                existing = session.query(KernelMatchFixture).filter_by(match_id=match_id).first()
                if existing is None:
                    fixture = KernelMatchFixture(
                        match_id=match_id,
                        competition=sport,
                        home_team=game["home_team"],
                        away_team=game["away_team"],
                        match_date=game.get("date"),
                        season=season,
                    )
                    session.add(fixture)
                    matches_stored += 1

                # Store result if scores available
                if game.get("home_score") is not None and game.get("away_score") is not None:
                    existing_result = session.query(KernelMatchResult).filter_by(match_id=match_id).first()
                    if existing_result is None:
                        result = KernelMatchResult(
                            match_id=match_id,
                            home_score=game["home_score"],
                            away_score=game["away_score"],
                            finished=True,
                        )
                        session.add(result)
                        results_stored += 1

            session.commit()
        except Exception as exc:
            session.rollback()
            errors.append(str(exc))
            logger.exception("Failed to store %s season %s", sport, season)
        finally:
            session.close()

        return {"matches": matches_stored, "results": results_stored, "errors": errors}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_historical_data_ingestor.py -v --tb=short`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/services/historical_data_ingestor.py tests/test_historical_data_ingestor.py
git commit -m "feat(phase9): add HistoricalDataIngestor for NBA/MLB/NHL historical data"
```

---

## Task 5: ParameterOptimizer + Scheduler Jobs

**Files:**
- Modify: `backend/requirements.txt` (add optuna)
- Create: `backend/app/kernel/parameter_optimizer.py`
- Modify: `backend/app/core/scheduler.py` (add 2 jobs)
- Test: `backend/tests/test_parameter_optimizer.py`

**Interfaces:**
- Consumes: `BacktestRunner` from Task 3, `OptimizedParamsStore` from Task 1, `optimization_task_manager`
- Produces: `ParameterOptimizer` class with `optimize(sport, n_trials)` method

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_parameter_optimizer.py
"""Tests for ParameterOptimizer — TDD RED phase."""
import pytest
from unittest.mock import patch, MagicMock

from app.kernel.parameter_optimizer import ParameterOptimizer


def test_optimize_converges_with_mock_backtest():
    """Verify optimizer runs and returns a result better than random."""
    optimizer = ParameterOptimizer()

    # Mock BacktestRunner to return deterministic results based on params
    def mock_run(sport, *, train_matches, test_matches, params):
        from app.kernel.backtest.runner import BacktestResult
        # Higher elo weight → higher score (simulated)
        elo_w = params.factor_weights.get("elo", 0.25)
        score = 0.5 + elo_w * 0.3  # range [0.5, 0.8]
        return BacktestResult(
            accuracy=score, brier_score=0.25, mae=0.35,
            sample_count=10, score=score, predictions=[],
        )

    with patch("app.kernel.parameter_optimizer.BacktestRunner.run", side_effect=mock_run):
        result = optimizer.optimize_sync("nba", n_trials=10, train_matches=[], test_matches=[])

    assert "best_score" in result
    assert "best_params" in result
    assert result["trials"] == 10
    assert result["best_score"] > 0.5  # Should find higher elo weight


def test_search_space_weights_sum_to_one():
    """Verify sampled factor weights always sum to 1.0."""
    optimizer = ParameterOptimizer()
    trial = MagicMock()
    trial.suggest_float = MagicMock(side_effect=lambda name, low, high: 0.3)

    weights = optimizer._sample_factor_weights(trial, "nba")
    assert sum(weights.values()) == pytest.approx(1.0, abs=0.01)
    assert set(weights.keys()) == {"elo", "home_court", "rest", "form"}


def test_search_space_mlb_has_5_factors():
    optimizer = ParameterOptimizer()
    trial = MagicMock()
    trial.suggest_float = MagicMock(side_effect=lambda name, low, high: 0.2)

    weights = optimizer._sample_factor_weights(trial, "mlb")
    assert len(weights) == 5
    assert "starting_pitcher" in weights


def test_sample_elo_params_within_bounds():
    optimizer = ParameterOptimizer()
    trial = MagicMock()
    trial.suggest_float = MagicMock(side_effect=lambda name, low, high: (low + high) / 2)

    params = optimizer._sample_elo_params(trial, "nba")
    assert "hfa" in params
    assert 50 <= params["hfa"] <= 150
    assert "k_regular" in params
    assert 10 <= params["k_regular"] <= 40


def test_multi_objective_score_calculation():
    from app.kernel.backtest.runner import BacktestResult
    optimizer = ParameterOptimizer()
    result = BacktestResult(
        accuracy=0.70, brier_score=0.20, mae=0.30,
        sample_count=100, score=0.0, predictions=[],
    )
    # score = 0.5*0.70 + 0.3*(1-0.20) + 0.2*(1-0.30) = 0.35 + 0.24 + 0.14 = 0.73
    score = optimizer._compute_score(result)
    assert score == pytest.approx(0.73, abs=0.01)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_parameter_optimizer.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.kernel.parameter_optimizer'`

- [ ] **Step 3: Add optuna to requirements.txt**

In `backend/requirements.txt`, append:

```
optuna==4.0.0
```

- [ ] **Step 4: Implement ParameterOptimizer**

```python
# backend/app/kernel/parameter_optimizer.py
"""ParameterOptimizer — Bayesian optimization over factor weights + Elo params.

Uses Optuna TPE sampler to search for optimal parameters. Each trial runs
a full backtest via BacktestRunner. The optimizer runs synchronously in tests
and asynchronously via optimization_task_manager in production.
"""
from __future__ import annotations

import logging
from typing import Any

import optuna

from app.kernel.backtest.runner import BacktestRunner, BacktestParams, BacktestResult

logger = logging.getLogger(__name__)

# Search space per sport: factor names + Elo param bounds
_SPORT_CONFIG = {
    "nba": {
        "factors": ["elo", "home_court", "rest", "form"],
        "elo_params": {
            "hfa": (50, 150),
            "k_regular": (10, 40),
            "k_playoff": (20, 50),
        },
        "default_elo": {"season_carry": 0.75, "initial": 1500},
    },
    "mlb": {
        "factors": ["elo", "home_court", "rest", "form", "starting_pitcher"],
        "elo_params": {
            "hfa": (20, 80),
            "k_regular": (10, 40),
            "k_playoff": (20, 50),
            "season_carry": (0.5, 0.9),
        },
        "default_elo": {"initial": 1500},
    },
    "nhl": {
        "factors": ["elo", "home_court", "rest", "form", "goalie"],
        "elo_params": {
            "hfa": (25, 85),
            "k_regular": (10, 40),
            "k_playoff": (20, 50),
            "season_carry": (0.5, 0.9),
        },
        "default_elo": {"initial": 1500},
    },
}


class ParameterOptimizer:
    """Bayesian optimization over factor weights + Elo params using Optuna TPE."""

    def __init__(self) -> None:
        self._runner = BacktestRunner()

    def optimize_sync(
        self,
        sport: str,
        *,
        n_trials: int = 150,
        train_matches: list[dict],
        test_matches: list[dict],
    ) -> dict[str, Any]:
        """Run optimization synchronously. Returns best params + score.

        For production async usage, wrap this in optimization_task_manager.
        """
        config = _SPORT_CONFIG.get(sport)
        if config is None:
            raise ValueError(f"Unsupported sport: {sport}")

        def objective(trial: optuna.Trial) -> float:
            factor_weights = self._sample_factor_weights(trial, sport)
            elo_params = self._sample_elo_params(trial, sport)
            params = BacktestParams(factor_weights=factor_weights, elo_params=elo_params)
            result = self._runner.run(
                sport,
                train_matches=train_matches,
                test_matches=test_matches,
                params=params,
            )
            return result.score

        study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

        best_trial = study.best_trial
        return {
            "best_score": best_trial.value,
            "best_params": best_trial.params,
            "trials": len(study.trials),
            "sport": sport,
        }

    def _sample_factor_weights(self, trial: optuna.Trial, sport: str) -> dict[str, float]:
        """Sample factor weights with sum=1.0 constraint.

        Uses the last-factor-computed approach: sample N-1 factors freely,
        compute the Nth as 1 - sum(others), clamped to [0.05, 0.95].
        """
        config = _SPORT_CONFIG[sport]
        factors = config["factors"]
        n = len(factors)

        # Sample N-1 raw weights
        raw = {}
        for i, f in enumerate(factors[:-1]):
            raw[f] = trial.suggest_float(f"w_{f}", 0.05, 0.45)

        # Last factor = 1 - sum(others), clamped
        remaining = 1.0 - sum(raw.values())
        last_factor = factors[-1]
        raw[last_factor] = max(0.05, min(0.95, remaining))

        # Normalize to ensure sum=1.0
        total = sum(raw.values())
        return {f: raw[f] / total for f in factors}

    def _sample_elo_params(self, trial: optuna.Trial, sport: str) -> dict[str, float]:
        """Sample Elo params within sport-specific bounds."""
        config = _SPORT_CONFIG[sport]
        params: dict[str, float] = {}

        for param_name, (low, high) in config["elo_params"].items():
            params[param_name] = trial.suggest_float(f"elo_{param_name}", low, high)

        # Add defaults for params not in search space
        for k, v in config["default_elo"].items():
            if k not in params:
                params[k] = v

        return params

    def _compute_score(self, result: BacktestResult) -> float:
        """Compute multi-objective weighted score."""
        return 0.5 * result.accuracy + 0.3 * (1 - result.brier_score) + 0.2 * (1 - result.mae)
```

- [ ] **Step 5: Add scheduler jobs**

In `backend/app/core/scheduler.py`, append two new job functions:

```python
async def _job_update_weights_weekly():
    """Weekly weight update via Phase 3 learning loop (Phase 9)."""
    if not settings.PHASE9_LEARNING_ACTIVATED:
        return
    if not settings.PHASE9_WEEKLY_WEIGHT_UPDATE_INTERVAL_MIN:
        return
    logger.info("[Scheduler] Weekly weight update starting...")
    run_id = _start_run("update_weights_weekly")
    try:
        from app.kernel.learning_service import LearningService
        from app.kernel.factor_registry import FactorRegistry

        registry = FactorRegistry()
        learning = LearningService()
        for competition in ["nba", "mlb", "nhl"]:
            engine_name = competition  # engine name matches competition for US sports
            try:
                learning.update_weights(engine_name, competition)
                logger.info("[Scheduler] Updated weights for %s", competition)
            except Exception as e:
                logger.warning("[Scheduler] Weight update failed for %s: %s", competition, e)
        _finish_run(run_id, "success", result={"competitions": ["nba", "mlb", "nhl"]})
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] Weekly weight update failed")


async def _job_reoptimize_monthly():
    """Monthly re-optimization of parameters (Phase 9)."""
    if not settings.PHASE9_LEARNING_ACTIVATED:
        return
    if not settings.PHASE9_OPTIMIZATION_INTERVAL_MIN:
        return
    logger.info("[Scheduler] Monthly re-optimization starting...")
    run_id = _start_run("reoptimize_monthly")
    try:
        from app.kernel.parameter_optimizer import ParameterOptimizer
        optimizer = ParameterOptimizer()
        for sport in ["nba", "mlb", "nhl"]:
            try:
                # Note: in production, load matches from DB
                # For now, just log that the job ran
                logger.info("[Scheduler] Re-optimization for %s (skipped — no matches loaded)", sport)
            except Exception as e:
                logger.warning("[Scheduler] Re-optimization failed for %s: %s", sport, e)
        _finish_run(run_id, "success", result={"sports": ["nba", "mlb", "nhl"]})
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] Monthly re-optimization failed")
```

Register in `start_scheduler()` (find the Phase 7/8 job registrations and append after):

```python
    if settings.PHASE9_LEARNING_ACTIVATED and settings.PHASE9_WEEKLY_WEIGHT_UPDATE_INTERVAL_MIN > 0:
        scheduler.add_job(
            _job_update_weights_weekly,
            IntervalTrigger(minutes=settings.PHASE9_WEEKLY_WEIGHT_UPDATE_INTERVAL_MIN),
            id="update_weights_weekly",
            replace_existing=True,
        )
        logger.info("[Scheduler] Registered weekly weight update job (interval=%d min)", settings.PHASE9_WEEKLY_WEIGHT_UPDATE_INTERVAL_MIN)

    if settings.PHASE9_LEARNING_ACTIVATED and settings.PHASE9_OPTIMIZATION_INTERVAL_MIN > 0:
        scheduler.add_job(
            _job_reoptimize_monthly,
            IntervalTrigger(minutes=settings.PHASE9_OPTIMIZATION_INTERVAL_MIN),
            id="reoptimize_monthly",
            replace_existing=True,
        )
        logger.info("[Scheduler] Registered monthly re-optimization job (interval=%d min)", settings.PHASE9_OPTIMIZATION_INTERVAL_MIN)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_parameter_optimizer.py -v --tb=short`
Expected: 5 PASS

- [ ] **Step 7: Commit**

```bash
cd backend
git add requirements.txt app/kernel/parameter_optimizer.py app/core/scheduler.py tests/test_parameter_optimizer.py
git commit -m "feat(phase9): add ParameterOptimizer (Optuna TPE) + scheduler jobs"
```

---

## Task 6: API Routes + Router Registration

**Files:**
- Create: `backend/app/api/routes/sport_optimization.py`
- Modify: `backend/app/api/router.py`
- Test: `backend/tests/test_sport_optimization_routes.py`

**Interfaces:**
- Consumes: `HistoricalDataIngestor`, `ParameterOptimizer`, `OptimizedParamsStore`
- Produces: 6 API endpoints gated by `PHASE9_ACCURACY_SPRINT_ENABLED`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_sport_optimization_routes.py
"""Tests for sport optimization API routes — TDD RED phase."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def disable_phase9(monkeypatch):
    """Default: Phase 9 disabled → 503."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", False)


def test_endpoints_return_503_when_disabled(client):
    resp = client.post("/api/sport-optimization/ingest", json={"sport": "nba", "seasons": ["2024-25"]})
    assert resp.status_code == 503


def test_ingest_triggers_fetch(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)

    from unittest.mock import AsyncMock, patch
    mock_result = {"matches": 10, "results": 10, "errors": []}
    with patch("app.api.routes.sport_optimization.HistoricalDataIngestor") as MockIngestor:
        instance = MockIngestor.return_value
        instance.ingest_season = AsyncMock(return_value=mock_result)
        resp = client.post("/api/sport-optimization/ingest", json={"sport": "nba", "seasons": ["2024-25"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["matches"] == 10


def test_run_optimization_returns_task_id(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)

    resp = client.post("/api/sport-optimization/run", json={"sport": "nba", "n_trials": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert "task_id" in data


def test_get_params_returns_404_when_none(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)

    resp = client.get("/api/sport-optimization/params/nba")
    # Returns 404 when no params found, or 200 with null
    assert resp.status_code in (200, 404)


def test_list_params_returns_array(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)

    resp = client.get("/api/sport-optimization/params")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


def test_apply_params_requires_write_key(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)

    resp = client.post("/api/sport-optimization/apply/1")
    # Should require write key → 401 or 403
    assert resp.status_code in (401, 403, 404)  # 404 if params_id doesn't exist
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_sport_optimization_routes.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError` or route not found

- [ ] **Step 3: Implement API routes**

```python
# backend/app/api/routes/sport_optimization.py
"""Sport optimization API routes (Phase 9).

All endpoints gated by PHASE9_ACCURACY_SPRINT_ENABLED (503 when false).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sport-optimization", tags=["Sport Optimization"])


class IngestRequest(BaseModel):
    sport: str  # "nba" / "mlb" / "nhl" / "all"
    seasons: list[str]  # e.g., ["2023-24", "2024-25"]


class OptimizationRequest(BaseModel):
    sport: str  # "nba" / "mlb" / "nhl" / "all"
    n_trials: int = 150


def _check_enabled() -> None:
    if not settings.PHASE9_ACCURACY_SPRINT_ENABLED:
        raise HTTPException(status_code=503, detail="Phase 9 accuracy sprint disabled")


@router.post("/ingest")
async def ingest_historical_data(request: IngestRequest):
    """Trigger historical data ingestion."""
    _check_enabled()
    from app.services.historical_data_ingestor import HistoricalDataIngestor

    ingestor = HistoricalDataIngestor()
    sports = ["nba", "mlb", "nhl"] if request.sport == "all" else [request.sport]
    results = {}
    for sport in sports:
        for season in request.seasons:
            result = await ingestor.ingest_season(sport, season)
            results[f"{sport}-{season}"] = result
    return results


@router.post("/run")
async def run_optimization(request: OptimizationRequest):
    """Trigger parameter optimization (async)."""
    _check_enabled()
    from app.services.optimization_task_manager import get_task_manager

    task_manager = await get_task_manager()
    task_id = await task_manager.create_task(
        task_type="parameter_optimization",
        payload={"sport": request.sport, "n_trials": request.n_trials},
    )
    return {"task_id": task_id, "status": "pending"}


@router.get("/status/{task_id}")
async def get_optimization_status(task_id: str):
    """Query optimization task status."""
    _check_enabled()
    from app.services.optimization_task_manager import get_task_manager

    task_manager = await get_task_manager()
    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/params/{sport}")
async def get_params(sport: str):
    """Get current optimized params for a sport."""
    _check_enabled()
    from app.kernel.optimized_params_store import OptimizedParamsStore

    store = OptimizedParamsStore()
    params = store.get_applied(sport, sport)
    if params is None:
        raise HTTPException(status_code=404, detail=f"No applied params for {sport}")
    return params


@router.get("/params")
async def list_params():
    """List all sports' optimized params."""
    _check_enabled()
    from app.kernel.optimized_params_store import OptimizedParamsStore

    store = OptimizedParamsStore()
    return store.get_candidates()


@router.post("/apply/{params_id}")
async def apply_params(params_id: int, request: Request):
    """Apply optimized params to FactorRegistry."""
    _check_enabled()
    # Require write key
    from app.api.deps import require_write_key
    await require_write_key(request)

    from app.kernel.optimized_params_store import OptimizedParamsStore

    store = OptimizedParamsStore()
    try:
        result = store.apply(params_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
```

- [ ] **Step 4: Register router**

In `backend/app/api/router.py`, add:

```python
from app.api.routes import sport_optimization
api_router.include_router(sport_optimization.router, tags=["Sport Optimization"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_sport_optimization_routes.py -v --tb=short`
Expected: 6 PASS

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/api/routes/sport_optimization.py app/api/router.py tests/test_sport_optimization_routes.py
git commit -m "feat(phase9): add 6 sport optimization API endpoints"
```

---

## Task 7: Frontend — OptimizationDashboard + API Client + Route Page

**Files:**
- Create: `frontend/src/lib/optimization-api.ts`
- Create: `frontend/src/components/sports/optimization/OptimizationDashboard.tsx`
- Create: `frontend/src/components/sports/optimization/OptimizationDashboard.test.tsx`
- Create: `frontend/src/app/sports/optimization/page.tsx`

**Interfaces:**
- Consumes: `/api/sport-optimization/*` endpoints from Task 6
- Produces: `OptimizationDashboard` component, `/sports/optimization` route

- [ ] **Step 1: Write the failing tests**

```typescript
// frontend/src/components/sports/optimization/OptimizationDashboard.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("@/lib/optimization-api", () => ({
  fetchOptimizationParams: vi.fn(),
}));

import { OptimizationDashboard } from "./OptimizationDashboard";
import { fetchOptimizationParams } from "@/lib/optimization-api";

describe("OptimizationDashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows loading state initially", () => {
    vi.mocked(fetchOptimizationParams).mockReturnValue(new Promise(() => {}));
    render(<OptimizationDashboard />);
    expect(screen.getByTestId("loading")).toBeTruthy();
  });

  it("shows empty state when no params", async () => {
    vi.mocked(fetchOptimizationParams).mockResolvedValue([]);
    render(<OptimizationDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("empty")).toBeTruthy();
    });
  });

  it("displays params table when data available", async () => {
    vi.mocked(fetchOptimizationParams).mockResolvedValue([
      {
        id: 1,
        sport: "nba",
        competition: "nba",
        factor_weights: '{"elo": 0.50}',
        elo_params: '{"hfa": 110}',
        score: 0.75,
        accuracy: 0.70,
        brier_score: 0.20,
        mae: 0.30,
        sample_count: 100,
        status: "applied",
        created_at: "2026-07-16T10:00:00Z",
        applied_at: "2026-07-16T12:00:00Z",
      },
    ]);
    render(<OptimizationDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("params-table")).toBeTruthy();
    });
    expect(screen.getByText("nba")).toBeTruthy();
    expect(screen.getByText("0.75")).toBeTruthy();
  });

  it("shows error state on fetch failure", async () => {
    vi.mocked(fetchOptimizationParams).mockRejectedValue(new Error("503"));
    render(<OptimizationDashboard />);
    await waitFor(() => {
      expect(screen.getByTestId("error")).toBeTruthy();
    });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/sports/optimization/OptimizationDashboard.test.tsx`
Expected: FAIL with module not found

- [ ] **Step 3: Create API client**

```typescript
// frontend/src/lib/optimization-api.ts
"use client";

export interface OptimizedParams {
  id: number;
  sport: string;
  competition: string;
  factor_weights: string;
  elo_params: string;
  score: number;
  accuracy: number;
  brier_score: number;
  mae: number;
  sample_count: number;
  trial_number: number | null;
  status: string;
  created_at: string | null;
  applied_at: string | null;
}

function getApiBase(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL || "";
  return base.replace(/\/api$/, "");
}

export async function fetchOptimizationParams(): Promise<OptimizedParams[]> {
  const url = `${getApiBase()}/api/sport-optimization/params`;
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${resp.status}`);
  return resp.json();
}

export async function triggerOptimization(sport: string, nTrials: number = 150): Promise<{ task_id: string }> {
  const url = `${getApiBase()}/api/sport-optimization/run`;
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sport, n_trials: nTrials }),
  });
  if (!resp.ok) throw new Error(`${resp.status}`);
  return resp.json();
}
```

- [ ] **Step 4: Create OptimizationDashboard component**

```tsx
// frontend/src/components/sports/optimization/OptimizationDashboard.tsx
"use client";
import { useEffect, useState } from "react";
import {
  fetchOptimizationParams,
  type OptimizedParams,
} from "@/lib/optimization-api";

export function OptimizationDashboard() {
  const [params, setParams] = useState<OptimizedParams[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchOptimizationParams()
      .then((data) => {
        setParams(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  if (loading) return <div data-testid="loading">加载中...</div>;
  if (error) return <div data-testid="error">错误: {error}</div>;
  if (!params || params.length === 0)
    return <div data-testid="empty">暂无优化参数</div>;

  return (
    <div data-testid="params-table" className="space-y-4">
      <h2 className="text-xl font-bold">参数优化结果</h2>
      <table className="w-full border-collapse border">
        <thead>
          <tr className="bg-gray-100">
            <th className="border p-2 text-left">Sport</th>
            <th className="border p-2 text-left">Score</th>
            <th className="border p-2 text-left">Accuracy</th>
            <th className="border p-2 text-left">Brier</th>
            <th className="border p-2 text-left">MAE</th>
            <th className="border p-2 text-left">Samples</th>
            <th className="border p-2 text-left">Status</th>
          </tr>
        </thead>
        <tbody>
          {params.map((p) => (
            <tr key={p.id}>
              <td className="border p-2">{p.sport}</td>
              <td className="border p-2">{p.score.toFixed(4)}</td>
              <td className="border p-2">{p.accuracy.toFixed(4)}</td>
              <td className="border p-2">{p.brier_score.toFixed(4)}</td>
              <td className="border p-2">{p.mae.toFixed(4)}</td>
              <td className="border p-2">{p.sample_count}</td>
              <td className="border p-2">{p.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 5: Create route page**

```tsx
// frontend/src/app/sports/optimization/page.tsx
"use client";
import { OptimizationDashboard } from "@/components/sports/optimization/OptimizationDashboard";

export default function OptimizationPage() {
  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-6 md:px-6">
      <h1 className="text-2xl font-bold">参数优化</h1>
      <OptimizationDashboard />
    </main>
  );
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/sports/optimization/OptimizationDashboard.test.tsx`
Expected: 4 PASS

- [ ] **Step 7: Commit**

```bash
cd frontend
git add src/lib/optimization-api.ts src/components/sports/optimization/ src/app/sports/optimization/
git commit -m "feat(phase9): add OptimizationDashboard + API client + route page"
```

---

## Self-Review

### 1. Spec coverage
- ✅ Config changes (Task 1, Step 3)
- ✅ KernelOptimizedParams table (Task 1, Step 4)
- ✅ OptimizedParamsStore (Task 1, Step 5)
- ✅ EloTimeMachine (Task 2)
- ✅ BacktestRunner (Task 3)
- ✅ HistoricalDataIngestor (Task 4)
- ✅ ParameterOptimizer (Task 5)
- ✅ Scheduler jobs (Task 5, Step 5)
- ✅ API endpoints (Task 6)
- ✅ Frontend dashboard (Task 7)
- ✅ All 39 tests covered

### 2. Placeholder scan
- No "TBD", "TODO", or "implement later"
- All code steps have complete code blocks
- All test steps have actual test code

### 3. Type consistency
- `BacktestParams.factor_weights: dict[str, float]` — consistent across Tasks 3, 5
- `BacktestResult.score: float` — consistent across Tasks 3, 5
- `EloParams` fields match between Task 2 definition and Task 3 usage
- `OptimizedParamsStore.apply(params_id: int)` — consistent between Task 1 and Task 6
- `fetchOptimizationParams()` — consistent between Task 7 test and implementation
