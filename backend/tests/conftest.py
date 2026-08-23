"""Test environment isolation for backend pytest suite.

This conftest ensures deterministic, hermetic test runs by:
1. Blocking dotenv from loading backend/.env during tests.
2. Redirecting all file-path settings (event store, databases, caches) to a
   session-scoped temp directory so no test reads real production data.
3. Snapshotting os.environ and restoring it after every test.
4. Resetting every module-level singleton/cache in ``app/`` around each test,
   driven by the ``_SINGLETON_RESETS`` table below.  Anything deliberately left
   alone is listed in ``_RESET_EXEMPT`` with a written reason;
   ``tests/test_singleton_reset_census.py`` asserts the two tables partition the
   real set of stateful globals exactly, so a new one cannot escape silently.
5. Snapshotting and restoring app.core.config.settings instance state.

Tests that explicitly need real dotenv behavior (e.g.
test_config_env_loading.py) can restore the real load_dotenv via
``_real_load_dotenv`` exported from this module.
"""
import os
import sys
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


# ---------------------------------------------------------------------------
# The singleton reset table -- the single source of truth.
#
# ``_reset_all_singletons()`` below drives off this table, and
# ``tests/test_singleton_reset_census.py`` asserts that this table plus
# ``_RESET_EXEMPT`` accounts for every module-level mutable global in ``app/``,
# exactly and in both directions.  Adding a stateful global to ``app/`` without
# touching one of the two tables fails that test instead of quietly escaping
# isolation and waiting for a collection-order change to surface it.
#
# Each row is ``(module path, global name, how to reset)``:
#   "none"        -> rebind the global to None
#   "clear"       -> call ``.clear()`` on the container in place
#   "cache_clear" -> functools cache: call ``.cache_clear()``
#   anything else -> the name of a zero-arg reset helper on that module to call.
#                    Prefer this: the module owns what "reset" means, and a
#                    helper poked from here instead goes dead -- which is why
#                    reset_llm_gateway_clients_for_tests had zero callers while
#                    this file re-implemented its body against the private dict.
# ---------------------------------------------------------------------------
_SINGLETON_RESETS: tuple[tuple[str, str, str], ...] = (
    # --- databases --------------------------------------------------------
    ("app.kernel.kernel_db", "_engine", "close_kernel_db"),
    ("app.kernel.kernel_db", "_SessionLocal", "close_kernel_db"),
    ("app.utils.prediction_db", "_engine", "none"),
    # _SessionLocal used to be left behind while _engine next door was nulled.
    # get_prediction_session() guards only on _SessionLocal, so the stale
    # sessionmaker kept handing out sessions bound to the discarded engine.
    # kernel_db.close_kernel_session() nulls both; match it.
    ("app.utils.prediction_db", "_SessionLocal", "none"),

    # --- long-lived process objects ---------------------------------------
    ("app.realtime.connection_manager", "_connection_manager", "none"),
    ("app.core.scheduler", "_scheduler_lock_handle", "none"),
    ("app.core.scheduler", "_RUN_TO_JOB", "clear"),
    ("app.services.world_cup_ai_optimization_service", "_ai_semaphore", "none"),
    ("app.services.gnews_service", "_gnews_client", "none"),
    ("app.services.llm_gateway_service", "_client_cache",
     "reset_llm_gateway_clients_for_tests"),

    # --- alert de-duplication cooldowns -----------------------------------
    ("app.services.drift_alert_dispatcher", "_last_dispatched",
     "_reset_cooldown_state"),
    ("app.services.scheduler_failure_alert_dispatcher", "_last_dispatched",
     "_reset_cooldown_state"),

    # --- provider quota / response caches ---------------------------------
    ("app.services.odds_api_service", "_quota_remaining", "none"),
    ("app.services.odds_api_service", "_quota_last_checked", "none"),
    ("app.services.event_audit_service", "_HISTORY_CACHE",
     "invalidate_history_cache"),
    ("app.services.football_live_availability_service", "_SNAPSHOT_CACHE",
     "clear_live_availability_cache"),
    ("app.services.football_live_injury_service", "_SNAPSHOT_CACHE",
     "clear_live_injury_cache"),
    ("app.services.football_live_referee_service", "_SNAPSHOT_CACHE",
     "clear_live_referee_cache"),
    ("app.services.football_live_schedule_service", "_SNAPSHOT_CACHE",
     "clear_live_schedule_cache"),
    ("app.services.football_live_style_service", "_SNAPSHOT_CACHE",
     "clear_live_style_cache"),
    ("app.services.football_live_weather_service", "_READING_CACHE",
     "clear_secondary_weather_cache"),
    ("app.services.football_live_xg_service", "_SNAPSHOT_CACHE",
     "clear_live_xg_cache"),
    ("app.services.market_totals_service", "_SNAPSHOT_CACHE",
     "clear_market_totals_cache"),
    ("app.services.mlb_live_park_service", "_SNAPSHOT_CACHE",
     "clear_live_park_cache"),
    ("app.services.nba_live_injury_service", "_SNAPSHOT_CACHE",
     "clear_live_injury_cache"),
    ("app.services.nba_live_ratings_service", "_SNAPSHOT_CACHE",
     "clear_live_ratings_cache"),
    ("app.services.nhl_live_xg_service", "_SNAPSHOT_CACHE",
     "clear_live_5v5_cache"),
    ("app.sports.football.football_weather", "_LIVE_CACHE",
     "_clear_live_weather_cache"),
    ("app.services.world_cup_weather_service", "_weather_cache", "clear"),
    ("app.api.routes.world_cup_analytics", "_TOURNAMENT_CACHE", "clear"),
    ("app.api.routes.world_cup_analytics", "_TOURNAMENT_CACHE_TIME", "clear"),

    # --- caches over files or env vars a test can rewrite ------------------
    # Each of these memoizes a value derived from a settings path or an env var
    # that conftest restores per test.  The cache has no key for that input, so
    # without a clear the restored env is a lie the second time around.
    ("app.kernel.engines.btd_model", "_load_params", "cache_clear"),
    ("app.kernel.engines.dixon_coles_engine", "_load_rho", "cache_clear"),
    ("app.services.world_cup_engines.world_cup_btd_model", "_load_params",
     "cache_clear"),
    ("app.services.world_cup_engines.world_cup_rule_engine", "_load_rho",
     "cache_clear"),
    ("app.services.world_cup_historical_results", "_load_results",
     "cache_clear"),
    ("app.services.world_cup_openfootball_data", "_load_json_file",
     "clear_openfootball_cache"),
)

