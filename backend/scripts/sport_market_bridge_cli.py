"""Sport market bridge manual verification CLI.

Usage:
    python -m scripts.sport_market_bridge_cli list
    python -m scripts.sport_market_bridge_cli list --match-id ID
    python -m scripts.sport_market_bridge_cli verify --match-id ID --contract-id ID
    python -m scripts.sport_market_bridge_cli reject --match-id ID --contract-id ID
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))


def _print(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def _cmd_list(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    init_kernel_db()
    store = SportMarketLinkStore()
    if args.match_id:
        items = store.get_links(match_id=args.match_id)
    else:
        items = store.get_pending_links()
    if not items:
        _print("[INFO] no items found")
        return 0
    _print(f"[OK] {len(items)} items:")
    for it in items:
        status = "verified" if it["verified"] else "PENDING"
        _print(
            f"  id={it['id']:<6} match={it['match_id']:<24} "
            f"contract={it['contract_id']:<16} src={it['source']:<12} "
            f"conf={it['link_confidence']:.2f} {status}"
        )
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    init_kernel_db()
    store = SportMarketLinkStore()
    links = store.get_links(match_id=args.match_id)
    target = next((l for l in links if l["contract_id"] == args.contract_id), None)
    if target is None:
        _print(f"[FAIL] no link found for match={args.match_id} contract={args.contract_id}")
        return 1
    ok = store.set_verified(link_id=target["id"], verified=True)
    if not ok:
        _print(f"[FAIL] could not verify link id={target['id']}")
        return 1
    _print(f"[OK] verified link id={target['id']} match={args.match_id} contract={args.contract_id}")
    return 0


def _cmd_reject(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    init_kernel_db()
    store = SportMarketLinkStore()
    links = store.get_links(match_id=args.match_id)
    target = next((l for l in links if l["contract_id"] == args.contract_id), None)
    if target is None:
        _print(f"[FAIL] no link found for match={args.match_id} contract={args.contract_id}")
        return 1
    ok = store.set_verified(link_id=target["id"], verified=False)
    if not ok:
        _print(f"[FAIL] could not reject link id={target['id']}")
        return 1
    _print(f"[OK] rejected link id={target['id']} match={args.match_id} contract={args.contract_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sport market bridge admin CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list pending (or per-match) links")
    p_list.add_argument("--match-id", default=None)
    p_list.set_defaults(func=_cmd_list)

    p_verify = sub.add_parser("verify", help="verify a pending link")
    p_verify.add_argument("--match-id", required=True)
    p_verify.add_argument("--contract-id", required=True)
    p_verify.set_defaults(func=_cmd_verify)

    p_reject = sub.add_parser("reject", help="reject a link")
    p_reject.add_argument("--match-id", required=True)
    p_reject.add_argument("--contract-id", required=True)
    p_reject.set_defaults(func=_cmd_reject)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
