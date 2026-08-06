# Backend Test Suite

Hermetic, deterministic pytest suite with zero network calls and session-isolated temp data.

## Running Tests

From the `backend/` directory:

```bash
python -m pytest tests
```

First-time setup — the suite needs the pinned dev dependencies, `pytest-asyncio` in particular (see [Asyncio](#asyncio)):

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
```

CI (`.github/workflows/ci.yml`, `backend-tests` job) runs, from `backend/`:

```bash
python -m compileall app tests
pytest tests/ --cov=app --cov-report=term-missing --cov-report=xml
```

alongside `ruff check app/`, `pip-audit -r requirements.txt`, and a non-blocking `mypy app/`.

**Result baseline (2026-08-05):** 3614 passed / 11 skipped / 0 failed, ~13 min on Windows / Python 3.11.9. CI pins Python 3.11.

## Test Isolation (conftest.py)

Every test runs in a hermetic bubble:

1. **`.env` blocked** — `load_dotenv` is patched to a no-op before any `app.*` import. Real environment loading is still accessible via `_real_load_dotenv` for tests that explicitly need it (e.g. `test_config_env_loading.py`).

2. **File paths redirected** — All settings pointing to databases, stores, or caches (`EVENT_STORE_FILE`, `LOOP_DB_FILE`, `WORLD_CUP_PREDICTION_DB_FILE`, etc.) are overridden at the `Settings` class level to point into a session-scoped `tempfile.mkdtemp()`. No test reads production data.

3. **`os.environ` snapshot/restore** — Captured once at session start, restored around every test. Prevents environment pollution between tests.

4. **Module singleton resets** — Every known mutable singleton is cleared before and after each test:
   - `kernel_db` engine (via `close_kernel_db()`)
   - `connection_manager._connection_manager`
   - `prediction_db._engine`
   - `llm_gateway_service._client_cache`
   - drift/scheduler alert dedup state
   - weather cache, GNews client, Odds API quota, scheduler lock, AI semaphore

5. **`settings` instance restore** — `app.core.config.settings` and `app.main.app` are canonical singleton objects. Tests that use `importlib.reload` (e.g. `test_config.py`, `test_main_frontend_mount.py`) replace them; the autouse fixture restores the originals so the object graph stays consistent.

The `_reset_module_singletons` fixture applies all of this automatically around every test via `autouse=True`.

## The `clean_env` Fixture

A convenience fixture that provides `monkeypatch` with the environment already isolated. Use it when your test needs to set env vars:

```python
def test_something(clean_env):
    clean_env.setenv("API_WRITE_KEY", "test-key")
    clean_env.setenv("FOOTBALL_LIVE_WEATHER_URL", "https://wx.test/forecast")
    # Test code here; os.environ is automatically restored after this test.
```

The fixture is defined in `conftest.py:247` and simply returns `monkeypatch` — the name documents its purpose. Note that `os.environ` is snapshotted and restored around *every* test by the autouse fixture, so a plain `monkeypatch` is equally safe; `clean_env` exists to make the intent explicit at the call site.

## Structure

```
tests/
├── conftest.py              # Test isolation (see above)
├── test_*.py                # 309 test files, collected by pytest
├── fixtures/                # Test data (e.g. fixtures/lol/sample_series.json)
└── manual/                  # Manual runner scripts (NOT pytest tests)
```

### Manual Scripts (`manual/`)

These are **not** part of the automated suite. They hit real network endpoints (Transfermarkt, The Odds API, OpenAI), burn API quota, or write to production databases. Each is a standalone `asyncio.run(main())` script with its own docstring. Run them individually:

```bash
python tests/manual/manual_<name>.py
```

See [`manual/README.md`](manual/README.md) for the full index.

## Test Coverage by Domain (top 15 prefixes)

| Prefix | Files | Examples |
|--------|-------|----------|
| `world_cup` | 42 | `test_world_cup_ai_engine.py`, `test_world_cup_analytics_routes.py` |
| `event` | 12 | `test_event_cache.py`, `test_event_category_service.py` |
| `kernel` | 11 | `test_kernel_btd_model.py`, `test_kernel_db_fixtures.py` |
| `football` | 8 | `test_football_data_client.py`, `test_football_weather.py` |
| `replay` | 7 | `test_replay_cli.py`, `test_replay_runner.py` |
| `quality` | 7 | `test_quality_alert_service.py`, `test_quality_alert_thresholds.py` |
| `market` | 7 | `test_market_filter_service.py`, `test_market_liquidity.py` |
| `lol` | 7 | `test_lol_adapter.py`, `test_lol_dry_run_import.py` |
| `decision` | 7 | `test_decision_diff_service.py`, `test_decision_quality_engine_integration.py` |
| `domain` | 6 | `test_domain_reliability_cli.py`, `test_domain_reliability_config.py` |
| `futures` | 5 | `test_futures_link_store.py`, `test_futures_market_service.py` |
| `prediction` | 5 | `test_prediction_calibration_service.py`, `test_prediction_history.py` |
| `scheduler` | 4 | `test_scheduler.py`, `test_scheduler_broadcast.py` |
| `polymarket` | 4 | `test_polymarket_event_source.py`, `test_polymarket_history_service.py` |
| `sports` | 3 | `test_sports_fact_service.py`, `test_sports_resolution_service.py` |

Counts are `test_<prefix>*.py` matches and overlap where one name prefixes another; they are a rough map of where coverage sits, not a partition of all 309 files.

## Asyncio

There is no `pytest.ini` / `pyproject.toml` in `backend/`. Asyncio mode is set programmatically in `conftest.py`:

```python
def pytest_configure(config):
    config.option.asyncio_mode = "auto"
```

Async test functions therefore work without an explicit `@pytest.mark.asyncio` decorator (20 files rely on this).

This requires **pytest-asyncio**, which lives in `requirements-dev.txt` — not `requirements.txt`. Installing a bare `pytest` is not enough: `pytest_configure` still succeeds (it just sets an unused attribute), collection still succeeds, and then every async test fails at call time with *"async def functions are not natively supported."* Install dev requirements before running the suite:

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
```

## Fixtures Directory

`fixtures/` holds test data for specific adapters. Currently:

- `fixtures/lol/sample_series.json` — consumed by `test_lol_adapter.py` and `test_lol_dry_run_import.py`.

Add new fixture data here when tests need static JSON/CSV input that is too large to inline.

## Key Test Patterns

**Patching settings:** Use `monkeypatch` or `unittest.mock.patch` at the module path:

```python
with patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_URL", "https://wx.test"):
    result = live_weather_for_match(match)
```

Patch at the module path that reads the value, not by mutating the shared `settings` object in place. The autouse fixture restores `settings.__dict__` after each test, so in-place mutation is not *unsafe* — but it is invisible to readers and leaks for the duration of the test.

**Stubbing network calls:** Patch `httpx.get`, `httpx.post`, `requests.get`, etc. at the module that imports them:

```python
with patch("app.services.odds_api_service.httpx.get", return_value=mock_response):
    ...
```

**Database writes in tests:** They go to the temp dir automatically. Use `close_kernel_db()` or call the singleton reset helpers if a test needs to reset mid-test.

**Avoiding permission-denied noise:** The `.pytest_cache`, `.pytest_tmp*`, and `.tmp_pytest*` directories cause "Permission denied" errors in recursive greps. Use `--include=*.py` or target `app/` and `tests/*.py` directly.
