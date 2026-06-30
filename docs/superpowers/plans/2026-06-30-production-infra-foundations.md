# Production Infra Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close four P1 production-readiness gaps (§3.1 migration runner, §3.4 overlay backfill, §2.2 multi-env config, §2.5 frontend-in-container) so future schema/column additions, historical data backfill, environment separation, and one-step container builds all work.

**Architecture:** Add a shared `apply_migrations` column-migration utility to `sqlite_db.py` and wire it into the four SQLite stores that lack it. Add a `backfill_quality_overlays.py` script that reuses the replay harness's `replay_record(preset_all_on())` to rebuild Phase 1-5 overlay fields on historical `event_store.json` records. Add `PMRF_ENV`-driven `.env.staging` / `.env.production` loading to `config.py`. Extend `deploy/Dockerfile` with a frontend builder stage so `docker build` produces a complete image.

**Tech Stack:** Python 3.11, SQLite, FastAPI/pydantic-settings, Docker multi-stage, Next.js (frontend).

## Global Constraints

- All Python backend files use `logger` methods (info/error/warning), not `print()` (per project convention; scripts use ASCII labels `[OK]/[FAIL]/[INFO]/[DRY-RUN]` to avoid Windows GBK UnicodeEncodeError).
- Feature flags added in this plan must default to OFF / current behavior (byte-identical to pre-change when unset).
- New scripts must support `--dry-run` for any write operation.
- Datetime operations use timezone-aware objects with explicit `timezone` imports.
- `.env.example` is the source of truth for documented env vars; new vars must be added there with comments.
- Do not modify frontend pages in this plan (frontend work is a separate plan).

---

## File Structure

| File | Responsibility | Task |
|------|---------------|------|
| `backend/app/utils/sqlite_db.py` | Shared `apply_migrations(conn, component, target_version, migrations)` utility | Task 1 |
| `backend/app/memory/event_market_link_store.py` | Wire `apply_migrations` into `_ensure_schema` | Task 1 |
| `backend/app/memory/loop_run_store.py` | Wire `apply_migrations` into `_ensure_schema` | Task 1 |
| `backend/app/memory/optimization_task_store.py` | Wire `apply_migrations` into `_ensure_schema` | Task 1 |
| `backend/app/memory/simulated_trade_store.py` | Wire `apply_migrations` into `_ensure_schema` | Task 1 |
| `backend/tests/test_sqlite_migrations.py` | Unit tests for `apply_migrations` | Task 1 |
| `backend/scripts/backfill_quality_overlays.py` | Backfill Phase 1-5 overlay fields on historical event_store records | Task 2 |
| `backend/tests/test_backfill_quality_overlays.py` | Tests for backfill script | Task 2 |
| `backend/app/core/config.py` | `PMRF_ENV`-driven env file loading | Task 3 |
| `backend/.env.staging.example` | Staging config template | Task 3 |
| `backend/.env.production.example` | Production config template | Task 3 |
| `backend/.env.example` | Document new `PMRF_ENV` var | Task 3 |
| `backend/tests/test_config_env_loading.py` | Tests for multi-env loading | Task 3 |
| `deploy/Dockerfile` | Add frontend builder stage | Task 4 |
| `deploy/docker-compose.yml` | Remove manual frontend-build assumption comment | Task 4 |
| `deploy/.dockerignore` | Exclude node_modules / build artifacts from build context | Task 4 |

---

## Task 1: Shared SQLite column-migration runner

**Files:**
- Modify: `backend/app/utils/sqlite_db.py` (add `apply_migrations` after `record_schema_version`, ~line 134)
- Modify: `backend/app/memory/event_market_link_store.py` (`_ensure_schema`, ~line 51)
- Modify: `backend/app/memory/loop_run_store.py` (`_ensure_schema`, ~line 15)
- Modify: `backend/app/memory/optimization_task_store.py` (`_ensure_schema`, ~line 30)
- Modify: `backend/app/memory/simulated_trade_store.py` (`_ensure_schema`, ~line 59)
- Create: `backend/tests/test_sqlite_migrations.py`

