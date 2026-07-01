"""Admin CLI for the Source Trust Registry (Plan 4 §6.1).

Usage:
    python -m scripts.source_trust_registry_cli list [--category CAT]
    python -m scripts.source_trust_registry_cli add --pattern P --type {domain,source_name}
           [--tier TIER] [--base-trust FLOAT] [--category CAT] [--notes TEXT]
    python -m scripts.source_trust_registry_cli delete --pattern P
    python -m scripts.source_trust_registry_cli export [--json]
    python -m scripts.source_trust_registry_cli import --file PATH [--dry-run]

Actions are INSERT/UPDATE/DELETE on the SQLite registry. ``list`` and
``export`` are read-only. Uses ASCII labels for Windows GBK safety.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.memory import source_trust_registry_store as registry


def _print(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def _cmd_list(args: argparse.Namespace) -> int:
    entries = registry.list_entries(list_category=args.category)
    if not entries:
        _print("[INFO] no entries found")
        return 0
    _print(f"[OK] {len(entries)} entries:")
    for e in entries:
        _print(
            f"  {e['pattern']:<40} type={e['pattern_type']:<12} "
            f"tier={e['tier'] or '-':<10} trust={e['base_trust']!s:<6} "
            f"cat={e['list_category'] or '-':<10} notes={e['notes']}"
        )
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    try:
        registry.upsert_entry(
            pattern=args.pattern,
            pattern_type=args.type,
            tier=args.tier,
            base_trust=args.base_trust,
            list_category=args.category,
            notes=args.notes or "",
        )
    except ValueError as exc:
        _print(f"[FAIL] {exc}")
        return 1
    _print(f"[OK] upserted: {args.pattern}")
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    ok = registry.delete_entry(args.pattern)
    if ok:
        _print(f"[OK] deleted: {args.pattern}")
        return 0
    _print(f"[FAIL] not found: {args.pattern}")
    return 1


def _cmd_export(args: argparse.Namespace) -> int:
    entries = registry.list_entries()
    payload = [
        {
            "pattern": e["pattern"],
            "pattern_type": e["pattern_type"],
            "tier": e["tier"],
            "base_trust": e["base_trust"],
            "list_category": e["list_category"],
            "notes": e["notes"],
        }
        for e in entries
    ]
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print(f"[OK] {len(payload)} entries (use --json for machine-readable)")
        for e in payload:
            _print(f"  {e['pattern']} | {e['pattern_type']} | {e['tier']}")
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    with open(args.file, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        _print("[FAIL] import file must be a JSON array of entries")
        return 1
    applied = 0
    skipped = 0
    for entry in data:
        if args.dry_run:
            _print(f"[DRY-RUN] would upsert: {entry.get('pattern')}")
            applied += 1
            continue
        try:
            registry.upsert_entry(
                pattern=entry["pattern"],
                pattern_type=entry["pattern_type"],
                tier=entry.get("tier"),
                base_trust=entry.get("base_trust"),
                list_category=entry.get("list_category"),
                notes=entry.get("notes", ""),
            )
            applied += 1
        except (ValueError, KeyError) as exc:
            _print(f"[WARN] skipped {entry.get('pattern')}: {exc}")
            skipped += 1
    _print(f"[OK] applied={applied} skipped={skipped}"
          + (" [DRY-RUN]" if args.dry_run else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Source Trust Registry admin CLI"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list entries")
    p_list.add_argument("--category", default=None)
    p_list.set_defaults(func=_cmd_list)

    p_add = sub.add_parser("add", help="add/update an entry")
    p_add.add_argument("--pattern", required=True)
    p_add.add_argument("--type", required=True,
                       choices=["domain", "source_name"])
    p_add.add_argument("--tier", default=None)
    p_add.add_argument("--base-trust", type=float, default=None)
    p_add.add_argument("--category", default=None)
    p_add.add_argument("--notes", default="")
    p_add.set_defaults(func=_cmd_add)

    p_del = sub.add_parser("delete", help="delete an entry")
    p_del.add_argument("--pattern", required=True)
    p_del.set_defaults(func=_cmd_delete)

    p_exp = sub.add_parser("export", help="export all entries")
    p_exp.add_argument("--json", action="store_true")
    p_exp.set_defaults(func=_cmd_export)

    p_imp = sub.add_parser("import", help="import entries from JSON file")
    p_imp.add_argument("--file", required=True)
    p_imp.add_argument("--dry-run", action="store_true")
    p_imp.set_defaults(func=_cmd_import)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
