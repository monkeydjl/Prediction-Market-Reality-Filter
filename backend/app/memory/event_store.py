"""event_store.py
================
Durable event intelligence store (JSON-file backed).

Unlike market_memory (a short-lived TTL compute cache), this is a durable store
of event intelligence records keyed by event_id. It is used for lookup and lets
the same event accumulate updates across scans.
"""

import os
import logging
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.models.event import EventRecord
from app.utils.file_store import locked_file, read_json, read_json_strict, write_json_atomic
from app.utils.helpers import utc_now


logger = logging.getLogger(__name__)


def _store_path() -> str:
    return os.path.abspath(settings.EVENT_STORE_FILE)


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
    return save_events([record], skip_invalid=False)[0]


def save_events(
    records: list[dict[str, Any]],
    *,
    skip_invalid: bool = True,
) -> list[dict[str, Any]]:
    """Upsert a batch of event records in one locked write.

    Each record is validated against EventRecord before storing. In batch mode,
    malformed records are skipped so one bad LLM output does not drop the rest
    of the batch. save_event keeps strict single-record semantics and raises.
    first_seen is preserved across updates; last_updated is refreshed.

    A re-scan must not erase a settled event: `outcome` and `calibration` are
    write-once results of resolution, so an incoming record that lacks them
    (a fresh discovery pass carries neither) inherits the stored ones rather than
    blanking them. Without this, re-discovering an already-resolved event by the
    same event_id would revert it to unresolved - dropping it from
    list_resolved_events, breaking auto-resolve idempotency, and losing the
    calibration sample. (tracking is preserved for the same reason - it is a
    user-owned decision.)
    """
    path = _store_path()
    stored: list[dict[str, Any]] = []
    with locked_file(path):
        store = _load_for_write(path)
        now = utc_now()
        for record in records:
            try:
                event_id = record["event_id"]
                existing = store.get(event_id) or {}
                existing_record = existing.get("record") or {}
                candidate = dict(record)
                # Tracking is a user-owned decision; a re-scan must not reset it to
                # the default. Preserve any existing tracking over the incoming one.
                existing_tracking = existing_record.get("tracking")
                if existing_tracking is not None:
                    candidate["tracking"] = existing_tracking
                # Outcome / calibration are resolution results: preserve them when the
                # incoming record (e.g. a re-discovery) does not carry them, so a
                # settled event is never silently reverted to unresolved.
                if "outcome" not in candidate and existing_record.get("outcome") is not None:
                    candidate["outcome"] = existing_record["outcome"]
                if "calibration" not in candidate and existing_record.get("calibration") is not None:
                    candidate["calibration"] = existing_record["calibration"]
                # Chinese title preservation: a re-scan that falls back to the
                # deterministic path produces an empty event_title_zh. Keep the
                # previously LLM-generated Chinese title so the UI never regresses
                # from Chinese back to English.
                if not candidate.get("event_title_zh") and existing_record.get("event_title_zh"):
                    candidate["event_title_zh"] = existing_record["event_title_zh"]
                EventRecord.model_validate(candidate)
            except Exception as exc:
                if not skip_invalid:
                    raise
                logger.warning(
                    "Skipping invalid event record in batch [event_id=%s]: %s",
                    record.get("event_id", "<missing>") if isinstance(record, dict) else "<non-dict>",
                    exc,
                )
                continue
            entry = {
                "event_id": event_id,
                "first_seen": existing.get("first_seen", now),
                "last_updated": now,
                "record": candidate,
            }
            store[event_id] = entry
            stored.append(entry)
        if stored:
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
        # Auto-archive: resolved events leave the active tracking list and enter
        # the calibration/review database. Preserves any existing priority so
        # the historical review page can still sort by human-assigned priority.
        tracking = record.get("tracking") or {}
        if not isinstance(tracking, dict):
            tracking = {}
        tracking["status"] = "archived"
        record["tracking"] = tracking
        EventRecord.model_validate(record)  # gate: bad outcome/calibration raises here
        now = utc_now()
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
        now = utc_now()
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


def auto_archive_expired(events: list[dict[str, Any]] | None = None) -> int:
    """Archive events whose source market is confirmed closed/expired.

    Modifies tracking.status to 'archived' for any non-resolved event whose
    source market is expired (per _is_source_expired).  Uses a direct low-level
    write (bypasses save_events) because save_events preserves tracking as a
    user-owned field.  Returns the count of newly archived events.

    Accepts an optional pre-loaded event list (e.g. from list_all_events) to
    avoid a duplicate store read during batch operations.
    """
    if events is None:
        events = list_all_events()
    to_archive: list[str] = []
    for entry in events:
        record = entry.get("record") or {}
        event_id = entry.get("event_id") or record.get("event_id", "")
        if not event_id:
            continue
        tracking = record.get("tracking") or {}
        current_status = tracking.get("status", "watching")
        if current_status == "archived":
            continue
        if (record.get("outcome") or {}).get("status"):
            continue  # resolved events are handled by their outcome
        if not _is_source_expired(record):
            continue
        to_archive.append(event_id)

    if not to_archive:
        return 0

    path = _store_path()
    with locked_file(path):
        store = _load_for_write(path)
        now = utc_now()
        for event_id in to_archive:
            entry = store.get(event_id)
            if entry is None:
                continue
            record = entry.get("record") or {}
            record.setdefault("tracking", {})["status"] = "archived"
            entry["last_updated"] = now
            store[event_id] = entry
        write_json_atomic(path, store, indent=2)

    logger = __import__("logging").getLogger(__name__)
    logger.info("Auto-archived %d events with expired source markets", len(to_archive))
    return len(to_archive)


