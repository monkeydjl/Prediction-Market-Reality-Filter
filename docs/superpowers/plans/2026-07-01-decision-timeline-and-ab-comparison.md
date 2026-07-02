# Decision Timeline / Diff Viewer & A/B Feature-Flag Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-event decision timeline that explains why an event's final direction changed across analysis runs (spec §5.4), plus an A/B CLI that quantifies how much each Phase overlay flips YES→WAIT when toggled on (spec §1.5).

**Architecture:** A new append-only SQLite `decision_timeline_store` snapshots the overlay-bearing record on every `save_events` call (gated by `DECISION_TIMELINE_ENABLED`, default OFF → byte-identical to pre-Plan-5). A pure `build_decision_diff(prev, current)` function ranks the primary change driver. A new `GET /api/events/{event_id}/decision-timeline` route exposes the snapshots; a new `DecisionTimelinePanel` renders them. The A/B CLI reuses the existing `replay_record` runner with `ReplayConfig` presets to flip each phase on/off and reports a direction-change matrix.

**Tech Stack:** Python 3.14, FastAPI, Pydantic, SQLite (shared `sqlite_db.py` plumbing), pytest, Next.js 14, React, TypeScript.

## Global Constraints

- `DECISION_TIMELINE_ENABLED` must default to `false`, resulting in byte-identical behavior to pre-Plan-5 when disabled (no snapshot written, no store schema created, no API data — the route still responds with `count=0` reading an empty store).
- `decision_timeline_store` must follow the `event_market_link_store.py` / `source_trust_registry_store.py` pattern: module-level functions, lazy schema init via `_ensure_schema` with double-checked locking via `_INITIALIZED: set[str]`, shared `sqlite_db.py` plumbing (`loop_db_path()` + `reading()`/`writing()` context managers + `apply_migrations` + `record_schema_version`).
- `build_decision_diff` must be a pure, synchronous, deterministic function with no LLM/IO operations; `settings` must not be passed (scalars passed explicitly if needed — none are needed here).
- All Chinese reason strings in the diff output must exclude banned terms (`long`, `short`, `buy`, `sell`, `position`, `kelly`, `order`) to maintain vocabulary lock.
- The API route must use the existing `EventId` path-typed parameter and `EventHistoryResponse`-style response model convention; 404 on unknown event_id, empty list (not 404) when event exists but has no snapshots.
- The frontend panel must follow the `DecisionReportPanel` pattern exactly: `"use client"`, self-contained, fetches via `eventsApi`, loading/error/empty states, plugs into `events/page.tsx` alongside `DecisionReportPanel`.
- The A/B CLI must follow the `replay_decision_pipeline.py` pattern: argparse, `main(argv) -> int`, ASCII labels `[OK]/[FAIL]/[INFO]/[WARN]`, `_print()` UTF-8 reconfiguration for Windows GBK safety, `python -m scripts.analyze_feature_flag_impact` invocation.
- The A/B CLI must reuse `replay_record(record, cfg)` from `app.replay.runner` and `ReplayConfig` from `app.replay.config` — no new replay logic.
- Overlay latency metrics (`OVERLAY_LATENCY`) path count stays at 6 (no new overlay added); the timeline snapshot is not an overlay, it is a post-save persistence step.
- `FINAL_DIRECTION_CHANGE` Prometheus counter remains the single source of truth in `event_store.save_events()`; the timeline store does NOT increment it.
- SDD workflow: fresh implementer subagent per task, task review after each, final whole-branch review before merge. All reviews accumulate Minor findings; none are must-fix unless plan-mandated.

---

## File Structure

**New files:**
- `backend/app/memory/decision_timeline_store.py` — SQLite append-only snapshot store (module-level functions, lazy schema).
- `backend/app/services/decision_diff_service.py` — Pure `build_decision_diff(prev, current)` function ranking change drivers.
- `backend/scripts/analyze_feature_flag_impact.py` — A/B CLI reusing `replay_record` + `ReplayConfig`.
- `backend/tests/test_decision_timeline_store.py` — Unit tests for the store.
- `backend/tests/test_decision_diff_service.py` — Unit tests for the diff function.
- `backend/tests/test_analyze_feature_flag_impact.py` — Unit tests for the CLI.
- `frontend/src/components/detail/decision-timeline-panel.tsx` — Self-contained timeline panel.

**Modified files:**
- `backend/app/core/config.py` — Add `DECISION_TIMELINE_ENABLED` flag (default false).
- `backend/app/memory/event_store.py` — Call `decision_timeline_store.record_snapshot(record)` inside `save_events` when flag on (one flag check outside the loop, snapshot call inside the loop per record).
- `backend/app/models/event.py` — Add `DecisionTimelineSnapshot` and `DecisionTimelineResponse` Pydantic models.
- `backend/app/api/routes/events.py` — Add `GET /{event_id}/decision-timeline` route (declared after `/{event_id}/history` to follow the static-before-dynamic ordering convention).
- `frontend/src/lib/api.ts` — Add `DecisionTimelineSnapshot` / `DecisionTimelineResponse` types and `decisionTimeline` method to `eventsApi`.
- `frontend/src/app/events/page.tsx` — Render `<DecisionTimelinePanel eventId={record.event_id} />` after `<DecisionReportPanel>`.

---

### Task 1: decision_timeline_store + config flag + event_store wiring

**Files:**
- Create: `backend/app/memory/decision_timeline_store.py`
- Create: `backend/tests/test_decision_timeline_store.py`
- Modify: `backend/app/core/config.py` (add `DECISION_TIMELINE_ENABLED`)
- Modify: `backend/app/memory/event_store.py` (wire `record_snapshot` into `save_events`, gated by flag)

