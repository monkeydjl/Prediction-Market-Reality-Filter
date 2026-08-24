"""Census of loop-DB rows that reference an event in the JSON event store.

E2 (跨 JSON/SQLite 无硬 FK). Events live in ``backend/event_store.json``; the
rows about them live in SQLite. No foreign key can span that boundary, so
nothing stops a row from outliving the event it names. ``DELETE
/events/{event_id}`` removes only the JSON record, and nothing prunes the
tables (``loop_db_maintenance`` is WAL truncation plus an integrity check), so
stranded rows accumulate for the life of the database.

This module is what stands in for the missing constraint: it counts the
references that no longer resolve, per table, and it counts the references a
single event is about to strand. Both read from one declared list of tables, so
the status reading and the delete warning can never disagree about what counts
as a reference.
"""
from __future__ import annotations

import logging
from typing import Any

from app.utils import sqlite_db
from app.utils.sqlite_db import reading

logger = logging.getLogger(__name__)


# Every loop-DB table whose ``event_id`` is a pointer that has to resolve to a
# record in the JSON event store.
#
# Declared as data rather than discovered at query time, for two reasons. A new
# table must be an explicit decision -- silently widening the SQL below the day
# someone adds an ``event_id`` column would change a published reading without
# anyone choosing to. And the table names are interpolated into SQL; a literal
# tuple keeps that provably safe.
#
# ``tests/test_event_ref_census.py`` scans the live schema for every table
# carrying an ``event_id`` column and asserts this tuple plus ``EXEMPT_TABLES``
# exactly partition it -- no missing table, no stale name, no overlap. Before
# that census existed the list was hand-maintained at two entries while six
# tables had the column, and the one genuinely stranded row in the live database
# sat in an unwatched table, so the reading said zero.
REFERENCING_TABLES: tuple[str, ...] = (
    "predictions",
    "event_market_links",
    "simulated_trades",
    "review_queue_items",
    "decision_timeline",
)

# Tables whose ``event_id`` is deliberately allowed to outlive the event, each
# with the reason. These are excluded from the count because a reading that is
# permanently non-zero by design is a reading nobody can act on.
EXEMPT_TABLES: dict[str, str] = {
    "domain_reliability_ledger": (
        "Two independent reasons. (1) Semantics: event_id is the dedup key of a "
        "credit already earned -- PRIMARY KEY (event_id, domain, category) exists "
        "so one domain is credited once per event -- not a pointer that has to "
        "resolve. The ledger is the domain's track record and is read by domain, "
        "never by event_id; deleting an event does not retract the fact that the "
        "domain was right on that occasion, and counting these would shrink a "
        "trust measurement for the same reason E1 refused to let a TTL evict "
        "resolved calibration samples. (2) Location: the store writes to "
        "settings.DOMAIN_RELIABILITY_DB_PATH, a different database from "
        "loop_db_path(), so the census below could not read the real table even "
        "if it wanted to -- the empty copy in the loop DB is an artifact of that "
        "setting having once pointed here. The first reason is the load-bearing "
        "one: it would still be exempt if it moved into the loop DB tomorrow."
    ),
}


def _existing_tables(conn: Any, wanted: tuple[str, ...]) -> set[str]:
    """Which of ``wanted`` exist in this database.

    Every store creates its table lazily on first use, so on a fresh deploy most
    of them are absent. Asking first turns "no such table" into the honest
    answer (a table with no rows strands nothing) instead of an exception per
    table per poll -- ``/api/health`` is polled by container healthchecks, and
    the previous per-table ``except`` logged a warning for each absent one.
    """
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    present = {str(row["name"]) for row in rows}
    return {table for table in wanted if table in present}


def dangling_counts(event_ids: set[str]) -> dict[str, int]:
    """Per-table count of distinct ``event_id`` values absent from ``event_ids``.

    Counts distinct ids rather than rows: the question is how many events are
    referenced but gone, and one missing event with forty timeline snapshots is
    one broken reference, not forty.

    One connection for every table. The previous version opened a fresh
    connection per table inside the loop, which cost one connect-and-close per
    watched table on every ``/api/health`` poll (E1's lesson, in the module that
    poll spends most of its time in).

    Degrades to zeros on a database error rather than raising: a status endpoint
    that 500s because the loop DB is unreadable is worse than one reporting no
    known dangling references, and the scheduler ledger surfaces the DB fault
    separately.
    """
    counts = dict.fromkeys(REFERENCING_TABLES, 0)
    path = sqlite_db.loop_db_path()
    try:
        with reading(path) as conn:
            for table in _existing_tables(conn, REFERENCING_TABLES):
                rows = conn.execute(
                    f"SELECT DISTINCT event_id FROM {table} "  # noqa: S608 - literal tuple
                    "WHERE event_id IS NOT NULL AND event_id != ''"
                ).fetchall()
                counts[table] = sum(
                    1 for row in rows if str(row["event_id"]) not in event_ids
                )
    except Exception:
        logger.warning("dangling reference census failed", exc_info=True)
        return dict.fromkeys(REFERENCING_TABLES, 0)
    return counts


def refs_for_event(event_id: str) -> dict[str, int]:
    """Per-table row count referencing one event, for tables with rows.

    Rows here, not distinct ids: the caller is about to strand this event and
    wants to know how much is left pointing at it. Tables with no matching row
    are omitted so a caller can report the non-empty ones without filtering.

    Same degradation as ``dangling_counts``: an unreadable loop DB must not turn
    a delete into a 500 after the JSON record is already gone.
    """
    if not event_id:
        return {}
    found: dict[str, int] = {}
    path = sqlite_db.loop_db_path()
    try:
        with reading(path) as conn:
            for table in _existing_tables(conn, REFERENCING_TABLES):
                row = conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table} "  # noqa: S608 - literal tuple
                    "WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                n = int(row["n"] or 0) if row is not None else 0
                if n:
                    found[table] = n
    except Exception:
        logger.warning(
            "event reference lookup failed [event_id=%s]", event_id, exc_info=True
        )
        return {}
    return found
