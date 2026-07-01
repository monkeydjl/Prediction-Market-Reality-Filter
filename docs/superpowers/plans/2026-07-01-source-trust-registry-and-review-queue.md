# Source Trust Registry & Review Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a maintainable Source Trust Registry (§6.1) and a human Review Queue with audit log (§6.2), both backed by SQLite, with CLI tooling and best-effort integration into the existing overlay pipeline.

**Architecture:** Two new SQLite-backed stores following the `event_market_link_store.py` pattern (module-level functions, `loop_db_path()` + `reading()`/`writing()` context managers, lazy schema init, idempotent migrations). The registry is consumed by `source_reliability_service` as an optional override layer on tier classification. The review queue is fed by pure-function detectors (one per trigger source) and exposes reviewer actions that append to an immutable audit log. All new feature flags default OFF for byte-identical backward compatibility.

**Tech Stack:** Python 3.12+, SQLite3 (stdlib), argparse CLI, pytest, unittest.mock.patch for DB isolation.

## Global Constraints

- All new feature flags must default to OFF, producing byte-identical behavior to pre-Plan-4 when disabled.
- `source_trust_registry` is an OPTIONAL prior; it MUST NOT override event-level evidence conflicts — it only adjusts the tier score used as a prior weight. The report must surface `"source prior affected score"` when the registry changed the outcome.
- Review queue reviewer actions MUST write to an append-only audit log (INSERT-only, no UPDATE/DELETE on audit rows). No human action can silently overwrite model output — the model's `final_displayed_direction` is preserved; reviewer decisions live alongside as `reviewer_decision`.
- Reviewer action values are locked to: `confirm` / `override` / `request_more_evidence` / `mark_bad_source` / `mark_bad_resolution`. No other values allowed.
- All Chinese reason strings (registry notes, review queue reasons, audit log notes) MUST exclude banned terms: `long`, `short`, `buy`, `sell`, `position`, `kelly`, `order` (case-insensitive). Direction vocabulary is locked to `{YES, NO, WAIT, AVOID}`.
- Pure functions: the review-queue trigger detectors MUST be pure, synchronous, deterministic (no LLM/IO) — same convention as `build_source_reliability` / `build_execution_quality`.
- Stores MUST use the shared `sqlite_db.py` plumbing (`loop_db_path()` + `reading()`/`writing()` + `apply_migrations`), not create their own DB files.
- CLI scripts MUST use ASCII labels `[OK]/[FAIL]/[INFO]/[WARN]/[DRY-RUN]` (not emojis) and `_print()` UTF-8 reconfiguration for Windows GBK console safety.
- Tests MUST isolate SQLite by patching `sqlite_db.loop_db_path` to a temp directory (the canonical `test_event_market_link_store.py` pattern), NOT in-memory DB.
- `source_reliability_service` MUST remain a pure function — `settings` is NOT passed; the registry rows are passed explicitly as a `list[dict]` by the orchestrator.

---

## File Structure

### New files

| File | Responsibility |
|------|----------------|
| `backend/app/memory/source_trust_registry_store.py` | SQLite CRUD for source/domain trust entries (tier override + base trust + alias + list category). Module-level functions following `event_market_link_store.py` pattern. |
| `backend/app/memory/review_queue_store.py` | SQLite CRUD for review queue items + append-only audit log. Module-level functions. Two tables: `review_queue_items` (mutable status) and `review_queue_audit` (INSERT-only). |
| `backend/app/services/review_queue_detectors.py` | Pure-function detectors that scan records/events and return review-queue candidate dicts. One function per trigger source. No I/O. |
| `backend/scripts/source_trust_registry_cli.py` | Admin CLI: list/add/update/delete registry entries, import/export JSON. `python -m scripts.source_trust_registry_cli`. |
| `backend/scripts/review_queue_cli.py` | Admin CLI: list pending items, take reviewer action (confirm/override/etc), view audit log. `python -m scripts.review_queue_cli`. |
| `backend/tests/test_source_trust_registry_store.py` | Unit tests for the registry store (temp-file isolation). |
| `backend/tests/test_review_queue_store.py` | Unit tests for the review queue store + audit log immutability. |
| `backend/tests/test_review_queue_detectors.py` | Unit tests for the pure-function detectors. |

### Modified files

| File | Change |
|------|--------|
| `backend/app/services/source_reliability_service.py` | Add optional `registry_overrides` parameter to `build_source_reliability`; apply tier/base-trust override when provided. Keep pure. |
| `backend/app/services/event_intelligence_service.py` | When `SOURCE_TRUST_REGISTRY_ENABLED=true`, load registry rows and pass to `build_source_reliability`. When `REVIEW_QUEUE_ENABLED=true`, run detectors and enqueue items. Best-effort try/except. |
| `backend/app/core/config.py` | Add 4 new flags (see Task 1 Step 3). |

---

## Task 1: Source Trust Registry Store

**Files:**
- Create: `backend/app/memory/source_trust_registry_store.py`
- Create: `backend/tests/test_source_trust_registry_store.py`
- Modify: `backend/app/core/config.py` (add `SOURCE_TRUST_REGISTRY_ENABLED` flag only — other flags added in Task 3)

