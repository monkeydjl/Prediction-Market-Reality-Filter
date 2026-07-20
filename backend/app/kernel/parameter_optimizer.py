# backend/app/kernel/parameter_optimizer.py
"""ParameterOptimizer — Bayesian optimization over factor weights + Elo params.

Uses Optuna TPE sampler to search for optimal parameters. Each trial runs
a full backtest via BacktestRunner. The optimizer runs synchronously in tests
and asynchronously via optimization_task_manager in production.
"""
from __future__ import annotations

import logging
from typing import Any

import optuna

from app.kernel.backtest.runner import BacktestRunner, BacktestParams, BacktestResult

logger = logging.getLogger(__name__)

# Search space per sport: factor names + Elo param bounds
_SPORT_CONFIG = {
    "nba": {
        "factors": ["elo", "home_court", "rest", "form"],
        "elo_params": {
            "hfa": (50, 150),
            "k_regular": (10, 40),
            "k_playoff": (20, 50),
        },
        "default_elo": {"season_carry": 0.75, "initial": 1500},
    },
    "mlb": {
        "factors": ["elo", "home_court", "rest", "form", "starting_pitcher"],
        "elo_params": {
            "hfa": (20, 80),
            "k_regular": (10, 40),
            "k_playoff": (20, 50),
            "season_carry": (0.5, 0.9),
        },
        "default_elo": {"initial": 1500},
    },
    "nhl": {
        "factors": ["elo", "home_court", "rest", "form", "goalie"],
        "elo_params": {
            "hfa": (25, 85),
            "k_regular": (10, 40),
            "k_playoff": (20, 50),
            "season_carry": (0.5, 0.9),
        },
        "default_elo": {"initial": 1500},
    },
}


class ParameterOptimizer:
    """Bayesian optimization over factor weights + Elo params using Optuna TPE."""

    def __init__(self) -> None:
        self._runner = BacktestRunner()

    def optimize_sync(
        self,
        sport: str,
        *,
        n_trials: int = 150,
        train_matches: list[dict],
        test_matches: list[dict],
    ) -> dict[str, Any]:
        """Run optimization synchronously. Returns best params + score.

        For production async usage, wrap this in optimization_task_manager.
        Persists the best candidate via OptimizedParamsStore.save_candidate()
        so the monthly re-optimization job (spec §8.2) produces a durable row.
        """
        config = _SPORT_CONFIG.get(sport)
        if config is None:
            raise ValueError(f"Unsupported sport: {sport}")

        # Map trial.number -> structured params + BacktestResult so we can
        # reconstruct the structured dicts for the best trial after search.
        trial_records: dict[int, dict[str, Any]] = {}

        def objective(trial: optuna.Trial) -> float:
            factor_weights = self._sample_factor_weights(trial, sport)
            elo_params = self._sample_elo_params(trial, sport)
            params = BacktestParams(factor_weights=factor_weights, elo_params=elo_params)
            result = self._runner.run(
                sport,
                train_matches=train_matches,
                test_matches=test_matches,
                params=params,
            )
            trial_records[trial.number] = {
                "factor_weights": factor_weights,
                "elo_params": elo_params,
                "result": result,
            }
            return result.score

        study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

        best_trial = study.best_trial
        best_record = trial_records.get(best_trial.number, {})
        best_result = best_record.get("result")
        factor_weights = best_record.get("factor_weights", {})
        elo_params = best_record.get("elo_params", {})

        # Persist best candidate (Fix 7 / spec §8.2). Best-effort: a persistence
        # failure must not invalidate the optimization result itself.
        saved: dict | None = None
        if best_result is not None and factor_weights:
            try:
                from app.kernel.optimized_params_store import OptimizedParamsStore
                store = OptimizedParamsStore()
                saved = store.save_candidate(
                    sport=sport,
                    competition=sport,
                    factor_weights=factor_weights,
                    elo_params=elo_params,
                    score=best_result.score,
                    accuracy=best_result.accuracy,
                    brier_score=best_result.brier_score,
                    mae=best_result.mae,
                    sample_count=best_result.sample_count,
                    trial_number=best_trial.number,
                )
                logger.info(
                    "[Optimizer] Saved best candidate for %s (id=%s, score=%.4f)",
                    sport,
                    saved.get("id"),
                    best_result.score,
                )
            except Exception:
                logger.exception(
                    "[Optimizer] Failed to persist best candidate for %s",
                    sport,
                )

        return {
            "best_score": best_trial.value,
            "best_params": best_trial.params,
            "trials": len(study.trials),
            "sport": sport,
            "saved_candidate": saved,
            # Backtest metrics for UI (P3-FE8) — same fields as saved candidate
            "accuracy": best_result.accuracy if best_result is not None else None,
            "brier_score": best_result.brier_score if best_result is not None else None,
            "mae": best_result.mae if best_result is not None else None,
            "sample_count": best_result.sample_count if best_result is not None else None,
            "train_count": len(train_matches),
            "test_count": len(test_matches),
            "factor_weights": factor_weights,
            "elo_params": elo_params,
            "score_formula": "0.5*accuracy + 0.3*(1-brier) + 0.2*(1-mae)",
        }

    def _sample_factor_weights(self, trial: optuna.Trial, sport: str) -> dict[str, float]:
        """Sample factor weights with sum=1.0 constraint.

        Uses the last-factor-computed approach: sample N-1 factors freely,
        compute the Nth as 1 - sum(others), clamped to [0.05, 0.95].
        """
        config = _SPORT_CONFIG[sport]
        factors = config["factors"]

        # Sample N-1 raw weights
        raw = {}
        for f in factors[:-1]:
            raw[f] = trial.suggest_float(f"w_{f}", 0.05, 0.45)

        # Last factor = 1 - sum(others), clamped
        remaining = 1.0 - sum(raw.values())
        last_factor = factors[-1]
        raw[last_factor] = max(0.05, min(0.95, remaining))

        # Normalize to ensure sum=1.0
        total = sum(raw.values())
        return {f: raw[f] / total for f in factors}

    def _sample_elo_params(self, trial: optuna.Trial, sport: str) -> dict[str, float]:
        """Sample Elo params within sport-specific bounds."""
        config = _SPORT_CONFIG[sport]
        params: dict[str, float] = {}

        for param_name, (low, high) in config["elo_params"].items():
            params[param_name] = trial.suggest_float(f"elo_{param_name}", low, high)

        # Add defaults for params not in search space
        for k, v in config["default_elo"].items():
            if k not in params:
                params[k] = v

        return params

    def _compute_score(self, result: BacktestResult) -> float:
        """Compute multi-objective weighted score."""
        return 0.5 * result.accuracy + 0.3 * (1 - result.brier_score) + 0.2 * (1 - result.mae)
