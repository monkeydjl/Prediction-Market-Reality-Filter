"""Low-cost local smoke checks for the running PMRF app.

The script assumes the backend and frontend are already running. It checks only
cheap read endpoints and static routes; it does not trigger discovery, analysis,
translation, or any external LLM calls.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


GENERIC_CATEGORIES = {"general", "prediction", "prediction_market", "prediction_question"}
SOURCE_CATEGORIES = {"Limitless", "Polymarket", "Kalshi", "Market"}


def fetch_json(url: str, timeout: float = 10.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        if response.status != 200:
            raise AssertionError(f"{url} returned HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def check_status(url: str, expected: int = 200, timeout: float = 10.0) -> None:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        if response.status != expected:
            raise AssertionError(f"{url} returned HTTP {response.status}, expected {expected}")


def validate_event_payload(payload: dict[str, Any]) -> None:
    events = payload.get("events")
    if not isinstance(events, list):
        raise AssertionError("event list response missing events[]")
    if not events:
        return
    for entry in events:
        if not isinstance(entry, dict):
            raise AssertionError("event entry is not an object")
        category = entry.get("category")
        if not isinstance(category, str) or not category:
            raise AssertionError(f"event {entry.get('event_id')} missing backend category")
        if category in SOURCE_CATEGORIES:
            raise AssertionError(f"event {entry.get('event_id')} has source category {category}")
        if category in GENERIC_CATEGORIES:
            title = ((entry.get("record") or {}).get("event_title") or "").strip()
            raise AssertionError(f"event {entry.get('event_id')} has generic category {category}: {title}")


def validate_category_counts(payload: dict[str, Any]) -> None:
    counts = payload.get("counts")
    if not isinstance(counts, dict):
        raise AssertionError("category-counts response missing counts object")
    bad_sources = SOURCE_CATEGORIES.intersection(counts)
    if bad_sources:
        raise AssertionError(f"category-counts includes source category: {sorted(bad_sources)}")


def run_smoke(frontend_base: str, backend_base: str) -> list[str]:
    checked: list[str] = []
    for path in ["/", "/events", "/quality", "/decisions"]:
        check_status(f"{frontend_base.rstrip('/')}{path}")
        checked.append(f"frontend {path}")

    check_status(f"{backend_base.rstrip('/')}/api")
    checked.append("backend /api")


    events = fetch_json(f"{backend_base.rstrip('/')}/api/events/?limit=10&status=active")
    validate_event_payload(events)
    checked.append("backend /api/events active list")

    counts = fetch_json(f"{backend_base.rstrip('/')}/api/events/category-counts?status=active")
    validate_category_counts(counts)
    checked.append("backend /api/events/category-counts")

    event_entries = events.get("events") or []
    if event_entries:
        event_id = event_entries[0].get("event_id")
        detail = fetch_json(f"{backend_base.rstrip('/')}/api/events/{event_id}")
        if detail.get("category") != event_entries[0].get("category"):
            raise AssertionError("detail category does not match list category")
        checked.append("backend /api/events/{id}")

    return checked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run low-cost local smoke checks.")
    parser.add_argument("--frontend", default="http://localhost:3000")
    parser.add_argument("--backend", default="http://localhost:8000")
    args = parser.parse_args(argv)
    try:
        checked = run_smoke(args.frontend, args.backend)
    except (AssertionError, OSError, urllib.error.URLError) as exc:
        print(f"[FAIL] smoke check failed: {exc}", file=sys.stderr)
        return 1
    for item in checked:
        print(f"[OK] {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
