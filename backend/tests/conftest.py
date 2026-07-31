"""Test environment isolation for backend pytest suite.

This conftest ensures deterministic test runs by:
1. Blocking dotenv from loading backend/.env during tests (prevents
   environment-specific values from leaking into the test baseline).
2. Resetting module-level singletons (kernel_db, connection_manager)
   around each test.
3. Snapshotting and restoring app.core.config.settings so that any
   test mutating settings cannot affect subsequent tests.

Tests that explicitly need real dotenv behavior (e.g.
test_config_env_loading.py) can restore the real load_dotenv via the
``_real_load_dotenv`` reference exported from this module.
"""
import os

# ---------------------------------------------------------------------------
# CRITICAL: Patch dotenv BEFORE any app.* import.
# app.core.config calls _load_env_files() at module level (which calls
# load_dotenv), and it gets imported transitively via kernel_db below.
# ---------------------------------------------------------------------------
import dotenv as _dotenv_module

_real_load_dotenv = _dotenv_module.load_dotenv


def _noop_load_dotenv(*args, **kwargs):
    """No-op replacement for dotenv.load_dotenv during tests."""
    return False


_dotenv_module.load_dotenv = _noop_load_dotenv

# ---------------------------------------------------------------------------
# Now safe to import app modules (config.py's _load_env_files is neutered).
# ---------------------------------------------------------------------------
import pytest  # noqa: E402

from app.kernel.kernel_db import close_kernel_db  # noqa: E402


def pytest_configure(config):
    """Set asyncio_mode=auto so async tests work without per-file markers."""
    config.option.asyncio_mode = "auto"


@pytest.fixture(autouse=True)
def _reset_module_singletons():
    """Reset kernel_db, connection_manager, and settings around each test."""
    from app.core.config import settings

    # Snapshot settings state before the test.
    settings_snapshot = dict(settings.__dict__)

    close_kernel_db()
    import app.realtime.connection_manager as cm_mod
    cm_mod._connection_manager = None

    yield

    # Restore settings to pre-test state.
    settings.__dict__.clear()
    settings.__dict__.update(settings_snapshot)

    close_kernel_db()
    cm_mod._connection_manager = None