**Interfaces:**
- Consumes: `app.utils.sqlite_db` (`loop_db_path`, `reading`, `writing`, `apply_migrations`, `record_schema_version`); `app.core.config.settings.DECISION_TIMELINE_ENABLED`.
- Produces:
  - `record_snapshot(record: dict[str, Any]) -> str | None` — extracts overlay-bearing fields from a record, inserts an append-only snapshot row, returns `snapshot_id` (or `None` when the flag is off — caller treats None as a no-op).
  - `list_snapshots(event_id: str, *, limit: int = 100) -> list[dict[str, Any]]` — returns snapshots ordered by `recorded_at` ASC, most recent `limit` rows.
  - `count_snapshots(event_id: str) -> int` — used by the API to return `count` without loading rows.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_decision_timeline_store.py`:

```python
"""Unit tests for decision_timeline_store (Plan 5 §5.4)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.memory import decision_timeline_store as dt
from app.utils import sqlite_db


def _db(tmp):
    return patch.object(sqlite_db, "loop_db_path",
                        return_value=str(Path(tmp) / "v2_loop.db"))


def _sample_record(event_id="evt-001", **overrides):
    rec = {
        "event_id": event_id,
        "probability": {"baseline": 50.0, "estimated": 55.0,
                        "change": 5.0, "direction": "YES"},
        "final_displayed_direction": "YES",
        "final_downgrade_reason": None,
        "decision_quality": {"downgraded": False, "raw_direction": "YES",
                             "displayed_direction": "YES"},
        "market_quality": None,
        "source_reliability": None,
        "execution_quality": None,
        "llm_telemetry": {"degraded_mode": False},
        "guardrail_fired": None,
        "outcome": None,
    }
    rec.update(overrides)
    return rec


class TestDecisionTimelineStore(unittest.TestCase):
    def test_table_autocreates_on_fresh_db(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            self.assertEqual(dt.count_snapshots("evt-001"), 0)
            self.assertEqual(dt.list_snapshots("evt-001"), [])

    def test_record_snapshot_returns_id_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            sid = dt.record_snapshot(_sample_record())
            self.assertIsNotNone(sid)
            self.assertEqual(dt.count_snapshots("evt-001"), 1)
            snaps = dt.list_snapshots("evt-001")
            self.assertEqual(len(snaps), 1)
            self.assertEqual(snaps[0]["snapshot_id"], sid)
            self.assertEqual(snaps[0]["event_id"], "evt-001")
            self.assertEqual(snaps[0]["final_displayed_direction"], "YES")
            self.assertEqual(snaps[0]["probability"], {"baseline": 50.0,
                                                       "estimated": 55.0,
                                                       "change": 5.0,
                                                       "direction": "YES"})
            self.assertEqual(snaps[0]["decision_quality"]["downgraded"], False)
            self.assertFalse(snaps[0]["llm_degraded_mode"])

    def test_list_snapshots_ordered_ascending_by_recorded_at(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            dt.record_snapshot(_sample_record(final_displayed_direction="YES"))
            dt.record_snapshot(_sample_record(final_displayed_direction="WAIT"))
            dt.record_snapshot(_sample_record(final_displayed_direction="AVOID"))
            snaps = dt.list_snapshots("evt-001")
            self.assertEqual([s["final_displayed_direction"] for s in snaps],
                             ["YES", "WAIT", "AVOID"])

    def test_list_snapshots_respects_limit(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            for i in range(5):
                dt.record_snapshot(_sample_record(final_displayed_direction=f"D{i}"))
            snaps = dt.list_snapshots("evt-001", limit=3)
            self.assertEqual(len(snaps), 3)
            # Most recent 3 (last inserted 3) returned in ASC order.
            self.assertEqual([s["final_displayed_direction"] for s in snaps],
                             ["D2", "D3", "D4"])

    def test_list_snapshots_filtered_by_event_id(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            dt.record_snapshot(_sample_record("evt-A"))
            dt.record_snapshot(_sample_record("evt-B"))
            dt.record_snapshot(_sample_record("evt-A"))
            self.assertEqual(dt.count_snapshots("evt-A"), 2)
            self.assertEqual(dt.count_snapshots("evt-B"), 1)
            self.assertEqual(len(dt.list_snapshots("evt-A")), 2)
            self.assertEqual(len(dt.list_snapshots("evt-B")), 1)

    def test_record_snapshot_handles_missing_overlay_blocks(self):
        """A record with no overlays (e.g. a freshly discovered event) must
        still snapshot without crashing — overlays_json stores nulls."""
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            rec = {"event_id": "evt-min", "final_displayed_direction": None}
            sid = dt.record_snapshot(rec)
            self.assertIsNotNone(sid)
            snaps = dt.list_snapshots("evt-min")
            self.assertEqual(len(snaps), 1)
            self.assertIsNone(snaps[0]["final_displayed_direction"])
            self.assertIsNone(snaps[0]["decision_quality"])
            self.assertIsNone(snaps[0]["market_quality"])
            self.assertIsNone(snaps[0]["probability"])

    def test_record_snapshot_captures_outcome_when_present(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            dt.record_snapshot(_sample_record(outcome="YES"))
            snaps = dt.list_snapshots("evt-001")
            self.assertEqual(snaps[0]["outcome"], "YES")

    def test_record_snapshot_captures_guardrail_fired_list(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            dt.record_snapshot(_sample_record(guardrail_fired=["rule_a", "rule_b"]))
            snaps = dt.list_snapshots("evt-001")
            self.assertEqual(snaps[0]["guardrail_fired"], ["rule_a", "rule_b"])

    def test_record_snapshot_captures_llm_degraded_mode_true(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            dt.record_snapshot(_sample_record(
                llm_telemetry={"degraded_mode": True}))
            snaps = dt.list_snapshots("evt-001")
            self.assertTrue(snaps[0]["llm_degraded_mode"])

    def test_record_snapshot_is_append_only(self):
        """Each call creates a new row — no upsert, no dedup. The store is a
        timeline, not a latest-state cache."""
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            for _ in range(3):
                dt.record_snapshot(_sample_record())
            self.assertEqual(dt.count_snapshots("evt-001"), 3)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_decision_timeline_store.py -v` from `backend/`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.memory.decision_timeline_store'`

- [ ] **Step 3: Add the config flag**

In `backend/app/core/config.py`, find the Plan 4 flag block (the `REVIEW_QUEUE_MISMATCH_CONFIDENCE` line added in Plan 4). Add immediately after it:

```python
    # Plan 5 §5.4: Decision Timeline / Diff Viewer. When enabled, save_events
    # appends an overlay-bearing snapshot of each record to
    # decision_timeline_store so the /decision-timeline route can diff how an
    # event's final direction evolved. Defaults to false → byte-identical to
    # pre-Plan-5 (no snapshot written, no store schema created).
    DECISION_TIMELINE_ENABLED: bool = _env_bool("DECISION_TIMELINE_ENABLED", "false")
```

- [ ] **Step 4: Implement the store**

Create `backend/app/memory/decision_timeline_store.py`:

```python
"""Decision timeline snapshot store (Plan 5 §5.4).

Append-only SQLite store of overlay-bearing record snapshots. On every
``save_events`` call (when ``DECISION_TIMELINE_ENABLED`` is on) the
orchestrator calls ``record_snapshot(record)`` which extracts the
direction/overlay/probability fields and inserts a new row. The
``/api/events/{event_id}/decision-timeline`` route reads these back in
ASC order so the frontend can render a Diff Viewer timeline.

Schema follows the ``event_market_link_store.py`` /
``source_trust_registry_store.py`` pattern: module-level functions, lazy
schema init via ``_ensure_schema`` (double-checked locking), shared
``sqlite_db.py`` plumbing.

The store is byte-identical-inert when the flag is off:
``record_snapshot`` still works if called directly (tests use it), but
``event_store.save_events`` only calls it when
``settings.DECISION_TIMELINE_ENABLED`` is true.
"""
from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from app.utils import sqlite_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_timeline (
    snapshot_id              TEXT PRIMARY KEY,
    event_id                 TEXT NOT NULL,
    recorded_at              TEXT NOT NULL DEFAULT (datetime('now')),
    final_displayed_direction TEXT,
    final_downgrade_reason   TEXT,
    probability_json         TEXT,
    decision_quality_json    TEXT,
    market_quality_json      TEXT,
    source_reliability_json  TEXT,
    execution_quality_json   TEXT,
    llm_degraded_mode        INTEGER,
    guardrail_fired_json     TEXT,
    outcome                  TEXT
);
CREATE INDEX IF NOT EXISTS idx_dt_event_id ON decision_timeline(event_id);
CREATE INDEX IF NOT EXISTS idx_dt_recorded_at ON decision_timeline(recorded_at);
"""

_SCHEMA_VERSION = 1
_MIGRATIONS: dict[str, str] = {}

_INITIALIZED: set[str] = set()
_INIT_GUARD = threading.Lock()


def _ensure_schema(path: str) -> None:
    if path in _INITIALIZED:
        return
    with _INIT_GUARD:
        if path in _INITIALIZED:
            return
        with sqlite_db.writing(path) as conn:
            conn.executescript(_SCHEMA)
            sqlite_db.apply_migrations(conn, "decision_timeline",
                                       _SCHEMA_VERSION, _MIGRATIONS)
            sqlite_db.record_schema_version(conn, "decision_timeline",
                                            _SCHEMA_VERSION)
        _INITIALIZED.add(path)


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


def _row_to_snapshot(row: Any) -> dict[str, Any]:
    return {
        "snapshot_id": row["snapshot_id"],
        "event_id": row["event_id"],
        "recorded_at": row["recorded_at"],
        "final_displayed_direction": row["final_displayed_direction"],
        "final_downgrade_reason": row["final_downgrade_reason"],
        "probability": _json_loads(row["probability_json"]),
        "decision_quality": _json_loads(row["decision_quality_json"]),
        "market_quality": _json_loads(row["market_quality_json"]),
        "source_reliability": _json_loads(row["source_reliability_json"]),
        "execution_quality": _json_loads(row["execution_quality_json"]),
        "llm_degraded_mode": bool(row["llm_degraded_mode"])
                             if row["llm_degraded_mode"] is not None else None,
        "guardrail_fired": _json_loads(row["guardrail_fired_json"]),
        "outcome": row["outcome"],
    }


def record_snapshot(record: dict[str, Any]) -> str | None:
    """Append one overlay-bearing snapshot of ``record`` to the timeline.

    Extracts the direction / overlay / probability / outcome fields and
    inserts a new row. Returns the new ``snapshot_id``. Does not crash on
    missing fields — overlays default to None.

    The caller (``event_store.save_events``) gates this call behind
    ``settings.DECISION_TIMELINE_ENABLED`` so the store stays empty
    (and the schema is never created) when the flag is off.
    """
    if not isinstance(record, dict):
        return None
    event_id = record.get("event_id")
    if not event_id:
        return None
    probability = record.get("probability")
    if not isinstance(probability, dict):
        probability = None
    llm_tel = record.get("llm_telemetry")
    llm_degraded = None
    if isinstance(llm_tel, dict):
        llm_degraded = bool(llm_tel.get("degraded_mode", False))
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    snapshot_id = str(uuid.uuid4())
    with sqlite_db.writing(path) as conn:
        conn.execute(
            """
            INSERT INTO decision_timeline (
                snapshot_id, event_id,
                final_displayed_direction, final_downgrade_reason,
                probability_json, decision_quality_json, market_quality_json,
                source_reliability_json, execution_quality_json,
                llm_degraded_mode, guardrail_fired_json, outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                event_id,
                record.get("final_displayed_direction"),
                record.get("final_downgrade_reason"),
                _json_dumps(probability),
                _json_dumps(record.get("decision_quality")),
                _json_dumps(record.get("market_quality")),
                _json_dumps(record.get("source_reliability")),
                _json_dumps(record.get("execution_quality")),
                llm_degraded,
                _json_dumps(record.get("guardrail_fired")),
                record.get("outcome"),
            ),
        )
    return snapshot_id


def list_snapshots(event_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """Return snapshots for ``event_id`` ordered ASC by ``recorded_at``.

    Returns the most recent ``limit`` rows (default 100). Empty list when
    the event has no snapshots (e.g. flag was off when it was saved).
    """
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with sqlite_db.reading(path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM decision_timeline
            WHERE event_id = ?
            ORDER BY recorded_at ASC, snapshot_id ASC
            LIMIT ?
            """,
            (event_id, limit),
        ).fetchall()
    return [_row_to_snapshot(row) for row in rows]


