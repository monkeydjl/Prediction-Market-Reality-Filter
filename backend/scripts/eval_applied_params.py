#!/usr/bin/env python3
"""Evaluate applied Optuna params vs settings Elo defaults on holdout split."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.optimized_params_store import OptimizedParamsStore
    from app.kernel.backtest.match_loader import (
        load_sport_matches_for_backtest,
        time_series_split,
    )
    from app.kernel.backtest.runner import BacktestRunner, BacktestParams
    from app.kernel.elo_params_resolve import settings_elo_params

    init_kernel_db()
    store = OptimizedParamsStore()
    runner = BacktestRunner()
    summary: dict = {}

    for sport in ("nba", "mlb", "nhl"):
        row = store.get_applied(sport, sport)
        if not row:
            summary[sport] = {"error": "no applied row"}
            print(f"[{sport}] no applied row")
            continue
        fw = (
            json.loads(row["factor_weights"])
            if isinstance(row["factor_weights"], str)
            else row["factor_weights"]
        )
        applied_elo = (
            json.loads(row["elo_params"])
            if isinstance(row["elo_params"], str)
            else row["elo_params"]
        )
        settings_elo = settings_elo_params(sport)
        matches = load_sport_matches_for_backtest(sport)
        train, test = time_series_split(matches, test_ratio=0.2)
        r_applied = runner.run(
            sport,
            train_matches=train,
            test_matches=test,
            params=BacktestParams(factor_weights=fw, elo_params=applied_elo),
        )
        r_settings = runner.run(
            sport,
            train_matches=train,
            test_matches=test,
            params=BacktestParams(factor_weights=fw, elo_params=settings_elo),
        )
        entry = {
            "id": row["id"],
            "train": len(train),
            "test": len(test),
            "applied": {
                "accuracy": r_applied.accuracy,
                "brier": r_applied.brier_score,
                "mae": r_applied.mae,
                "score": r_applied.score,
                "n": r_applied.sample_count,
            },
            "settings_elo": {
                "accuracy": r_settings.accuracy,
                "brier": r_settings.brier_score,
                "mae": r_settings.mae,
                "score": r_settings.score,
                "n": r_settings.sample_count,
            },
            "delta_acc": r_applied.accuracy - r_settings.accuracy,
            "delta_score": r_applied.score - r_settings.score,
            "factor_weights": fw,
            "applied_elo": applied_elo,
            "settings_elo_params": settings_elo,
        }
        summary[sport] = entry
        print(
            f"[{sport}] id={row['id']} train={len(train)} test={len(test)} "
            f"applied_acc={r_applied.accuracy:.4f} settings_acc={r_settings.accuracy:.4f} "
            f"d_acc={entry['delta_acc']:+.4f} d_score={entry['delta_score']:+.4f}"
        )

    print("--- summary ---")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
