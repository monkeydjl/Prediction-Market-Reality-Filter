"""Domain reliability store (LATER #2).

SQLite-backed aggregate statistics for per-domain evidence reliability.
Tables live in the configured domain reliability SQLite database. Uses an
idempotency ledger so incremental apply_resolution can be called safely on
re-resolve.

Schema follows the source_trust_registry_store pattern: module-level
functions, lazy schema init, sqlite_db.writing/reading.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from app.core.config import settings
from app.services.domain_reliability_service import (
    attribute_evidence,
    compute_reliability_stats,
)
from app.utils import sqlite_db
from app.utils.helpers import utc_now

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS domain_reliability (
    domain           TEXT NOT NULL,
    category         TEXT NOT NULL DEFAULT '_all',
    sample_count     INTEGER NOT NULL DEFAULT 0,
    correct_count    INTEGER NOT NULL DEFAULT 0,
    wrong_count      INTEGER NOT NULL DEFAULT 0,
    credibility_sum  REAL NOT NULL DEFAULT 0.0,
    brier_sum        REAL NOT NULL DEFAULT 0.0,
    brier_count      INTEGER NOT NULL DEFAULT 0,
    first_seen       TEXT NOT NULL,
    last_updated     TEXT NOT NULL,
    PRIMARY KEY (domain, category)
);

CREATE TABLE IF NOT EXISTS domain_reliability_ledger (
    event_id      TEXT NOT NULL,
    domain        TEXT NOT NULL,
    category      TEXT NOT NULL,
    correct       INTEGER NOT NULL,
    credibility   REAL,
    first_seen    TEXT NOT NULL,
    PRIMARY KEY (event_id, domain, category)
);
"""

_SCHEMA_VERSION = 2
# v1 -> v2 (Q3): the aggregate row carried only the 0/1 direction hit rate, so
# the prior fed into build_source_reliability could not tell a confident correct
# call from a lucky coin flip. An existing v1 DB gets the columns at 0/0, which
# reports honestly as "no gradeable sample yet" rather than as a perfect Brier.
_MIGRATIONS: dict[str, str] = {
    "brier_sum": "REAL NOT NULL DEFAULT 0.0",
    "brier_count": "INTEGER NOT NULL DEFAULT 0",
}

_INITIALIZED: set[str] = set()
_INIT_GUARD = threading.Lock()


def _db_path() -> str:
    return settings.DOMAIN_RELIABILITY_DB_PATH


def _ensure_schema(path: str) -> None:
    if path in _INITIALIZED:
        return
    with _INIT_GUARD:
        if path in _INITIALIZED:
            return
        with sqlite_db.writing(path) as conn:
            conn.executescript(_SCHEMA)
            sqlite_db.apply_migrations(conn, "domain_reliability",
                                       _SCHEMA_VERSION, _MIGRATIONS)
            sqlite_db.record_schema_version(conn, "domain_reliability",
                                            _SCHEMA_VERSION)
        _INITIALIZED.add(path)


def _row_to_stat(row: Any) -> dict[str, Any]:
    sample = row["sample_count"]
    correct = row["correct_count"]
    credibility_sum = row["credibility_sum"]
    brier_sum = row["brier_sum"]
    brier_count = row["brier_count"]
    wrong = sample - correct
    min_samples = settings.DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES
    return {
        "domain": row["domain"],
        "category": row["category"],
        "sample_count": sample,
        "correct_count": correct,
        "wrong_count": wrong,
        "credibility_sum": credibility_sum,
        "reliability_score": (correct / sample) if sample > 0 else None,
        "credibility_avg": (credibility_sum / sample) if sample > 0 else None,
        # Brier is averaged over brier_count, never over sample_count: an
        # attribution whose event was never frozen has no gradeable estimate,
        # and dividing by the wider count would report it as a perfect 0.0.
        "brier_sum": brier_sum,
        "brier_count": brier_count,
        "brier_avg": (brier_sum / brier_count) if brier_count > 0 else None,
        "brier_skill_score": (
            1.0 - (brier_sum / brier_count) if brier_count > 0 else None
        ),
        "insufficient_samples": sample < min_samples,
        "first_seen": row["first_seen"],
        "last_updated": row["last_updated"],
    }


