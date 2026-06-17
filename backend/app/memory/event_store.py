"""event_store.py
================
Durable event intelligence store (JSON-file backed).

Unlike market_memory (a short-lived TTL compute cache), this is a durable store
of event intelligence records keyed by event_id. It is used for lookup and lets
the same event accumulate updates across scans.
"""

import os
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.models.event import EventRecord
from app.utils.file_store import locked_file, read_json, read_json_strict, write_json_atomic


def _store_path() -> str:
    return os.path.abspath(settings.EVENT_STORE_FILE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_unlocked(path: str) -> dict[str, Any]:
    data = read_json(path, {})
    return data if isinstance(data, dict) else {}


def _load_for_write(path: str) -> dict[str, Any]:
    """Strict load for read-modify-write paths: raises on corrupt/IO error so
    the caller aborts instead of overwriting the durable store with empty data."""
    data = read_json_strict(path, {})
    return data if isinstance(data, dict) else {}


def save_event(record: dict[str, Any]) -> dict[str, Any]:
    """Upsert a single event record by event_id. Returns the stored entry."""
    return save_events([record])[0]


def save_events(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Upsert a batch of event records in one locked write.

    Each record is validated against EventRecord before storing, so a malformed
    record raises instead of silently corrupting the store. first_seen is
    preserved across updates; last_updated is refreshed.
    """
    path = _store_path()
    stored: list[dict[str, Any]] = []
    with locked_file(path):
        store = _load_for_write(path)
        now = _now()
        for record in records:
            event_id = record["event_id"]
            existing = store.get(event_id) or {}
            # Tracking is a user-owned decision; a re-scan must not reset it to
            # the default. Preserve any existing tracking over the incoming one.
            existing_tracking = (existing.get("record") or {}).get("tracking")
            if existing_tracking is not None:
                record["tracking"] = existing_tracking
            EventRecord.model_validate(record)
            entry = {
                "event_id": event_id,
                "first_seen": existing.get("first_seen", now),
                "last_updated": now,
                "record": record,
            }
            store[event_id] = entry
            stored.append(entry)
        write_json_atomic(path, store, indent=2)
    return stored


def get_event(event_id: str) -> dict[str, Any] | None:
    """Return the stored entry for event_id, or None if not stored."""
    return _load_unlocked(_store_path()).get(event_id)


def resolve_event(
    event_id: str,
    outcome: dict[str, Any],
    calibration: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Attach an outcome (and optional calibration) to a stored event record.

    Merges `outcome` into the record and, when provided, `calibration` as a
    top-level record field. Re-validates the full record against EventRecord
    (so a malformed outcome or calibration raises instead of corrupting the
    store). Returns the updated entry, or None when event_id is not stored
    (callers raise 404). first_seen is preserved; last_updated is refreshed.

    `calibration` is a top-level record field, not nested under outcome,
    because the Outcome model is strict (extra fields are ignored); the
    EventRecord is permissive (extra="allow"), so the calibration snapshot
    survives validation and persists.
    """
    path = _store_path()
    with locked_file(path):
        store = _load_for_write(path)
        entry = store.get(event_id)
        if entry is None:
            return None
        record = entry.get("record") or {}
        record["outcome"] = outcome
        if calibration is not None:
            record["calibration"] = calibration
        EventRecord.model_validate(record)  # gate: bad outcome/calibration raises here
        now = _now()
        updated = {
            "event_id": event_id,
            "first_seen": entry.get("first_seen", now),
            "last_updated": now,
            "record": record,
        }
        store[event_id] = updated
        write_json_atomic(path, store, indent=2)
        return updated


def set_tracking(
    event_id: str,
    status: str | None = None,
    priority: str | None = None,
) -> dict[str, Any] | None:
    """Update the human tracking decision (status / priority) on a stored event.

    Merges the provided fields into record["tracking"], re-validates the full
    record against EventRecord, and saves. Returns the updated entry, or None
    when event_id is unknown (callers raise 404). first_seen is preserved;
    last_updated is refreshed.
    """
    path = _store_path()
    with locked_file(path):
        store = _load_for_write(path)
        entry = store.get(event_id)
        if entry is None:
            return None
        record = entry.get("record") or {}
        tracking = dict(record.get("tracking") or {})
        if status is not None:
            tracking["status"] = status
        if priority is not None:
            tracking["priority"] = priority
        record["tracking"] = tracking
        EventRecord.model_validate(record)  # gate: bad tracking raises here
        now = _now()
        updated = {
            "event_id": event_id,
            "first_seen": entry.get("first_seen", now),
            "last_updated": now,
            "record": record,
        }
        store[event_id] = updated
        write_json_atomic(path, store, indent=2)
        return updated


def list_all_events() -> list[dict[str, Any]]:
    """Return every stored entry, unranked and unbounded.

    Unlike list_events (ranked by value_score, capped), this returns the full
    store so batch operations like auto-resolve do not silently miss
    low-value-score events. Order is the store's insertion/upsert order.
    """
    return list(_load_unlocked(_store_path()).values())


def list_resolved_events() -> list[dict[str, Any]]:
    """Return every stored entry that has been resolved (has an outcome).

    Unlike list_events, this is unranked and unbounded: it returns the full set
    of resolved entries so a calibration aggregate never silently drops
    low-value-score events. Order is the store's insertion/upsert order.
    """
    store = _load_unlocked(_store_path())
    return [
        entry for entry in store.values()
        if (entry.get("record") or {}).get("outcome") is not None
    ]


def list_events(limit: int = 50) -> list[dict[str, Any]]:
    """Return stored entries sorted by record value_score (descending)."""
    entries = _load_unlocked(_store_path()).values()
    ranked = sorted(
        entries,
        key=lambda e: e.get("record", {}).get("value_score", 0),
        reverse=True,
    )
    return ranked[:limit]
