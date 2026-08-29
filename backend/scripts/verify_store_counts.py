"""Count the records in every state store, so a restore can be verified.

A restore that "succeeded" is not a restore that worked. `restore_stores.py`
reports per-file checksums against the archive, which proves the bytes it wrote
match the bytes it read — it cannot tell you the archive was the *right* one, or
that the store you care about was in it at all. An archive written before
2026-08-28 contains four of the eight stores and restores cleanly.

So: take a census before, take it after, diff them. This is the missing
measurement, not a nicety. `docs/ops/RUNBOOK.md` uses it either side of the
restore step.

Two shapes matter and one rule does not cover both:

    event_store.json   {event_id: {...}, ...}          -> len(top level)
    sports_facts.json  {"updated_at": ..., "facts": []} -> len(d["facts"])

Counting top-level keys would report `sports_facts.json: 2` for a file holding
186 facts, and would report 2 both before and after a restore that dropped every
one of them. A census whose number cannot move is worse than no census, so each
store declares how it is counted and `COUNTERS` is asserted to cover exactly the
declared state stores.
"""
from __future__ import annotations

import argparse
import io
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from app.core import runtime_stores
from app.core.config import settings


def _count_json_mapping(path: Path) -> dict[str, int]:
    """Records keyed by id at the top level (event store, analysis cache)."""
    with io.open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected a JSON object, got {type(data).__name__}")
    return {"records": len(data)}


def _count_json_facts(path: Path) -> dict[str, int]:
    """`{"updated_at": ..., "facts": [...]}` — the list is the payload.

    Counting the top-level keys here would always answer 2.
    """
    with io.open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    facts = data.get("facts") if isinstance(data, dict) else None
    if not isinstance(facts, list):
        raise ValueError(f"{path.name}: expected a 'facts' list, got {type(facts).__name__}")
    return {"facts": len(facts)}


def _count_jsonl(path: Path) -> dict[str, int]:
    """Append-only audit trail: one record per non-blank line."""
    with io.open(path, encoding="utf-8") as fh:
        return {"records": sum(1 for line in fh if line.strip())}


