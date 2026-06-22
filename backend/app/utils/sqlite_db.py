"""sqlite_db.py
=============
Shared SQLite plumbing for the V2 loop store.

The platform's durable stores are JSON files (see file_store.py); the V2 feedback
loop needs relational integrity (verified joins, append-only rows, uniqueness)
that JSON cannot enforce, so the loop tables live in a single SQLite file
(settings.LOOP_DB_FILE). This module centralizes connection setup so every loop
table opens the database the same way.

This is the first SQLite usage in the codebase; M1's predictions table reuses it.
Writes are serialized through a module-level lock (mirroring file_store's per-file
locking), and each operation opens a short-lived connection - simplest correct
pattern at this volume and avoids sharing a connection across the async/threadpool
boundary.
"""

import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from app.core.config import settings
from app.utils.helpers import utc_now

# Serializes writes across threads. SQLite handles its own file locking, but a
# process-level lock keeps concurrent writers in the same process from racing on
# the read-modify-write paths (e.g. upsert) and turns lock contention into a wait
# rather than an OperationalError.
_WRITE_LOCK = threading.Lock()
_SCHEMA_VERSION_TABLE = "loop_schema_versions"


def loop_db_path() -> str:
    """Absolute path of the single SQLite file holding all V2 loop tables.

    Every loop store (event_market_links, predictions, ...) opens this one file,
    so tests isolate the whole loop DB by patching this single function to a temp
    path - there is no per-store path to forget.
    """
    import os
    return os.path.abspath(settings.LOOP_DB_FILE)



def connect(path: str) -> sqlite3.Connection:
    """Open a loop-store connection with the standard pragmas.

    check_same_thread=False so a connection can be used from FastAPI's worker
    threads; WAL improves read/write concurrency; foreign_keys=ON enforces the
    relational integrity the loop depends on. Caller is responsible for closing
    (use the `writing` / `reading` context managers below).
    """
    conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def wal_checkpoint(
    path: str | None = None,
    *,
    mode: str = "TRUNCATE",
) -> dict[str, int]:
    """Run an explicit WAL checkpoint for the loop DB.

    SQLite auto-checkpoints eventually, but long-running processes can still
    leave a large WAL file. The scheduler/startup path calls this helper so
    maintenance is deliberate and testable.
    """
    db_path = path or loop_db_path()
    normalized = mode.upper()
    if normalized not in {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}:
        raise ValueError(f"Unsupported WAL checkpoint mode: {mode}")
    with _WRITE_LOCK:
        conn = connect(db_path)
        try:
            row = conn.execute(f"PRAGMA wal_checkpoint({normalized})").fetchone()
        finally:
            conn.close()
    if row is None:
        return {"busy": 0, "log": 0, "checkpointed": 0}
    return {
        "busy": int(row[0] or 0),
        "log": int(row[1] or 0),
        "checkpointed": int(row[2] or 0),
    }


def integrity_check(path: str | None = None) -> list[str]:
    """Return the raw PRAGMA integrity_check messages for the loop DB."""
    db_path = path or loop_db_path()
    with reading(db_path) as conn:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    return [str(row[0]) for row in rows]


def maintain(path: str | None = None) -> dict[str, object]:
    """Checkpoint the WAL and verify basic SQLite integrity.

    Returns a compact payload for run ledgers / tests. Raises RuntimeError when
    SQLite reports corruption so health checks can degrade loudly.
    """
    db_path = path or loop_db_path()
    checkpoint = wal_checkpoint(db_path, mode="TRUNCATE")
    integrity = integrity_check(db_path)
    ok = integrity == ["ok"]
    if not ok:
        raise RuntimeError(f"SQLite integrity_check failed: {integrity}")
    return {"checkpoint": checkpoint, "integrity": integrity, "ok": ok}


def record_schema_version(conn: sqlite3.Connection, component: str, version: int) -> None:
    """Record the current schema version for one loop-store component."""
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA_VERSION_TABLE} (
            component  TEXT PRIMARY KEY,
            version    INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        INSERT INTO {_SCHEMA_VERSION_TABLE} (component, version, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(component) DO UPDATE SET
            version=excluded.version,
            updated_at=excluded.updated_at
        """,
        (component, int(version), utc_now()),
    )


def schema_versions(path: str | None = None) -> dict[str, int]:
    """Return recorded schema versions for the SQLite loop-store components."""
    db_path = path or loop_db_path()
    try:
        with reading(db_path) as conn:
            rows = conn.execute(
                f"SELECT component, version FROM {_SCHEMA_VERSION_TABLE}"
            ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {str(row["component"]): int(row["version"]) for row in rows}


@contextmanager
def reading(path: str) -> Iterator[sqlite3.Connection]:
    """Read-only connection scope. No write lock; WAL allows concurrent reads."""
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def writing(path: str) -> Iterator[sqlite3.Connection]:
    """Write connection scope: acquires the write lock, commits on success,
    rolls back on exception, and always closes the connection."""
    with _WRITE_LOCK:
        conn = connect(path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
