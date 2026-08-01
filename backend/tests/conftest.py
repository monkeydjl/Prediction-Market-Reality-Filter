"""Test environment isolation for backend pytest suite.

This conftest ensures deterministic, hermetic test runs by:
1. Blocking dotenv from loading backend/.env during tests.
2. Redirecting all file-path settings (event store, databases, caches) to a
   session-scoped temp directory so no test reads real production data.
3. Snapshotting os.environ and restoring it after every test.
4. Resetting ALL known module-level singletons/caches around each test.
5. Snapshotting and restoring app.core.config.settings instance state.

Tests that explicitly need real dotenv behavior (e.g.
test_config_env_loading.py) can restore the real load_dotenv via
``_real_load_dotenv`` exported from this module.
"""
import os
import tempfile

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

# ---------------------------------------------------------------------------
# Redirect all file-path settings to a session temp directory.
# This modifies CLASS-level attributes on Settings so the defaults are
# hermetic.  Individual tests can still override via monkeypatch (instance
# attributes shadow class attributes) and the override is removed on teardown.
# ---------------------------------------------------------------------------
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="pmrf_test_data_")

from app.core.config import Settings as _SettingsClass  # noqa: E402

# Override class-level file path defaults to point to the temp dir.
_SettingsClass.EVENT_STORE_FILE = os.path.join(_TEST_DATA_DIR, "event_store.json")
_SettingsClass.EVENT_CACHE_FILE = os.path.join(_TEST_DATA_DIR, "event_cache.json")
_SettingsClass.LOOP_DB_FILE = os.path.join(_TEST_DATA_DIR, "v2_loop.db")
_SettingsClass.WORLD_CUP_PREDICTION_DB_FILE = os.path.join(_TEST_DATA_DIR, "world_cup_predictions.db")
_SettingsClass.DOMAIN_RELIABILITY_DB_PATH = os.path.join(_TEST_DATA_DIR, "domain_reliability.db")
_SettingsClass.WORLD_CUP_DATA_FILE = os.path.join(_TEST_DATA_DIR, "world_cup_data.json")
_SettingsClass.WORLD_CUP_SOURCE_BUNDLE_FILE = os.path.join(_TEST_DATA_DIR, "world_cup_source_bundle.json")
_SettingsClass.SPORTS_FACT_FILE = os.path.join(_TEST_DATA_DIR, "sports_facts.json")

# Also update the singleton instance so code that already imported settings
# sees the temp paths immediately.
from app.core.config import settings as _settings_instance  # noqa: E402
import app.core.config as _config_module  # noqa: E402

_settings_instance.EVENT_STORE_FILE = _SettingsClass.EVENT_STORE_FILE
_settings_instance.EVENT_CACHE_FILE = _SettingsClass.EVENT_CACHE_FILE
_settings_instance.LOOP_DB_FILE = _SettingsClass.LOOP_DB_FILE
_settings_instance.WORLD_CUP_PREDICTION_DB_FILE = _SettingsClass.WORLD_CUP_PREDICTION_DB_FILE
_settings_instance.DOMAIN_RELIABILITY_DB_PATH = _SettingsClass.DOMAIN_RELIABILITY_DB_PATH
_settings_instance.WORLD_CUP_DATA_FILE = _SettingsClass.WORLD_CUP_DATA_FILE
_settings_instance.WORLD_CUP_SOURCE_BUNDLE_FILE = _SettingsClass.WORLD_CUP_SOURCE_BUNDLE_FILE
_settings_instance.SPORTS_FACT_FILE = _SettingsClass.SPORTS_FACT_FILE

# Save the canonical settings object.  test_config.py uses importlib.reload()
# which replaces app.core.config.settings with a NEW object; other modules
# (security, routes) still reference the original.  We restore the original
# after every test so the object graph stays consistent.
_ORIGINAL_SETTINGS = _settings_instance

# Save the canonical app.main module state.  test_main_frontend_mount.py
# reloads app.main, creating a new app object and new settings reference.
import app.main as _main_module  # noqa: E402
_ORIGINAL_APP = _main_module.app


