"""One-shot migration: persist event_store schema upgrade on disk.

Addresses production-readiness finding F4: ``_load_unlocked`` /
``_load_for_write`` now normalize records in-memory on every read, but
the on-disk ``event_store.json`` still has 70 historical records with
no ``schema_version`` field. The next ``save_events`` write would
persist the upgrade for the touched record, but untouched records stay
un-upgraded on disk indefinitely.

This script does a one-shot read-modify-write that:
1. Loads event_store.json (strict — fails loudly on corrupt store).
2. Runs ``normalize_event_record`` on every record.
3. Writes the upgraded store back atomically.
4. Reports the count of records upgraded (records that gained at least
   one new field).

Idempotent: running twice is a no-op (already-current records gain no
new fields). Safe to re-run after a schema bump.

Usage:
    # Preview which records would be upgraded (default: dry-run)
    python -m scripts.migrate_event_store_schema

    # Actually write the upgraded store
    python -m scripts.migrate_event_store_schema --apply

Exit codes:
    0 — success (or dry-run completed)
    1 — store missing / corrupt / write failed
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# Make backend importable when run as a script.
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.core.config import settings  # noqa: E402
from app.services.event_schema import (  # noqa: E402
    CURRENT_SCHEMA_VERSION,
    normalize_event_record,
)
from app.utils.file_store import locked_file, read_json_strict, write_json_atomic  # noqa: E402


def _print(text: str) -> None:
    """UTF-8 safe print for Windows GBK consoles."""
    enc = getattr(sys.stdout, "encoding", "") or ""
    if enc.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            text = text.encode("ascii", errors="replace").decode("ascii")
    print(text)


def migrate_event_store_schema(
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Run the schema migration on event_store.json.

    Args:
        apply: When False (default), only preview which records would be
            upgraded. When True, writes the upgraded store atomically.

    Returns a dict with keys: applied, store_path, total_records,
    upgraded_count, schema_version.
    """
    store_path = Path(settings.EVENT_STORE_FILE).resolve()
    if not store_path.exists():
        return {
            "applied": False,
            "store_path": str(store_path),
            "total_records": 0,
            "upgraded_count": 0,
            "schema_version": CURRENT_SCHEMA_VERSION,
            "error": "store file not found",
        }

    with locked_file(str(store_path)):
        store = read_json_strict(str(store_path), {})
        if not isinstance(store, dict):
            raise RuntimeError(
                f"event_store.json is not a dict (got {type(store).__name__})"
            )

        total = 0
        upgraded = 0
        for event_id, entry in store.items():
            if not isinstance(entry, dict):
                continue
            total += 1
            record = entry.get("record")
            if not isinstance(record, dict):
                continue
            # Snapshot fields present BEFORE normalize to detect upgrades.
            pre_keys = set(record.keys())
            normalize_event_record(record)
            post_keys = set(record.keys())
            if post_keys > pre_keys:
                upgraded += 1

        if apply and upgraded > 0:
            write_json_atomic(str(store_path), store, indent=2)

        return {
            "applied": apply,
            "store_path": str(store_path),
            "total_records": total,
            "upgraded_count": upgraded,
            "schema_version": CURRENT_SCHEMA_VERSION,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate event_store.json to the current schema version.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write the upgraded store (default: dry-run only).",
    )
    args = parser.parse_args(argv)

    try:
        result = migrate_event_store_schema(apply=args.apply)
    except (FileNotFoundError, RuntimeError) as e:
        _print(f"[FAIL] {e}")
        return 1

    if result.get("error"):
        _print(f"[FAIL] {result['error']}: {result['store_path']}")
        return 1

    mode = "[OK] Applied" if result["applied"] else "[DRY-RUN] Preview"
    _print(
        f"{mode} schema migration for {result['store_path']}\n"
        f"  schema_version target: {result['schema_version']}\n"
        f"  total records:    {result['total_records']}\n"
        f"  upgraded records: {result['upgraded_count']}"
    )
    if not result["applied"] and result["upgraded_count"] > 0:
        _print("\n  Run with --apply to persist the upgrade.")
    elif result["applied"] and result["upgraded_count"] > 0:
        _print("\n  event_store.json upgraded successfully.")
    elif result["upgraded_count"] == 0:
        _print("\n  All records already at current schema (no-op).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
