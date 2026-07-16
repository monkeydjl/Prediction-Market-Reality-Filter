"""Sport settlement CLI.

Usage:
    python -m scripts.sport_settlement_cli process --match-id ID
    python -m scripts.sport_settlement_cli scan [--limit N]
    python -m scripts.sport_settlement_cli calibrations [--engine E] [--competition C]
    python -m scripts.sport_settlement_cli history [--limit N] [--engine E]
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


def _cmd_process(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.market_settlement_service import MarketSettlementService
    init_kernel_db()
    svc = MarketSettlementService()
    result = svc.process_settlement(args.match_id)
    if result.status == "already_processed":
        _print(f"[INFO] match={args.match_id} already processed")
        return 0
    if result.status.startswith("skipped"):
        _print(f"[SKIP] match={args.match_id} status={result.status} reason={result.skip_reason}")
        return 0
    _print(f"[OK] match={args.match_id} status={result.status} settlements={result.settlements_count}")
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.market_settlement_service import MarketSettlementService
    init_kernel_db()
    svc = MarketSettlementService()
    result = svc.scan_and_process(limit=args.limit)
    _print(
        f"[OK] scanned={result.scanned} processed={result.processed} "
        f"skipped={result.skipped} already={result.already_processed} errors={result.errors}"
    )
    for detail in result.error_details:
        _print(f"  ERROR: {detail}")
    return 0


def _cmd_calibrations(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.market_settlement_service import MarketSettlementService
    init_kernel_db()
    svc = MarketSettlementService()
    cals = svc.get_calibrations(engine=args.engine, competition=args.competition)
    if not cals:
        _print("[INFO] no market calibrations found")
        return 0
    _print(f"[OK] {len(cals)} calibrations:")
    for cal in cals:
        _print(
            f"  engine={cal['engine']:<20} competition={cal['competition']:<10} "
            f"slope={cal['slope']:.3f} intercept={cal['intercept']:+.3f} "
            f"samples={cal['sample_count']} avg_brier={cal['avg_brier']:.4f} "
            f"dir_acc={cal['direction_accuracy']:.2%}"
        )
    return 0


def _cmd_history(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.market_settlement_service import MarketSettlementService
    init_kernel_db()
    svc = MarketSettlementService()
    items = svc.get_history(limit=args.limit, engine=args.engine)
    if not items:
        _print("[INFO] no settlements found")
        return 0
    _print(f"[OK] {len(items)} settlements (limit={args.limit}):")
    for s in items:
        settlement_str = f"{s['settlement_implied_prob']:.3f}" if s['settlement_implied_prob'] is not None else "N/A"
        brier_str = f"{s['brier_score']:.4f}" if s['brier_score'] is not None else "N/A"
        _print(
            f"  match={s['match_id']:<20} outcome={s['mapped_outcome']:<10} "
            f"engine={s['engine']:<20} model={s['model_prob']:.3f} "
            f"settlement={settlement_str} brier={brier_str} "
            f"status={s['status']}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sport settlement CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_process = sub.add_parser("process", help="process settlement for a single match")
    p_process.add_argument("--match-id", required=True)
    p_process.set_defaults(func=_cmd_process)

    p_scan = sub.add_parser("scan", help="scan and process finished matches")
    p_scan.add_argument("--limit", type=int, default=50)
    p_scan.set_defaults(func=_cmd_scan)

    p_cal = sub.add_parser("calibrations", help="show market calibrations")
    p_cal.add_argument("--engine", default=None)
    p_cal.add_argument("--competition", default=None)
    p_cal.set_defaults(func=_cmd_calibrations)

    p_hist = sub.add_parser("history", help="show settlement history")
    p_hist.add_argument("--limit", type=int, default=20)
    p_hist.add_argument("--engine", default=None)
    p_hist.set_defaults(func=_cmd_history)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
