"""Admin CLI for the Review Queue (Plan 4 §6.2).

Usage:
    python -m scripts.review_queue_cli list [--trigger T] [--status {pending,resolved}]
    python -m scripts.review_queue_cli action --item-id ID --reviewer NAME
           --action {confirm,override,request_more_evidence,mark_bad_source,
                     mark_bad_resolution} [--note TEXT]
    python -m scripts.review_queue_cli audit [--item-id ID]

``list`` shows pending items by default. ``action`` resolves an item and
appends to the audit log. ``audit`` shows the audit log (global or per-item).
Uses ASCII labels for Windows GBK safety.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.memory import review_queue_store as rq


def _print(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def _cmd_list(args: argparse.Namespace) -> int:
    if args.status == "resolved":
        items = rq.list_resolved(limit=200)
    else:
        items = rq.list_pending(trigger=args.trigger)
    if not items:
        _print("[INFO] no items found")
        return 0
    _print(f"[OK] {len(items)} items:")
    for it in items:
        _print(
            f"  {it['item_id'][:8]}  evt={it['event_id']:<12} "
            f"trigger={it['trigger']:<28} sev={it['severity']:<5} "
            f"reason={it['reason']}"
        )
    return 0


def _cmd_action(args: argparse.Namespace) -> int:
    try:
        rq.take_action(
            item_id=args.item_id,
            reviewer=args.reviewer,
            action=args.action,
            note=args.note or "",
        )
    except ValueError as exc:
        _print(f"[FAIL] {exc}")
        return 1
    except KeyError as exc:
        _print(f"[FAIL] {exc}")
        return 1
    _print(f"[OK] action={args.action} on item={args.item_id[:8]}")
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    log = rq.get_audit_log(item_id=args.item_id)
    if not log:
        _print("[INFO] no audit entries found")
        return 0
    _print(f"[OK] {len(log)} audit entries:")
    for entry in log:
        _print(
            f"  #{entry['audit_id']}  item={entry['item_id'][:8]}  "
            f"reviewer={entry['reviewer']:<10} action={entry['action']:<24} "
            f"note={entry['note']}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Review Queue admin CLI"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list items")
    p_list.add_argument("--trigger", default=None)
    p_list.add_argument("--status", default="pending",
                        choices=["pending", "resolved"])
    p_list.set_defaults(func=_cmd_list)

    p_act = sub.add_parser("action", help="take reviewer action")
    p_act.add_argument("--item-id", required=True)
    p_act.add_argument("--reviewer", required=True)
    p_act.add_argument("--action", required=True,
                       choices=["confirm", "override",
                                "request_more_evidence",
                                "mark_bad_source", "mark_bad_resolution"])
    p_act.add_argument("--note", default="")
    p_act.set_defaults(func=_cmd_action)

    p_aud = sub.add_parser("audit", help="show audit log")
    p_aud.add_argument("--item-id", default=None)
    p_aud.set_defaults(func=_cmd_audit)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
