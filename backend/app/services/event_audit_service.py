"""event_audit_service.py
=======================
Append-only event observation log (event_audit.jsonl).

Separate from analysis_audit.jsonl. One line per event observation, capturing a
probability snapshot so probability change can be tracked over time.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.utils.file_store import locked_file, rewrite_lines_atomic


def _audit_path() -> str:
    return os.path.abspath(settings.EVENT_AUDIT_FILE)


def record_event(record: dict[str, Any]) -> None:
    """Append one probability snapshot for an event record.

    Once the audit log exceeds the configured compaction threshold, it is
    rewritten in place keeping only the most recent
    EVENT_AUDIT_MAX_PER_EVENT snapshots per event_id. This bounds the file size
    over long runs without losing recent trend history. Compaction runs inside
    the same lock as the append.
    """
    path = _audit_path()
    probability = record.get("probability") or {}
    credibility = record.get("credibility") or {}
    impact = record.get("impact") or {}
    source = record.get("source") or {}
    line = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_id": record.get("event_id"),
        "event_title": record.get("event_title"),
        "baseline": probability.get("baseline"),
        "estimated": probability.get("estimated"),
        "change": probability.get("change"),
        "direction": probability.get("direction"),
        "credibility_score": credibility.get("score"),
        "impact_score": impact.get("score"),
        "value_score": record.get("value_score"),
        "source_type": source.get("type"),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with locked_file(path):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        _maybe_compact(path)


def record_outcome(
    event_id: str,
    event_title: str,
    outcome: dict[str, Any],
) -> None:
    """Append one outcome snapshot for an event.

    Marked `kind: "outcome"` so history consumers can filter it out of the
    probability-snapshot view. `estimated` is intentionally None: analyze_trend
    skips non-numeric estimates, so an outcome snapshot never pollutes the
    probability trajectory (the outcome is not a probability estimate).
    """
    path = _audit_path()
    line = {
        "kind": "outcome",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_id": event_id,
        "event_title": event_title,
        "estimated": None,
        "outcome": {
            "status": outcome.get("status"),
            "actual_outcome": outcome.get("actual_outcome"),
            "confidence": outcome.get("confidence"),
            "source": outcome.get("source"),
        },
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with locked_file(path):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        _maybe_compact(path)


def _maybe_compact(path: str) -> None:
    """Shrink the audit log if it exceeds the configured threshold.

    Keeps the most recent EVENT_AUDIT_MAX_PER_EVENT snapshots per event_id
    (snapshots are stored oldest-to-newest, so the tail is the most recent).
    No-op when the threshold is 0 or the file is under it. Best-effort: a
    compaction failure is logged and swallowed so it never breaks the request
    that triggered it.
    """
    threshold = settings.EVENT_AUDIT_COMPACTION_THRESHOLD
    if threshold <= 0:
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = [ln for ln in handle if ln.strip()]
        if len(lines) <= threshold:
            return
        records = []
        for ln in lines:
            try:
                records.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        compacted = _compact_records(records, settings.EVENT_AUDIT_MAX_PER_EVENT)
        new_lines = [json.dumps(snap, ensure_ascii=False) for snap in compacted]
        # Atomic rewrite (tempfile + os.replace) so a crash mid-compaction
        # cannot truncate the audit log and lose history.
        rewrite_lines_atomic(path, new_lines)
    except Exception as exc:
        # Compaction is a maintenance optimization; never let it break the
        # append that already succeeded.
        import logging
        logging.getLogger(__name__).warning(
            "event_audit compaction skipped: %s", exc
        )


def _compact_records(
    records: list[dict[str, Any]], max_per_event: int
) -> list[dict[str, Any]]:
    """Keep at most the most recent `max_per_event` snapshots per event_id.

    Input order is oldest-to-newest; output preserves that order. Lines with no
    event_id are dropped (they cannot be grouped and are not queryable anyway).
    """
    if max_per_event <= 0:
        return records
    per_event: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for snap in records:
        event_id = snap.get("event_id")
        if not event_id:
            continue
        if event_id not in per_event:
            per_event[event_id] = []
            order.append(event_id)
        per_event[event_id].append(snap)
    kept: list[dict[str, Any]] = []
    for event_id in order:
        snapshots = per_event[event_id]
        # Keep outcome snapshots separate from probability snapshots: outcome
        # snapshots (kind="outcome") are resolution markers, not probability
        # estimates, so they must not consume the probability budget or be
        # crowded out by it. Keep the most recent outcome (at most one) plus
        # the most recent `max_per_event` probability snapshots, in original
        # oldest-to-newest order.
        outcomes = [s for s in snapshots if s.get("kind") == "outcome"]
        probabilities = [s for s in snapshots if s.get("kind") != "outcome"]
        kept_probabilities = probabilities[-max_per_event:]
        # Merge back in chronological order. Both lists are oldest-to-newest
        # and the outcome is the last event in the event's life, so appending
        # the kept outcome after the probabilities preserves the timeline.
        kept.extend(kept_probabilities)
        if outcomes:
            kept.append(outcomes[-1])
    return kept


def _read_all(path: str) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    records: list[dict[str, Any]] = []
    with locked_file(path):
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def load_recent_events(limit: int = 100) -> list[dict[str, Any]]:
    """Return the most recent event observations (oldest-to-newest)."""
    return _read_all(_audit_path())[-limit:]


def history_for_event(event_id: str) -> list[dict[str, Any]]:
    """Return all audit snapshots for one event_id, oldest-to-newest."""
    return [
        record
        for record in _read_all(_audit_path())
        if record.get("event_id") == event_id
    ]


def histories_by_event() -> dict[str, list[dict[str, Any]]]:
    """Group every audit snapshot by event_id, oldest-to-newest within each group.

    One pass over the log (unlike history_for_event, which re-reads per event),
    so this is the efficient way to summarize every tracked event at once.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in _read_all(_audit_path()):
        event_id = record.get("event_id")
        if not event_id:
            continue
        grouped.setdefault(event_id, []).append(record)
    return grouped