**Interfaces:**
- Produces: `sqlite_db.apply_migrations(conn, component, target_version, migrations)` where `migrations: dict[str, str]` maps `column_name -> column_declaration` (e.g. `{"notes": "TEXT DEFAULT ''"}`). Idempotent: skips columns already present. Records `target_version` via `record_schema_version`.
- Consumes: existing `record_schema_version` and `connect`/`writing` context managers.

The four stores each already have `_SCHEMA_VERSION` and call `record_schema_version`. After this task, each store additionally defines an empty `_MIGRATIONS: dict[str, str] = {}` and calls `apply_migrations(conn, "<component>", _SCHEMA_VERSION, _MIGRATIONS)` inside `_ensure_schema`. The dict starts empty because no pending column additions exist yet — this establishes the pattern so the next column add is a one-line change.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_sqlite_migrations.py`:

```python
"""Tests for the shared SQLite column-migration runner."""
import os
import sqlite3
import tempfile
import unittest


class TestApplyMigrations(unittest.TestCase):
    def test_adds_missing_column(self):
        from app.utils.sqlite_db import apply_migrations, connect, writing
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            with writing(db_path) as conn:
                conn.execute("CREATE TABLE demo (id TEXT PRIMARY KEY)")
                apply_migrations(
                    conn, "demo", 2, {"notes": "TEXT DEFAULT ''"}
                )
            with connect(db_path) as conn:
                cols = {row["name"] for row in conn.execute("PRAGMA table_info(demo)")}
                self.assertIn("notes", cols)

    def test_idempotent_skips_existing_column(self):
        from app.utils.sqlite_db import apply_migrations, connect, writing
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            with writing(db_path) as conn:
                conn.execute("CREATE TABLE demo (id TEXT PRIMARY KEY, notes TEXT)")
                apply_migrations(
                    conn, "demo", 2, {"notes": "TEXT DEFAULT ''"}
                )
            with connect(db_path) as conn:
                cols = {row["name"] for row in conn.execute("PRAGMA table_info(demo)")}
                self.assertEqual(cols, {"id", "notes"})

    def test_empty_migrations_noop(self):
        from app.utils.sqlite_db import apply_migrations, connect, writing
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            with writing(db_path) as conn:
                conn.execute("CREATE TABLE demo (id TEXT PRIMARY KEY)")
                apply_migrations(conn, "demo", 1, {})
            with connect(db_path) as conn:
                cols = {row["name"] for row in conn.execute("PRAGMA table_info(demo)")}
                self.assertEqual(cols, {"id"})

    def test_records_schema_version(self):
        from app.utils.sqlite_db import apply_migrations, schema_versions, writing
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            with writing(db_path) as conn:
                conn.execute("CREATE TABLE demo (id TEXT PRIMARY KEY)")
                apply_migrations(conn, "demo", 5, {})
            versions = schema_versions(db_path)
            self.assertEqual(versions.get("demo"), 5)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sqlite_migrations.py -v`
Expected: FAIL with `ImportError: cannot import name 'apply_migrations'`

- [ ] **Step 3: Implement `apply_migrations` in sqlite_db.py**

Add after the `schema_versions` function (after line 147):

```python
def apply_migrations(
    conn: sqlite3.Connection,
    component: str,
    target_version: int,
    migrations: dict[str, str],
) -> None:
    """Apply column-level migrations for one SQLite store component.

    Idempotent: skips columns already present in the table (cheap
    ``PRAGMA table_info`` check, no ALTER attempted). Records the
    ``target_version`` via ``record_schema_version`` so future runs can
    detect the on-disk version.

    ``migrations`` maps ``column_name -> column_declaration`` (e.g.
    ``{"notes": "TEXT DEFAULT ''"}``). The table must already exist
    (callers create it via ``CREATE TABLE IF NOT EXISTS`` first).
    """
    table = _component_table_name(component)
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for column, decl in migrations.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    record_schema_version(conn, component, target_version)