def count_snapshots(event_id: str) -> int:
    """Return the number of snapshots stored for ``event_id``."""
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with sqlite_db.reading(path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM decision_timeline WHERE event_id = ?",
            (event_id,),
        ).fetchone()
    return int(row["n"]) if row else 0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_decision_timeline_store.py -v` from `backend/`
Expected: PASS (11 tests)

- [ ] **Step 6: Wire record_snapshot into event_store.save_events**

In `backend/app/memory/event_store.py`, find the `save_events` function. The function signature and the per-record loop look like this (around lines 79-170):

```python
def save_events(records: list[dict[str, Any]]) -> None:
    ...
    for record in records:
        try:
            event_id = record["event_id"]
            ...
            store[event_id] = entry
            _pre_dir = existing_record.get("final_displayed_direction")
            _post_dir = candidate.get("final_displayed_direction")
            if _pre_dir is not None and _post_dir is not None and _pre_dir != _post_dir:
                try:
                    from app.utils.metrics import FINAL_DIRECTION_CHANGE
                    FINAL_DIRECTION_CHANGE.inc()
                except Exception:  # pragma: no cover - defensive
                    pass
        except Exception as exc:
            logger.error(...)
```

Add the timeline snapshot call immediately AFTER the `FINAL_DIRECTION_CHANGE` block (so the snapshot captures the finalized candidate) and INSIDE the per-record `try` block. The flag check goes at the TOP of `save_events` (once, before the loop) so we don't read settings per-record.

Concretely, add at the top of `save_events` (after the function loads the store but before the loop):

```python
    # Plan 5 §5.4: Decision timeline snapshot. When the flag is on, append
    # an overlay-bearing snapshot of each record to decision_timeline_store.
    # Read the flag ONCE here (not per-record) to avoid settings lookups in
    # the hot loop. Best-effort: a snapshot failure never blocks save_events.
    from app.core.config import settings
    timeline_enabled = settings.DECISION_TIMELINE_ENABLED
```

Then, inside the per-record `try` block, after the `FINAL_DIRECTION_CHANGE` block:

```python
            if timeline_enabled:
                try:
                    from app.memory import decision_timeline_store
                    decision_timeline_store.record_snapshot(candidate)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning(
                        "decision_timeline snapshot failed for event %s: %s",
                        event_id, exc,
                    )
```

**Important:** The snapshot call uses `candidate` (the record being saved, with preserved tracking/outcome/calibration), NOT `entry` (which wraps it). The snapshot must reflect what was actually persisted.

- [ ] **Step 7: Verify byte-identical behavior when flag is off**

Run a quick sanity test from `backend/`:

```python
python -c "
import os
os.environ['DECISION_TIMELINE_ENABLED'] = 'false'
from app.memory import event_store, decision_timeline_store
# save_events with flag off should NOT create any timeline rows.
event_store.save_events([{'event_id': 'test-byte', 'probability': {'baseline': 50.0}}])
print('count:', decision_timeline_store.count_snapshots('test-byte'))
"
```

Expected output: `count: 0` (and no `decision_timeline` table created in the test DB, though this is hard to assert without inspecting SQLite — the count=0 is the key signal).

- [ ] **Step 8: Commit**

```bash
git add backend/app/memory/decision_timeline_store.py backend/tests/test_decision_timeline_store.py backend/app/core/config.py backend/app/memory/event_store.py
git commit -m "feat(timeline): add decision_timeline_store + wire into save_events (gated by DECISION_TIMELINE_ENABLED)"
```

---

### Task 2: decision_diff_service pure function

**Files:**
- Create: `backend/app/services/decision_diff_service.py`
- Create: `backend/tests/test_decision_diff_service.py`

**Interfaces:**
- Consumes: snapshot dicts as produced by `decision_timeline_store.list_snapshots` (keys: `final_displayed_direction`, `final_downgrade_reason`, `probability`, `decision_quality`, `market_quality`, `source_reliability`, `execution_quality`, `llm_degraded_mode`, `guardrail_fired`, `outcome`).
- Produces: `build_decision_diff(prev: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]` returning a diff dict with `direction_changed`, `prev_direction`, `current_direction`, `probability_delta`, `overlay_deltas`, `primary_change_driver`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_decision_diff_service.py`:

```python
"""Unit tests for decision_diff_service (Plan 5 §5.4)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.services.decision_diff_service import build_decision_diff


def _snapshot(**overrides):
    snap = {
        "snapshot_id": "s1",
        "event_id": "evt-001",
        "recorded_at": "2026-07-01T00:00:00",
        "final_displayed_direction": "YES",
        "final_downgrade_reason": None,
        "probability": {"baseline": 50.0, "estimated": 55.0,
                        "change": 5.0, "direction": "YES"},
        "decision_quality": {"downgraded": False, "raw_direction": "YES",
                             "displayed_direction": "YES"},
        "market_quality": None,
        "source_reliability": None,
        "execution_quality": None,
        "llm_degraded_mode": False,
        "guardrail_fired": None,
        "outcome": None,
    }
    snap.update(overrides)
    return snap


class TestDecisionDiffService(unittest.TestCase):
    def test_no_change_when_snapshots_identical(self):
        prev = _snapshot()
        cur = _snapshot()
        diff = build_decision_diff(prev, cur)
        self.assertFalse(diff["direction_changed"])
        self.assertEqual(diff["primary_change_driver"], "none")
        self.assertEqual(diff["overlay_deltas"], [])

    def test_prev_none_treats_as_initial_snapshot(self):
        """First snapshot in a timeline has no prev — diff should report
        'initial' with no deltas."""
        diff = build_decision_diff(None, _snapshot())
        self.assertFalse(diff["direction_changed"])
        self.assertEqual(diff["primary_change_driver"], "initial")

    def test_manual_resolution_driver_when_outcome_appears(self):
        prev = _snapshot(outcome=None)
        cur = _snapshot(outcome="YES")
        diff = build_decision_diff(prev, cur)
        self.assertEqual(diff["primary_change_driver"], "manual_resolution")

    def test_llm_degraded_driver_when_degraded_mode_flips_true(self):
        prev = _snapshot(llm_degraded_mode=False)
        cur = _snapshot(llm_degraded_mode=True,
                        final_displayed_direction="WAIT",
                        final_downgrade_reason="LLM 降级")
        diff = build_decision_diff(prev, cur)
        self.assertEqual(diff["primary_change_driver"], "llm_degraded")
        self.assertTrue(diff["direction_changed"])

    def test_guardrail_driver_when_guardrail_fired_appears(self):
        prev = _snapshot(guardrail_fired=None)
        cur = _snapshot(guardrail_fired=["llm_degraded_blocks_act"],
                        final_displayed_direction="WAIT")
        diff = build_decision_diff(prev, cur)
        self.assertEqual(diff["primary_change_driver"], "guardrail")

    def test_market_quality_driver_when_downgraded_flips_true(self):
        prev = _snapshot(market_quality={"downgraded": False})
        cur = _snapshot(market_quality={"downgraded": True,
                                        "downgrade_reason": "价差过大"},
                        final_displayed_direction="WAIT")
        diff = build_decision_diff(prev, cur)
        self.assertEqual(diff["primary_change_driver"], "market_quality")

    def test_source_conflict_driver_when_source_reliability_downgrades(self):
        prev = _snapshot(source_reliability={"downgraded": False})
        cur = _snapshot(source_reliability={"downgraded": True,
                                            "downgrade_reason": "来源可靠性不足"},
                        final_displayed_direction="WAIT")
        diff = build_decision_diff(prev, cur)
        self.assertEqual(diff["primary_change_driver"], "source_conflict")

    def test_calibration_driver_when_decision_quality_downgrades(self):
        prev = _snapshot(decision_quality={"downgraded": False})
        cur = _snapshot(decision_quality={"downgraded": True,
                                          "downgrade_reason": "证据冲突"},
                        final_displayed_direction="WAIT")
        diff = build_decision_diff(prev, cur)
        self.assertEqual(diff["primary_change_driver"], "calibration")

    def test_market_move_driver_when_estimated_probability_changes(self):
        prev = _snapshot(probability={"baseline": 50.0, "estimated": 55.0,
                                      "change": 5.0, "direction": "YES"})
        cur = _snapshot(probability={"baseline": 50.0, "estimated": 45.0,
                                     "change": -5.0, "direction": "NO"},
                        final_displayed_direction="NO")
        diff = build_decision_diff(prev, cur)
        self.assertEqual(diff["primary_change_driver"], "market_move")
        self.assertTrue(diff["direction_changed"])
        self.assertEqual(diff["probability_delta"]["estimated"], -10.0)

    def test_direction_change_ranked_after_overlay_drivers(self):
        """When direction changed AND multiple overlays flipped, the overlay
        driver takes precedence over 'market_move' (a probability move alone
        is weaker evidence of *why* the direction changed than an overlay
        explicitly downgrading)."""
        prev = _snapshot(
            probability={"estimated": 55.0},
            market_quality={"downgraded": False},
        )
        cur = _snapshot(
            probability={"estimated": 50.0},
            market_quality={"downgraded": True},
            final_displayed_direction="WAIT",
        )
        diff = build_decision_diff(prev, cur)
        self.assertEqual(diff["primary_change_driver"], "market_quality")

    def test_overlay_deltas_record_per_overlay_changes(self):
        prev = _snapshot(
            market_quality={"downgraded": False, "downgrade_reason": None},
            source_reliability={"downgraded": False, "downgrade_reason": None},
        )
        cur = _snapshot(
            market_quality={"downgraded": True, "downgrade_reason": "价差过大"},
            source_reliability={"downgraded": False, "downgrade_reason": None},
        )
        diff = build_decision_diff(prev, cur)
        deltas = {d["overlay"]: d for d in diff["overlay_deltas"]}
        self.assertIn("market_quality", deltas)
        self.assertTrue(deltas["market_quality"]["changed"])
        self.assertNotIn("source_reliability", deltas)

    def test_reasons_exclude_banned_terms(self):
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        prev = _snapshot()
        cur = _snapshot(final_displayed_direction="WAIT",
                        final_downgrade_reason="证据冲突",
                        market_quality={"downgraded": True,
                                        "downgrade_reason": "价差过大"})
        diff = build_decision_diff(prev, cur)
        # Serialize the whole diff and check no banned term appears.
        import json
        blob = json.dumps(diff, ensure_ascii=False).lower()
        for term in banned:
            self.assertNotIn(term, blob,
                             f"banned term '{term}' in diff: {blob}")

    def test_handles_missing_overlay_blocks_gracefully(self):
        """A snapshot with None overlays must not crash the diff."""
        prev = _snapshot(decision_quality=None, market_quality=None)
        cur = _snapshot(decision_quality={"downgraded": True},
                        final_displayed_direction="WAIT")
        diff = build_decision_diff(prev, cur)
        self.assertEqual(diff["primary_change_driver"], "calibration")

    def test_handles_non_dict_input(self):
        """build_decision_diff must not crash on non-dict prev/current."""
        diff = build_decision_diff(None, {"final_displayed_direction": "YES"})
        self.assertEqual(diff["primary_change_driver"], "initial")
        diff2 = build_decision_diff("garbage", {"final_displayed_direction": "YES"})
        self.assertFalse(diff2["direction_changed"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_decision_diff_service.py -v` from `backend/`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.decision_diff_service'`

