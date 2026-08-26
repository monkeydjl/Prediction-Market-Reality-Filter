"""event_store.py
================
Durable event intelligence store (JSON-file backed).

Unlike market_memory (a short-lived TTL compute cache), this is a durable store
of event intelligence records keyed by event_id. It is used for lookup and lets
the same event accumulate updates across scans.
"""

import os
import logging
from typing import Any

from app.core.config import settings
from app.models.event import EventRecord
from app.services.category_inference import infer_category_from_text
from app.services.event_schema import normalize_event_record
from app.utils.file_store import locked_file, read_json, read_json_strict, write_json_atomic
from app.utils.helpers import utc_now


logger = logging.getLogger(__name__)


def _store_path() -> str:
    return os.path.abspath(settings.EVENT_STORE_FILE)


def _load_unlocked(path: str) -> dict[str, Any]:
    data = read_json(path, {})
    if not isinstance(data, dict):
        return {}
    # P0-2 §4: normalize every record on read so callers always see the
    # current schema (overlay fields backfilled via setdefault). This is
    # an in-memory upgrade only — the on-disk store keeps its original
    # shape until the next save_events() write persists the upgrade.
    # Best-effort: a malformed entry is left untouched (callers handle
    # the missing-record case via .get("record") or {}).
    for event_id, entry in data.items():
        if not isinstance(entry, dict):
            continue
        record = entry.get("record")
        if isinstance(record, dict):
            try:
                normalize_event_record(record)
            except Exception:
                # Defensive: normalize is setdefault-only and should not
                # raise, but we never want a read path to fail.
                pass
    return data


def _load_for_write(path: str) -> dict[str, Any]:
    """Strict load for read-modify-write paths: raises on corrupt/IO error so
    the caller aborts instead of overwriting the durable store with empty data.

    Also normalizes every record on read (same as _load_unlocked) so the
    read-modify-write cycle persists the schema upgrade on the next write."""
    data = read_json_strict(path, {})
    if not isinstance(data, dict):
        return {}
    for event_id, entry in data.items():
        if not isinstance(entry, dict):
            continue
        record = entry.get("record")
        if isinstance(record, dict):
            try:
                normalize_event_record(record)
            except Exception:
                pass
    return data


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
    # Plan 5 §5.4: Decision timeline snapshot. When the flag is on, append
    # an overlay-bearing snapshot of each record to decision_timeline_store.
    # Read the flag ONCE here (not per-record) to avoid settings lookups in
    # the hot loop. Best-effort: a snapshot failure never blocks save_events.
    timeline_enabled = settings.DECISION_TIMELINE_ENABLED
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
                #
                # P0-6 metrics: detect a change in final_displayed_direction
                # across the save_events update. We compare the pre-existing
                # stored direction against the incoming candidate direction
                # and increment FINAL_DIRECTION_CHANGE when they differ.
                # This catches BOTH guardrail-induced changes (YES/NO -> WAIT)
                # AND ordinary re-scan drift (e.g. a re-analysis flips YES to
                # NO). The guardrail-only counter in event_intelligence_service
                # is a subset of this — kept for finer-grained attribution.
                _pre_dir = existing_record.get("final_displayed_direction")
                _post_dir = candidate.get("final_displayed_direction")
                if _pre_dir is not None and _post_dir is not None and _pre_dir != _post_dir:
                    try:
                        from app.utils.metrics import FINAL_DIRECTION_CHANGE
                        FINAL_DIRECTION_CHANGE.inc()
                    except Exception:  # pragma: no cover - defensive
                        pass
                if not candidate.get("event_title_zh") and existing_record.get("event_title_zh"):
                    candidate["event_title_zh"] = existing_record["event_title_zh"]
                # Schema upgrade: backfill any overlay field introduced after the
                # record's schema_version (see event_schema.normalize_event_record).
                # Idempotent on already-current records.
                normalize_event_record(candidate)
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
            # Plan 5 §5.4: Decision timeline snapshot — only after the record
            # passes normalization + validation, so malformed records that are
            # skipped don't leave "ghost snapshots" in the timeline store.
            if timeline_enabled:
                try:
                    from app.memory import decision_timeline_store
                    decision_timeline_store.record_snapshot(candidate)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning(
                        "decision_timeline snapshot failed for event %s: %s",
                        event_id, exc,
                    )
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
    entry = _load_unlocked(_store_path()).get(event_id)
    return _with_category(entry) if entry is not None else None


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