# Module-level state deliberately NOT reset, each with the reason.  The census
# test requires a non-empty reason here for anything it finds that this file
# does not reset -- "we forgot" cannot masquerade as "we decided".
_RESET_EXEMPT: dict[str, str] = {
    # Import-time registries.  The migration decorators populate these once at
    # import; clearing one deletes the schema history, and the next store call
    # would then create a table with no migrations applied.
    "app.memory.decision_timeline_store._MIGRATIONS": "import-time migration registry",
    "app.memory.domain_reliability_store._MIGRATIONS": "import-time migration registry",
    "app.memory.event_market_link_store._MIGRATIONS": "import-time migration registry",
    "app.memory.llm_daily_spend_store._MIGRATIONS": "import-time migration registry",
    "app.memory.loop_run_store._MIGRATIONS": "import-time migration registry",
    "app.memory.optimization_task_store._MIGRATIONS": "import-time migration registry",
    "app.memory.review_queue_store._MIGRATIONS": "import-time migration registry",
    "app.memory.simulated_trade_store._MIGRATIONS": "import-time migration registry",
    "app.memory.source_trust_registry_store._MIGRATIONS": "import-time migration registry",

    # Path-keyed memo of an idempotent CREATE TABLE IF NOT EXISTS.  A test that
    # wants a fresh schema points the store at a unique tempfile path (see
    # test_domain_reliability_store._TempDBMixin), so a stale hit cannot occur;
    # clearing would re-run DDL on every test that touches a store.  It grows
    # with dead paths within a session, bounded by the test count.
    "app.memory.decision_timeline_store._INITIALIZED": "path-keyed idempotent-DDL memo",
    "app.memory.domain_reliability_store._INITIALIZED": "path-keyed idempotent-DDL memo",
    "app.memory.event_market_link_store._INITIALIZED": "path-keyed idempotent-DDL memo",
    "app.memory.llm_daily_spend_store._INITIALIZED": "path-keyed idempotent-DDL memo",
    "app.memory.prediction_store._INITIALIZED": "path-keyed idempotent-DDL memo",
    "app.memory.review_queue_store._INITIALIZED": "path-keyed idempotent-DDL memo",
    "app.memory.simulated_trade_store._INITIALIZED": "path-keyed idempotent-DDL memo",
    "app.memory.source_trust_registry_store._INITIALIZED": "path-keyed idempotent-DDL memo",

    # Caches over static in-repo constants.  Nothing a test can write changes
    # the answer, so clearing only costs the rebuild.
    "app.sports.football.football_style._folded_index":
        "folds the static _TEAM_STYLE constant; operators update it by PR",
    "app.sports._shared.team_aliases._alias_index":
        "folds the static alias registry, keyed on competition",
    "app.services.world_cup_engines.world_cup_gbm_engine._load_models":
        "loads LightGBM boosters from disk; its own test file clears it when it "
        "swaps the model files",

    # Deliberately empty constant, not runtime state.
    "app.sports.football.adapters.epl_adapter._STAGE_MAP":
        "EPL has no knockout stage, so the map is empty by design",

    # Clearing these would corrupt live resources rather than isolate them.
    "app.utils.file_store._LOCKS":
        "path -> RLock registry; dropping an entry hands a DIFFERENT lock for a "
        "path another thread is already holding",
    "app.utils.file_store._HELD_CROSS_PROCESS":
        "tracks open OS file handles; clearing leaks them",
    "app.utils.background_tasks._PENDING":
        "strong references that keep in-flight tasks from being garbage "
        "collected mid-run; never observed dirty at teardown",
}


def _reset_all_singletons():
    """Reset every module-level mutable singleton/cache listed in the table.

    Only modules already in ``sys.modules`` are touched: a module that was never
    imported cannot hold dirty state, and importing all 30-odd of them on every
    test would drag the whole live-provider surface into every run.
    """
    for module_path, attr, action in _SINGLETON_RESETS:
        module = sys.modules.get(module_path)
        if module is None:
            continue
        if action == "none":
            setattr(module, attr, None)
        elif action == "clear":
            getattr(module, attr).clear()
        elif action == "cache_clear":
            getattr(module, attr).cache_clear()
        else:
            getattr(module, action)()

    # Not a module-level global, so it cannot live in the table above: the
    # PredictionKernel singleton is stashed as an attribute ON the _get_kernel
    # function.  It survived every teardown while conftest closed the kernel DB
    # out from under the FactorRegistry the cached kernel still held.
    predictions_mod = sys.modules.get("app.api.routes.predictions")
    if predictions_mod is not None:
        predictions_mod.reset_kernel_singleton()


@pytest.fixture
def clean_env(monkeypatch):
    """Helper fixture: provides monkeypatch with env already isolated.

    Usage in tests:
        def test_something(clean_env):
            clean_env.setenv("API_WRITE_KEY", "test-key")
            ...
    """
    return monkeypatch
