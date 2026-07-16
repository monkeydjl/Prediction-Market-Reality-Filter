# Phase 13: Pre-existing Test Isolation Fix — Design Spec

**Date:** 2026-07-16
**Status:** Approved (autonomous design per standing authorization)
**Predecessor:** Phase 12 (Futures/Championship Markets)

## 1. Goal

Fix the pre-existing test isolation bug in `backend/tests/` where the `kernel_db` module's global singletons (`_engine`, `_SessionLocal`) leak across test boundaries, causing tests to silently operate on the wrong database when a prior test fails to clean up.

## 2. Background

`backend/app/kernel/kernel_db.py` uses module-level singletons:
```python
_engine = None
_SessionLocal = None

def init_kernel_db(db_path: str | None = None) -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        return  # <-- singleton: silently reuses existing engine
    ...
```

The `close_kernel_db()` / `close_kernel_session()` function resets these to `None`. However, not all test fixtures call it consistently:

- **Well-isolated tests** (call `close` BEFORE `init` AND in teardown): `test_kernel_factor_registry.py`, `test_historical_data_ingestor.py`, `test_calibration_fusion_service.py`, `test_edge_db.py`, `test_edge_detector_service.py`, `test_edge_store.py`
- **Vulnerable tests** (call `init` without `close` before, only `close` in teardown): `test_engine_score_persistence.py`, `test_kernel_learning_service.py`, `test_learning_endpoints.py`, `test_factor_registry_persistence.py`, `test_kernel_db_fixtures.py`, `test_learning_dynamic_outcomes.py`, `test_learning_calibration.py`, `test_learning_weights.py`, `test_kernel_prediction_kernel.py`, `test_api_predictions.py`, `test_db_migration.py`

The vulnerable tests work today only because pytest happens to run well-isolated tests first (or the well-isolated tests' teardown cleans up before the vulnerable tests run). If test execution order changes (e.g., `pytest-randomly`, alphabetically different files, or parallel `pytest-xdist`), the vulnerable tests' `init_kernel_db(tmp_path)` calls will be no-ops, and they'll operate on whatever database the prior test left behind.

## 3. Non-Goals

- Fixing `prediction_db.py` global state — its tests mock `close_prediction_session` and don't use real DB paths, so no isolation issue exists.
- Modifying any existing test files — the fix is purely additive (new `conftest.py`).
- Fixing test isolation in frontend tests — Vitest already isolates by default.
- Adding `pytest-randomly` or `pytest-xdist` — out of scope; the fix prepares for them but doesn't add them.

## 4. Architecture

Create `backend/tests/conftest.py` with an **autouse** fixture that resets the `kernel_db` global state before each test. This is the standard pytest pattern for global-state isolation:

```python
# backend/tests/conftest.py
"""Auto-reset kernel_db global state before each test.

kernel_db uses module-level singletons (_engine, _SessionLocal) that persist
across tests. Without this fixture, a test that calls init_kernel_db(path_A)
without cleanup will cause the next test's init_kernel_db(path_B) to be a
no-op (singleton returns early), silently operating on path_A's database.

The autouse fixture calls close_kernel_db() before each test, ensuring every
test starts with _engine=None and _SessionLocal=None. Tests that need a DB
then call init_kernel_db(their_tmp_path) which correctly creates a fresh engine.
"""
import pytest

from app.kernel.kernel_db import close_kernel_db


@pytest.fixture(autouse=True)
def _reset_kernel_db_state():
    """Reset kernel_db module-level singletons before each test."""
    close_kernel_db()
    yield
    close_kernel_db()
```

The fixture is autouse, so it applies to ALL tests in `backend/tests/` without any test file modifications. The `close_kernel_db()` call before each test ensures `_engine` and `_SessionLocal` are `None`, so any subsequent `init_kernel_db(path)` call correctly creates a new engine bound to the specified path. The `close_kernel_db()` call after each test cleans up any state the test left behind.

## 5. Verification

After adding `conftest.py`:
1. All existing tests must still pass (no regressions)
2. Running tests in reverse alphabetical order must pass (proves isolation)
3. Running `test_kernel_learning_service.py` immediately after `test_kernel_factor_registry.py` must pass (both use `init_kernel_db` with different tmp_paths)

## 6. Success Criteria

1. `backend/tests/conftest.py` exists with the autouse fixture
2. All existing backend tests pass (zero regressions)
3. Tests pass when run in reverse order (`pytest --reverse` or `pytest -p no:randomly` with manual reversal)
4. No existing test files are modified

## 7. Phase Boundaries

### Zero-invasion (must NOT modify):
- All existing test files
- All source files (`backend/app/**/*.py`)
- All frontend files

### New files:
- `backend/tests/conftest.py`

## 8. Estimate

- 1 task, 0 new tests (verification by existing test suite), 1 new file (~15 lines)
