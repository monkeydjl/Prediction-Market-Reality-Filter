"""Backfill Phase 1-5 quality overlay fields on historical event_store records.

Pre-Phase events were frozen before the overlay pipeline existed, so their
records lack ``decision_quality`` / ``market_quality`` / ``source_reliability``
/ ``llm_telemetry`` / ``final_displayed_direction`` / ``final_downgrade_reason``.

This script reuses the replay harness (``replay_record`` with
``preset_all_on()``) to rebuild the overlays from the frozen record's inputs
and writes them back to ``event_store.json``.

Limitation: Phase 5 LLM token usage is unrecoverable (the original LLM call
is not re-run during replay). The rebuilt ``llm_telemetry`` block will carry
``degraded_mode=True`` as a placeholder, flagged in the telemetry block's
``replayed`` field. This is acceptable because backfill targets the overlay
*structure* for replay/dashboard sample coverage, not live cost accounting.

Usage:
    python -m scripts.backfill_quality_overlays --dry-run
    python -m scripts.backfill_quality_overlays --apply
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

# Make backend importable when run as a script.
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.core.config import settings  # noqa: E402
from app.utils.file_store import (  # noqa: E402
    locked_file,
    read_json_strict,
    write_json_atomic,
)

logger = logging.getLogger(__name__)

# Overlay field names that this script populates. A record is considered
# "needs backfill" if it is missing ``decision_quality`` (the first overlay
# built). Other fields may be legitimately absent (e.g. ``market_quality`` is
# only built for prediction_market sources), so we key off decision_quality
# as the leading indicator.
_OVERLAY_FIELDS = (
    "decision_quality",
    "market_quality",
    "source_reliability",
    "llm_telemetry",
    "final_displayed_direction",
    "final_downgrade_reason",
)


def backfill_quality_overlays(
    dry_run: bool = True,
    event_ids: list[str] | None = None,
) -> dict[str, int]:
    """Backfill overlay fields on historical event_store.json records.

    Args:
        dry_run: When True, report what would change without writing.
        event_ids: Optional filter; only backfill these event IDs. None =
            all events.

    Returns:
        ``{"would_backfill": N, "skipped": M, "backfilled": K, "errors": E}``.
        ``would_backfill`` is set in dry-run; ``backfilled`` in apply mode.
    """
    from app.replay.config import ReplayConfig
    from app.replay.runner import replay_record

    store_path = Path(settings.EVENT_STORE_FILE).resolve()
    if not store_path.exists():
        logger.error("event_store.json not found at %s", store_path)
        return {"would_backfill": 0, "skipped": 0, "backfilled": 0, "errors": 0}

    id_filter = set(event_ids) if event_ids else None
    # Force all overlay flags ON regardless of runtime .env state. The script's
    # documented intent is to rebuild all 5 overlays + merge + guardrail on
    # historical records; relying on ``preset_all_on()`` would silently no-op
    # in environments where ``DECISION_QUALITY_ENABLED`` etc. are unset/false
    # (the default), defeating the backfill.
    cfg = ReplayConfig(
        decision_quality_enabled=True,
        market_quality_enabled=True,
        source_reliability_enabled=True,
        llm_telemetry_enabled=True,
        guardrails_enabled=True,
    )

    would_backfill = 0
    skipped = 0
    backfilled = 0
    errors = 0

    with locked_file(str(store_path)):
        store = read_json_strict(str(store_path), {})
        if not isinstance(store, dict):
            raise RuntimeError(
                f"event_store.json is not a dict (got {type(store).__name__})"
            )

        for event_id, entry in store.items():
            if not isinstance(entry, dict):
                continue
            if id_filter is not None and event_id not in id_filter:
                continue
            record = entry.get("record")
            if not isinstance(record, dict):
                continue
            # Skip records that already have decision_quality (already backfilled
            # or produced post-Phase). Other overlay fields may be legitimately
            # absent, so decision_quality is the leading indicator.
            if "decision_quality" in record:
                skipped += 1
                continue
            try:
                replayed = replay_record(record, cfg)
                # Copy rebuilt overlay fields back onto the stored record.
                for field in _OVERLAY_FIELDS:
                    if field in replayed:
                        record[field] = replayed[field]
                backfilled += 1
                would_backfill += 1
            except Exception as exc:
                logger.error("[FAIL] backfill %s: %s", event_id, exc)
                errors += 1

        if dry_run:
            logger.info(
                "[DRY-RUN] would backfill=%d skipped=%d errors=%d",
                would_backfill, skipped, errors,
            )
            return {
                "would_backfill": would_backfill,
                "skipped": skipped,
                "backfilled": 0,
                "errors": errors,
            }

        # Apply: persist atomically.
        write_json_atomic(str(store_path), store, indent=2)

    logger.info(
        "[OK] backfilled=%d skipped=%d errors=%d",
        backfilled, skipped, errors,
    )
    return {
        "would_backfill": 0,
        "skipped": skipped,
        "backfilled": backfilled,
        "errors": errors,
    }


def _main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Backfill Phase 1-5 quality overlays on historical records."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report only, no writes.")
    mode.add_argument("--apply", action="store_true", help="Write backfill to event_store.json.")
    parser.add_argument(
        "--event-id", action="append", dest="event_ids",
        help="Limit to these event IDs (repeatable).",
    )
    args = parser.parse_args()
    result = backfill_quality_overlays(
        dry_run=args.dry_run,
        event_ids=args.event_ids,
    )
    # UTF-8 safe print for Windows GBK consoles.
    import json
    enc = getattr(sys.stdout, "encoding", "") or ""
    if enc.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    print(json.dumps(result, indent=2))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(_main())
