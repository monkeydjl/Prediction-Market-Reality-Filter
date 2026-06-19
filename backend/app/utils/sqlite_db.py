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

# Serializes writes across threads. SQLite handles its own file locking, but a
# process-level lock keeps concurrent writers in the same process from racing on
# the read-modify-write paths (e.g. upsert) and turns lock contention into a wait
# rather than an OperationalError.
_WRITE_LOCK = threading.Lock()


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
