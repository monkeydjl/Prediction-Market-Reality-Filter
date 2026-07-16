"""Auto-reset kernel_db global state before each test.

kernel_db uses module-level singletons (_engine, _SessionLocal) that persist
across tests. Without this fixture, a test that calls init_kernel_db(path_A)
without cleanup will cause the next test's init_kernel_db(path_B) to be a
no-op (singleton returns early when _engine is not None), silently operating
on path_A's database instead of path_B's.

The autouse fixture calls close_kernel_db() before AND after each test,
ensuring every test starts with _engine=None and _SessionLocal=None. Tests
that need a DB then call init_kernel_db(their_tmp_path) which correctly
creates a fresh engine bound to the specified path.

This is non-invasive: no existing test files need modification. The fixture
applies to ALL tests in backend/tests/ automatically.
"""
import pytest

from app.kernel.kernel_db import close_kernel_db


@pytest.fixture(autouse=True)
def _reset_kernel_db_state():
    """Reset kernel_db module-level singletons (_engine, _SessionLocal) around each test."""
    close_kernel_db()
    yield
    close_kernel_db()
