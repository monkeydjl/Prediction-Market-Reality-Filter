"""Sport edge detector CLI.

Usage:
    python -m scripts.sport_edge_cli detect --match-id ID
    python -m scripts.sport_edge_cli latest --match-id ID
    python -m scripts.sport_edge_cli discrepancies [--limit N] [--min-abs-edge F]
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


def _cmd_detect(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.edge_detector_service import EdgeDetectorService
    init_kernel_db()
    svc = EdgeDetectorService()
    summary = svc.detect_edges(args.match_id)
    if summary.skipped:
        _print(f"[SKIP] match={args.match_id} reason={summary.skip_reason}")
        return 0
    _print(f"[OK] match={args.match_id} engine={summary.engine_name} outcomes={len(summary.outcomes)}")
    for edge in summary.outcomes:
        _print(
            f"  outcome={edge.mapped_outcome:<10} "
            f"model={edge.model_prob:.3f} market={edge.market_prob:.3f} "
            f"raw={edge.raw_edge:+.3f} adj={edge.adjusted_edge:+.3f} "
            f"trust={edge.trust:.2f} liq={edge.liquidity_factor:.2f} "
            f"stale={edge.stale}"
        )
    return 0


def _cmd_latest(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.edge_detector_service import EdgeDetectorService
    init_kernel_db()
    svc = EdgeDetectorService()
    edges = svc.get_latest_edges(args.match_id)
    if not edges:
        _print(f"[INFO] no edges found for match={args.match_id}")
        return 0
    _print(f"[OK] {len(edges)} edges for match={args.match_id}:")
    for edge in edges:
        _print(
            f"  outcome={edge.mapped_outcome:<10} "
            f"model={edge.model_prob:.3f} market={edge.market_prob:.3f} "
            f"raw={edge.raw_edge:+.3f} adj={edge.adjusted_edge:+.3f} "
            f"stale={edge.stale} captured={edge.captured_at}"
        )
    return 0


def _cmd_discrepancies(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.edge_detector_service import EdgeDetectorService
    init_kernel_db()
    svc = EdgeDetectorService()
    edges = svc.get_top_discrepancies(limit=args.limit, min_abs_edge=args.min_abs_edge)
    if not edges:
        _print("[INFO] no discrepancies found")
        return 0
    _print(f"[OK] {len(edges)} discrepancies (limit={args.limit}, min_abs_edge={args.min_abs_edge}):")
    for edge in edges:
        _print(
            f"  match={edge.match_id:<24} outcome={edge.mapped_outcome:<10} "
            f"adj={edge.adjusted_edge:+.3f} raw={edge.raw_edge:+.3f} "
            f"stale={edge.stale}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sport edge detector CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_detect = sub.add_parser("detect", help="compute and persist edges for a match")
    p_detect.add_argument("--match-id", required=True)
    p_detect.set_defaults(func=_cmd_detect)

    p_latest = sub.add_parser("latest", help="show latest edge per outcome for a match")
    p_latest.add_argument("--match-id", required=True)
    p_latest.set_defaults(func=_cmd_latest)

    p_disc = sub.add_parser("discrepancies", help="show top edge discrepancies")
    p_disc.add_argument("--limit", type=int, default=20)
    p_disc.add_argument("--min-abs-edge", type=float, default=0.0, dest="min_abs_edge")
    p_disc.set_defaults(func=_cmd_discrepancies)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