def connect_readonly(path: Path) -> sqlite3.Connection:
    """Open a store for counting and nothing else.

    Public and separate from `_count_sqlite` so a test can assert the connection
    itself refuses writes. Inlining the URI at the call site meant the read-only
    guarantee was only ever asserted against a URI the *test* built — dropping
    `mode=ro` from production broke no write assertion.

    Reading a WAL database this way still materialises a 32 KB `-shm` and an
    empty `-wal`: SQLite needs the shared-memory index to coordinate with
    possible writers. Those are left alone deliberately. A `-wal` beside a live
    database can hold committed frames, so deleting one to tidy up would destroy
    data. The store's own bytes are never touched.
    """
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _count_sqlite(path: Path) -> dict[str, int]:
    """Row count per user table, plus a `TOTAL`.

    Read-only URI so a census never creates or migrates a store, and never
    perturbs one the app is using. Internal `sqlite_*` tables are skipped: they
    are SQLite's own bookkeeping, not records anyone restored.
    """
    conn = connect_readonly(path)
    try:
        tables = [
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        counts: dict[str, int] = {}
        for table in tables:
            # Table names come from sqlite_master, not from user input; quoted
            # anyway because a legal identifier can still need it.
            row = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
            counts[table] = int(row[0])
    finally:
        conn.close()
    counts["TOTAL"] = sum(counts.values())
    return counts


# How each declared state store is counted. Keyed by setting name so a new store
# cannot be added without deciding what "a record" means for it — see
# `test_every_declared_state_store_has_a_counter`.
COUNTERS: dict[str, Callable[[Path], dict[str, int]]] = {
    "EVENT_STORE_FILE": _count_json_mapping,
    "EVENT_AUDIT_FILE": _count_jsonl,
    "EVENT_CACHE_FILE": _count_json_mapping,
    "SPORTS_FACT_FILE": _count_json_facts,
    "LOOP_DB_FILE": _count_sqlite,
    "KERNEL_DB_FILE": _count_sqlite,
    "WORLD_CUP_PREDICTION_DB_FILE": _count_sqlite,
    "DOMAIN_RELIABILITY_DB_PATH": _count_sqlite,
}


def census() -> dict[str, Any]:
    """Count every declared state store. Never raises for one bad store."""
    stores: dict[str, Any] = {}
    for name in runtime_stores.state_setting_names():
        counter = COUNTERS.get(name)
        if counter is None:
            # Reported, not skipped, and reported *before* the path is resolved:
            # `getattr(settings, name)` without a default raises for a store that
            # is declared but has no setting, which made this branch unreachable
            # and turned a new uncounted store into a crash instead of a finding.
            stores[name] = {
                "error": "no counter declared for this store; add one to COUNTERS",
            }
            continue
        raw = getattr(settings, name, None)
        if raw is None:
            stores[name] = {"error": "declared as a state store but no such setting"}
            continue
        path = Path(raw)
        if not path.exists():
            stores[name] = {"path": str(path), "missing": True}
            continue
        try:
            stores[name] = {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "counts": counter(path),
            }
        except Exception as exc:
            stores[name] = {"path": str(path), "error": f"{type(exc).__name__}: {exc}"}
    return {"stores": stores}


def _totals(entry: dict[str, Any]) -> dict[str, int]:
    counts = entry.get("counts")
    return counts if isinstance(counts, dict) else {}


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Diff two censuses. `ok` is False when anything decreased or vanished.

    A restore is allowed to *add* records — the archive can be newer than the
    live store for a table the operator did not touch. It is never allowed to
    lose them, and a store that was present before and missing after is the
    loudest failure this can report.
    """
    losses: list[str] = []
    rows: list[dict[str, Any]] = []
    for name in sorted(set(before.get("stores", {})) | set(after.get("stores", {}))):
        b = before.get("stores", {}).get(name, {})
        a = after.get("stores", {}).get(name, {})
        bt, at = _totals(b), _totals(a)
        if b and not b.get("missing") and a.get("missing"):
            losses.append(f"{name}: present before, missing after")
        for key in sorted(set(bt) | set(at)):
            bv, av = bt.get(key), at.get(key)
            if bv is not None and av is not None and av < bv:
                losses.append(f"{name}.{key}: {bv} -> {av} ({av - bv})")
            elif bv is not None and av is None:
                losses.append(f"{name}.{key}: {bv} -> absent")
            if bv != av:
                rows.append({"store": name, "key": key, "before": bv, "after": av})
    return {"ok": not losses, "losses": losses, "changed": rows}


def _format(result: dict[str, Any]) -> str:
    lines: list[str] = []
    for name, entry in result["stores"].items():
        if entry.get("missing"):
            lines.append(f"[MISSING] {name}  {entry['path']}")
            continue
        if "error" in entry:
            lines.append(f"[ERROR]   {name}  {entry['error']}")
            continue
        counts = entry["counts"]
        headline = counts.get("TOTAL", counts.get("records", counts.get("facts")))
        lines.append(f"[OK]      {name}  {headline} ({entry['size_bytes']} bytes)")
        if len(counts) > 1:
            for key, value in counts.items():
                if key != "TOTAL":
                    lines.append(f"            {key}: {value}")
    return "\n".join(lines)


def _format_comparison(diff: dict[str, Any]) -> str:
    lines = [
        "[OK] no store lost records"
        if diff["ok"]
        else "[FAIL] records were lost:",
    ]
    lines += [f"  {loss}" for loss in diff["losses"]]
    if diff["changed"]:
        lines.append("changed:")
        lines += [
            f"  {r['store']}.{r['key']}: {r['before']} -> {r['after']}"
            for r in diff["changed"]
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Count records in every state store. Run before a restore with "
            "--save, then after with --compare to prove nothing was lost."
        )
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--save", metavar="FILE", help="write this census to FILE")
    parser.add_argument(
        "--compare",
        metavar="FILE",
        help="diff the current census against a census saved earlier; "
        "exit 1 if any store lost records",
    )
    args = parser.parse_args(argv)

    result = census()

    if args.save:
        with io.open(args.save, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)

    if args.compare:
        with io.open(args.compare, encoding="utf-8") as fh:
            before = json.load(fh)
        diff = compare(before, result)
        print(json.dumps(diff, ensure_ascii=False, indent=2) if args.json else _format_comparison(diff))
        return 0 if diff["ok"] else 1

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) if args.json else _format(result))
    broken = [n for n, e in result["stores"].items() if "error" in e]
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