def set_tracking_bulk(
    event_ids: list[str],
    *,
    status: str | None = None,
    priority: str | None = None,
) -> list[str]:
    """Apply one tracking update to many events in a single locked write.

    Stands to ``set_tracking`` as ``save_events`` stands to ``save_event``: the
    same read-modify-write, once for the whole batch instead of once per event.
    That distinction is the whole point (E1, scale debt) — every mutating call
    rewrites the entire store file, so a caller that loops over ``set_tracking``
    pays the full file cost per event AND holds the cross-process lock for the
    duration, blocking the scheduler and every event read behind it.

    Returns the event_ids actually updated, in the order given. An unknown id is
    skipped (mirroring ``set_tracking`` returning None), and so is a record the
    update would make invalid — one bad record must not abort the rest of the
    batch, exactly as in ``save_events``. Callers must count the returned ids
    rather than the ids they passed in, or they would report skipped work as
    done.

    Nothing is written when no id matched, so a no-op batch does not rewrite the
    file for nothing.
    """
    if not event_ids:
        return []
    if status is None and priority is None:
        return []
    path = _store_path()
    updated_ids: list[str] = []
    with locked_file(path):
        store = _load_for_write(path)
        now = utc_now()
        for event_id in event_ids:
            entry = store.get(event_id)
            if entry is None:
                continue
            record = entry.get("record") or {}
            tracking = dict(record.get("tracking") or {})
            if status is not None:
                tracking["status"] = status
            if priority is not None:
                tracking["priority"] = priority
            candidate = dict(record)
            candidate["tracking"] = tracking
            try:
                EventRecord.model_validate(candidate)
            except Exception as exc:
                logger.warning(
                    "Skipping invalid tracking update in batch [event_id=%s]: %s",
                    event_id, exc,
                )
                continue
            store[event_id] = {
                "event_id": event_id,
                "first_seen": entry.get("first_seen", now),
                "last_updated": now,
                "record": candidate,
            }
            updated_ids.append(event_id)
        if updated_ids:
            write_json_atomic(path, store, indent=2)
    return updated_ids


def list_all_events() -> list[dict[str, Any]]:
    """Return every stored entry, unranked and unbounded.

    Unlike list_events (ranked by value_score, capped), this returns the full
    store so batch operations like auto-resolve do not silently miss
    low-value-score events. Order is the store's insertion/upsert order.
    """
    return list(_load_unlocked(_store_path()).values())


def store_bytes() -> int:
    """Size of the durable store file on disk, in bytes.

    E1 (scale debt): every mutating call rewrites the whole file, so the cost of
    one write scales with this number. Exposing it means the point at which the
    JSON store has to become a real database is a reading an operator can watch,
    rather than something first felt as a slow dashboard.

    Bytes only, no record count: every caller that wants the count already holds
    the loaded store (``loop_status`` has ``list_all_events()``, the metrics
    refresh counts during its own walk), and returning a count here would make
    each of them re-read and re-parse the whole file for a number they have in
    hand -- the very amplification this reading exists to expose. A missing file
    reports 0, since a fresh deploy has no store yet.
    """
    try:
        return os.path.getsize(_store_path())
    except OSError:
        return 0


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
    archived: list[str] = []
    with locked_file(path):
        store = _load_for_write(path)
        now = utc_now()
        for event_id in to_archive:
            # Not `entry`: the scan loop above already bound that name to an
            # element of `events`, and a name reused in one function keeps the
            # first binding's type - so the Optional from .get() had nowhere to
            # go.
            stored = store.get(event_id)
            if stored is None:
                continue
            record = stored.get("record") or {}
            record.setdefault("tracking", {})["status"] = "archived"
            stored["last_updated"] = now
            store[event_id] = stored
            archived.append(event_id)
        write_json_atomic(path, store, indent=2)

    logger = __import__("logging").getLogger(__name__)
    # Count what was written, not what was requested. `events` is scanned before
    # the lock is taken, so an id deleted in that window (DELETE /events/{id})
    # hits the `stored is None` skip above; reporting len(to_archive) would
    # credit the scheduler run with archiving a record that no longer exists.
    logger.info("Auto-archived %d events with expired source markets", len(archived))
    return len(archived)