def _committed_probability(event_id: str) -> float | None:
    """The 0-100 estimate frozen for this event before its outcome was known.

    Reads ``predictions.ai_probability``, which ``freeze_prediction`` writes once
    at first sight and never overwrites -- unlike ``record["ai_probability"]``,
    which every re-scan rewrites. Returns None when the event was never frozen
    or the stored value is unusable; callers must then leave ``brier`` unset
    rather than fall back to the record's latest estimate.

    Best-effort: the loop DB is a different file from this store's, and a domain
    reliability write must not fail because that file is unavailable.
    """
    try:
        from app.memory.prediction_store import get_prediction

        row = get_prediction(event_id)
    except Exception:
        logger.warning(
            "committed probability lookup failed for %s; recording the "
            "attribution without a Brier", event_id, exc_info=True,
        )
        return None
    if not row:
        return None
    value = row.get("ai_probability")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def apply_resolution(record: dict[str, Any]) -> None:
    """Incrementally apply one resolved event.

    Calls attribute_evidence(record). For each attribution, writes both
    the real category row and the domain _all row. Uses
    domain_reliability_ledger to skip already-processed
    event/domain/category attributions.

    The committed probability is looked up ONCE for the whole record, before the
    attribution loop: one event yields one row per (domain, category) pair, so a
    lookup inside the loop would re-read the same prediction row for every
    domain that appeared on the event.
    """
    committed = _committed_probability(str(record.get("event_id") or ""))
    attributions = attribute_evidence(record, committed_probability=committed)
    if not attributions:
        return

    path = _db_path()
    _ensure_schema(path)
    now = utc_now()

    with sqlite_db.writing(path) as conn:
        for attr in attributions:
            event_id = attr["event_id"]
            domain = attr["domain"]
            category = attr["category"]
            correct = 1 if attr["correct"] else 0
            wrong = 0 if correct else 1
            credibility = attr.get("credibility")
            brier = attr.get("brier")
            brier_add = float(brier) if brier is not None else 0.0
            brier_n = 1 if brier is not None else 0

            # Idempotency check against the original attribution key only.
            # If (event_id, domain, category) is already in the ledger, skip
            # the entire attribution (both the category row and the _all row).
            existing = conn.execute(
                "SELECT 1 FROM domain_reliability_ledger "
                "WHERE event_id = ? AND domain = ? AND category = ?",
                (event_id, domain, category),
            ).fetchone()
            if existing:
                continue

            # Record the original attribution key in the ledger.
            conn.execute(
                "INSERT INTO domain_reliability_ledger "
                "(event_id, domain, category, correct, credibility, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, domain, category, correct, credibility, now),
            )

            # Upsert both the (domain, category) row and the (domain, "_all") row.
            for cat in (category, "_all"):
                conn.execute(
                    "INSERT INTO domain_reliability "
                    "(domain, category, sample_count, correct_count, "
                    "wrong_count, credibility_sum, brier_sum, brier_count, "
                    "first_seen, last_updated) "
                    "VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(domain, category) DO UPDATE SET "
                    "sample_count = sample_count + 1, "
                    "correct_count = correct_count + ?, "
                    "wrong_count = wrong_count + ?, "
                    "credibility_sum = credibility_sum + ?, "
                    "brier_sum = brier_sum + ?, "
                    "brier_count = brier_count + ?, "
                    "last_updated = ?",
                    (domain, cat, correct,
                     wrong, credibility or 0.0, brier_add, brier_n, now, now,
                     correct, wrong,
                     credibility or 0.0, brier_add, brier_n, now),
                )


def rebuild_from_records(records: list[dict[str, Any]]) -> None:
    """Clear and rebuild all aggregate and ledger rows from records."""
    all_attributions: list[dict[str, Any]] = []
    for record in records:
        all_attributions.extend(
            attribute_evidence(
                record,
                committed_probability=_committed_probability(
                    str(record.get("event_id") or "")
                ),
            )
        )

    stats = compute_reliability_stats(all_attributions)

    path = _db_path()
    _ensure_schema(path)
    now = utc_now()

    with sqlite_db.writing(path) as conn:
        conn.execute("DELETE FROM domain_reliability")
        conn.execute("DELETE FROM domain_reliability_ledger")

        for (domain, category), s in stats.items():
            for cat in (category, "_all"):
                # Check if we already wrote this (domain, _all) combo
                existing = conn.execute(
                    "SELECT sample_count, correct_count, credibility_sum "
                    "FROM domain_reliability WHERE domain = ? AND category = ?",
                    (domain, cat),
                ).fetchone()

                if existing:
                    # Accumulate into existing _all row
                    conn.execute(
                        "UPDATE domain_reliability SET "
                        "sample_count = sample_count + ?, "
                        "correct_count = correct_count + ?, "
                        "wrong_count = wrong_count + ?, "
                        "credibility_sum = credibility_sum + ?, "
                        "brier_sum = brier_sum + ?, "
                        "brier_count = brier_count + ?, "
                        "last_updated = ? "
                        "WHERE domain = ? AND category = ?",
                        (s["sample_count"], s["correct_count"], s["wrong_count"],
                         s["credibility_sum"], s["brier_sum"], s["brier_count"],
                         now, domain, cat),
                    )
                else:
                    conn.execute(
                        "INSERT INTO domain_reliability "
                        "(domain, category, sample_count, correct_count, "
                        "wrong_count, credibility_sum, brier_sum, brier_count, "
                        "first_seen, last_updated) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (domain, cat, s["sample_count"], s["correct_count"],
                         s["wrong_count"], s["credibility_sum"], s["brier_sum"],
                         s["brier_count"], now, now),
                    )

        # Rebuild ledger
        for attr in all_attributions:
            conn.execute(
                "INSERT OR IGNORE INTO domain_reliability_ledger "
                "(event_id, domain, category, correct, credibility, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (attr["event_id"], attr["domain"], attr["category"],
                 1 if attr["correct"] else 0, attr.get("credibility"), now),
            )


def get_stats(
    domain: str | None = None,
    category: str | None = None,
    min_samples: int = 0,
) -> list[dict[str, Any]]:
    """Query stats with optional filters."""
    path = _db_path()
    _ensure_schema(path)

    clauses: list[str] = []
    params: list[Any] = []

    if domain is not None:
        clauses.append("domain = ?")
        params.append(domain)
    if category is not None:
        clauses.append("category = ?")
        params.append(category)
    if min_samples > 0:
        clauses.append("sample_count >= ?")
        params.append(min_samples)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    order = " ORDER BY domain, category"

    with sqlite_db.reading(path) as conn:
        rows = conn.execute(
            f"SELECT * FROM domain_reliability{where}{order}", params
        ).fetchall()

    return [_row_to_stat(row) for row in rows]


def get_domain_summary(domain: str) -> dict[str, Any] | None:
    """Return the _all row for one domain, if present."""
    stats = get_stats(domain=domain, category="_all")
    return stats[0] if stats else None