def list_resolved_events() -> list[dict[str, Any]]:
    """Return every stored entry that has been genuinely resolved.

    Unlike list_events, this is unranked and unbounded: it returns the full set
    of resolved entries so a calibration aggregate never silently drops
    low-value-score events. Order is the store's insertion/upsert order.

    Only outcomes with status == "resolved" are returned. A non-resolved status
    (e.g. "invalid", written when a verified event->market link diverges) records
    that the event was settled but excludes it from calibration, so a wrong-link
    settlement never enters the Brier aggregate.
    """
    store = _load_unlocked(_store_path())
    return [
        entry for entry in store.values()
        if (outcome := (entry.get("record") or {}).get("outcome")) is not None
        and outcome.get("status", "resolved") == "resolved"
    ]


def _category(record: dict[str, Any]) -> str:
    legacy = record.get("legacy_analysis") or {}
    source = record.get("source") or {}
    if source.get("type") == "sports_event":
        return "sports_event"
    return str(
        legacy.get("base_rate_category")
        or source.get("type")
        or source.get("platform")
        or "general"
    )


def _tracking_status(record: dict[str, Any]) -> str:
    tracking = record.get("tracking") or {}
    status = tracking.get("status")
    if status in {"tracking", "archived"}:
        return str(status)
    return "watching"


def _probability(record: dict[str, Any], key: str) -> float:
    try:
        return float((record.get("probability") or {}).get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _event_query_text(entry: dict[str, Any]) -> str:
    record = entry.get("record") or {}
    return " ".join(
        str(value or "")
        for value in (
            record.get("event_title"),
            record.get("event_title_zh"),
            record.get("event_summary"),
            _category(record),
        )
    ).lower()


def _is_source_expired(record: dict[str, Any]) -> bool:
    """Return True when the source market is clearly closed or past its close date.

    Checks:
    1. source.closed == True (Polymarket)
    2. source.status in settled statuses (Kalshi)
    3. source.end_date or source.close_time is in the past (and the event is
       not resolved — resolved events are handled by their outcome.status).
    """
    source = (record or {}).get("source") or {}
    # Explicit close / settled flag from source adapter
    if source.get("closed") is True:
        return True
    status = str(source.get("status", "") or "").lower()
    if status in {"settled", "finalized", "closed", "determined", "resolved"}:
        return True

    # Don't auto-expire if already resolved (outcome.status controls that)
    if (record.get("outcome") or {}).get("status"):
        return False

    # End-date / close-time check
    end_str = str(source.get("end_date") or source.get("close_time") or "").strip()
    if not end_str:
        return False
    try:
        from datetime import datetime, timezone
        end_date = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        return end_date < datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


def _filtered_ranked_events(
    *,
    query: str = "",
    status: str = "all",
    category: str = "all",
    sort: str = "value",
    exclude_expired: bool = True,
) -> list[dict[str, Any]]:
    entries = list(_load_unlocked(_store_path()).values())
    q = query.strip().lower()
    if q:
        entries = [entry for entry in entries if q in _event_query_text(entry)]
    if status == "active":
        entries = [
            entry for entry in entries
            if _tracking_status(entry.get("record") or {}) != "archived"
        ]
    elif status != "all":
        entries = [
            entry for entry in entries
            if _tracking_status(entry.get("record") or {}) == status
        ]
    if category != "all":
        entries = [
            entry for entry in entries
            if _category(entry.get("record") or {}) == category
        ]
    if exclude_expired:
        entries = [
            entry for entry in entries
            if not _is_source_expired(entry.get("record") or {})
        ]

    def sort_key(entry: dict[str, Any]) -> float:
        record = entry.get("record") or {}
        if sort == "delta":
            return abs(_probability(record, "change"))
        if sort == "probability":
            return _probability(record, "estimated")
        if sort == "support":
            return float(((record.get("credibility") or {}).get("confidence") or 0.0))
        return float(record.get("value_score") or 0.0)

    return sorted(entries, key=sort_key, reverse=True)


def count_events(
    *,
    query: str = "",
    status: str = "all",
    category: str = "all",
    sort: str = "value",
    exclude_expired: bool = True,
) -> int:
    """Count stored entries after the same filters used by list_events."""
    return len(_filtered_ranked_events(
        query=query,
        status=status,
        category=category,
        sort=sort,
        exclude_expired=exclude_expired,
    ))


def list_events(
    limit: int = 50,
    offset: int = 0,
    *,
    query: str = "",
    status: str = "all",
    category: str = "all",
    sort: str = "value",
    exclude_expired: bool = True,
) -> list[dict[str, Any]]:
    """Return stored entries filtered and sorted for the dashboard table."""
    ranked = _filtered_ranked_events(
        query=query,
        status=status,
        category=category,
        sort=sort,
        exclude_expired=exclude_expired,
    )
    return ranked[offset:offset + limit]