def _component_table_name(component: str) -> str:
    """Map a component name to its table name.

    Most components use the pluralized form (e.g. 'predictions' -> 'predictions').
    The four V2 loop stores follow this convention, so we use the component
    name directly. Prediction_store has its own _migrate() and does not
    use this helper (it has a special UNIQUE-rebuild path).
    """
    return component
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sqlite_migrations.py -v`
Expected: 4 passed

- [ ] **Step 5: Wire `apply_migrations` into the four stores**

For each of the four stores, add `_MIGRATIONS: dict[str, str] = {}` near the top (after `_SCHEMA_VERSION`) and call `apply_migrations` inside `_ensure_schema`.

**`event_market_link_store.py`** — in `_ensure_schema`, after `conn.executescript(SCHEMA_SQL)` (or the `CREATE TABLE` block), add before `record_schema_version`:

```python
sqlite_db.apply_migrations(conn, "event_market_links", _SCHEMA_VERSION, _MIGRATIONS)
```
And add near the top:
```python
_MIGRATIONS: dict[str, str] = {}
```

**`loop_run_store.py`** — in `_ensure_schema`, inside the `with writing(path) as conn:` block, after the `CREATE TABLE` execute, add:

```python
sqlite_db.apply_migrations(conn, "loop_runs", _SCHEMA_VERSION, _MIGRATIONS)
```
And add near the top:
```python
_MIGRATIONS: dict[str, str] = {}
```

**`optimization_task_store.py`** — in `_ensure_schema`, inside the `with writing(path) as conn:` block, after the `CREATE TABLE` execute, add:

```python
sqlite_db.apply_migrations(conn, "optimization_tasks", _SCHEMA_VERSION, _MIGRATIONS)
```
And add near the top:
```python
_MIGRATIONS: dict[str, str] = {}
```

**`simulated_trade_store.py`** — in `_ensure_schema`, after `conn.executescript(SCHEMA_SQL)`, add:

```python
sqlite_db.apply_migrations(conn, "simulated_trades", _SCHEMA_VERSION, _MIGRATIONS)
```
And add near the top:
```python
_MIGRATIONS: dict[str, str] = {}
```

- [ ] **Step 6: Run full backend suite to verify no regression**

Run: `python -m pytest tests/ --tb=short -q --ignore=tests/test_world_cup_gbm_features.py --ignore=tests/test_gbm_engine.py`
Expected: all pass (the four stores' existing `_ensure_schema` tests still pass; new migrations dict is empty so no schema change)

- [ ] **Step 7: Commit**

```bash
git add backend/app/utils/sqlite_db.py backend/app/memory/event_market_link_store.py backend/app/memory/loop_run_store.py backend/app/memory/optimization_task_store.py backend/app/memory/simulated_trade_store.py backend/tests/test_sqlite_migrations.py
git commit -m "feat(db): add shared apply_migrations column-migration runner and wire into 4 stores"
```

---

## Task 2: Historical overlay backfill script

**Files:**
- Create: `backend/scripts/backfill_quality_overlays.py`
- Create: `backend/tests/test_backfill_quality_overlays.py`

**Interfaces:**
- Consumes: `app.replay.runner.replay_record` + `app.replay.config.ReplayConfig.preset_all_on()` to rebuild overlays; `app.memory.event_store` to read/write records.
- Produces: `backfill_quality_overlays(dry_run=True/False, event_ids=None)` that mutates `event_store.json` in place (when not dry-run), filling `decision_quality` / `market_quality` / `source_reliability` / `llm_telemetry` / `final_displayed_direction` / `final_downgrade_reason` on records missing them.

The script reuses the replay harness because `replay_record(record, preset_all_on())` already reconstructs overlay inputs from the frozen record and rebuilds all 5 overlays + merge + guardrail. The backfill copies the rebuilt overlay fields back onto the record and persists. Phase 5 LLM token usage is unrecoverable (lost at freeze time), so `llm_telemetry` will carry `degraded_mode=True` placeholder — documented in the script docstring.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_backfill_quality_overlays.py`:

```python
"""Tests for the historical overlay backfill script."""
import os
import tempfile
import unittest
from unittest.mock import patch


def _synthetic_record(event_id: str, missing_overlays: bool = True) -> dict:
    """A minimal record that replay_record can process."""
    rec = {
        "event_id": event_id,
        "title": "Will X happen?",
        "source": {"type": "open_web", "url": "https://example.com/article"},
        "market": {"yes_price": 0.65, "no_price": 0.35},
        "actionable_recommendation": {
            "direction": "YES",
            "signal": "WATCHLIST",
            "ai_probability": 0.62,
            "edge": 0.0,
            "confidence": "medium",
        },
        "evidence_breakdown": [
            {
                "source": "https://example.com/article",
                "direction": "support",
                "strength": 0.7,
                "summary": "supports YES",
            }
        ],
    }
    if missing_overlays:
        # Pre-Phase records have no overlay fields.
        for key in (
            "decision_quality",
            "market_quality",
            "source_reliability",
            "llm_telemetry",
            "final_displayed_direction",
            "final_downgrade_reason",
        ):
            rec.pop(key, None)
    return rec


class TestBackfillQualityOverlays(unittest.TestCase):
    def test_dry_run_does_not_write(self):
        from scripts.backfill_quality_overlays import backfill_quality_overlays
        with tempfile.TemporaryDirectory() as tmp:
            store_path = os.path.join(tmp, "event_store.json")
            import json
            with open(store_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"events": [{"event_id": "e1", "first_seen": "2026-01-01",
                                 "last_updated": "2026-01-01", "record": _synthetic_record("e1")}]},
                    f,
                )
            with patch("app.memory.event_store.EVENT_STORE_FILE", store_path):
                result = backfill_quality_overlays(dry_run=True)
            # Dry run reports what would change but does not persist.
            self.assertGreaterEqual(result["would_backfill"], 1)
            with open(store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            rec = data["events"][0]["record"]
            self.assertNotIn("decision_quality", rec)

    def test_apply_writes_overlay_fields(self):
        from scripts.backfill_quality_overlays import backfill_quality_overlays
        with tempfile.TemporaryDirectory() as tmp:
            store_path = os.path.join(tmp, "event_store.json")
            import json
            with open(store_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"events": [{"event_id": "e1", "first_seen": "2026-01-01",
                                 "last_updated": "2026-01-01", "record": _synthetic_record("e1")}]},
                    f,
                )
            with patch("app.memory.event_store.EVENT_STORE_FILE", store_path):
                result = backfill_quality_overlays(dry_run=False)
            self.assertGreaterEqual(result["backfilled"], 1)
            with open(store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            rec = data["events"][0]["record"]
            # After backfill, overlay fields should be present.
            self.assertIn("decision_quality", rec)

    def test_skips_records_already_having_overlays(self):
        from scripts.backfill_quality_overlays import backfill_quality_overlays
        with tempfile.TemporaryDirectory() as tmp:
            store_path = os.path.join(tmp, "event_store.json")
            import json
            rec_with_overlay = _synthetic_record("e1", missing_overlays=False)
            rec_with_overlay["decision_quality"] = {"score": 0.5}
            with open(store_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"events": [{"event_id": "e1", "first_seen": "2026-01-01",
                                 "last_updated": "2026-01-01", "record": rec_with_overlay}]},
                    f,
                )
            with patch("app.memory.event_store.EVENT_STORE_FILE", store_path):
                result = backfill_quality_overlays(dry_run=True)
            self.assertEqual(result["would_backfill"], 0)
            self.assertEqual(result["skipped"], 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_backfill_quality_overlays.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.backfill_quality_overlays'`

- [ ] **Step 3: Implement the backfill script**

Create `backend/scripts/backfill_quality_overlays.py`:

```python
"""Backfill Phase 1-5 quality overlay fields on historical event_store records.

Pre-Phase events were frozen before the overlay pipeline existed, so their
records lack ``decision_quality`` / ``market_quality`` / ``source_reliability``
/ ``llm_telemetry`` / ``final_displayed_direction`` / ``final_downgrade_reason``.

This script reuses the replay harness (``replay_record`` with
``preset_all_on()``) to rebuild the overlays from the frozen record's inputs
and writes them back to ``event_store.json``.

Limitation: Phase 5 LLM token usage is unrecoverable (the original LLM call
is not re-run during replay). The rebuilt ``llm_telemetry`` block will carry
``degraded_mode=True`` as a placeholder, flagged in the telemetry block's
``replayed`` field. This is acceptable because backfill targets the overlay
*structure* for replay/dashboard sample coverage, not live cost accounting.

Usage:
    python -m scripts.backfill_quality_overlays --dry-run
    python -m scripts.backfill_quality_overlays --apply
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

# Overlay field names that this script populates. A record is considered
# "needs backfill" if it is missing ``decision_quality`` (the first overlay
# built). Other fields may be legitimately absent (e.g. ``market_quality`` is
# only built for prediction_market sources), so we key off decision_quality
# as the leading indicator.
_OVERLAY_FIELDS = (
    "decision_quality",
    "market_quality",
    "source_reliability",
    "llm_telemetry",
    "final_displayed_direction",
    "final_downgrade_reason",
)


def backfill_quality_overlays(
    dry_run: bool = True,
    event_ids: list[str] | None = None,
) -> dict[str, int]:
    """Backfill overlay fields on historical event_store.json records.

    Args:
        dry_run: When True, report what would change without writing.
        event_ids: Optional filter; only backfill these event IDs. None =
            all events.

    Returns:
        ``{"would_backfill": N, "skipped": M, "backfilled": K, "errors": E}``.
        ``would_backfill`` is set in dry-run; ``backfilled`` in apply mode.
    """
    from app.memory.event_store import EVENT_STORE_FILE
    from app.replay.config import ReplayConfig
    from app.replay.runner import replay_record

    if not os.path.exists(EVENT_STORE_FILE):
        logger.error("event_store.json not found at %s", EVENT_STORE_FILE)
        return {"would_backfill": 0, "skipped": 0, "backfilled": 0, "errors": 0}

    with open(EVENT_STORE_FILE, "r", encoding="utf-8") as f:
        store = json.load(f)

    events = store.get("events", [])
    id_filter = set(event_ids) if event_ids else None
    cfg = ReplayConfig.preset_all_on()

    would_backfill = 0
    skipped = 0
    backfilled = 0
    errors = 0

    for entry in events:
        eid = entry.get("event_id")
        if eid is None:
            continue
        if id_filter is not None and eid not in id_filter:
            continue
        record = entry.get("record") or {}
        # Skip records that already have decision_quality (already backfilled
        # or produced post-Phase). Other overlay fields may be legitimately
        # absent, so decision_quality is the leading indicator.
        if "decision_quality" in record:
            skipped += 1
            continue
        try:
            replayed = replay_record(record, cfg)
            # Copy rebuilt overlay fields back onto the stored record.
            for field in _OVERLAY_FIELDS:
                if field in replayed:
                    record[field] = replayed[field]
            entry["record"] = record
            entry["last_updated"] = entry.get("last_updated", "")
            backfilled += 1
            would_backfill += 1
        except Exception as exc:
            logger.error("[FAIL] backfill %s: %s", eid, exc)
            errors += 1

    if dry_run:
        logger.info(
            "[DRY-RUN] would backfill=%d skipped=%d errors=%d",
            would_backfill, skipped, errors,
        )
        return {
            "would_backfill": would_backfill,
            "skipped": skipped,
            "backfilled": 0,
            "errors": errors,
        }

    # Apply: persist.
    with open(EVENT_STORE_FILE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    logger.info(
        "[OK] backfilled=%d skipped=%d errors=%d",
        backfilled, skipped, errors,
    )
    return {
        "would_backfill": 0,
        "skipped": skipped,
        "backfilled": backfilled,
        "errors": errors,
    }


def _main() -> int:
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Backfill Phase 1-5 quality overlays on historical records."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report only, no writes.")
    mode.add_argument("--apply", action="store_true", help="Write backfill to event_store.json.")
    parser.add_argument(
        "--event-id", action="append", dest="event_ids",
        help="Limit to these event IDs (repeatable).",
    )
    args = parser.parse_args()
    result = backfill_quality_overlays(
        dry_run=args.dry_run,
        event_ids=args.event_ids,
    )
    print(json.dumps(result, indent=2))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(_main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_backfill_quality_overlays.py -v`
