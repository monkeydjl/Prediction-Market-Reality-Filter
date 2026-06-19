"""event_market_link_store.py
==========================
Durable store for event->market contract links (SQLite-backed).

This is the M0 identity layer: it persists the binding between an internal event
and the specific prediction-market contract used to resolve it. Until now that
binding was ephemeral (computed by question match at resolve time, never stored),
so a fuzzy match could silently score an event against the wrong market's outcome.

A link is only eligible to be scored when ``verified`` is True. Unverified links
(e.g. fuzzy question matches below the auto-verify threshold) are recorded but
fail-closed - callers use get_verified_link() and skip scoring when it is None.

Styled after event_store.py (upsert / get / list helpers), but backed by the
SQLite loop store (settings.LOOP_DB_FILE) for relational integrity. See
docs/user/DATABASE_DESIGN.md "Identity and Linkage Integrity".
"""

import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from app.models.event import MarketLink
from app.utils import sqlite_db
from app.utils.sqlite_db import reading, writing

_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_market_links (
    id                  TEXT PRIMARY KEY,
    event_id            TEXT NOT NULL,
    market_name         TEXT NOT NULL DEFAULT '',
    contract_id         TEXT NOT NULL DEFAULT '',
    market_question     TEXT NOT NULL DEFAULT '',
    resolution_criteria TEXT NOT NULL DEFAULT '',
    link_method         TEXT NOT NULL DEFAULT 'auto',
    link_confidence     REAL NOT NULL DEFAULT 0.0,
    verified            INTEGER NOT NULL DEFAULT 0,
    linked_at           TEXT NOT NULL DEFAULT '',
    UNIQUE(event_id, contract_id)
);
CREATE INDEX IF NOT EXISTS idx_eml_event ON event_market_links(event_id);
CREATE INDEX IF NOT EXISTS idx_eml_contract ON event_market_links(contract_id);
"""

_INITIALIZED: set[str] = set()
_INIT_GUARD = threading.Lock()


def _ensure_schema(path: str) -> None:
    """Create the table on first use of a given DB path (idempotent)."""
    if path in _INITIALIZED:
        return
    with _INIT_GUARD:
        if path in _INITIALIZED:
            return
        with writing(path) as conn:
            conn.executescript(_SCHEMA)
        _INITIALIZED.add(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["verified"] = bool(data["verified"])
    return data


def upsert_link(
    event_id: str,
    *,
    market_name: str = "",
    contract_id: str = "",
    market_question: str = "",
    resolution_criteria: str = "",
    link_method: str = "auto",
    link_confidence: float = 0.0,
    verified: bool = False,
) -> dict[str, Any]:
    """Insert or update a link, keyed by (event_id, contract_id).

    Validates the fields through MarketLink before writing (a malformed link
    raises instead of corrupting the store, mirroring event_store's validation
    gate). Returns the stored link as a dict.
    """
    link = MarketLink(
        event_id=event_id,
        market_name=market_name,
        contract_id=contract_id,
        market_question=market_question,
        resolution_criteria=resolution_criteria,
        link_method=link_method,
        link_confidence=link_confidence,
        verified=verified,
        linked_at=_now(),
    )
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with writing(path) as conn:
        conn.execute(
            """
            INSERT INTO event_market_links (
                id, event_id, market_name, contract_id, market_question,
                resolution_criteria, link_method, link_confidence, verified, linked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id, contract_id) DO UPDATE SET
                market_name=excluded.market_name,
                market_question=excluded.market_question,
                resolution_criteria=excluded.resolution_criteria,
                link_method=excluded.link_method,
                link_confidence=excluded.link_confidence,
                verified=excluded.verified,
                linked_at=excluded.linked_at
            """,
            (
                str(uuid.uuid4()), link.event_id, link.market_name, link.contract_id,
                link.market_question, link.resolution_criteria, link.link_method,
                link.link_confidence, int(link.verified), link.linked_at,
            ),
        )
    return get_link(event_id, contract_id) or link.model_dump()


def get_link(event_id: str, contract_id: str) -> dict[str, Any] | None:
    """Return the link for a specific (event_id, contract_id), or None."""
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        row = conn.execute(
            "SELECT * FROM event_market_links WHERE event_id=? AND contract_id=?",
            (event_id, contract_id),
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_verified_link(event_id: str) -> dict[str, Any] | None:
    """Return the verified link for an event (most recent if several), or None.

    None means fail-closed: the caller must not score the event. This is the
    single gate the resolve path consults before attaching an outcome.
    """
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        row = conn.execute(
            """
            SELECT * FROM event_market_links
            WHERE event_id=? AND verified=1
            ORDER BY linked_at DESC LIMIT 1
            """,
            (event_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_links(event_id: str) -> list[dict[str, Any]]:
    """Return all links for an event (verified and not), newest first."""
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        rows = conn.execute(
            "SELECT * FROM event_market_links WHERE event_id=? ORDER BY linked_at DESC",
            (event_id,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def list_pending() -> list[dict[str, Any]]:
    """Return all unverified links - the human review / verification queue."""
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        rows = conn.execute(
            "SELECT * FROM event_market_links WHERE verified=0 ORDER BY linked_at DESC",
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def set_verified(event_id: str, contract_id: str, verified: bool) -> dict[str, Any] | None:
    """Promote (verify) or demote a link. Returns the updated link, or None when
    no such (event_id, contract_id) link exists."""
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with writing(path) as conn:
        cur = conn.execute(
            "UPDATE event_market_links SET verified=? WHERE event_id=? AND contract_id=?",
            (int(verified), event_id, contract_id),
        )
        if cur.rowcount == 0:
            return None
    return get_link(event_id, contract_id)
