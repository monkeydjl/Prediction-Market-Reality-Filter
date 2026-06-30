from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any, Iterator

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.utils.file_store import (
    locked_file,
    read_json_strict,
    rewrite_lines_atomic,
    write_json_atomic,
)


_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SQLITE_WRITE_LOCK = threading.Lock()
_OLD_ID_RE = re.compile(r"^[0-9a-f]{12}$")


@contextmanager
def _sqlite_reading(path: str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _sqlite_writing(path: str) -> Iterator[sqlite3.Connection]:
    with _SQLITE_WRITE_LOCK:
        conn = sqlite3.connect(path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _default_path(env_name: str, relative: str) -> str:
    return os.path.abspath(os.getenv(env_name, str(_BACKEND_DIR / relative)))


def _event_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _candidate_texts(record: dict[str, Any]) -> list[str]:
    legacy = record.get("legacy_analysis") or {}
    values = [
        record.get("event_title"),
        legacy.get("event_question"),
        legacy.get("market_question"),
    ]
    seen: set[str] = set()
    texts: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            texts.append(text)
            seen.add(text)
    return texts


def _new_id_for_entry(old_id: str, entry: dict[str, Any]) -> str | None:
    if not _OLD_ID_RE.fullmatch(old_id):
        return None
    record = entry.get("record") or {}
    if not isinstance(record, dict):
        return None
    for text in _candidate_texts(record):
        new_id = _event_id(text)
        if new_id.startswith(old_id):
            return new_id
    return None


def _load_event_store(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    data = read_json_strict(path, {})
    return data if isinstance(data, dict) else {}


def _build_mapping(store: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for old_id, entry in store.items():
        if not isinstance(entry, dict):
            continue
        new_id = _new_id_for_entry(str(old_id), entry)
        if new_id and new_id != old_id:
            mapping[str(old_id)] = new_id
    return mapping


def _event_store_conflicts(
    store: dict[str, Any],
    mapping: dict[str, str],
) -> list[str]:
    conflicts: list[str] = []
    reverse: dict[str, str] = {}
    for old_id, new_id in mapping.items():
        other_old = reverse.get(new_id)
        if other_old is not None and other_old != old_id:
            conflicts.append(
                f"multiple old event_ids map to {new_id}: {other_old}, {old_id}"
            )
        reverse[new_id] = old_id
        if new_id in store and new_id != old_id:
            conflicts.append(
                f"event_store already has target event_id {new_id} for old {old_id}"
            )
    return conflicts


def _table_exists(conn: Any, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)


def _sqlite_counts_and_conflicts(
    db_path: str,
    mapping: dict[str, str],
) -> tuple[int, int, list[str]]:
    if not mapping or not os.path.exists(db_path):
        return 0, 0, []
    old_ids = list(mapping)
    prediction_updates = 0
    link_updates = 0
    conflicts: list[str] = []

    with _sqlite_reading(db_path) as conn:
        if _table_exists(conn, "predictions"):
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM predictions WHERE event_id IN ({_placeholders(old_ids)})",
                old_ids,
            ).fetchone()
            prediction_updates = int(row["n"] or 0)
            for old_id, new_id in mapping.items():
                old_row = conn.execute(
                    "SELECT 1 FROM predictions WHERE event_id=?",
                    (old_id,),
                ).fetchone()
                new_row = conn.execute(
                    "SELECT 1 FROM predictions WHERE event_id=?",
                    (new_id,),
                ).fetchone()
                if old_row is not None and new_row is not None:
                    conflicts.append(
                        f"predictions already has target event_id {new_id} for old {old_id}"
                    )

        if _table_exists(conn, "event_market_links"):
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM event_market_links WHERE event_id IN ({_placeholders(old_ids)})",
                old_ids,
            ).fetchone()
            link_updates = int(row["n"] or 0)
            for old_id, new_id in mapping.items():
                rows = conn.execute(
                    "SELECT contract_id FROM event_market_links WHERE event_id=?",
                    (old_id,),
                ).fetchall()
                for row in rows:
                    contract_id = row["contract_id"]
                    existing = conn.execute(
                        """
                        SELECT 1 FROM event_market_links
                        WHERE event_id=? AND contract_id=?
                        """,
                        (new_id, contract_id),
                    ).fetchone()
                    if existing is not None:
                        conflicts.append(
                            "event_market_links already has target "
                            f"({new_id}, {contract_id}) for old {old_id}"
                        )

    return prediction_updates, link_updates, conflicts


def _audit_update_count(path: str, mapping: dict[str, str]) -> int:
    if not mapping or not os.path.exists(path):
        return 0
    count = 0
    with locked_file(path):
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("event_id") in mapping:
                    count += 1
    return count


def _rewrite_entry_event_id(entry: dict[str, Any], new_id: str) -> dict[str, Any]:
    updated = dict(entry)
    updated["event_id"] = new_id
    record = dict(updated.get("record") or {})
    record["event_id"] = new_id
    updated["record"] = record
    return updated


def _apply_event_store(path: str, mapping: dict[str, str]) -> None:
    if not mapping:
        return
    with locked_file(path):
        store = _load_event_store(path)
        rewritten: dict[str, Any] = {}
        for event_id, entry in store.items():
            new_id = mapping.get(str(event_id))
            if new_id and isinstance(entry, dict):
                rewritten[new_id] = _rewrite_entry_event_id(entry, new_id)
            else:
                rewritten[str(event_id)] = entry
        write_json_atomic(path, rewritten, indent=2)


def _apply_audit(path: str, mapping: dict[str, str]) -> None:
    if not mapping or not os.path.exists(path):
        return
    with locked_file(path):
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
        rewritten: list[str] = []
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                rewritten.append(line)
                continue
            new_id = mapping.get(str(record.get("event_id") or ""))
            if new_id:
                record["event_id"] = new_id
                rewritten.append(json.dumps(record, ensure_ascii=False))
            else:
                rewritten.append(line)
        rewrite_lines_atomic(path, rewritten)


def _apply_sqlite(db_path: str, mapping: dict[str, str]) -> None:
    if not mapping or not os.path.exists(db_path):
        return
    with _sqlite_writing(db_path) as conn:
        if _table_exists(conn, "predictions"):
            for old_id, new_id in mapping.items():
                conn.execute(
                    "UPDATE predictions SET event_id=? WHERE event_id=?",
                    (new_id, old_id),
                )
        if _table_exists(conn, "event_market_links"):
            for old_id, new_id in mapping.items():
                conn.execute(
                    "UPDATE event_market_links SET event_id=? WHERE event_id=?",
                    (new_id, old_id),
                )


def migrate_event_ids(
    *,
    event_store_path: str | None = None,
    event_audit_path: str | None = None,
    loop_db_path: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    event_store_path = os.path.abspath(
        event_store_path or _default_path("EVENT_STORE_FILE", "event_store.json")
    )
    event_audit_path = os.path.abspath(
        event_audit_path or _default_path("EVENT_AUDIT_FILE", "event_audit.jsonl")
    )
    loop_db_path = os.path.abspath(
        loop_db_path or _default_path("LOOP_DB_FILE", "v2_loop.db")
    )

    store = _load_event_store(event_store_path)
    mapping = _build_mapping(store)
    conflicts = _event_store_conflicts(store, mapping)
    prediction_updates, link_updates, sqlite_conflicts = _sqlite_counts_and_conflicts(
        loop_db_path, mapping
    )
    conflicts.extend(sqlite_conflicts)
    report = {
        "apply": apply,
        "event_store_path": event_store_path,
        "event_audit_path": event_audit_path,
        "loop_db_path": loop_db_path,
        "mappings": [
            {"old_event_id": old_id, "new_event_id": new_id}
            for old_id, new_id in sorted(mapping.items())
        ],
        "conflicts": conflicts,
        "event_store_updates": len(mapping),
        "audit_updates": _audit_update_count(event_audit_path, mapping),
        "prediction_updates": prediction_updates,
        "link_updates": link_updates,
    }
    if not apply:
        return report
    if conflicts:
        raise RuntimeError("event_id migration has conflicts; run dry-run first")

    _apply_sqlite(loop_db_path, mapping)
    _apply_event_store(event_store_path, mapping)
    _apply_audit(event_audit_path, mapping)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate legacy 12-hex event IDs to the current 16-hex IDs."
    )
    parser.add_argument("--apply", action="store_true", help="write changes")
    parser.add_argument("--event-store", default=None)
    parser.add_argument("--event-audit", default=None)
    parser.add_argument("--loop-db", default=None)
    args = parser.parse_args()

    try:
        report = migrate_event_ids(
            event_store_path=args.event_store,
            event_audit_path=args.event_audit,
            loop_db_path=args.loop_db,
            apply=args.apply,
        )
    except Exception as exc:
        print(f"event_id migration failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
