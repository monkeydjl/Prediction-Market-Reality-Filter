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
    """Insert a pending review item, or refresh the existing pending item
    for the same ``(event_id, trigger)``. Returns the item_id.

    Idempotent while an item is pending: the orchestrator re-runs detectors
    on every overlay build, so without dedup the same (event_id, trigger)
    would pile up duplicate pending rows during periodic refresh. When a
    pending item already exists, its severity/reason/context are refreshed
    in place (latest detector run wins) and the existing item_id is
    returned — no new row, no audit entry.

    After an item is resolved, a new enqueue creates a NEW pending row
    (re-review is allowed).
    """
    _check_vocabulary(reason)
    import json
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    context_json = json.dumps(context, ensure_ascii=False)
    with sqlite_db.writing(path) as conn:
        existing = conn.execute(
            "SELECT item_id FROM review_queue_items "
            "WHERE event_id = ? AND trigger = ? AND status = 'pending'",
            (event_id, trigger),
        ).fetchone()
        if existing is not None:
            item_id = existing["item_id"]
            conn.execute(
                """
                UPDATE review_queue_items SET
                    severity = ?, reason = ?, context_json = ?
                WHERE item_id = ?
                """,
                (severity, reason, context_json, item_id),
            )
            return item_id
        item_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO review_queue_items
                (item_id, event_id, trigger, severity, reason, context_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (item_id, event_id, trigger, severity, reason, context_json),
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