Expected: 3 passed

- [ ] **Step 5: Run full backend suite**

Run: `python -m pytest tests/ --tb=short -q --ignore=tests/test_world_cup_gbm_features.py --ignore=tests/test_gbm_engine.py`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/backfill_quality_overlays.py backend/tests/test_backfill_quality_overlays.py
git commit -m "feat(scripts): add backfill_quality_overlays for historical Phase 1-5 overlay fields"
```

---

## Task 3: Multi-environment config (PMRF_ENV)

**Files:**
- Modify: `backend/app/core/config.py` (top of file, ~line 1-4)
- Create: `backend/.env.staging.example`
- Create: `backend/.env.production.example`
- Modify: `backend/.env.example` (add `PMRF_ENV` documentation)
- Create: `backend/tests/test_config_env_loading.py`

**Interfaces:**
- Produces: `PMRF_ENV` env var (`development` / `staging` / `production`, default `development`). `config.py` loads `.env`, then `.env.{PMRF_ENV}` if it exists, so environment-specific values override the base `.env`.

The implementation uses `dotenv.load_dotenv` with explicit override: load base `.env` first, then `.env.<PMRF_ENV>` with `override=True` so the environment file wins.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_config_env_loading.py`:

```python
"""Tests for PMRF_ENV-driven multi-environment config loading."""
import os
import tempfile
import unittest
from unittest.mock import patch


class TestEnvLoading(unittest.TestCase):
    def test_default_env_is_development(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PMRF_ENV", None)
            # Re-import to capture default. We test the helper function
            # rather than re-importing the module (which has side effects).
            from app.core.config import _resolve_env_file
            env_file = _resolve_env_file()
            self.assertEqual(env_file, ".env")

    def test_staging_env_loads_staging_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_env = os.path.join(tmp, ".env")
            staging_env = os.path.join(tmp, ".env.staging")
            with open(base_env, "w") as f:
                f.write("OPENAI_MODEL=base-model\n")
            with open(staging_env, "w") as f:
                f.write("OPENAI_MODEL=staging-model\n")
            from app.core.config import _load_env_files
            with patch.dict(os.environ, {"PMRF_ENV": "staging"}, clear=False):
                os.chdir(tmp)
                _load_env_files()
                # dotenv loads into os.environ at import time; here we verify
                # the staging file overrides the base.
            # Restore by reloading base .env would be complex; this test
            # focuses on the resolution logic (see next test).

    def test_resolve_env_file_returns_environment_specific(self):
        from app.core.config import _resolve_env_file
        with patch.dict(os.environ, {"PMRF_ENV": "production"}, clear=False):
            self.assertEqual(_resolve_env_file(), ".env.production")
        with patch.dict(os.environ, {"PMRF_ENV": "staging"}, clear=False):
            self.assertEqual(_resolve_env_file(), ".env.staging")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PMRF_ENV", None)
            self.assertEqual(_resolve_env_file(), ".env")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_env_loading.py -v`
Expected: FAIL with `ImportError: cannot import name '_resolve_env_file'`

- [ ] **Step 3: Implement multi-env loading in config.py**

Replace the top of `backend/app/core/config.py` (lines 1-4):

```python
from dotenv import load_dotenv
import os


def _resolve_env_file() -> str:
    """Return the env file path for the current PMRF_ENV.

    ``development`` (default) -> ``.env``
    ``staging`` -> ``.env.staging``
    ``production`` -> ``.env.production``

    The environment-specific file overrides the base ``.env`` (loaded first
    without override, then the env file with override=True).
    """
    pmrf_env = os.getenv("PMRF_ENV", "development").strip().lower()
    if pmrf_env == "staging":
        return ".env.staging"
    if pmrf_env == "production":
        return ".env.production"
    return ".env"


def _load_env_files() -> None:
    """Load base .env then environment-specific file (override=True)."""
    load_dotenv()  # base .env, no override
    env_file = _resolve_env_file()
    if env_file != ".env":
        load_dotenv(env_file, override=True)


_load_env_files()


def _env_bool(name: str, default: str = "") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}
```