def list_resolved_events(
    events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return every stored entry that has been genuinely resolved.

    Unlike list_events, this is unranked and unbounded: it returns the full set
    of resolved entries so a calibration aggregate never silently drops
    low-value-score events. Order is the store's insertion/upsert order.

    Only outcomes with status == "resolved" are returned. A non-resolved status
    (e.g. "invalid", written when a verified event->market link diverges) records
    that the event was settled but excludes it from calibration, so a wrong-link
    settlement never enters the Brier aggregate.

    Accepts an optional pre-loaded event list (e.g. from list_all_events) to
    avoid a duplicate whole-file read, the same way auto_archive_expired does.
    A caller that already holds the store -- loop_status, which backs the
    /api/health poll -- otherwise pays a second full read and parse of the
    entire file just to count these (E1: scale debt). The predicate stays here
    so it cannot drift from the one the calibration aggregate is built on.
    """
    if events is None:
        events = list_all_events()
    return [
        entry for entry in events
        if (outcome := (entry.get("record") or {}).get("outcome")) is not None
        and outcome.get("status", "resolved") == "resolved"
    ]


def _category(record: dict[str, Any]) -> str:
    legacy = record.get("legacy_analysis") or {}
    source = record.get("source") or {}
    if source.get("type") == "sports_event":
        return "sports_event"
    # Derived once per record rather than per field: _category runs for every
    # entry of every listing, count and search pass.
    generic = _generic_source_categories()
    return (
        _specific_category(legacy.get("base_rate_category"), generic)
        or _specific_category(source.get("category"), generic)
        or _specific_category(source.get("event_type"), generic)
        or _specific_category(source.get("type"), generic)
        or _specific_category(source.get("platform"), generic)
        or infer_category_from_text(
            record.get("event_title"),
            record.get("event_title_zh"),
            record.get("event_summary"),
            source.get("question"),
            source.get("title"),
            source.get("name"),
        )
        or "general"
    )


def _with_category(entry: dict[str, Any]) -> dict[str, Any]:
    projected = dict(entry)
    projected["category"] = _category(projected.get("record") or {})
    return projected


# Source *type* values the adapters write, plus the placeholders they use when a
# type is missing. Every one of these says where a record came from, never what
# it is about, so none may survive into the category dimension.
_GENERIC_SOURCE_TYPES = frozenset({
    "prediction",
    "predictions",
    "prediction_market",
    "prediction_question",
    "open_web",
    "manual",
    "market",
    "unknown",
})

# Settings fields the event-source adapters read for the platform they stamp on
# each record. Reading the names back out of settings -- rather than repeating
# the strings here -- means renaming a source through the environment cannot
# leave a stale spelling behind, and keeps this list to attribute *names*, which
# `test_event_store_source_names` pins against a scan of the adapter modules so
# a new source cannot arrive uncovered.
_PLATFORM_NAME_SETTINGS = (
    "KALSHI_SOURCE_NAME",
    "LIMITLESS_SOURCE_NAME",
    "MANIFOLD_SOURCE_NAME",
    "METACULUS_SOURCE_NAME",
    "OPEN_WEB_SOURCE_NAME",
    "OPINION_SOURCE_NAME",
    "PREDICT_FUN_SOURCE_NAME",
    "WORLD_CUP_SOURCE_NAME",
)

# polymarket_event_source stamps its platform as a literal; it has no setting.
_LITERAL_PLATFORM_NAMES = ("Polymarket",)


def _category_token(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _generic_source_categories() -> frozenset[str]:
    """Provenance labels that must never be read as a subject category.

    A record whose category fields are all absent falls through to
    ``source.type`` and then ``source.platform``, and if either survives, the
    event's *provenance* is presented as its subject: 48 of the 235 records in
    the live store were filed under the category ``manifold``, which was the
    single largest bucket in the dashboard's category dropdown. Only three
    platform names used to be listed here -- exactly the three that were live
    when the set was written -- so the six added since (Opinion, Predict.fun,
    Metaculus, Manifold, the World Cup source and Open Web) all leaked, as did
    the ``open_web`` and ``manual`` source types.
    """
    names = {_category_token(name) for name in _LITERAL_PLATFORM_NAMES}
    for attr in _PLATFORM_NAME_SETTINGS:
        token = _category_token(str(getattr(settings, attr, "") or ""))
        if token:
            names.add(token)
    return _GENERIC_SOURCE_TYPES | names


def _specific_category(value: Any, generic: frozenset[str] | None = None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if generic is None:
        generic = _generic_source_categories()
    return None if _category_token(cleaned) in generic else cleaned


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


def _is_resolved_record(record: dict[str, Any]) -> bool:
    outcome = (record or {}).get("outcome") or {}
    if outcome.get("actual_outcome") is None:
        return False
    return outcome.get("status", "resolved") == "resolved"


def source_close_time(record: dict[str, Any]) -> str:
    """The source market's close timestamp, or "" when the adapter records none.

    The field name is per-platform -- Polymarket writes ``end_date``, Kalshi
    writes ``close_time``, and Limitless writes neither -- so the precedence is
    named here once and read by every caller.  ``_is_source_expired`` below and
    the settlement monitor in ``event_resolve_service`` both need it, and if
    each spelled the fallback chain itself they could drift into disagreeing
    about whether an event is past due.
    """
    source = (record or {}).get("source") or {}
    return str(source.get("end_date") or source.get("close_time") or "").strip()


def is_source_expired(record: dict[str, Any]) -> bool:
    """Public form of :func:`_is_source_expired`, for cross-module reuse.

    ``event_resolve_service`` decides whether a platform's settlement feed is
    actually late by this predicate rather than its own, so "this market has
    closed" means one thing across the codebase.
    """
    return _is_source_expired(record)


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
    end_str = source_close_time(record)
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
    resolved_only: bool = False,
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
    if resolved_only:
        entries = [
            entry for entry in entries
            if _is_resolved_record(entry.get("record") or {})
        ]
    if exclude_expired and not resolved_only:
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
    resolved_only: bool = False,
) -> int:
    """Count stored entries after the same filters used by list_events."""
    return len(_filtered_ranked_events(
        query=query,
        status=status,
        category=category,
        sort=sort,
        exclude_expired=exclude_expired,
        resolved_only=resolved_only,
    ))


def count_events_by_category(
    *,
    query: str = "",
    status: str = "all",
    sort: str = "value",
    exclude_expired: bool = True,
    resolved_only: bool = False,
) -> dict[str, int]:
    """Return per-category event counts after non-category filters.

    The result is independent of the active category filter, so a dashboard
    category dropdown can show stable counts that do not jump when the user
    switches between categories.
    """
    entries = _filtered_ranked_events(
        query=query,
        status=status,
        category="all",
        sort=sort,
        exclude_expired=exclude_expired,
        resolved_only=resolved_only,
    )
    counts: dict[str, int] = {}
    for entry in entries:
        record = entry.get("record") or {}
        cat = _category(record)
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def list_events(
    limit: int = 50,
    offset: int = 0,
    *,
    query: str = "",
    status: str = "all",
    category: str = "all",
    sort: str = "value",
    exclude_expired: bool = True,
    resolved_only: bool = False,
) -> list[dict[str, Any]]:
    """Return stored entries filtered and sorted for the dashboard table.

    Delegates to ``list_events_page`` and drops the total, so the page a caller
    who does not need the count gets is produced by the same one ranking
    implementation -- rather than a second copy that could drift from it.
    """
    page, _total = list_events_page(
        limit,
        offset,
        query=query,
        status=status,
        category=category,
        sort=sort,
        exclude_expired=exclude_expired,
        resolved_only=resolved_only,
    )
    return page


def list_events_page(
    limit: int = 50,
    offset: int = 0,
    *,
    query: str = "",
    status: str = "all",
    category: str = "all",
    sort: str = "value",
    exclude_expired: bool = True,
    resolved_only: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Return one page of filtered entries AND how many matched, from one read.

    ``list_events`` + ``count_events`` compute the identical filtered ranking
    twice, from two separate reads of the whole store file. Beyond doubling the
    cost of the dashboard's busiest endpoint, the two reads happen at two
    different instants: a write landing between them (the scheduler's
    save_events, auto_archive_expired, another operator's set_tracking) makes
    the reported total describe a store the returned page did not come from.
    The dashboard sizes its pager off that total, so the mismatch can bounce a
    reader to a "last page" computed from a store it never saw.

    One pass, one instant: the page is a slice of the same ranking the count is
    the length of.
    """
    ranked = _filtered_ranked_events(
        query=query,
        status=status,
        category=category,
        sort=sort,
        exclude_expired=exclude_expired,
        resolved_only=resolved_only,
    )
    page = [_with_category(entry) for entry in ranked[offset:offset + limit]]
    return page, len(ranked)