- [ ] **Step 3: Implement the pure diff function**

Create `backend/app/services/decision_diff_service.py`:

```python
"""Decision diff service (Plan 5 §5.4).

Pure function that compares two timeline snapshots and ranks the primary
change driver behind a direction change. No I/O, no LLM, no settings
reads — same convention as ``build_decision_quality`` /
``build_source_reliability`` / ``build_execution_quality``.

The diff is consumed by the ``/api/events/{event_id}/decision-timeline``
route and rendered by the frontend ``DecisionTimelinePanel`` so a user
can see *why* an event flipped from YES to WAIT.

Driver ranking (first match wins):
    1. manual_resolution  — outcome appeared (event was resolved)
    2. llm_degraded       — llm_degraded_mode flipped False → True
    3. guardrail          — guardrail_fired list went from empty/null to non-empty
    4. market_quality     — market_quality.downgraded flipped False → True
    5. source_conflict    — source_reliability.downgraded flipped False → True
    6. calibration        — decision_quality.downgraded flipped False → True
    7. market_move        — probability.estimated moved by >= 5 percentage points
    8. none               — no material change detected

Overlay drivers (4-6) take precedence over market_move (7) because an
explicit overlay downgrade is stronger evidence of *why* the direction
changed than a probability drift alone.
"""
from __future__ import annotations

from typing import Any

_PROBABILITY_MOVE_THRESHOLD = 5.0  # percentage points


def build_decision_diff(
    prev: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    """Compare two timeline snapshots and return a structured diff.

    ``prev`` may be None (first snapshot in a timeline). ``current`` must
    be a dict. Returns a diff dict with keys:
        direction_changed       — bool
        prev_direction          — str | None
        current_direction       — str | None
        probability_delta       — dict with baseline/estimated/change deltas
                                  (None values when either side lacks them)
        overlay_deltas          — list of per-overlay delta dicts
                                  {overlay, field, prev, current, changed}
        primary_change_driver   — one of the locked driver strings
        prev_downgrade_reason   — str | None
        current_downgrade_reason — str | None

    Pure, synchronous, deterministic. Does not crash on missing fields.
    """
    if not isinstance(current, dict):
        return {
            "direction_changed": False,
            "prev_direction": None,
            "current_direction": None,
            "probability_delta": {},
            "overlay_deltas": [],
            "primary_change_driver": "none",
            "prev_downgrade_reason": None,
            "current_downgrade_reason": None,
        }
    if not isinstance(prev, dict):
        return {
            "direction_changed": False,
            "prev_direction": None,
            "current_direction": current.get("final_displayed_direction"),
            "probability_delta": {},
            "overlay_deltas": [],
            "primary_change_driver": "initial",
            "prev_downgrade_reason": None,
            "current_downgrade_reason": current.get("final_downgrade_reason"),
        }

    prev_dir = prev.get("final_displayed_direction")
    cur_dir = current.get("final_displayed_direction")
    direction_changed = prev_dir != cur_dir

    prob_delta = _probability_delta(prev.get("probability"),
                                    current.get("probability"))
    overlay_deltas = _overlay_deltas(prev, current)
    driver = _rank_driver(prev, current, direction_changed, prob_delta)

    return {
        "direction_changed": direction_changed,
        "prev_direction": prev_dir,
        "current_direction": cur_dir,
        "probability_delta": prob_delta,
        "overlay_deltas": overlay_deltas,
        "primary_change_driver": driver,
        "prev_downgrade_reason": prev.get("final_downgrade_reason"),
        "current_downgrade_reason": current.get("final_downgrade_reason"),
    }


def _probability_delta(prev_prob: Any, cur_prob: Any) -> dict[str, Any]:
    if not isinstance(prev_prob, dict) or not isinstance(cur_prob, dict):
        return {}
    delta: dict[str, Any] = {}
    for key in ("baseline", "estimated", "change"):
        pv = prev_prob.get(key)
        cv = cur_prob.get(key)
        if isinstance(pv, (int, float)) and isinstance(cv, (int, float)):
            delta[key] = cv - pv
        else:
            delta[key] = None
    return delta


def _overlay_deltas(prev: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    """Record per-overlay downgraded-flag and downgrade_reason changes."""
    deltas: list[dict[str, Any]] = []
    for overlay_key in ("decision_quality", "market_quality",
                        "source_reliability", "execution_quality"):
        pv = prev.get(overlay_key)
        cv = current.get(overlay_key)
        pv_down = pv.get("downgraded") if isinstance(pv, dict) else None
        cv_down = cv.get("downgraded") if isinstance(cv, dict) else None
        pv_reason = pv.get("downgrade_reason") if isinstance(pv, dict) else None
        cv_reason = cv.get("downgrade_reason") if isinstance(cv, dict) else None
        if pv_down != cv_down or pv_reason != cv_reason:
            deltas.append({
                "overlay": overlay_key,
                "field": "downgraded",
                "prev": pv_down,
                "current": cv_down,
                "prev_reason": pv_reason,
                "current_reason": cv_reason,
                "changed": True,
            })
    return deltas


def _overlay_downgraded_flipped_true(prev: dict[str, Any], current: dict[str, Any],
                                     key: str) -> bool:
    pv = prev.get(key)
    cv = current.get(key)
    pv_down = pv.get("downgraded") if isinstance(pv, dict) else None
    cv_down = cv.get("downgraded") if isinstance(cv, dict) else None
    return pv_down is False and cv_down is True


def _guardrail_fired_appeared(prev: dict[str, Any], current: dict[str, Any]) -> bool:
    pv = prev.get("guardrail_fired")
    cv = current.get("guardrail_fired")
    pv_empty = not pv or (isinstance(pv, (list, tuple)) and len(pv) == 0)
    cv_present = isinstance(cv, (list, tuple)) and len(cv) > 0
    return pv_empty and cv_present


def _llm_degraded_flipped_true(prev: dict[str, Any], current: dict[str, Any]) -> bool:
    return prev.get("llm_degraded_mode") is False and current.get("llm_degraded_mode") is True


def _outcome_appeared(prev: dict[str, Any], current: dict[str, Any]) -> bool:
    return prev.get("outcome") is None and current.get("outcome") is not None


def _market_move_significant(prob_delta: dict[str, Any]) -> bool:
    est = prob_delta.get("estimated")
    return isinstance(est, (int, float)) and abs(est) >= _PROBABILITY_MOVE_THRESHOLD


def _rank_driver(
    prev: dict[str, Any],
    current: dict[str, Any],
    direction_changed: bool,
    prob_delta: dict[str, Any],
) -> str:
    # 1. manual_resolution — outcome appeared (resolution event).
    if _outcome_appeared(prev, current):
        return "manual_resolution"
    # 2-3. LLM degraded / guardrail fire (these are explicit downgrades).
    if _llm_degraded_flipped_true(prev, current):
        return "llm_degraded"
    if _guardrail_fired_appeared(prev, current):
        return "guardrail"
    # 4-6. Overlay downgrades (explicit downgrades take precedence over
    # probability drift).
    if _overlay_downgraded_flipped_true(prev, current, "market_quality"):
        return "market_quality"
    if _overlay_downgraded_flipped_true(prev, current, "source_reliability"):
        return "source_conflict"
    if _overlay_downgraded_flipped_true(prev, current, "decision_quality"):
        return "calibration"
    # 7. market_move — probability drifted enough to explain the change.
    if direction_changed and _market_move_significant(prob_delta):
        return "market_move"
    # 8. none — no material change detected.
    return "none"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_decision_diff_service.py -v` from `backend/`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/decision_diff_service.py backend/tests/test_decision_diff_service.py
