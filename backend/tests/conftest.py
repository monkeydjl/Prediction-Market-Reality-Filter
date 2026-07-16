"""Auto-reset module-level singletons before each test.

kernel_db and connection_manager both use module-level singletons that
persist across tests. Without this fixture, a test that calls
init_kernel_db(path_A) without cleanup will cause the next test's
init_kernel_db(path_B) to be a no-op (singleton returns early when
_engine is not None), silently operating on path_A's database instead
of path_B's. Similarly, connection_manager's _connection_manager
singleton leaks across tests if not reset.

The autouse fixture calls close_kernel_db() and resets
connection_manager before AND after each test, ensuring every test
starts with clean module state.

This is non-invasive: no existing test files need modification. The fixture
applies to ALL tests in backend/tests/ automatically.
"""
import pytest

from app.kernel.kernel_db import close_kernel_db


@pytest.fixture(autouse=True)
def _reset_module_singletons():
    """Reset kernel_db and connection_manager module-level singletons around each test."""
    close_kernel_db()
    # Reset connection_manager singleton (Phase 10)
    import app.realtime.connection_manager as cm_mod
    cm_mod._connection_manager = None
    yield
    close_kernel_db()
    cm_mod._connection_manager = None
