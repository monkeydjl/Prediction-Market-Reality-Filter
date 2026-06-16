"""event_cache.py
===============
Event-flow LLM compute cache (TTL), separate from the legacy market_cache.

The event discover/analyze flow caches per-question analysis results to avoid
re-calling the LLM within the TTL window. This was previously borrowed from
market_memory (market_cache.json), which coupled the event flow to a legacy
file also written by /scan and the scheduler. This module gives the event flow
its own cache file (event_cache.json) so it is self-contained; the legacy
/scan and scheduler keep using market_cache.json unchanged (dual-track).

Semantics mirror market_memory: a 1-hour TTL, key = normalized question
(strip + lowercase + truncate to 200 chars), expired entries purged on write.
This is a compute cache, not durable storage - the event store (event_store.json)
holds the durable records, and the event audit log (event_audit.jsonl) holds
the probability snapshots.
"""

import os
import time
from typing import Any

from app.core.config import settings
from app.utils.file_store import locked_file, read_json, write_json_atomic


_DEFAULT_TTL = 3600  # 1 hour, same as market_cache


def _cache_file() -> str:
    return os.path.abspath(settings.EVENT_CACHE_FILE)


def _cache_key(question: str) -> str:
    return question.strip().lower()[:200]


def get_cached_event(question: str, ttl: int = _DEFAULT_TTL) -> dict[str, Any] | None:
    """Return the cached event record for `question`, or None if missing/expired."""
    cache = read_json(_cache_file(), {})
    if not isinstance(cache, dict):
        return None
    entry = cache.get(_cache_key(question))
    if entry is None:
        return None
    if time.time() - entry.get("cached_at", 0) > ttl:
        return None
    return entry.get("record")


def set_cached_event(question: str, record: dict[str, Any]) -> None:
    """Cache an event record for `question`, purging expired entries."""
    path = _cache_file()
    with locked_file(path):
        cache = read_json(path, {})
        if not isinstance(cache, dict):
            cache = {}
        cache[_cache_key(question)] = {
            "cached_at": time.time(),
            "question": question,
            "record": record,
        }
        _purge_expired(cache)
        write_json_atomic(path, cache, indent=2)


def _purge_expired(cache: dict[str, Any], ttl: int = _DEFAULT_TTL) -> None:
    now = time.time()
    expired = [
        key for key, value in cache.items()
        if now - value.get("cached_at", 0) > ttl
    ]
    for key in expired:
        del cache[key]