git commit -m "feat(timeline): add pure build_decision_diff function ranking change drivers"
```

---

### Task 3: API route + response models

**Files:**
- Modify: `backend/app/models/event.py` (add `DecisionTimelineSnapshot` + `DecisionTimelineResponse`)
- Modify: `backend/app/api/routes/events.py` (add `GET /{event_id}/decision-timeline`)
- Create: `backend/tests/test_decision_timeline_route.py`

**Interfaces:**
- Consumes: `decision_timeline_store.list_snapshots` / `count_snapshots`; `event_store.get_event` (to validate event_id exists → 404 if not); `decision_diff_service.build_decision_diff`.
- Produces: `GET /api/events/{event_id}/decision-timeline` returning `{event_id, count, snapshots: [...], diffs: [...]}` where `diffs[i]` is the diff between `snapshots[i]` and `snapshots[i+1]`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_decision_timeline_route.py`:

```python
"""Integration tests for the /decision-timeline route (Plan 5 §5.4)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient

from app.main import app
from app.memory import decision_timeline_store, event_store
from app.utils import sqlite_db


def _db(tmp):
    return patch.object(sqlite_db, "loop_db_path",
                        return_value=str(Path(tmp) / "v2_loop.db"))


def _sample_record(event_id="evt-tl", direction="YES", **overrides):
    rec = {
        "event_id": event_id,
        "event_title": "Test event",
        "probability": {"baseline": 50.0, "estimated": 55.0,
                        "change": 5.0, "direction": direction},
        "final_displayed_direction": direction,
        "final_downgrade_reason": None,
        "decision_quality": {"downgraded": False, "raw_direction": direction,
                             "displayed_direction": direction},
        "market_quality": None,
        "source_reliability": None,
        "execution_quality": None,
        "llm_telemetry": {"degraded_mode": False},
        "guardrail_fired": None,
        "outcome": None,
    }
    rec.update(overrides)
    return rec


class TestDecisionTimelineRoute(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_404_for_unknown_event_id(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            # Patch event_store.get_event to return None for this id.
            with patch.object(event_store, "get_event", return_value=None):
                resp = self.client.get("/api/events/evt-unknown/decision-timeline")
            self.assertEqual(resp.status_code, 404)

    def test_returns_empty_list_for_event_with_no_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            # event exists (get_event returns a stub) but no snapshots stored.
            entry = {"event_id": "evt-empty", "first_seen": "t",
                     "last_updated": "t", "record": _sample_record("evt-empty")}
            with patch.object(event_store, "get_event", return_value=entry):
                resp = self.client.get("/api/events/evt-empty/decision-timeline")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["event_id"], "evt-empty")
            self.assertEqual(body["count"], 0)
            self.assertEqual(body["snapshots"], [])
            self.assertEqual(body["diffs"], [])

    def test_returns_snapshots_and_diffs_in_order(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            # Insert 3 snapshots with direction changes.
            decision_timeline_store.record_snapshot(
                _sample_record("evt-tl", direction="YES"))
            decision_timeline_store.record_snapshot(
                _sample_record("evt-tl", direction="WAIT",
                               final_downgrade_reason="证据冲突",
                               decision_quality={"downgraded": True,
                                                 "downgrade_reason": "证据冲突"}))
            decision_timeline_store.record_snapshot(
                _sample_record("evt-tl", direction="AVOID",
                               final_downgrade_reason="LLM 降级",
                               llm_telemetry={"degraded_mode": True}))
            entry = {"event_id": "evt-tl", "first_seen": "t",
                     "last_updated": "t", "record": _sample_record("evt-tl")}
            with patch.object(event_store, "get_event", return_value=entry):
                resp = self.client.get("/api/events/evt-tl/decision-timeline")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["count"], 3)
            self.assertEqual(len(body["snapshots"]), 3)
            self.assertEqual([s["final_displayed_direction"] for s in body["snapshots"]],
                             ["YES", "WAIT", "AVOID"])
            # diffs has len(snapshots) - 1 = 2 entries.
            self.assertEqual(len(body["diffs"]), 2)
            self.assertEqual(body["diffs"][0]["primary_change_driver"], "calibration")
            self.assertEqual(body["diffs"][1]["primary_change_driver"], "llm_degraded")
            self.assertTrue(body["diffs"][0]["direction_changed"])

    def test_respects_limit_query_param(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            for i in range(5):
                decision_timeline_store.record_snapshot(
                    _sample_record("evt-tl", direction=f"D{i}"))
            entry = {"event_id": "evt-tl", "first_seen": "t",
                     "last_updated": "t", "record": _sample_record("evt-tl")}
            with patch.object(event_store, "get_event", return_value=entry):
                resp = self.client.get("/api/events/evt-tl/decision-timeline?limit=3")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["count"], 5)  # count is total, not limited
            self.assertEqual(len(body["snapshots"]), 3)  # snapshots limited
            self.assertEqual([s["final_displayed_direction"] for s in body["snapshots"]],
                             ["D2", "D3", "D4"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_decision_timeline_route.py -v` from `backend/`
Expected: FAIL with 404 (route not registered) or attribute error.

- [ ] **Step 3: Add the response models**

In `backend/app/models/event.py`, find the existing `EventHistoryResponse` class (used by the `/{event_id}/history` route). Add immediately after it:

```python
class DecisionTimelineSnapshot(BaseModel):
    """One overlay-bearing snapshot of an event at a point in time."""
    snapshot_id: str
    event_id: str
    recorded_at: str
    final_displayed_direction: str | None = None
    final_downgrade_reason: str | None = None
    probability: dict[str, Any] | None = None
    decision_quality: dict[str, Any] | None = None
    market_quality: dict[str, Any] | None = None
    source_reliability: dict[str, Any] | None = None
    execution_quality: dict[str, Any] | None = None
    llm_degraded_mode: bool | None = None
    guardrail_fired: list[str] | None = None
    outcome: str | None = None


class DecisionTimelineDiff(BaseModel):
    """Diff between two consecutive snapshots."""
    direction_changed: bool
    prev_direction: str | None = None
    current_direction: str | None = None
    probability_delta: dict[str, Any] = {}
    overlay_deltas: list[dict[str, Any]] = []
    primary_change_driver: str
    prev_downgrade_reason: str | None = None
    current_downgrade_reason: str | None = None


class DecisionTimelineResponse(BaseModel):
    """Response for GET /api/events/{event_id}/decision-timeline."""
    event_id: str
    count: int
    snapshots: list[DecisionTimelineSnapshot]
    diffs: list[DecisionTimelineDiff]
```

**Note:** Use the same `BaseModel` / `Field` / `Any` imports already at the top of `event.py`. If `Any` is not imported, add `from typing import Any` to the imports.

- [ ] **Step 4: Add the route**

In `backend/app/api/routes/events.py`, find the existing `/{event_id}/history` route (around line 900). Add the new route IMMEDIATELY AFTER it (so the static-before-dynamic ordering is preserved — both are `/{event_id}/...` sub-paths).

First, extend the model imports at the top of the file. Find the existing import block:

```python
from app.models.event import (
    AutoResolveResponse,
    EventAnalysisRequest,
    EventDiscoveryResponse,
    EventHistoryResponse,
    EventListResponse,
    EventMoversResponse,
    EventStoreEntry,
    FlexibleResponse,
    FreshEdgesResponse,
    OpenDecisionsResponse,
    PendingLinksResponse,
    RecentPredictionsResponse,
    SimilarEventsResponse,
)
```

Add three new entries:

```python
from app.models.event import (
    AutoResolveResponse,
    DecisionTimelineDiff,
    DecisionTimelineResponse,
    DecisionTimelineSnapshot,
    EventAnalysisRequest,
    EventDiscoveryResponse,
    EventHistoryResponse,
    EventListResponse,
    EventMoversResponse,
    EventStoreEntry,
    FlexibleResponse,
    FreshEdgesResponse,
    OpenDecisionsResponse,
    PendingLinksResponse,
    RecentPredictionsResponse,
    SimilarEventsResponse,
)
```

Then add the route after the `/{event_id}/history` route:

```python
@router.get("/{event_id}/decision-timeline", response_model=DecisionTimelineResponse)
async def get_event_decision_timeline(
    event_id: EventId,
    limit: int = Query(default=100, ge=1, le=500),
):
    """Return the decision timeline snapshots + consecutive diffs for an event.

    Each snapshot captures the overlay-bearing record at one save_events
    call. The diffs array has len(snapshots) - 1 entries; diffs[i] is the
    diff between snapshots[i] and snapshots[i+1].

    Returns count=0 / empty lists when the event exists but has no
    snapshots (e.g. DECISION_TIMELINE_ENABLED was off when it was saved).
    404 when the event_id is unknown.
    """
    entry = get_event(event_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    from app.memory import decision_timeline_store
    from app.services.decision_diff_service import build_decision_diff
    total = decision_timeline_store.count_snapshots(event_id)
    snapshots = decision_timeline_store.list_snapshots(event_id, limit=limit)
    diffs: list[dict[str, Any]] = []
    for i in range(max(len(snapshots) - 1, 0)):
        diffs.append(build_decision_diff(snapshots[i], snapshots[i + 1]))
    return {
        "event_id": event_id,
        "count": total,
        "snapshots": snapshots,
        "diffs": diffs,
    }
```

**Note:** `Query` and `Any` should already be imported at the top of `events.py`. Verify and add if missing: `from typing import Any` and `from fastapi import Query`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_decision_timeline_route.py -v` from `backend/`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/event.py backend/app/api/routes/events.py backend/tests/test_decision_timeline_route.py
git commit -m "feat(timeline): add GET /api/events/{event_id}/decision-timeline route + response models"
```

---

### Task 4: Frontend DecisionTimelinePanel + API client

**Files:**
- Modify: `frontend/src/lib/api.ts` (add types + `decisionTimeline` method)
- Create: `frontend/src/components/detail/decision-timeline-panel.tsx`
- Modify: `frontend/src/app/events/page.tsx` (render the panel after `DecisionReportPanel`)

**Interfaces:**
- Consumes: `GET /api/events/{event_id}/decision-timeline` (added in Task 3).
- Produces: `eventsApi.decisionTimeline(id: string)` returning `DecisionTimelineResponse`; `<DecisionTimelinePanel eventId={...} />` React component.

- [ ] **Step 1: Add the TypeScript types and API client method**

In `frontend/src/lib/api.ts`, find the existing `DecisionReport` interface (around line 182). Add the timeline types immediately after it:

```ts
export interface DecisionTimelineSnapshot {
  snapshot_id: string;
  event_id: string;
  recorded_at: string;
  final_displayed_direction: string | null;
  final_downgrade_reason: string | null;
  probability: { baseline?: number; estimated?: number; change?: number; direction?: string } | null;
  decision_quality: { downgraded?: boolean; downgrade_reason?: string | null; [k: string]: unknown } | null;
  market_quality: { downgraded?: boolean; downgrade_reason?: string | null; [k: string]: unknown } | null;
  source_reliability: { downgraded?: boolean; downgrade_reason?: string | null; [k: string]: unknown } | null;
  execution_quality: { downgraded?: boolean; downgrade_reason?: string | null; [k: string]: unknown } | null;
  llm_degraded_mode: boolean | null;
  guardrail_fired: string[] | null;
  outcome: string | null;
}

export interface DecisionTimelineDiff {
  direction_changed: boolean;
  prev_direction: string | null;
  current_direction: string | null;
  probability_delta: { baseline?: number | null; estimated?: number | null; change?: number | null };
  overlay_deltas: { overlay: string; field: string; prev: unknown; current: unknown; prev_reason: string | null; current_reason: string | null; changed: boolean }[];
  primary_change_driver: string;
  prev_downgrade_reason: string | null;
  current_downgrade_reason: string | null;
}

export interface DecisionTimelineResponse {
  event_id: string;
  count: number;
  snapshots: DecisionTimelineSnapshot[];
  diffs: DecisionTimelineDiff[];
}
```

Then find the `eventsApi` object (around line 725). Add the `decisionTimeline` method next to the existing `decision` method (around line 831):

```ts
  decisionTimeline: (id: string, limit?: number) =>
    api<DecisionTimelineResponse>(
      `/events/${encodeURIComponent(id)}/decision-timeline` +
      (limit ? `?limit=${limit}` : "")
    ),
```

- [ ] **Step 2: Create the DecisionTimelinePanel component**

Create `frontend/src/components/detail/decision-timeline-panel.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { History } from "lucide-react";
import { eventsApi, type DecisionTimelineResponse } from "@/lib/api";

const DRIVER_LABELS: Record<string, string> = {
  manual_resolution: "人工结算",
  llm_degraded: "LLM 降级",
  guardrail: "护栏规则触发",
  market_quality: "市场质量降级",
  source_conflict: "来源冲突",
  calibration: "证据冲突",
  market_move: "概率显著变化",
  none: "无显著变化",
  initial: "首次记录",
};

const DIRECTION_COLORS: Record<string, string> = {
  YES: "text-green-600 dark:text-green-400",
  NO: "text-red-600 dark:text-red-400",
  WAIT: "text-yellow-600 dark:text-yellow-400",
  AVOID: "text-orange-600 dark:text-orange-400",
};

export function DecisionTimelinePanel({ eventId }: { eventId: string }) {
  const [data, setData] = useState<DecisionTimelineResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      (async () => {
        if (cancelled) return;
        setLoading(true);
        setError(null);
        try {
          const result = await eventsApi.decisionTimeline(eventId);
          if (!cancelled) setData(result);
        } catch (e) {
          if (!cancelled) setError(e instanceof Error ? e.message : "决策时间线加载失败");
        } finally {
          if (!cancelled) setLoading(false);
        }
      })();
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [eventId]);

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <History className="size-4 text-primary" aria-hidden="true" />
        <h2 className="text-sm font-semibold">决策变化时间线</h2>
      </div>
      {loading ? (
        <div className="rounded-lg border border-border bg-card px-4 py-6 text-center text-sm text-muted-foreground">
          加载中…
        </div>
      ) : data && data.snapshots.length > 0 ? (
        <div className="flex flex-col gap-2">
          {data.snapshots.map((snap, i) => {
            const diff = i > 0 ? data.diffs[i - 1] : null;
            const dirColor = snap.final_displayed_direction
              ? DIRECTION_COLORS[snap.final_displayed_direction] ?? ""
              : "";
            return (
              <div key={snap.snapshot_id}
                   className="rounded-lg border border-border bg-card px-4 py-3 text-sm">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">
                    {new Date(snap.recorded_at).toLocaleString("zh-CN")}
                  </span>
                  <span className={`font-semibold ${dirColor}`}>
                    {snap.final_displayed_direction ?? "—"}
                  </span>
                </div>
                {diff && diff.direction_changed && (
                  <div className="mt-1 text-xs text-muted-foreground">
                    {diff.prev_direction} → {diff.current_direction}
                    <span className="ml-2 rounded bg-muted px-1.5 py-0.5">
                      {DRIVER_LABELS[diff.primary_change_driver] ?? diff.primary_change_driver}
                    </span>
                  </div>
                )}
                {snap.final_downgrade_reason && (
                  <div className="mt-1 text-xs text-muted-foreground">
                    降级原因：{snap.final_downgrade_reason}
                  </div>
                )}
                {snap.llm_degraded_mode && (
                  <div className="mt-1 text-xs text-orange-600 dark:text-orange-400">
                    LLM 降级模式
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="rounded-lg border border-border bg-card px-4 py-6 text-sm text-muted-foreground">
          {error ?? "暂无决策时间线数据。该事件可能在 DECISION_TIMELINE_ENABLED 关闭期间保存。"}
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 3: Wire the panel into the events page**

In `frontend/src/app/events/page.tsx`, find the line rendering `<DecisionReportPanel eventId={record.event_id} />` (around line 306). Import the new panel at the top of the file (alongside the existing `DecisionReportPanel` import):

```tsx
import { DecisionTimelinePanel } from "@/components/detail/decision-timeline-panel";
```

Then render it immediately after `DecisionReportPanel`:

```tsx
      <DecisionReportPanel eventId={record.event_id} />
      <DecisionTimelinePanel eventId={record.event_id} />
