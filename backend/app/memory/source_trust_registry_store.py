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