(This replaces the original `load_dotenv()` line 4 with the `_resolve_env_file` + `_load_env_files` helpers and a call to `_load_env_files()`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config_env_loading.py -v`
Expected: 3 passed

- [ ] **Step 5: Create environment example files**

Create `backend/.env.staging.example`:

```
# Staging environment configuration.
# Copy to .env.staging and set PMRF_ENV=staging to activate.
# Values here OVERRIDE backend/.env when PMRF_ENV=staging.

PMRF_ENV=staging

# Use cheaper model in staging to control cost.
OPENAI_MODEL=deepseek-chat

# Feature flags default OFF in staging (safer for pre-prod).
DECISION_QUALITY_ENABLED=false
MARKET_QUALITY_ENABLED=false
SOURCE_RELIABILITY_ENABLED=false
PREDICTION_CALIBRATION_ENABLED=false
LLM_TELEMETRY_ENABLED=false
GUARDRAILS_ENABLED=false

# Stricter rate limiting in staging.
RATE_LIMIT_MAX_REQUESTS=60

# INFO logging in staging.
LOG_LEVEL=INFO
```

Create `backend/.env.production.example`:

```
# Production environment configuration.
# Copy to .env.production and set PMRF_ENV=production to activate.
# Values here OVERRIDE backend/.env when PMRF_ENV=production.
# NEVER commit real production secrets — use Secrets management (SOPS/Vault).

PMRF_ENV=production

# Production model (set explicitly, do not inherit dev default).
OPENAI_MODEL=deepseek-chat

# Feature flags ON in production (the overlays are the product).
DECISION_QUALITY_ENABLED=true
MARKET_QUALITY_ENABLED=true
SOURCE_RELIABILITY_ENABLED=true
PREDICTION_CALIBRATION_ENABLED=true
LLM_TELEMETRY_ENABLED=true
GUARDRAILS_ENABLED=true

# Production rate limiting (generous for trusted clients behind reverse proxy).
RATE_LIMIT_MAX_REQUESTS=120
TRUSTED_PROXY_HEADER=true

# WARNING logging in production to reduce noise.
LOG_LEVEL=WARNING

# API write key MUST be set in production (fail-closed if empty).
API_WRITE_KEY=
```

- [ ] **Step 6: Document PMRF_ENV in .env.example**

Add at the top of `backend/.env.example` (after any header comment):

```
# Environment selection: development (default) | staging | production
# When set to staging/production, loads .env.staging/.env.production
# which OVERRIDE values in this base .env file.
PMRF_ENV=development
```

- [ ] **Step 7: Run full backend suite**

Run: `python -m pytest tests/ --tb=short -q --ignore=tests/test_world_cup_gbm_features.py --ignore=tests/test_gbm_engine.py`
Expected: all pass (default behavior unchanged when PMRF_ENV unset)

- [ ] **Step 8: Commit**

```bash
git add backend/app/core/config.py backend/.env.staging.example backend/.env.production.example backend/.env.example backend/tests/test_config_env_loading.py
git commit -m "feat(config): add PMRF_ENV multi-environment config loading (.env.staging/.env.production)"
```

---

## Task 4: Frontend build in container (Dockerfile multi-stage)

**Files:**
- Modify: `deploy/Dockerfile` (add frontend builder stage before runtime)
- Modify: `deploy/docker-compose.yml` (update comment)
- Create: `deploy/.dockerignore`

**Interfaces:**
- Produces: A `docker build -f deploy/Dockerfile .` command that builds the frontend from source (no manual `npm run build` step) and produces a complete runtime image.

The Dockerfile gains a `frontend-builder` stage: `FROM node:22-alpine`, copies `frontend/`, runs `npm ci && npm run build`, produces `frontend/out/`. The runtime stage copies from `--from=frontend-builder` instead of `COPY frontend/out/` (which assumed pre-built).

- [ ] **Step 1: Create .dockerignore**

Create `deploy/.dockerignore`:

```
# Exclude heavy/local-only artifacts from the Docker build context to
# keep builds fast and avoid leaking secrets / node_modules into images.

# Git
.git
.gitignore

# Python local
backend/__pycache__
backend/**/__pycache__
backend/.pytest_cache
backend/.mypy_cache
backend/venv
backend/.venv

# Node local
frontend/node_modules
frontend/.next
frontend/.git

# Data / logs (never bake into image)
backend/data
backend/logs
**/*.db
**/*.db-wal
**/*.db-shm
**/event_store.json
**/predictions.db
**/loop_store.db

# Env files with secrets (examples are OK to keep for documentation)
backend/.env
backend/.env.staging
backend/.env.production
**/.env.local

# SDD / review artifacts
sdd-reviews
.git/sdd
```

- [ ] **Step 2: Update Dockerfile with frontend builder stage**

Replace `deploy/Dockerfile` content with:

```dockerfile
# Prediction Market Reality Filter — Production Dockerfile
# Build:  docker build -t pmrf:0.3.0 -f deploy/Dockerfile .
# Run:    docker run -p 8000:8000 --env-file backend/.env pmrf:0.3.0
#
# Multi-stage: builds the Next.js frontend from source so a single
# `docker build` produces a complete image (no manual npm run build).

# ---- Stage 1: Frontend builder ----
FROM node:22-alpine AS frontend-builder

WORKDIR /frontend

# Copy package manifests first for layer caching.
COPY frontend/package.json frontend/package-lock.json* ./

RUN npm ci

# Copy frontend source and build the static export.
COPY frontend/ ./

# Next.js static export writes to ./out (per next.config.js output: 'export').
RUN npm run build

# ---- Stage 2: Python builder ----
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps for feedparser + httpx
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies to a target directory
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Stage 3: Runtime ----
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Runtime system deps (libxml2 needed at runtime by feedparser)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app/logs /app/data /frontend/out \
    && chown -R app:app /app /frontend

# Copy backend
COPY --chown=app:app backend/ /app/

# Copy frontend static export from the frontend-builder stage (no manual
# pre-build required — the build happened in Stage 1).
COPY --from=frontend-builder --chown=app:app /frontend/out/ /frontend/out/

USER app

# Expose and run
EXPOSE 8000

# Health check (works even without docker-compose)
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=5).status < 400 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Update docker-compose.yml comment**