def _restore_config_module():
    """Ensure app.core.config.settings and app.main.app are the canonical
    objects (survives importlib.reload in test_config / test_main_frontend_mount)."""
    import app.core.config as cfg
    import app.main as main_mod
    # Restore the canonical settings singleton.
    cfg.settings = _ORIGINAL_SETTINGS
    # Restore the canonical app object and its settings reference.
    main_mod.app = _ORIGINAL_APP
    main_mod.settings = _ORIGINAL_SETTINGS
    # Re-apply class-level path overrides (reload redefines the class).
    cls = type(_ORIGINAL_SETTINGS)
    cls.EVENT_STORE_FILE = os.path.join(_TEST_DATA_DIR, "event_store.json")
    cls.EVENT_CACHE_FILE = os.path.join(_TEST_DATA_DIR, "event_cache.json")
    cls.LOOP_DB_FILE = os.path.join(_TEST_DATA_DIR, "v2_loop.db")
    cls.WORLD_CUP_PREDICTION_DB_FILE = os.path.join(_TEST_DATA_DIR, "world_cup_predictions.db")
    cls.DOMAIN_RELIABILITY_DB_PATH = os.path.join(_TEST_DATA_DIR, "domain_reliability.db")
    cls.WORLD_CUP_DATA_FILE = os.path.join(_TEST_DATA_DIR, "world_cup_data.json")
    cls.WORLD_CUP_SOURCE_BUNDLE_FILE = os.path.join(_TEST_DATA_DIR, "world_cup_source_bundle.json")
    cls.SPORTS_FACT_FILE = os.path.join(_TEST_DATA_DIR, "sports_facts.json")
    # Ensure instance paths are set.
    _ORIGINAL_SETTINGS.EVENT_STORE_FILE = cls.EVENT_STORE_FILE
    _ORIGINAL_SETTINGS.EVENT_CACHE_FILE = cls.EVENT_CACHE_FILE
    _ORIGINAL_SETTINGS.LOOP_DB_FILE = cls.LOOP_DB_FILE
    _ORIGINAL_SETTINGS.WORLD_CUP_PREDICTION_DB_FILE = cls.WORLD_CUP_PREDICTION_DB_FILE
    _ORIGINAL_SETTINGS.DOMAIN_RELIABILITY_DB_PATH = cls.DOMAIN_RELIABILITY_DB_PATH
    _ORIGINAL_SETTINGS.WORLD_CUP_DATA_FILE = cls.WORLD_CUP_DATA_FILE
    _ORIGINAL_SETTINGS.WORLD_CUP_SOURCE_BUNDLE_FILE = cls.WORLD_CUP_SOURCE_BUNDLE_FILE
    _ORIGINAL_SETTINGS.SPORTS_FACT_FILE = cls.SPORTS_FACT_FILE

# ---------------------------------------------------------------------------
# Session-level environment snapshot.
# ---------------------------------------------------------------------------
_SESSION_ENV: dict[str, str] = {}


def pytest_configure(config):
    """Set asyncio_mode=auto so async tests work without per-file markers."""
    config.option.asyncio_mode = "auto"


@pytest.fixture(autouse=True, scope="session")
def _session_env_snapshot():
    """Capture os.environ once at session start; restore at session end."""
    global _SESSION_ENV
    _SESSION_ENV = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(_SESSION_ENV)


@pytest.fixture(autouse=True)
def _reset_module_singletons():
    """Reset all known module-level singletons, caches, and settings."""
    from app.core.config import settings

    # --- Snapshot mutable state before the test ---
    settings_snapshot = dict(settings.__dict__)
    env_snapshot = dict(os.environ)

    # --- Pre-test resets ---
    close_kernel_db()
    _reset_all_singletons()

    yield

    # --- Post-test restores ---
    # Restore os.environ (environment bandit).
    os.environ.clear()
    os.environ.update(env_snapshot)

    # Restore config module integrity (handles importlib.reload in test_config).
    _restore_config_module()

    # Restore settings instance state.
    settings.__dict__.clear()
    settings.__dict__.update(settings_snapshot)

    # Reset singletons again so no test leaks state forward.
    close_kernel_db()
    _reset_all_singletons()


def _reset_all_singletons():
    """Reset every known module-level mutable singleton/cache."""
    # kernel_db engine
    close_kernel_db()

    # connection_manager
    import app.realtime.connection_manager as cm_mod
    cm_mod._connection_manager = None

    # prediction_db engine
    try:
        import app.utils.prediction_db as pdb_mod
        if getattr(pdb_mod, "_engine", None) is not None:
            pdb_mod._engine = None
    except Exception:
        pass

    # LLM client cache
    try:
        import app.services.llm_gateway_service as llm_mod
        llm_mod._client_cache.clear()
    except Exception:
        pass

    # Drift alert dispatcher dedup state
    try:
        import app.services.drift_alert_dispatcher as drift_mod
        drift_mod._last_dispatched.clear()
    except Exception:
        pass

    # Scheduler failure alert dispatcher dedup state
    try:
        import app.services.scheduler_failure_alert_dispatcher as sfad_mod
        sfad_mod._last_dispatched.clear()
    except Exception:
        pass

    # Weather cache
    try:
        import app.services.world_cup_weather_service as weather_mod
        weather_mod._weather_cache.clear()
    except Exception:
        pass

    # GNews client
    try:
        import app.services.gnews_service as gnews_mod
        gnews_mod._gnews_client = None
    except Exception:
        pass

    # Odds API quota state
    try:
        import app.services.odds_api_service as odds_mod
        odds_mod._quota_remaining = None
        odds_mod._quota_last_checked = None
    except Exception:
        pass

    # Scheduler lock handle
    try:
        import app.core.scheduler as sched_mod
        sched_mod._scheduler_lock_handle = None
    except Exception:
        pass

    # AI semaphore
    try:
        import app.services.world_cup_ai_optimization_service as ai_mod
        ai_mod._ai_semaphore = None
    except Exception:
        pass


@pytest.fixture
def clean_env(monkeypatch):
    """Helper fixture: provides monkeypatch with env already isolated.

    Usage in tests:
        def test_something(clean_env):
            clean_env.setenv("API_WRITE_KEY", "test-key")
            ...
    """
    return monkeypatch