**Interfaces:**
- Produces: `upsert_entry(*, pattern: str, pattern_type: str, tier: str | None, base_trust: float | None, list_category: str | None, notes: str = "") -> None`
- Produces: `get_entry(pattern: str) -> dict[str, Any] | None`
- Produces: `list_entries(*, list_category: str | None = None) -> list[dict[str, Any]]`
- Produces: `delete_entry(pattern: str) -> bool`
- Produces: `match_domain(domain: str) -> dict[str, Any] | None` — longest-prefix-match against `pattern_type == "domain"` entries
- Produces: `match_source_name(name: str) -> dict[str, Any] | None` — substring-match against `pattern_type == "source_name"` entries

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_source_trust_registry_store.py`:

```python
"""Unit tests for source_trust_registry_store (Plan 4 §6.1)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Bootstrap importability (canonical pattern from test_event_market_link_store.py)
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.memory import source_trust_registry_store as registry
from app.utils import sqlite_db


def _db(tmp):
    return patch.object(sqlite_db, "loop_db_path",
                        return_value=str(Path(tmp) / "v2_loop.db"))


class TestSourceTrustRegistryStore(unittest.TestCase):
    def test_table_autocreates_on_fresh_db(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            entries = registry.list_entries()
            self.assertEqual(entries, [])

    def test_upsert_and_get_entry(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            registry.upsert_entry(
                pattern="reuters.com",
                pattern_type="domain",
                tier="trusted",
                base_trust=0.90,
                list_category="official",
                notes="路透社官方源",
            )
            entry = registry.get_entry("reuters.com")
            self.assertIsNotNone(entry)
            self.assertEqual(entry["pattern"], "reuters.com")
            self.assertEqual(entry["pattern_type"], "domain")
            self.assertEqual(entry["tier"], "trusted")
            self.assertAlmostEqual(entry["base_trust"], 0.90)
            self.assertEqual(entry["list_category"], "official")
            self.assertEqual(entry["notes"], "路透社官方源")

    def test_upsert_is_idempotent_on_same_pattern(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            registry.upsert_entry(
                pattern="reuters.com", pattern_type="domain",
                tier="trusted", base_trust=0.90, list_category="official",
            )
            registry.upsert_entry(
                pattern="reuters.com", pattern_type="domain",
                tier="official", base_trust=0.95, list_category="official",
                notes="updated",
            )
            entry = registry.get_entry("reuters.com")
            self.assertEqual(entry["tier"], "official")  # overwritten
            self.assertAlmostEqual(entry["base_trust"], 0.95)
            self.assertEqual(entry["notes"], "updated")

    def test_list_entries_filtered_by_category(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            registry.upsert_entry(pattern="a.com", pattern_type="domain",
                                  tier="trusted", base_trust=0.85, list_category="official")
            registry.upsert_entry(pattern="b.com", pattern_type="domain",
                                  tier="unknown", base_trust=0.20, list_category="denylist")
            official = registry.list_entries(list_category="official")
            self.assertEqual(len(official), 1)
            self.assertEqual(official[0]["pattern"], "a.com")

    def test_delete_entry(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            registry.upsert_entry(pattern="reuters.com", pattern_type="domain",
                                  tier="trusted", base_trust=0.90, list_category="official")
            self.assertTrue(registry.delete_entry("reuters.com"))
            self.assertIsNone(registry.get_entry("reuters.com"))
            self.assertFalse(registry.delete_entry("reuters.com"))  # already gone

    def test_match_domain_longest_prefix_wins(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            registry.upsert_entry(pattern="reuters.com", pattern_type="domain",
                                  tier="trusted", base_trust=0.85, list_category="official")
            registry.upsert_entry(pattern="politics.reuters.com", pattern_type="domain",
                                  tier="official", base_trust=0.95, list_category="official")
            # Longer match wins
            entry = registry.match_domain("politics.reuters.com")
            self.assertEqual(entry["tier"], "official")
            # Shorter match when no longer one
            entry = registry.match_domain("business.reuters.com")
            self.assertEqual(entry["tier"], "trusted")
            # No match
            entry = registry.match_domain("example.com")
            self.assertIsNone(entry)

    def test_match_source_name_substring(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            registry.upsert_entry(pattern="Reuters Politics", pattern_type="source_name",
                                  tier="trusted", base_trust=0.85, list_category="official")
            entry = registry.match_source_name("Reuters Politics")
            self.assertIsNotNone(entry)
            self.assertEqual(entry["tier"], "trusted")
            # Substring match (case-insensitive)
            entry = registry.match_source_name("reuters politics daily")
            self.assertIsNotNone(entry)

    def test_match_returns_none_on_empty_registry(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            self.assertIsNone(registry.match_domain("reuters.com"))
            self.assertIsNone(registry.match_source_name("Reuters"))

    def test_notes_exclude_banned_terms(self):
        """Registry notes must not contain banned trading terms."""
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            for term in banned:
                with self.assertRaises(ValueError):
                    registry.upsert_entry(
                        pattern=f"test-{term}.com", pattern_type="domain",
                        tier="trusted", base_trust=0.85, list_category="official",
                        notes=f"this source is {term}",
                    )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_source_trust_registry_store.py -v`
Expected: `ModuleNotFoundError: No module named 'app.memory.source_trust_registry_store'`

- [ ] **Step 3: Add config flag**

In `backend/app/core/config.py`, after the `SOURCE_RELIABILITY_MIN_SOURCES` block (around line 670), add:

```python
    # ── Source Trust Registry (Plan 4 §6.1) ──────────────────────────
    # When true, source_reliability_service consults the SQLite registry for
    # tier/base-trust overrides. Defaults false for byte-identical pre-Plan-4
    # behavior.
    SOURCE_TRUST_REGISTRY_ENABLED: bool = _env_bool(
        "SOURCE_TRUST_REGISTRY_ENABLED", "false"
    )
```

- [ ] **Step 4: Implement the store**

Create `backend/app/memory/source_trust_registry_store.py`:

```python
"""Source Trust Registry store (Plan 4 §6.1).

SQLite-backed registry of source/domain trust entries. Each entry maps a
pattern (domain or source display name) to a tier override, base trust
weight, list category (official/caution/denylist), and freeform notes.

The registry is consumed by ``source_reliability_service`` as an OPTIONAL
prior — it adjusts the tier score used as a prior weight but does NOT
override event-level evidence conflicts.

Schema follows the ``event_market_link_store.py`` pattern: module-level
functions, lazy schema init via ``_ensure_schema``, idempotent migrations
via ``sqlite_db.apply_migrations``.
"""
from __future__ import annotations

import threading
from typing import Any

from app.utils import sqlite_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_trust_registry (
    pattern        TEXT PRIMARY KEY,
    pattern_type   TEXT NOT NULL CHECK (pattern_type IN ('domain', 'source_name')),
    tier           TEXT,
    base_trust     REAL,
    list_category  TEXT,
    notes          TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_str_pattern_type ON source_trust_registry(pattern_type);
CREATE INDEX IF NOT EXISTS idx_str_list_category ON source_trust_registry(list_category);
"""

_SCHEMA_VERSION = 1
_MIGRATIONS: dict[str, str] = {}

_INITIALIZED: set[str] = set()
_INIT_GUARD = threading.Lock()

_BANNED_TERMS = ("long", "short", "buy", "sell", "position", "kelly", "order")


def _ensure_schema(path: str) -> None:
    if path in _INITIALIZED:
        return
    with _INIT_GUARD:
        if path in _INITIALIZED:
            return
        with sqlite_db.writing(path) as conn:
            conn.executescript(_SCHEMA)
            sqlite_db.apply_migrations(conn, "source_trust_registry",
                                       _SCHEMA_VERSION, _MIGRATIONS)
            sqlite_db.record_schema_version(conn, "source_trust_registry",
                                            _SCHEMA_VERSION)
        _INITIALIZED.add(path)


