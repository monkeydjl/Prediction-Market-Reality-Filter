"""backfill_prediction_snapshots.py
=================================
One-time migration script: backfill Phase 3 snapshot columns for predictions
that were frozen BEFORE Phase 3 (PREDICTION_CALIBRATION_ENABLED) was deployed.

Context
-------
``freeze_prediction`` uses ``INSERT ... ON CONFLICT(event_id) DO NOTHING``, so
pre-existing frozen predictions are skipped — their snapshot columns
(``snapshot_question``, ``snapshot_recommendation``, ``snapshot_confidence``,
``snapshot_evidence_strength``, ``snapshot_conflict_score``,
``snapshot_market_quality_score``, ``snapshot_source_platform``) remain at
their defaults (empty string / NULL).

This script finds those rows and populates the snapshot from the stored event
record (``event_store``) via ``build_prediction_snapshot``. Per the spec
(docs/superpowers/specs/2026-06-30-decision-quality-engine-design.md §1616-1621):
"Pre-existing frozen predictions MUST be backfilled by a one-time migration
script; the snapshot store MUST NOT use freeze_prediction's idempotent skip
path to avoid leaving gaps."

Usage (from the backend/ directory):
    python scripts/backfill_prediction_snapshots.py --dry-run   # preview only
    python scripts/backfill_prediction_snapshots.py              # apply

Exit codes: 0 = success, 1 = error.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

# Make `app` importable when run as a plain file (sys.path[0] is scripts/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from app.utils import sqlite_db  # noqa: E402
from app.memory.event_store import get_event  # noqa: E402
from app.memory.prediction_store import _ensure_schema  # noqa: E402
from app.utils.sqlite_db import reading, writing  # noqa: E402

logger = logging.getLogger("backfill_prediction_snapshots")

# A row needs backfill when snapshot_question is empty (the default from
# _empty_snapshot). We check snapshot_question because it is always populated
# when build_prediction_snapshot succeeds (it falls back to the event title).
_BACKFILL_QUERY = """
    SELECT event_id FROM predictions
    WHERE snapshot_question IS NULL OR snapshot_question = ''
    ORDER BY created_at ASC
"""

_UPDATE_SQL = """
    UPDATE predictions SET
        snapshot_question = ?,
        snapshot_recommendation = ?,
        snapshot_confidence = ?,
        snapshot_evidence_strength = ?,
        snapshot_conflict_score = ?,
        snapshot_market_quality_score = ?,
        snapshot_source_platform = ?
    WHERE event_id = ?
"""


def find_rows_needing_backfill(path: str) -> list[str]:
    """Return event_ids of prediction rows with empty snapshot_question."""
    _ensure_schema(path)
    with reading(path) as conn:
        rows = conn.execute(_BACKFILL_QUERY).fetchall()
    return [row["event_id"] for row in rows]


def backfill_row(event_id: str, dry_run: bool, path: str) -> dict[str, str]:
    """Backfill snapshot for one prediction row. Returns a status dict.

    Status values: "backfilled", "skipped_no_event", "skipped_already_set",
    "error".
    """
    # Fetch the stored event record to compute the snapshot from.
    # event_store.get_event returns the ENTRY wrapper
    # {"event_id", "first_seen", "last_updated", "record": <actual_event_dict>};
    # unwrap to get the record dict that build_prediction_snapshot expects.
    entry = get_event(event_id)
    if entry is None:
        # The event may have been deleted from event_store but the prediction
        # row remains. We cannot reconstruct the snapshot without the record.
        return {"event_id": event_id, "status": "skipped_no_event"}
    record = entry.get("record") or {}

    # Compute the snapshot (same logic as freeze_prediction).
    from app.services.prediction_calibration_service import build_prediction_snapshot

    try:
        snapshot = build_prediction_snapshot(record)
    except Exception as exc:
        logger.warning("snapshot build failed for %s: %s", event_id, exc)
        return {"event_id": event_id, "status": "error", "error": str(exc)}

    # Check if snapshot is still empty (event record lacked the fields
    # needed for a meaningful snapshot).
    if not snapshot.get("snapshot_question"):
        return {"event_id": event_id, "status": "skipped_no_event"}

    if dry_run:
        return {
            "event_id": event_id,
            "status": "would_backfill",
            "snapshot_question": snapshot["snapshot_question"][:80],
        }

    # Write the snapshot columns (UPDATE, not INSERT — the row already exists).
    with writing(path) as conn:
        conn.execute(
            _UPDATE_SQL,
            (
                snapshot["snapshot_question"],
                snapshot["snapshot_recommendation"],
                snapshot["snapshot_confidence"],
                snapshot["snapshot_evidence_strength"],
                snapshot["snapshot_conflict_score"],
                snapshot["snapshot_market_quality_score"],
                snapshot["snapshot_source_platform"],
                event_id,
            ),
        )

    return {"event_id": event_id, "status": "backfilled"}


def run(dry_run: bool = False) -> dict[str, int]:
    """Run the backfill. Returns a summary dict of status -> count."""
    path = sqlite_db.loop_db_path()
    event_ids = find_rows_needing_backfill(path)
    logger.info("Found %d prediction rows needing snapshot backfill", len(event_ids))

    summary: dict[str, int] = {}
    for event_id in event_ids:
        result = backfill_row(event_id, dry_run, path)
        status = result["status"]
        summary[status] = summary.get(status, 0) + 1
        if dry_run:
            preview = result.get("snapshot_question", "")
            logger.info("  [dry-run] %s -> %s (%s)", event_id, status, preview)
        else:
            logger.info("  %s -> %s", event_id, status)

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill Phase 3 snapshot columns for pre-existing predictions."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview which rows would be backfilled without writing changes.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # PREDICTION_CALIBRATION_ENABLED must be on for build_prediction_snapshot
    # to produce non-empty snapshots. Warn (don't hard-fail) so the script
    # can still run when the flag is off but the user explicitly invoked it.
    from app.core.config import settings
    if not settings.PREDICTION_CALIBRATION_ENABLED:
        logger.warning(
            "PREDICTION_CALIBRATION_ENABLED is false — snapshots will be empty. "
            "Set it to true in .env before running this script for real backfill."
        )

    summary = run(dry_run=args.dry_run)

    mode = "dry-run" if args.dry_run else "applied"
    logger.info("Backfill complete (%s): %s", mode, summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