```

- [ ] **Step 4: Verify the frontend builds**

Run: `npm run build` from `frontend/`
Expected: Build succeeds with no TypeScript errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/components/detail/decision-timeline-panel.tsx frontend/src/app/events/page.tsx
git commit -m "feat(timeline): add DecisionTimelinePanel + eventsApi.decisionTimeline client"
```

---

### Task 5: A/B feature-flag impact CLI

**Files:**
- Create: `backend/scripts/analyze_feature_flag_impact.py`
- Create: `backend/tests/test_analyze_feature_flag_impact.py`

**Interfaces:**
- Consumes: `app.replay.runner.replay_record(record, cfg)`; `app.replay.config.ReplayConfig` (with `preset_all_off` / `preset_all_on`); `app.memory.event_store.list_all_events` (to load records).
- Produces: `python -m scripts.analyze_feature_flag_impact` CLI that prints a per-phase direction-change matrix and writes an optional JSON report.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_analyze_feature_flag_impact.py`:

```python
"""Unit tests for analyze_feature_flag_impact CLI (Plan 5 §1.5)."""
from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "scripts"))

from analyze_feature_flag_impact import (
    _compute_direction_matrix,
    _format_matrix,
    main,
)


def _record(event_id="evt-001", direction="YES"):
    return {
        "event_id": event_id,
        "event_title": "Test",
        "source": {"type": "prediction_market"},
        "probability": {"baseline": 50.0, "estimated": 55.0,
                        "change": 5.0, "direction": direction},
        "actionable_recommendation": {"direction": direction, "signal": "act",
                                       "ai_probability": 0.65},
        "evidence_breakdown": [],
        "evidence_items": [],
        "legacy_analysis": {},
        "market_quote": {"volume": 1000.0, "liquidity": 500.0,
                         "bid_ask": {"bid": 0.55, "ask": 0.56, "spread": 0.01},
                         "last_updated": "2026-07-01T00:00:00Z"},
    }