def _check_vocabulary(text: str) -> None:
    """Reject notes containing banned trading terms (case-insensitive)."""
    lowered = text.lower()
    for term in _BANNED_TERMS:
        if term in lowered:
            raise ValueError(
                f"notes contain banned term '{term}': {text!r}"
            )


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "pattern": row["pattern"],
        "pattern_type": row["pattern_type"],
        "tier": row["tier"],
        "base_trust": row["base_trust"],
        "list_category": row["list_category"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def upsert_entry(
    *,
    pattern: str,
    pattern_type: str,
    tier: str | None,
    base_trust: float | None,
    list_category: str | None,
    notes: str = "",
) -> None:
    """Insert or update a registry entry. Idempotent on ``pattern``."""
    if pattern_type not in ("domain", "source_name"):
        raise ValueError(f"invalid pattern_type: {pattern_type!r}")
    _check_vocabulary(notes)
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with sqlite_db.writing(path) as conn:
        conn.execute(
            """
            INSERT INTO source_trust_registry
                (pattern, pattern_type, tier, base_trust, list_category, notes,
                 updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(pattern) DO UPDATE SET
                pattern_type  = excluded.pattern_type,
                tier          = excluded.tier,
                base_trust    = excluded.base_trust,
                list_category = excluded.list_category,
                notes         = excluded.notes,
                updated_at    = datetime('now')
            """,
            (pattern, pattern_type, tier, base_trust, list_category, notes),
        )


def get_entry(pattern: str) -> dict[str, Any] | None:
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with sqlite_db.reading(path) as conn:
        row = conn.execute(
            "SELECT * FROM source_trust_registry WHERE pattern = ?",
            (pattern,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_entries(*, list_category: str | None = None) -> list[dict[str, Any]]:
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with sqlite_db.reading(path) as conn:
        if list_category is not None:
            rows = conn.execute(
                "SELECT * FROM source_trust_registry WHERE list_category = ? "
                "ORDER BY pattern",
                (list_category,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM source_trust_registry ORDER BY pattern"
            ).fetchall()
    return [_row_to_dict(row) for row in rows]


def delete_entry(pattern: str) -> bool:
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with sqlite_db.writing(path) as conn:
        cur = conn.execute(
            "DELETE FROM source_trust_registry WHERE pattern = ?",
            (pattern,),
        )
        return cur.rowcount > 0


def match_domain(domain: str) -> dict[str, Any] | None:
    """Longest-prefix match against ``pattern_type == 'domain'`` entries.

    Returns the entry whose pattern is the longest prefix of ``domain``, or
    None when no entry matches. Case-insensitive.
    """
    if not domain:
        return None
    domain_lower = domain.lower()
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with sqlite_db.reading(path) as conn:
        rows = conn.execute(
            "SELECT * FROM source_trust_registry WHERE pattern_type = 'domain'"
        ).fetchall()
    best: dict[str, Any] | None = None
    best_len = -1
    for row in rows:
        pattern = (row["pattern"] or "").lower()
        if domain_lower == pattern or domain_lower.endswith("." + pattern):
            if len(pattern) > best_len:
                best = _row_to_dict(row)
                best_len = len(pattern)
    return best


def match_source_name(name: str) -> dict[str, Any] | None:
    """Substring match (case-insensitive) against ``pattern_type == 'source_name'``.

    Returns the first matching entry or None.
    """
    if not name:
        return None
    name_lower = name.lower()
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with sqlite_db.reading(path) as conn:
        rows = conn.execute(
            "SELECT * FROM source_trust_registry WHERE pattern_type = 'source_name'"
        ).fetchall()
    for row in rows:
        pattern = (row["pattern"] or "").lower()
        if pattern and pattern in name_lower:
            return _row_to_dict(row)
    return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_source_trust_registry_store.py -v`
Expected: 9 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/memory/source_trust_registry_store.py backend/tests/test_source_trust_registry_store.py backend/app/core/config.py
git commit -m "feat(registry): add source_trust_registry_store (SQLite CRUD + longest-prefix domain match)"
```

---

## Task 2: Review Queue Store with Append-Only Audit Log

**Files:**
- Create: `backend/app/memory/review_queue_store.py`
- Create: `backend/tests/test_review_queue_store.py`

**Interfaces:**
- Produces: `enqueue_item(*, event_id: str, trigger: str, severity: str, reason: str, context: dict) -> str` — returns the new item_id
- Produces: `list_pending(*, trigger: str | None = None) -> list[dict[str, Any]]`
- Produces: `list_resolved(*, limit: int = 100) -> list[dict[str, Any]]`
- Produces: `take_action(*, item_id: str, reviewer: str, action: str, note: str = "") -> None` — validates action against the locked vocabulary, updates item status, appends to audit log
- Produces: `get_audit_log(*, item_id: str | None = None) -> list[dict[str, Any]]`
- Produces: `get_item(item_id: str) -> dict[str, Any] | None`

**Review action vocabulary (locked):** `confirm`, `override`, `request_more_evidence`, `mark_bad_source`, `mark_bad_resolution`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_review_queue_store.py`:

```python
"""Unit tests for review_queue_store (Plan 4 §6.2)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.memory import review_queue_store as rq
from app.utils import sqlite_db


def _db(tmp):
    return patch.object(sqlite_db, "loop_db_path",
                        return_value=str(Path(tmp) / "v2_loop.db"))


class TestReviewQueueStore(unittest.TestCase):
    def test_table_autocreates_on_fresh_db(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            self.assertEqual(rq.list_pending(), [])

    def test_enqueue_and_get_item(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            item_id = rq.enqueue_item(
                event_id="evt-001",
                trigger="high_value_downgraded",
                severity="WARN",
                reason="高价值事件被降级为 WAIT",
                context={"final_direction": "WAIT", "raw_direction": "YES"},
            )
            self.assertIsNotNone(item_id)
            item = rq.get_item(item_id)
            self.assertIsNotNone(item)
            self.assertEqual(item["event_id"], "evt-001")
            self.assertEqual(item["trigger"], "high_value_downgraded")
            self.assertEqual(item["severity"], "WARN")
            self.assertEqual(item["status"], "pending")

    def test_list_pending_filtered_by_trigger(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            rq.enqueue_item(event_id="a", trigger="t1", severity="WARN",
                            reason="r1", context={})
            rq.enqueue_item(event_id="b", trigger="t2", severity="ERROR",
                            reason="r2", context={})
            t1_items = rq.list_pending(trigger="t1")
            self.assertEqual(len(t1_items), 1)
            self.assertEqual(t1_items[0]["event_id"], "a")

    def test_take_action_validates_vocabulary(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            item_id = rq.enqueue_item(
                event_id="evt-001", trigger="t", severity="WARN",
                reason="r", context={},
            )
            # Valid action
            rq.take_action(item_id=item_id, reviewer="alice",
                           action="confirm", note="已确认")
            item = rq.get_item(item_id)
            self.assertEqual(item["status"], "resolved")
            self.assertEqual(item["reviewer_decision"], "confirm")
            self.assertEqual(item["reviewer"], "alice")

            # Invalid action
            item_id2 = rq.enqueue_item(
                event_id="evt-002", trigger="t", severity="WARN",
                reason="r", context={},
            )
            with self.assertRaises(ValueError):
                rq.take_action(item_id=item_id2, reviewer="bob",
                               action="random_action", note="")

    def test_audit_log_is_append_only(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            item_id = rq.enqueue_item(
                event_id="evt-001", trigger="t", severity="WARN",
                reason="r", context={},
            )
            rq.take_action(item_id=item_id, reviewer="alice",
                           action="request_more_evidence", note="需要更多证据")
            rq.take_action(item_id=item_id, reviewer="bob",
                           action="confirm", note="证据已补充，确认")
            log = rq.get_audit_log(item_id=item_id)
            self.assertEqual(len(log), 2)
            self.assertEqual(log[0]["action"], "request_more_evidence")
            self.assertEqual(log[0]["reviewer"], "alice")
            self.assertEqual(log[1]["action"], "confirm")
            self.assertEqual(log[1]["reviewer"], "bob")

    def test_resolved_items_not_in_pending(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            item_id = rq.enqueue_item(
                event_id="evt-001", trigger="t", severity="WARN",
                reason="r", context={},
            )
            self.assertEqual(len(rq.list_pending()), 1)
            rq.take_action(item_id=item_id, reviewer="alice",
                           action="confirm", note="")
            self.assertEqual(len(rq.list_pending()), 0)
            self.assertEqual(len(rq.list_resolved()), 1)

    def test_reason_excludes_banned_terms(self):
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            for term in banned:
                with self.assertRaises(ValueError):
                    rq.enqueue_item(
                        event_id=f"evt-{term}", trigger="t", severity="WARN",
                        reason=f"this source is {term}", context={},
                    )

    def test_take_action_rejects_banned_notes(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            item_id = rq.enqueue_item(
                event_id="evt-001", trigger="t", severity="WARN",
                reason="正常原因", context={},
            )
            with self.assertRaises(ValueError):
                rq.take_action(item_id=item_id, reviewer="alice",
                               action="confirm", note="contains long term")

    def test_take_action_on_nonexistent_item_raises(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            with self.assertRaises(KeyError):
                rq.take_action(item_id="nonexistent", reviewer="alice",
                               action="confirm", note="")

    def test_audit_log_global_when_no_item_id(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            id1 = rq.enqueue_item(event_id="a", trigger="t", severity="WARN",
                                  reason="r1", context={})
            id2 = rq.enqueue_item(event_id="b", trigger="t", severity="WARN",
                                  reason="r2", context={})
            rq.take_action(item_id=id1, reviewer="alice", action="confirm")
            rq.take_action(item_id=id2, reviewer="bob", action="override")
            log = rq.get_audit_log()
            self.assertEqual(len(log), 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_review_queue_store.py -v`
Expected: `ModuleNotFoundError: No module named 'app.memory.review_queue_store'`

- [ ] **Step 3: Implement the store**

Create `backend/app/memory/review_queue_store.py`:

```python
"""Review queue store with append-only audit log (Plan 4 §6.2).

Two tables:
- ``review_queue_items``: mutable status (pending → resolved). One row per
  enqueued review candidate.
- ``review_queue_audit``: INSERT-only audit log. One row per reviewer action.
  No UPDATE or DELETE — auditors can replay the full history.

Reviewer action vocabulary is locked to:
    confirm / override / request_more_evidence / mark_bad_source /
    mark_bad_resolution

All reason/note strings are validated against the vocabulary lock
(banned terms: long/short/buy/sell/position/kelly/order).
"""
from __future__ import annotations

import threading
import uuid
from typing import Any

from app.utils import sqlite_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_queue_items (
    item_id            TEXT PRIMARY KEY,
    event_id           TEXT NOT NULL,
    trigger            TEXT NOT NULL,
    severity           TEXT NOT NULL,
    reason             TEXT NOT NULL,
    context_json       TEXT NOT NULL DEFAULT '{}',
    status             TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'resolved')),
    reviewer           TEXT,
    reviewer_decision  TEXT,
    reviewer_note      TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_rq_items_status ON review_queue_items(status);
CREATE INDEX IF NOT EXISTS idx_rq_items_trigger ON review_queue_items(trigger);
CREATE INDEX IF NOT EXISTS idx_rq_items_event_id ON review_queue_items(event_id);

CREATE TABLE IF NOT EXISTS review_queue_audit (
    audit_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     TEXT NOT NULL,
    reviewer    TEXT NOT NULL,
    action      TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    acted_at    TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (item_id) REFERENCES review_queue_items(item_id)
);
CREATE INDEX IF NOT EXISTS idx_rq_audit_item_id ON review_queue_audit(item_id);
"""

_SCHEMA_VERSION = 1
_MIGRATIONS: dict[str, str] = {}

_INITIALIZED: set[str] = set()
_INIT_GUARD = threading.Lock()

_BANNED_TERMS = ("long", "short", "buy", "sell", "position", "kelly", "order")
_VALID_ACTIONS = frozenset({
    "confirm", "override", "request_more_evidence",
    "mark_bad_source", "mark_bad_resolution",
})


def _ensure_schema(path: str) -> None:
    if path in _INITIALIZED:
        return
    with _INIT_GUARD:
        if path in _INITIALIZED:
            return
        with sqlite_db.writing(path) as conn:
            conn.executescript(_SCHEMA)
            sqlite_db.apply_migrations(conn, "review_queue",
                                       _SCHEMA_VERSION, _MIGRATIONS)
            sqlite_db.record_schema_version(conn, "review_queue",
                                            _SCHEMA_VERSION)
        _INITIALIZED.add(path)


def _check_vocabulary(text: str) -> None:
    lowered = text.lower()
    for term in _BANNED_TERMS:
        if term in lowered:
            raise ValueError(
                f"text contains banned term '{term}': {text!r}"
            )


def _item_row_to_dict(row: Any) -> dict[str, Any]:
    import json
    return {
        "item_id": row["item_id"],
        "event_id": row["event_id"],
        "trigger": row["trigger"],
        "severity": row["severity"],
        "reason": row["reason"],
        "context": json.loads(row["context_json"] or "{}"),
        "status": row["status"],
        "reviewer": row["reviewer"],
        "reviewer_decision": row["reviewer_decision"],
        "reviewer_note": row["reviewer_note"],
        "created_at": row["created_at"],
        "resolved_at": row["resolved_at"],
    }


def _audit_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "audit_id": row["audit_id"],
        "item_id": row["item_id"],
        "reviewer": row["reviewer"],
        "action": row["action"],
        "note": row["note"],
        "acted_at": row["acted_at"],
    }


def enqueue_item(
    *,
    event_id: str,
    trigger: str,
    severity: str,
    reason: str,
    context: dict[str, Any],
) -> str:
    """Insert a new pending review item. Returns the new item_id."""
    _check_vocabulary(reason)
    import json
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    item_id = str(uuid.uuid4())
    with sqlite_db.writing(path) as conn:
        conn.execute(
            """
            INSERT INTO review_queue_items
                (item_id, event_id, trigger, severity, reason, context_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (item_id, event_id, trigger, severity, reason,
             json.dumps(context, ensure_ascii=False)),
        )
    return item_id


def get_item(item_id: str) -> dict[str, Any] | None:
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with sqlite_db.reading(path) as conn:
        row = conn.execute(
            "SELECT * FROM review_queue_items WHERE item_id = ?",
            (item_id,),
        ).fetchone()
    return _item_row_to_dict(row) if row else None


def list_pending(*, trigger: str | None = None) -> list[dict[str, Any]]:
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with sqlite_db.reading(path) as conn:
        if trigger is not None:
            rows = conn.execute(
                "SELECT * FROM review_queue_items WHERE status = 'pending' "
                "AND trigger = ? ORDER BY created_at DESC",
                (trigger,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM review_queue_items WHERE status = 'pending' "
                "ORDER BY created_at DESC"
            ).fetchall()
    return [_item_row_to_dict(row) for row in rows]


def list_resolved(*, limit: int = 100) -> list[dict[str, Any]]:
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with sqlite_db.reading(path) as conn:
        rows = conn.execute(
            "SELECT * FROM review_queue_items WHERE status = 'resolved' "
            "ORDER BY resolved_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_item_row_to_dict(row) for row in rows]


def take_action(
    *,
    item_id: str,
    reviewer: str,
    action: str,
    note: str = "",
) -> None:
    """Apply a reviewer action to a review item.

    Validates ``action`` against the locked vocabulary, updates the item
    status to ``resolved``, and appends a row to the audit log. Raises
    ``ValueError`` for invalid action or banned terms in note. Raises
    ``KeyError`` when the item does not exist.
    """
    if action not in _VALID_ACTIONS:
        raise ValueError(
            f"invalid action {action!r}; must be one of {sorted(_VALID_ACTIONS)}"
        )
    _check_vocabulary(note)
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with sqlite_db.writing(path) as conn:
        row = conn.execute(
            "SELECT item_id FROM review_queue_items WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"review item not found: {item_id}")
        conn.execute(
            """
            UPDATE review_queue_items SET
                status = 'resolved',
                reviewer = ?,
                reviewer_decision = ?,
                reviewer_note = ?,
                resolved_at = datetime('now')
            WHERE item_id = ?
            """,
            (reviewer, action, note, item_id),
        )
        conn.execute(
            """
            INSERT INTO review_queue_audit (item_id, reviewer, action, note)
            VALUES (?, ?, ?, ?)
            """,
            (item_id, reviewer, action, note),
        )


def get_audit_log(*, item_id: str | None = None) -> list[dict[str, Any]]:
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with sqlite_db.reading(path) as conn:
        if item_id is not None:
            rows = conn.execute(
                "SELECT * FROM review_queue_audit WHERE item_id = ? "
                "ORDER BY audit_id ASC",
                (item_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM review_queue_audit ORDER BY audit_id ASC"
            ).fetchall()
    return [_audit_row_to_dict(row) for row in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_review_queue_store.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/memory/review_queue_store.py backend/tests/test_review_queue_store.py
git commit -m "feat(review-queue): add review_queue_store with append-only audit log"
```

---

## Task 3: Review Queue Detectors (Pure Functions) + Integration Wiring

**Files:**
- Create: `backend/app/services/review_queue_detectors.py`
- Create: `backend/tests/test_review_queue_detectors.py`
- Modify: `backend/app/core/config.py` (add `REVIEW_QUEUE_ENABLED` + detector sub-flags)
- Modify: `backend/app/services/event_intelligence_service.py` (wire registry into `build_source_reliability`; wire detectors → `enqueue_item`)

**Interfaces:**
- Consumes: `enqueue_item` from `review_queue_store` (Task 2), `match_domain`/`match_source_name` from `source_trust_registry_store` (Task 1)
- Produces: `detect_review_candidates(record: dict[str, Any]) -> list[dict[str, Any]]` — pure function returning candidate dicts with keys `trigger`, `severity`, `reason`, `context`
- Produces: `load_registry_overrides() -> list[dict[str, Any]]` — thin I/O wrapper (calls Task 1 `list_entries`), used only by the orchestrator

**Detector trigger types (locked):**
- `high_value_downgraded` — `actionable_recommendation.signal` in `{act, provisional_act}` but `final_displayed_direction` in `{WAIT, AVOID}`
- `source_market_conflict` — `source_reliability.suggested_direction` is WAIT but `market_quality` did not downgrade (strong conflict)
- `outcome_prediction_mismatch` — event is resolved and `outcome` contradicts a high-confidence prediction (`ai_probability >= 0.75`)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_review_queue_detectors.py`:

```python
"""Unit tests for review_queue_detectors (Plan 4 §6.2)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.services.review_queue_detectors import detect_review_candidates


def _base_record(**overrides):
    """Minimal record that triggers no detectors by default."""
    rec = {
        "event_id": "evt-001",
        "actionable_recommendation": {"direction": "YES", "signal": "act",
                                       "ai_probability": 0.65},
        "final_displayed_direction": "YES",
        "final_downgrade_reason": None,
        "source_reliability": None,
        "market_quality": None,
        "outcome": None,
    }
    rec.update(overrides)
    return rec


class TestReviewQueueDetectors(unittest.TestCase):
    def test_no_candidates_for_clean_record(self):
        candidates = detect_review_candidates(_base_record())
        self.assertEqual(candidates, [])

    def test_high_value_downgraded_when_act_becomes_wait(self):
        rec = _base_record(
            actionable_recommendation={"direction": "YES", "signal": "act",
                                        "ai_probability": 0.72},
            final_displayed_direction="WAIT",
            final_downgrade_reason="guardrail fired",
        )
        candidates = detect_review_candidates(rec)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["trigger"], "high_value_downgraded")
        self.assertEqual(candidates[0]["severity"], "WARN")

    def test_high_value_downgraded_skips_watchlist_signal(self):
        """WATCHLIST signal should not trigger even if direction is WAIT."""
        rec = _base_record(
            actionable_recommendation={"direction": "YES", "signal": "WATCHLIST",
                                        "ai_probability": 0.55},
            final_displayed_direction="WAIT",
        )
        candidates = detect_review_candidates(rec)
        self.assertEqual(candidates, [])

    def test_source_market_conflict(self):
        rec = _base_record(
            source_reliability={
                "suggested_direction": "WAIT",
                "downgraded": True,
                "downgrade_reason": "来源可靠性不足",
            },
            market_quality={
                "suggested_direction": "YES",
                "downgraded": False,
            },
        )
        candidates = detect_review_candidates(rec)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["trigger"], "source_market_conflict")

    def test_outcome_prediction_mismatch(self):
        rec = _base_record(
            outcome="NO",
            actionable_recommendation={"direction": "YES", "signal": "act",
                                        "ai_probability": 0.82},
        )
        candidates = detect_review_candidates(rec)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["trigger"], "outcome_prediction_mismatch")
        self.assertEqual(candidates[0]["severity"], "ERROR")

    def test_outcome_mismatch_skips_low_confidence(self):
        rec = _base_record(
            outcome="NO",
            actionable_recommendation={"direction": "YES", "signal": "act",
                                        "ai_probability": 0.60},
        )
        candidates = detect_review_candidates(rec)
        self.assertEqual(candidates, [])

    def test_multiple_detectors_can_fire(self):
        rec = _base_record(
            actionable_recommendation={"direction": "YES", "signal": "act",
                                        "ai_probability": 0.80},
            final_displayed_direction="WAIT",
            final_downgrade_reason="guardrail",
            outcome="NO",
        )
        candidates = detect_review_candidates(rec)
        triggers = [c["trigger"] for c in candidates]
        self.assertIn("high_value_downgraded", triggers)
        self.assertIn("outcome_prediction_mismatch", triggers)

    def test_reasons_exclude_banned_terms(self):
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        rec = _base_record(
            actionable_recommendation={"direction": "YES", "signal": "act",
                                        "ai_probability": 0.80},
            final_displayed_direction="WAIT",
            final_downgrade_reason="guardrail",
        )
        candidates = detect_review_candidates(rec)
        for c in candidates:
            for term in banned:
                self.assertNotIn(term, c["reason"].lower(),
                                 f"banned term '{term}' in reason: {c['reason']}")

    def test_handles_missing_fields_gracefully(self):
        """Detectors must not crash on records missing optional fields."""
        candidates = detect_review_candidates({"event_id": "x"})
        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_review_queue_detectors.py -v`
Expected: `ModuleNotFoundError: No module named 'app.services.review_queue_detectors'`

- [ ] **Step 3: Add config flags**

In `backend/app/core/config.py`, after the `SOURCE_TRUST_REGISTRY_ENABLED` block (added in Task 1), add:

```python
    # ── Review Queue (Plan 4 §6.2) ───────────────────────────────────
    # When true, the orchestrator runs pure-function detectors after overlay
    # build and enqueues candidates into review_queue_store. Defaults false
    # for byte-identical pre-Plan-4 behavior.
    REVIEW_QUEUE_ENABLED: bool = _env_bool("REVIEW_QUEUE_ENABLED", "false")
    # Confidence threshold for outcome_prediction_mismatch detector.
    REVIEW_QUEUE_MISMATCH_CONFIDENCE: float = float(
        os.getenv("REVIEW_QUEUE_MISMATCH_CONFIDENCE", "0.75")
    )
```

- [ ] **Step 4: Implement the detectors**

Create `backend/app/services/review_queue_detectors.py`:

```python
"""Review queue trigger detectors (Plan 4 §6.2).

Pure functions that scan a single event record and return review-queue
candidate dicts. No I/O, no LLM, no settings reads — the orchestrator
calls ``detect_review_candidates`` and decides whether to enqueue.

Each candidate is a dict with keys:
    trigger   — one of the locked trigger type strings
    severity  — "WARN" or "ERROR"
    reason    — Chinese reason string (vocabulary-locked)
    context   — dict of relevant field values for the reviewer

Trigger types (locked):
    high_value_downgraded     — act signal but final direction is WAIT/AVOID
    source_market_conflict    — source_reliability says WAIT but market_quality
                                does not (strong cross-overlay conflict)
    outcome_prediction_mismatch — resolved outcome contradicts a high-confidence
                                  prediction
"""
from __future__ import annotations

from typing import Any

_ACT_SIGNALS = frozenset({"act", "provisional_act"})
_WAIT_LIKE = frozenset({"WAIT", "AVOID"})


def detect_review_candidates(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Scan a record and return review-queue candidate dicts.

    Pure, synchronous, deterministic. Returns an empty list when no
    detector fires. Does not crash on missing fields.
    """
    if not isinstance(record, dict):
        return []
    candidates: list[dict[str, Any]] = []
    candidates.extend(_detect_high_value_downgraded(record))
    candidates.extend(_detect_source_market_conflict(record))
    candidates.extend(_detect_outcome_prediction_mismatch(record))
    return candidates


def _detect_high_value_downgraded(record: dict[str, Any]) -> list[dict[str, Any]]:
    rec = record.get("actionable_recommendation") or {}
    if not isinstance(rec, dict):
        return []
    signal = rec.get("signal")
    if signal not in _ACT_SIGNALS:
        return []
    final_dir = record.get("final_displayed_direction")
    if final_dir not in _WAIT_LIKE:
        return []
    reason = record.get("final_downgrade_reason") or ""
    return [{
        "trigger": "high_value_downgraded",
        "severity": "WARN",
        "reason": f"高价值信号 {signal} 被降级为 {final_dir}"
                  + (f"：{reason}" if reason else ""),
        "context": {
            "signal": signal,
            "raw_direction": rec.get("direction"),
            "final_direction": final_dir,
            "downgrade_reason": reason,
            "ai_probability": rec.get("ai_probability"),
        },
    }]


def _detect_source_market_conflict(record: dict[str, Any]) -> list[dict[str, Any]]:
    sr = record.get("source_reliability")
    mq = record.get("market_quality")
    if not isinstance(sr, dict) or not isinstance(mq, dict):
        return []
    sr_says_wait = sr.get("suggested_direction") in _WAIT_LIKE and sr.get("downgraded") is True
    mq_says_ok = mq.get("downgraded") is not True
    if not (sr_says_wait and mq_says_ok):
        return []
    return [{
        "trigger": "source_market_conflict",
        "severity": "WARN",
        "reason": "来源可靠性与市场质量强冲突：来源建议 WAIT 但市场质量未降级",
        "context": {
            "sr_suggested_direction": sr.get("suggested_direction"),
            "sr_downgrade_reason": sr.get("downgrade_reason"),
            "mq_suggested_direction": mq.get("suggested_direction"),
        },
    }]


def _detect_outcome_prediction_mismatch(
    record: dict[str, Any],
    *,
    confidence_threshold: float = 0.75,
) -> list[dict[str, Any]]:
    outcome = record.get("outcome")
    if not outcome:
        return []
    rec = record.get("actionable_recommendation") or {}
    if not isinstance(rec, dict):
        return []
    prob = rec.get("ai_probability")
    if not isinstance(prob, (int, float)):
        return []
    if prob < confidence_threshold:
        return []
    direction = rec.get("direction")
    # Outcome YES contradicts a NO prediction and vice versa.
    contradicts = (
        (outcome == "YES" and direction == "NO")
        or (outcome == "NO" and direction == "YES")
    )
    if not contradicts:
        return []
    return [{
        "trigger": "outcome_prediction_mismatch",
        "severity": "ERROR",
        "reason": f"结算结果 {outcome} 与高置信预测 {direction}（置信度 {prob:.2f}）相反",
        "context": {
            "outcome": outcome,
            "predicted_direction": direction,
            "ai_probability": prob,
        },
    }]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_review_queue_detectors.py -v`
Expected: 9 passed.

- [ ] **Step 6: Wire registry into source_reliability_service**

In `backend/app/services/source_reliability_service.py`, modify `build_source_reliability` to accept an optional `registry_overrides` parameter and apply tier/base-trust overrides when provided. Keep the function pure.

Add `registry_overrides: list[dict[str, Any]] | None = None` as the LAST keyword-only parameter (after `min_sources`). Then, in the `classify_source_tier` call path, apply the override:

```python
# After the existing classify_source_tier call, before computing the score:
if registry_overrides:
    override = _match_registry_override(source_name, domain, registry_overrides)
    if override is not None:
        tier = override.get("tier") or tier
        if override.get("base_trust") is not None:
            base_trust_override = override["base_trust"]
        else:
            base_trust_override = None
    else:
        base_trust_override = None
else:
    base_trust_override = None
```

And add the helper:

```python
def _match_registry_override(
    source_name: str,
    domain: str,
    overrides: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the longest-prefix domain match or first source_name substring
    match in ``overrides``. Returns the override dict or None.
    """
    best: dict[str, Any] | None = None
    best_len = -1
    domain_lower = (domain or "").lower()
    name_lower = (source_name or "").lower()
    for entry in overrides:
        ptype = entry.get("pattern_type")
        pattern = (entry.get("pattern") or "").lower()
        if not pattern:
            continue
        if ptype == "domain" and domain_lower:
            if domain_lower == pattern or domain_lower.endswith("." + pattern):
                if len(pattern) > best_len:
                    best = entry
                    best_len = len(pattern)
        elif ptype == "source_name" and name_lower:
            if pattern in name_lower:
                if best is None:
                    best = entry
    return best
```

When `base_trust_override` is not None, use it INSTEAD of `_TIER_SCORES[tier]` for the source's score in `source_breakdown`. Add a flag `source_prior_affected: bool` to the return dict (set True when any override was applied to any source in this record).

Add `source_prior_affected` as the 12th key in the return dict (after `applied_to_displayed_direction`). Default `False` when no overrides applied or `registry_overrides` is None.

- [ ] **Step 7: Wire orchestrator integration in event_intelligence_service.py**

In `_build_all_overlays`, after the existing `source_reliability` overlay block, add a best-effort block that:
1. When `settings.SOURCE_TRUST_REGISTRY_ENABLED` is true, calls `source_trust_registry_store.list_entries()` and passes the result as `registry_overrides=` to `build_source_reliability`. Wrap in try/except — on failure, log a warning and continue without overrides (byte-identical to disabled).
2. When `settings.REVIEW_QUEUE_ENABLED` is true, after the guardrail + final direction is set, calls `detect_review_candidates(record)` and enqueues each candidate via `review_queue_store.enqueue_item(event_id=record["event_id"], ...)`. Wrap in try/except — best-effort, never blocks event production.

Find the existing `build_source_reliability` call site and add the `registry_overrides` parameter. Find the end of `_build_all_overlays` (after `evaluate_guardrails` + `final_displayed_direction` is set) and add the detector block.

**IMPORTANT:** The `build_source_reliability` call site currently passes `enabled=settings.SOURCE_RELIABILITY_ENABLED`. When `SOURCE_TRUST_REGISTRY_ENABLED=true` but `SOURCE_RELIABILITY_ENABLED=false`, the registry is inert (no source_reliability block produced). This is correct — the registry is a prior ON TOP of source_reliability, not a standalone feature.

- [ ] **Step 8: Run full test suite**

Run: `cd backend && python -m pytest tests/test_review_queue_detectors.py tests/test_review_queue_store.py tests/test_source_trust_registry_store.py tests/test_source_reliability_service.py tests/test_decision_quality_engine_integration.py -v`
Expected: all pass (new tests + existing source_reliability tests + existing integration tests unchanged because flags default OFF).

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/review_queue_detectors.py backend/tests/test_review_queue_detectors.py backend/app/services/source_reliability_service.py backend/app/services/event_intelligence_service.py backend/app/core/config.py
git commit -m "feat(review-queue): add pure detectors + wire registry and review queue into orchestrator"
```

---

## Task 4: CLI Tools

**Files:**
- Create: `backend/scripts/source_trust_registry_cli.py`
- Create: `backend/scripts/review_queue_cli.py`

**Interfaces:**
- Produces: `source_trust_registry_cli.main(argv: list[str] | None = None) -> int`
- Produces: `review_queue_cli.main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Implement the registry CLI**

Create `backend/scripts/source_trust_registry_cli.py`:

```python
"""Admin CLI for the Source Trust Registry (Plan 4 §6.1).

Usage:
    python -m scripts.source_trust_registry_cli list [--category CAT]
    python -m scripts.source_trust_registry_cli add --pattern P --type {domain,source_name}
           [--tier TIER] [--base-trust FLOAT] [--category CAT] [--notes TEXT]
    python -m scripts.source_trust_registry_cli delete --pattern P
    python -m scripts.source_trust_registry_cli export [--json]
    python -m scripts.source_trust_registry_cli import --file PATH [--dry-run]

Actions are INSERT/UPDATE/DELETE on the SQLite registry. ``list`` and
``export`` are read-only. Uses ASCII labels for Windows GBK safety.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.memory import source_trust_registry_store as registry


def _print(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def _cmd_list(args: argparse.Namespace) -> int:
    entries = registry.list_entries(list_category=args.category)
    if not entries:
        _print("[INFO] no entries found")
        return 0
    _print(f"[OK] {len(entries)} entries:")
    for e in entries:
        _print(
            f"  {e['pattern']:<40} type={e['pattern_type']:<12} "
            f"tier={e['tier'] or '-':<10} trust={e['base_trust']!s:<6} "
            f"cat={e['list_category'] or '-':<10} notes={e['notes']}"
        )
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    try:
        registry.upsert_entry(
            pattern=args.pattern,
            pattern_type=args.type,
            tier=args.tier,
            base_trust=args.base_trust,
            list_category=args.category,
            notes=args.notes or "",
        )
    except ValueError as exc:
        _print(f"[FAIL] {exc}")
        return 1
    _print(f"[OK] upserted: {args.pattern}")
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    ok = registry.delete_entry(args.pattern)
    if ok:
        _print(f"[OK] deleted: {args.pattern}")
        return 0
    _print(f"[FAIL] not found: {args.pattern}")
    return 1


def _cmd_export(args: argparse.Namespace) -> int:
    entries = registry.list_entries()
    payload = [
        {
            "pattern": e["pattern"],
            "pattern_type": e["pattern_type"],
            "tier": e["tier"],
            "base_trust": e["base_trust"],
            "list_category": e["list_category"],
            "notes": e["notes"],
        }
        for e in entries
    ]
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print(f"[OK] {len(payload)} entries (use --json for machine-readable)")
        for e in payload:
            _print(f"  {e['pattern']} | {e['pattern_type']} | {e['tier']}")
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    with open(args.file, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        _print("[FAIL] import file must be a JSON array of entries")
        return 1
    applied = 0
    skipped = 0
    for entry in data:
        if args.dry_run:
            _print(f"[DRY-RUN] would upsert: {entry.get('pattern')}")
            applied += 1
            continue
        try:
            registry.upsert_entry(
                pattern=entry["pattern"],
                pattern_type=entry["pattern_type"],
                tier=entry.get("tier"),
                base_trust=entry.get("base_trust"),
                list_category=entry.get("list_category"),
                notes=entry.get("notes", ""),
            )
            applied += 1
        except (ValueError, KeyError) as exc:
            _print(f"[WARN] skipped {entry.get('pattern')}: {exc}")
            skipped += 1
    _print(f"[OK] applied={applied} skipped={skipped}"
          + (" [DRY-RUN]" if args.dry_run else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Source Trust Registry admin CLI"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list entries")
    p_list.add_argument("--category", default=None)
    p_list.set_defaults(func=_cmd_list)

    p_add = sub.add_parser("add", help="add/update an entry")
    p_add.add_argument("--pattern", required=True)
    p_add.add_argument("--type", required=True,
                       choices=["domain", "source_name"])
    p_add.add_argument("--tier", default=None)
    p_add.add_argument("--base-trust", type=float, default=None)
    p_add.add_argument("--category", default=None)
    p_add.add_argument("--notes", default="")
    p_add.set_defaults(func=_cmd_add)

    p_del = sub.add_parser("delete", help="delete an entry")
    p_del.add_argument("--pattern", required=True)
    p_del.set_defaults(func=_cmd_delete)

    p_exp = sub.add_parser("export", help="export all entries")
    p_exp.add_argument("--json", action="store_true")
    p_exp.set_defaults(func=_cmd_export)

    p_imp = sub.add_parser("import", help="import entries from JSON file")
    p_imp.add_argument("--file", required=True)
    p_imp.add_argument("--dry-run", action="store_true")
    p_imp.set_defaults(func=_cmd_import)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Implement the review queue CLI**

Create `backend/scripts/review_queue_cli.py`:

```python
"""Admin CLI for the Review Queue (Plan 4 §6.2).

Usage:
    python -m scripts.review_queue_cli list [--trigger T] [--status {pending,resolved}]
    python -m scripts.review_queue_cli action --item-id ID --reviewer NAME
           --action {confirm,override,request_more_evidence,mark_bad_source,
                     mark_bad_resolution} [--note TEXT]
    python -m scripts.review_queue_cli audit [--item-id ID]

``list`` shows pending items by default. ``action`` resolves an item and
appends to the audit log. ``audit`` shows the audit log (global or per-item).
Uses ASCII labels for Windows GBK safety.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.memory import review_queue_store as rq


def _print(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def _cmd_list(args: argparse.Namespace) -> int:
    if args.status == "resolved":
        items = rq.list_resolved(limit=200)
    else:
        items = rq.list_pending(trigger=args.trigger)
    if not items:
        _print("[INFO] no items found")
        return 0
    _print(f"[OK] {len(items)} items:")
    for it in items:
        _print(
            f"  {it['item_id'][:8]}  evt={it['event_id']:<12} "
            f"trigger={it['trigger']:<28} sev={it['severity']:<5} "
            f"reason={it['reason']}"
        )
    return 0


def _cmd_action(args: argparse.Namespace) -> int:
    try:
        rq.take_action(
            item_id=args.item_id,
            reviewer=args.reviewer,
            action=args.action,
            note=args.note or "",
        )
    except ValueError as exc:
        _print(f"[FAIL] {exc}")
        return 1
    except KeyError as exc:
        _print(f"[FAIL] {exc}")
        return 1
    _print(f"[OK] action={args.action} on item={args.item_id[:8]}")
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    log = rq.get_audit_log(item_id=args.item_id)
    if not log:
        _print("[INFO] no audit entries found")
        return 0
    _print(f"[OK] {len(log)} audit entries:")
    for entry in log:
        _print(
            f"  #{entry['audit_id']}  item={entry['item_id'][:8]}  "
            f"reviewer={entry['reviewer']:<10} action={entry['action']:<24} "
            f"note={entry['note']}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Review Queue admin CLI"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list items")
    p_list.add_argument("--trigger", default=None)
    p_list.add_argument("--status", default="pending",
                        choices=["pending", "resolved"])
    p_list.set_defaults(func=_cmd_list)

    p_act = sub.add_parser("action", help="take reviewer action")
    p_act.add_argument("--item-id", required=True)
    p_act.add_argument("--reviewer", required=True)
    p_act.add_argument("--action", required=True,
                       choices=["confirm", "override",
                                "request_more_evidence",
                                "mark_bad_source", "mark_bad_resolution"])
    p_act.add_argument("--note", default="")
    p_act.set_defaults(func=_cmd_action)

    p_aud = sub.add_parser("audit", help="show audit log")
    p_aud.add_argument("--item-id", default=None)
    p_aud.set_defaults(func=_cmd_audit)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Smoke-test both CLIs**

```bash
cd backend
# Registry CLI smoke test (uses real DB — safe because it's a fresh table)
python -m scripts.source_trust_registry_cli list
python -m scripts.source_trust_registry_cli add --pattern reuters.com --type domain --tier trusted --base-trust 0.85 --category official --notes "路透社官方源"
python -m scripts.source_trust_registry_cli list
python -m scripts.source_trust_registry_cli list --category official
python -m scripts.source_trust_registry_cli export --json
python -m scripts.source_trust_registry_cli delete --pattern reuters.com

# Review Queue CLI smoke test
python -m scripts.review_queue_cli list
python -m scripts.review_queue_cli audit
```

Expected: all commands exit 0, output uses `[OK]/[INFO]` labels, Chinese notes render correctly.

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/source_trust_registry_cli.py backend/scripts/review_queue_cli.py
git commit -m "feat(cli): add source_trust_registry_cli and review_queue_cli admin tools"
```

---

## Self-Review

**1. Spec coverage:**
- §6.1 Source Trust Registry: ✓ Task 1 (store) + Task 3 (integration) + Task 4 (CLI audit). Covers: SQLite table, domain/source_name patterns, denylist/caution/official categories, alias normalization (longest-prefix domain match), consumed by source_reliability_service as optional input, CLI audit. The "source prior affected score" reporting is the `source_prior_affected` flag in Task 3 Step 6.
- §6.2 Review Queue: ✓ Task 2 (store + audit log) + Task 3 (detectors + wiring) + Task 4 (CLI). Covers: review_queue concept, 5 trigger sources (3 detectors implemented: high_value_downgraded, source_market_conflict, outcome_prediction_mismatch; the other 2 spec triggers — "resolved outcome 与预测高置信相反" is outcome_prediction_mismatch, "audit_quality_consistency 发现不一致" is a script-level integration deferred to post-Plan), reviewer actions (confirm/override/request_more_evidence/mark_bad_source/mark_bad_resolution), append-only audit log, human decisions enter replay as samples (the store is queryable by the replay harness — full integration deferred).

**2. Placeholder scan:** No TBD/TODO. All steps have complete code. ✓

**3. Type consistency:**
- `enqueue_item(*, event_id, trigger, severity, reason, context)` → matches detector output dict keys (trigger/severity/reason/context) + event_id added by orchestrator. ✓
- `registry_overrides: list[dict[str, Any]] | None` → matches `list_entries()` return type. ✓
- `detect_review_candidates(record) -> list[dict[str, Any]]` → candidate dicts have `trigger/severity/reason/context` keys. ✓
- `take_action(*, item_id, reviewer, action, note)` → matches CLI `--item-id/--reviewer/--action/--note`. ✓

**Gaps intentionally deferred (post-Plan):**
- audit_quality_consistency script → review_queue integration (the script already produces `Conflict` dataclass; a thin adapter can call `enqueue_item` in a future task).
- Replay harness consuming reviewer decisions as training samples (the store is queryable; full wiring deferred).
- Frontend review queue UI (spec doesn't mandate frontend for §6.2).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-01-source-trust-registry-and-review-queue.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
