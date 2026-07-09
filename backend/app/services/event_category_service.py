"""Utilities for repairing and auditing event category coverage."""

from __future__ import annotations

from typing import Any

from app.memory import event_store
from app.services.base_rate_service import classify_market


def _legacy_category(record: dict[str, Any]) -> str:
    legacy = record.get("legacy_analysis") or {}
    return str(legacy.get("base_rate_category") or "").strip()


def _question_text(record: dict[str, Any]) -> str:
    return str(
        record.get("event_title")
        or (record.get("source") or {}).get("question")
        or ""
    ).strip()


def backfill_event_categories(*, dry_run: bool = True) -> dict[str, Any]:
    """Backfill missing/unknown ``legacy_analysis.base_rate_category`` values.

    The event list and guardrails still use ``source.category`` / ``event_type``
    as display fallbacks, but calibration and prediction freezing key off
    ``legacy_analysis.base_rate_category``. This repair only fills records whose
    category is absent or explicitly ``unknown`` and only when the deterministic
    classifier can produce a non-unknown category.
    """

    entries = event_store.list_all_events()
    updates: list[dict[str, Any]] = []
    updated_records: list[dict[str, Any]] = []
    skipped_known = 0
    skipped_unknown = 0

    for entry in entries:
        record = dict(entry.get("record") or {})
        current = _legacy_category(record)
        if current and current != "unknown":
            skipped_known += 1
            continue

        question = _question_text(record)
        classified = classify_market(question)
        if classified.category == "unknown":
            skipped_unknown += 1
            continue

        legacy = dict(record.get("legacy_analysis") or {})
        legacy["base_rate_category"] = classified.category
        record["legacy_analysis"] = legacy
        updates.append({
            "event_id": entry.get("event_id") or record.get("event_id"),
            "old_category": current or "",
            "new_category": classified.category,
            "title": question,
        })
        updated_records.append(record)

    if updated_records and not dry_run:
        event_store.save_events(updated_records, skip_invalid=False)

    return {
        "status": "ok",
        "dry_run": dry_run,
        "checked_count": len(entries),
        "updated_count": len(updates),
        "skipped_known_count": skipped_known,
        "skipped_still_unknown_count": skipped_unknown,
        "updates": updates,
    }
