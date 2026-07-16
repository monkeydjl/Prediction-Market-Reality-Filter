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
        """
        config = _SPORT_CONFIG.get(sport)
        if config is None:
            raise ValueError(f"Unsupported sport: {sport}")

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
            return result.score

        study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

        best_trial = study.best_trial
        return {
            "best_score": best_trial.value,
            "best_params": best_trial.params,
            "trials": len(study.trials),
            "sport": sport,
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
