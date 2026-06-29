"""Event record schema versioning and normalization.

Addresses production-readiness gap §3.2: event_store.json records had no
schema_version field, and ``EventRecord(extra="allow")`` silently passed
records that were missing newly-added overlay fields (source_reliability,
llm_telemetry). This module provides:

1. ``CURRENT_SCHEMA_VERSION`` — the version new records are written with.
2. ``normalize_event_record(record)`` — upgrade an in-memory record dict to
   the current version by backfilling default values for fields introduced
   after the record's own schema_version.

Versioning scheme (semver-like, dotted string comparison via tuple parse):

- ``v1.0``  — pre-Phase-1 records. No overlay fields, no schema_version.
- ``v2.0``  — Phase 1-3 added: decision_quality, market_quality,
              final_displayed_direction, final_downgrade_reason.
- ``v2.1``  — Phase 4-5 added: source_reliability, llm_telemetry.

Forward path: every new field that flows into EventRecord gets a new minor
version bump and a corresponding ``setdefault`` in ``_UPGRADE_STEPS``.

This is a pure function: no I/O, no logging side effects, no settings reads.
Caller (event_store) decides when to call it (read path vs. write path vs.
background migration script).
"""
from __future__ import annotations

from typing import Any

# Current schema version. Bump when adding a new field to EventRecord.
#同步更新 backend/app/models/event.py::EventRecord.schema_version default.
CURRENT_SCHEMA_VERSION = "v2.1"

# Ordered upgrade steps: (version_introduced, field_name, default_value).
# When a record's schema_version < step.version, the field is backfilled
# with default_value (using setdefault so explicit values are preserved).
_UPGRADE_STEPS: tuple[tuple[str, str, Any], ...] = (
    # v2.0: Phase 1-3 overlays (decision_quality, market_quality, merge outputs).
    ("v2.0", "decision_quality", None),
    ("v2.0", "market_quality", None),
    ("v2.0", "final_displayed_direction", None),
    ("v2.0", "final_downgrade_reason", None),
    # v2.1: Phase 4-5 overlays.
    ("v2.1", "source_reliability", None),
    ("v2.1", "llm_telemetry", None),
)


def _parse_version(version: str) -> tuple[int, int]:
    """Parse 'v2.1' → (2, 1). Unknown / malformed → (0, 0) (treated as v1.0)."""
    if not isinstance(version, str) or not version.startswith("v"):
        return (0, 0)
    try:
        parts = version[1:].split(".")
        if len(parts) == 1:
            return (int(parts[0]), 0)
        return (int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return (0, 0)


def normalize_event_record(record: dict[str, Any]) -> dict[str, Any]:
    """Upgrade an in-memory event record to the current schema version.

    Backfills any overlay field that was introduced after the record's own
    ``schema_version`` (or after v1.0 if missing entirely). Existing explicit
    values are preserved via ``setdefault``. Always sets
    ``schema_version`` to ``CURRENT_SCHEMA_VERSION`` at the end.

    Does NOT mutate nested dicts (overlay blocks, outcome, etc.) — only
    top-level fields declared in EventRecord are managed here.

    Safe to call on:
    - Brand-new records (no schema_version → upgraded + tagged)
    - Old records missing fields (backfilled)
    - Already-current records (no-op, returns the same dict)

    Returns the same dict object (mutated in place) so callers can chain.
    """
    if not isinstance(record, dict):
        return record

    record_version = _parse_version(record.get("schema_version", "v1.0"))

    for step_version, field_name, default_value in _UPGRADE_STEPS:
        step_tuple = _parse_version(step_version)
        if record_version < step_tuple:
            # Field was introduced after the record's version → backfill.
            record.setdefault(field_name, default_value)

    record["schema_version"] = CURRENT_SCHEMA_VERSION
    return record


def needs_upgrade(record: dict[str, Any]) -> bool:
    """Return True if the record's schema_version is below current.

    Useful for migration scripts that want to count how many records need
    upgrading before doing the write.
    """
    if not isinstance(record, dict):
        return False
    return _parse_version(record.get("schema_version", "v1.0")) < _parse_version(CURRENT_SCHEMA_VERSION)
