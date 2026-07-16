"""Sport recommendation CLI.

Usage:
    python -m scripts.sport_recommendation_cli match --match-id ID
    python -m scripts.sport_recommendation_cli open [--limit N] [--decision act|provisional_act|watch]
    python -m scripts.sport_recommendation_cli picks [--limit N] [--min-abs-edge F]
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


def _cmd_match(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.sport_recommendation_service import SportRecommendationService
    init_kernel_db()
    svc = SportRecommendationService()
    rec = svc.get_recommendation(args.match_id)
    if rec is None:
        _print(f"[INFO] no edges found for match={args.match_id}")
        return 0
    _print(f"[OK] match={args.match_id}")
    _print(f"  outcome={rec.mapped_outcome} direction={rec.direction} decision={rec.decision}")
    _print(f"  edge={rec.edge_pct:+.2f}pp raw_edge={rec.raw_edge_pct:+.2f}pp")
    _print(f"  confidence={rec.confidence} risk={rec.risk_level} trust={rec.trust:.2f}")
    _print(f"  allocation={rec.suggested_allocation_pct}% calibration={rec.calibration_status}")
    _print(f"  engine={rec.engine_name} competition={rec.competition}")
    _print(f"  rationale: {rec.rationale}")
    return 0


def _cmd_open(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.sport_recommendation_service import SportRecommendationService
    init_kernel_db()
    svc = SportRecommendationService()
    recs = svc.get_open_decisions(limit=args.limit, decision=args.decision)
    if not recs:
        _print("[INFO] no open decisions found")
        return 0
    _print(f"[OK] {len(recs)} open decisions (limit={args.limit}, decision={args.decision}):")
    for rec in recs:
        _print(
            f"  match={rec.match_id:<24} outcome={rec.mapped_outcome:<10} "
            f"dir={rec.direction:<6} decision={rec.decision:<14} "
            f"edge={rec.edge_pct:+.2f}pp"
        )
    return 0


def _cmd_picks(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.sport_recommendation_service import SportRecommendationService
    init_kernel_db()
    svc = SportRecommendationService()
    recs = svc.get_top_picks(limit=args.limit, min_abs_edge_pct=args.min_abs_edge * 100)
    if not recs:
        _print("[INFO] no picks found")
        return 0
    _print(f"[OK] {len(recs)} picks (limit={args.limit}, min_abs_edge={args.min_abs_edge}):")
    for rec in recs:
        _print(
            f"  match={rec.match_id:<24} outcome={rec.mapped_outcome:<10} "
            f"dir={rec.direction:<6} decision={rec.decision:<14} "
            f"edge={rec.edge_pct:+.2f}pp"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sport recommendation CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_match = sub.add_parser("match", help="show recommendation for a single match")
    p_match.add_argument("--match-id", required=True)
    p_match.set_defaults(func=_cmd_match)

    p_open = sub.add_parser("open", help="list open decisions (act/provisional_act/watch)")
    p_open.add_argument("--limit", type=int, default=20)
    p_open.add_argument("--decision", default=None, choices=["act", "provisional_act", "watch"])
    p_open.set_defaults(func=_cmd_open)

    p_picks = sub.add_parser("picks", help="list top edge picks (all decisions)")
    p_picks.add_argument("--limit", type=int, default=20)
    p_picks.add_argument("--min-abs-edge", type=float, default=0.0, dest="min_abs_edge")
    p_picks.set_defaults(func=_cmd_picks)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
