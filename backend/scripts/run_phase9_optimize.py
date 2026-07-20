#!/usr/bin/env python3
"""CLI helper: load historical matches from kernel DB and run ParameterOptimizer.

Usage (from backend/):
  python scripts/run_phase9_optimize.py --sport nba --n-trials 20
  python scripts/run_phase9_optimize.py --sport all --n-trials 10

Requires:
  - PHASE9_ACCURACY_SPRINT_ENABLED path is operational code-wise
  - kernel DB already contains fixtures+results (run ingest first)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure backend package root is on path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 9 offline optimization")
    parser.add_argument(
        "--sport",
        default="nba",
        choices=["nba", "mlb", "nhl", "all"],
        help="Sport to optimize (default: nba)",
    )
    parser.add_argument(
        "--n-trials",
        type=int,
        default=20,
        help="Optuna trials per sport (default: 20)",
    )
    parser.add_argument(
        "--test-ratio",
        type=float,
        default=0.2,
        help="Chronological test split ratio (default: 0.2)",
    )
    args = parser.parse_args()

    from app.kernel.backtest.match_loader import (
        load_sport_matches_for_backtest,
        time_series_split,
    )
    from app.kernel.parameter_optimizer import ParameterOptimizer

    sports = ["nba", "mlb", "nhl"] if args.sport == "all" else [args.sport]
    optimizer = ParameterOptimizer()
    summary: dict = {}

    for sport in sports:
        matches = load_sport_matches_for_backtest(sport)
        print(f"[{sport}] loaded {len(matches)} matches")
        if len(matches) < 5:
            summary[sport] = {
                "error": f"not enough matches ({len(matches)}); ingest first",
            }
            continue
        train, test = time_series_split(matches, test_ratio=args.test_ratio)
        print(
            f"[{sport}] train={len(train)} test={len(test)} "
            f"trials={args.n_trials}"
        )
        result = optimizer.optimize_sync(
            sport,
            n_trials=max(1, min(args.n_trials, 500)),
            train_matches=train,
            test_matches=test,
        )
        summary[sport] = result
        print(f"[{sport}] done: {json.dumps(result, default=str)[:400]}")

    print("--- summary ---")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