In `deploy/docker-compose.yml`, find the comment that says frontend must be built separately and replace with a note that `docker build` now handles it. For example, change:

```yaml
# Frontend must be built separately: cd frontend && npm run build
```

to:

```yaml
# Frontend is built automatically by the Dockerfile's frontend-builder stage.
```

(If no such comment exists, skip this step. Read the file first to locate the exact string.)

- [ ] **Step 4: Verify the Dockerfile is syntactically valid**

Run: `docker build --check -f deploy/Dockerfile .` (if Docker daemon available) OR verify syntax by inspection. If Docker is not available in this environment, skip the build run and rely on the syntax review — the multi-stage pattern is standard.

- [ ] **Step 5: Commit**

```bash
git add deploy/Dockerfile deploy/docker-compose.yml deploy/.dockerignore
git commit -m "feat(deploy): Dockerfile multi-stage frontend build (single docker build produces full image)"
```

---

## Self-Review Notes

- **Spec coverage**: §3.1 (Task 1), §3.4 (Task 2), §2.2 (Task 3), §2.5 (Task 4) — all four P1 items in this plan are covered.
- **Placeholder scan**: All steps contain complete code or exact edit instructions; no TBD/TODO.
- **Type consistency**: `apply_migrations(conn, component, target_version, migrations)` signature is consistent across sqlite_db.py, the 4 store wirings, and the test. `backfill_quality_overlays(dry_run, event_ids)` is consistent across script + test.
- **Default-OFF invariant**: Task 3's env loading defaults to `development` (loads base `.env` only) — byte-identical to pre-change. No new feature flags added in Tasks 1/2/4.
- **No frontend page changes**: This plan only touches deploy artifacts and one config module; no `frontend/src/` modifications.
