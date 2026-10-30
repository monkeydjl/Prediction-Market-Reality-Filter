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

Two readings, not one, because "how many rows point at a deleted event" and
"how much work is still blocked by a deleted event" are different questions
with different shelf lives. The first is history and can only grow -- a closed
trade whose event was purged is a fact, not a defect. The second is the one an
operator can act on, and it is bounded by the rows that still have a next step.
Reporting only the first made the dashboard badge permanently non-zero after
the first purge, which is the failure ``EXEMPT_TABLES`` exists to avoid -- a
reading nobody can act on. ``actionable_dangling_counts`` is the second.
"""
from __future__ import annotations

import logging
import sqlite3
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


# Which rows of each watched table still have a next step that a missing event
# would block. ``None`` means "never": the table records things that already
# happened, so nothing downstream waits on the event.
#
# Not cosmetic. Measured on the live database after one purge (audit 53.95):
# ``dangling_counts`` read 1 forever because a *voided* trade still named the
# deleted event, while nothing was actually wrong -- the trade had been voided
# precisely because its event was gone. A reading that cannot return to zero
# stops being a signal, which is the same reasoning that put
# ``domain_reliability_ledger`` in EXEMPT_TABLES above.
#
# Each predicate names a column the table's own schema declares; a test scans
# the CREATE TABLE bodies and fails when a predicate drifts from the schema
# (renamed column or table) instead of silently counting nothing.
LIVE_PREDICATES: dict[str, str | None] = {
    # awaiting resolution -- resolve_pending walks these by event
    "predictions": "status = 'open'",
    # awaiting the human link review; a verified link is a settled binding
    "event_market_links": "verified = 0",
    # awaiting settlement; closed and voided are both terminal
    "simulated_trades": "status = 'open'",
    # awaiting a reviewer; 'resolved' is the only other state (CHECK constraint)
    "review_queue_items": "status = 'pending'",
    # append-only snapshots of decisions already taken
    "decision_timeline": None,
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


def actionable_dangling_counts(event_ids: set[str]) -> dict[str, int]:
    """Per-table count of distinct stranded ``event_id`` values that still
    block a next step.

    Same shape and degradation as ``dangling_counts`` -- one connection, zeros
    when the database is unreadable, distinct ids rather than rows -- but each
    table is filtered by ``LIVE_PREDICATES``. Tables declared ``None`` stay at
    zero: their rows are history and no action waits on them.

    If a predicate names a column the table does not have, that table falls back
    to the unfiltered count with one info line. The row cannot be classified,
    and over-reporting a stranded reference is the safe direction (the operator
    sees a number and looks), whereas zero would hide a live one. Logged at info
    rather than warning because ``/api/health`` is polled by container
    healthchecks -- a schema mismatch would otherwise write a warning per poll.
    """
    counts = dict.fromkeys(REFERENCING_TABLES, 0)
    path = sqlite_db.loop_db_path()
    try:
        with reading(path) as conn:
            for table in _existing_tables(conn, REFERENCING_TABLES):
                predicate = LIVE_PREDICATES.get(table)
                if predicate is None:
                    continue
                base = (
                    f"SELECT DISTINCT event_id FROM {table} "  # noqa: S608 - literal tuple
                    "WHERE event_id IS NOT NULL AND event_id != ''"
                )
                try:
                    rows = conn.execute(f"{base} AND ({predicate})").fetchall()
                except sqlite3.OperationalError:
                    logger.info(
                        "live predicate for %s does not match its schema; "
                        "counting every row",
                        table,
                    )
                    rows = conn.execute(base).fetchall()
                counts[table] = sum(
                    1 for row in rows if str(row["event_id"]) not in event_ids
                )
    except Exception:
        logger.warning("actionable reference census failed", exc_info=True)
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