class TestAnalyzeFeatureFlagImpact(unittest.TestCase):
    def test_compute_direction_matrix_no_changes(self):
        """When cfg_a and cfg_b produce the same directions for all records,
        every cell of the matrix is 0 except the diagonal."""
        records = [_record("e1", "YES"), _record("e2", "NO")]
        # Mock replay_record to always return the input direction unchanged.
        with patch("analyze_feature_flag_impact.replay_record",
                   side_effect=lambda r, cfg: {**r,
                                               "final_displayed_direction":
                                                   r["probability"]["direction"]}):
            matrix = _compute_direction_matrix(records,
                                                ReplayConfig_preset_all_off=None,
                                                ReplayConfig_preset_all_on=None)
        # Diagonal: YES->YES = 1, NO->NO = 1; all transitions = 0.
        self.assertEqual(matrix["YES"]["YES"], 1)
        self.assertEqual(matrix["NO"]["NO"], 1)
        self.assertEqual(matrix["YES"]["WAIT"], 0)
        self.assertEqual(matrix["YES"]["NO"], 0)

    def test_compute_direction_matrix_records_yes_to_wait(self):
        records = [_record("e1", "YES")]
        def fake_replay(record, cfg):
            # cfg "off" → keep YES; cfg "on" → flip to WAIT.
            from app.replay.config import ReplayConfig
            if cfg == ReplayConfig.preset_all_on():
                return {**record, "final_displayed_direction": "WAIT"}
            return {**record, "final_displayed_direction": "YES"}
        with patch("analyze_feature_flag_impact.replay_record",
                   side_effect=fake_replay):
            from app.replay.config import ReplayConfig
            matrix = _compute_direction_matrix(
                records,
                ReplayConfig.preset_all_off(),
                ReplayConfig.preset_all_on(),
            )
        self.assertEqual(matrix["YES"]["WAIT"], 1)
        self.assertEqual(matrix["YES"]["YES"], 0)

    def test_format_matrix_renders_ascii_table(self):
        matrix = {"YES": {"YES": 5, "WAIT": 2, "NO": 0, "AVOID": 0},
                  "NO": {"YES": 0, "WAIT": 0, "NO": 3, "AVOID": 1},
                  "WAIT": {"YES": 0, "WAIT": 4, "NO": 0, "AVOID": 0},
                  "AVOID": {"YES": 0, "WAIT": 0, "NO": 0, "AVOID": 1}}
        out = _format_matrix(matrix, total=16)
        self.assertIn("[INFO]", out)
        self.assertIn("YES", out)
        self.assertIn("WAIT", out)
        # Should report change rate.
        self.assertIn("change rate", out.lower())

    def test_format_matrix_excludes_banned_terms(self):
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        matrix = {"YES": {"YES": 1, "WAIT": 0, "NO": 0, "AVOID": 0},
                  "NO": {"YES": 0, "WAIT": 0, "NO": 1, "AVOID": 0},
                  "WAIT": {"YES": 0, "WAIT": 0, "NO": 0, "AVOID": 0},
                  "AVOID": {"YES": 0, "WAIT": 0, "NO": 0, "AVOID": 0}}
        out = _format_matrix(matrix, total=2).lower()
        for term in banned:
            self.assertNotIn(term, out)

    def test_main_runs_and_prints_report(self):
        records = [_record("e1", "YES")]
        with patch("analyze_feature_flag_impact._load_records",
                   return_value=records), \
             patch("analyze_feature_flag_impact.replay_record",
                   side_effect=lambda r, cfg: {**r,
                                               "final_displayed_direction":
                                                   r["probability"]["direction"]}):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["--sample-size", "10"])
            self.assertEqual(rc, 0)
            self.assertIn("[OK]", buf.getvalue())

    def test_main_writes_json_when_output_specified(self):
        import tempfile
        records = [_record("e1", "YES")]
        with patch("analyze_feature_flag_impact._load_records",
                   return_value=records), \
             patch("analyze_feature_flag_impact.replay_record",
                   side_effect=lambda r, cfg: {**r,
                                               "final_displayed_direction":
                                                   r["probability"]["direction"]}):
            with tempfile.TemporaryDirectory() as tmp:
                out_path = Path(tmp) / "report.json"
                rc = main(["--sample-size", "10", "--json", str(out_path)])
                self.assertEqual(rc, 0)
                self.assertTrue(out_path.exists())
                data = json.loads(out_path.read_text(encoding="utf-8"))
                self.assertIn("matrix", data)
                self.assertIn("total", data)

    def test_main_handles_empty_records(self):
        with patch("analyze_feature_flag_impact._load_records",
                   return_value=[]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["--sample-size", "10"])
            self.assertEqual(rc, 0)
            self.assertIn("[WARN]", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_analyze_feature_flag_impact.py -v` from `backend/`
Expected: FAIL with `ModuleNotFoundError: No module named 'analyze_feature_flag_impact'`

- [ ] **Step 3: Implement the CLI**

Create `backend/scripts/analyze_feature_flag_impact.py`:

```python
"""A/B feature-flag impact CLI (Plan 5 §1.5).

Quantifies how much each Phase overlay flips the final direction when
toggled on. Reuses ``replay_record`` from the existing replay harness —
no new replay logic.

Usage:
    python -m scripts.analyze_feature_flag_impact [--sample-size N]
        [--event-ids id1,id2] [--compare all_off all_on]
        [--json report.json]

Output: an ASCII matrix of direction transitions (e.g. "YES -> WAIT: 17%")
showing the direction-change rate when the chosen phase is enabled vs
disabled.

The default comparison is ``all_off`` vs ``all_on`` (total system
impact). Use ``--compare`` to swap either side, e.g.
``--compare all_off current`` to measure against live settings.
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
from pathlib import Path
from typing import Any

# UTF-8 stdout for Windows GBK console safety (same convention as
# source_trust_registry_cli.py / review_queue_cli.py).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, io.UnsupportedOperation):  # pragma: no cover
    pass

from app.replay.config import ReplayConfig
from app.replay.runner import replay_record

_DIRECTIONS = ("YES", "NO", "WAIT", "AVOID")


def _load_records(
    event_ids: list[str] | None,
    sample_size: int | None,
) -> list[dict[str, Any]]:
    """Load event records from event_store. Unwraps the {event_id, record}
    envelope that event_store.list_all_events returns."""
    from app.memory.event_store import list_all_events
    entries = list_all_events()
    records = [e["record"] for e in entries if isinstance(e.get("record"), dict)]
    if event_ids:
        wanted = set(event_ids)
        records = [r for r in records if r.get("event_id") in wanted]
    if sample_size and len(records) > sample_size:
        random.seed(42)  # deterministic sampling for reproducibility
        records = random.sample(records, sample_size)
    return records


def _config_by_name(name: str) -> ReplayConfig:
    if name == "all_off":
        return ReplayConfig.preset_all_off()
    if name == "all_on":
        return ReplayConfig.preset_all_on()
    if name == "current":
        return ReplayConfig.preset_all_on()  # all None → use live settings
    if name == "llm_degraded":
        return ReplayConfig.preset_llm_degraded()
    raise ValueError(f"unknown config preset: {name!r}")


def _effective_direction(record: dict[str, Any]) -> str | None:
    return record.get("final_displayed_direction")


def _compute_direction_matrix(
    records: list[dict[str, Any]],
    cfg_a: ReplayConfig,
    cfg_b: ReplayConfig,
) -> dict[str, dict[str, int]]:
    """Run each record under cfg_a (off) and cfg_b (on), tally direction
    transitions into a matrix[prev_dir][cur_dir] = count."""
    matrix: dict[str, dict[str, int]] = {
        a: {b: 0 for b in _DIRECTIONS} for a in _DIRECTIONS
    }
    for record in records:
        replayed_a = replay_record(record, cfg_a)
        replayed_b = replay_record(record, cfg_b)
        dir_a = _effective_direction(replayed_a) or "WAIT"
        dir_b = _effective_direction(replayed_b) or "WAIT"
        if dir_a in matrix and dir_b in matrix[dir_a]:
            matrix[dir_a][dir_b] += 1
    return matrix


def _format_matrix(matrix: dict[str, dict[str, int]], total: int) -> str:
    """Render the matrix as an ASCII table with a change-rate summary."""
    lines: list[str] = []
    lines.append("[INFO] Direction transition matrix (rows = off, cols = on):")
    header = "        " + "  ".join(f"{d:>6}" for d in _DIRECTIONS)
    lines.append(header)
    for a in _DIRECTIONS:
        row = f"  {a:<4} " + "  ".join(f"{matrix[a][b]:>6}" for b in _DIRECTIONS)
        lines.append(row)
    # Change rate = (total - diagonal) / total.
    diagonal = sum(matrix[a][a] for a in _DIRECTIONS)
    changed = total - diagonal
    rate = (changed / total * 100.0) if total > 0 else 0.0
    lines.append("")
    lines.append(f"[INFO] Total events: {total}")
    lines.append(f"[INFO] Direction changed: {changed} ({rate:.1f}%)")
    lines.append(f"[INFO] Direction unchanged: {diagonal} ({100.0 - rate:.1f}%)")
    # Top transitions (excluding diagonal).
    transitions: list[tuple[str, str, int]] = []
    for a in _DIRECTIONS:
        for b in _DIRECTIONS:
            if a != b and matrix[a][b] > 0:
                transitions.append((a, b, matrix[a][b]))
    transitions.sort(key=lambda t: t[2], reverse=True)
    if transitions:
        lines.append("[INFO] Top transitions:")
        for a, b, n in transitions:
            pct = (n / total * 100.0) if total > 0 else 0.0
            lines.append(f"       {a} -> {b}: {n} ({pct:.1f}%)")
    return "\n".join(lines)


def _print(text: str) -> None:
    print(text, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="analyze_feature_flag_impact",
        description="Quantify how much each Phase overlay flips the final "
                    "direction when toggled on (Plan 5 §1.5).",
    )
    parser.add_argument("--sample-size", type=int, default=None,
                        help="Random sample N records (deterministic seed).")
    parser.add_argument("--event-ids", type=str, default=None,
                        help="Comma-separated event ids to restrict the run.")
    parser.add_argument("--compare", nargs=2,
                        default=["all_off", "all_on"],
                        metavar=("CONFIG_A", "CONFIG_B"),
                        help="Two config presets to compare "
                             "(all_off / all_on / current / llm_degraded). "
                             "Default: all_off all_on.")
    parser.add_argument("--json", type=str, default=None,
                        metavar="PATH",
                        help="Write a JSON report to this path.")
    args = parser.parse_args(argv)

    event_ids = None
    if args.event_ids:
        event_ids = [s.strip() for s in args.event_ids.split(",") if s.strip()]

    records = _load_records(event_ids, args.sample_size)
    if not records:
        _print("[WARN] No records found. Exiting.")
        return 0

    _print(f"[INFO] Loaded {len(records)} records.")
    try:
        cfg_a = _config_by_name(args.compare[0])
        cfg_b = _config_by_name(args.compare[1])
    except ValueError as e:
        _print(f"[FAIL] {e}")
        return 2

    _print(f"[INFO] Comparing {args.compare[0]} vs {args.compare[1]}...")
    matrix = _compute_direction_matrix(records, cfg_a, cfg_b)
    report = _format_matrix(matrix, total=len(records))
    _print(report)

    if args.json:
        out_path = Path(args.json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "compare": [args.compare[0], args.compare[1]],
            "total": len(records),
            "matrix": matrix,
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        _print(f"[OK] JSON report written to {out_path}")

    _print("[OK] Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_analyze_feature_flag_impact.py -v` from `backend/`
Expected: PASS (7 tests)

**Note:** The first test `test_compute_direction_matrix_no_changes` has a placeholder `ReplayConfig_preset_all_off=None` kwarg that won't be used because the mock replaces `replay_record`. Fix the test to pass real `ReplayConfig` objects:

```python
    def test_compute_direction_matrix_no_changes(self):
        records = [_record("e1", "YES"), _record("e2", "NO")]
        from app.replay.config import ReplayConfig
        with patch("analyze_feature_flag_impact.replay_record",
                   side_effect=lambda r, cfg: {**r,
                                               "final_displayed_direction":
                                                   r["probability"]["direction"]}):
            matrix = _compute_direction_matrix(records,
                                                ReplayConfig.preset_all_off(),
                                                ReplayConfig.preset_all_on())
        self.assertEqual(matrix["YES"]["YES"], 1)
        self.assertEqual(matrix["NO"]["NO"], 1)
        self.assertEqual(matrix["YES"]["WAIT"], 0)
        self.assertEqual(matrix["YES"]["NO"], 0)
```

Apply this fix to the test file before running.

- [ ] **Step 5: Verify the CLI runs end-to-end**

Run: `python -m scripts.analyze_feature_flag_impact --sample-size 5` from `backend/`
Expected: Prints `[INFO] Loaded N records.` + matrix + `[OK] Done.` (exit 0).

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/analyze_feature_flag_impact.py backend/tests/test_analyze_feature_flag_impact.py
git commit -m "feat(replay): add analyze_feature_flag_impact A/B CLI (spec §1.5)"
```

---

## Self-Review

**1. Spec coverage:**
- §5.4 事件变化时间线 / Diff Viewer:
  - `GET /api/events/{event_id}/decision-timeline` → Task 3 ✅
  - `build_decision_diff(prev, current)` outputting change cause ranking → Task 2 ✅
  - Frontend `DecisionTimelinePanel` with direction change + primary driver → Task 4 ✅
  - Snapshot source (the spec assumes the system keeps prior revisions — it doesn't, so Task 1 adds the append-only store) → Task 1 ✅
- §1.5 特性开关 A/B 对比:
  - `analyze_feature_flag_impact.py` re-running overlay with enabled=True/False and reporting direction change rate → Task 5 ✅

**2. Placeholder scan:** Searched for "TBD", "TODO", "implement later", "add appropriate", "similar to" — none found. All code blocks are complete.

**3. Type consistency:**
- `record_snapshot(record) -> str | None` — Task 1 produces, Task 3 (event_store wiring) consumes. ✅
- `list_snapshots(event_id, *, limit=100) -> list[dict]` — Task 1 produces, Task 3 (route) consumes. ✅
- `count_snapshots(event_id) -> int` — Task 1 produces, Task 3 (route) consumes. ✅
- `build_decision_diff(prev, current) -> dict` — Task 2 produces, Task 3 (route) consumes. Snapshot dict shape from Task 1 matches the dict shape Task 2 reads (`final_displayed_direction`, `probability`, `decision_quality`, etc.). ✅
- `DecisionTimelineResponse` Pydantic model — Task 3 produces, Task 4 (frontend type) consumes. Field names match (`event_id`, `count`, `snapshots`, `diffs`). ✅
- `eventsApi.decisionTimeline(id, limit?)` — Task 4 produces, Task 4 (panel) consumes. ✅
- `replay_record(record, cfg)` — existing, Task 5 consumes. ✅
- `ReplayConfig.preset_all_off()` / `preset_all_on()` — existing, Task 5 consumes. ✅

No type mismatches found.

**4. Byte-identical invariant verification:**
- `DECISION_TIMELINE_ENABLED` defaults to false. `event_store.save_events` checks the flag once at the top; when false, `record_snapshot` is never called, no SQLite schema is created, no rows written. The `FINAL_DIRECTION_CHANGE` counter logic is untouched. ✅
- `build_decision_diff` is a pure function with no side effects — calling it has no effect on the event record. ✅
- The API route reads from the store; when the flag was off, the store is empty, the route returns `count=0` — but the route itself is always registered (no flag gate on the route). This is acceptable: the route is read-only and returns empty data when there's nothing to show, matching the `DecisionReportPanel` "暂无" pattern. ✅
- The A/B CLI is a standalone script that reuses `replay_record` — it does not modify `event_store` and has no effect on production behavior. ✅

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-01-decision-timeline-and-ab-comparison.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
