# backend/tests/test_parameter_optimizer.py
"""Tests for ParameterOptimizer — TDD RED phase."""
import pytest
from unittest.mock import patch, MagicMock

from app.kernel.parameter_optimizer import ParameterOptimizer


def test_optimize_converges_with_mock_backtest():
    """Verify optimizer runs and returns a result better than random."""
    optimizer = ParameterOptimizer()

    # Mock BacktestRunner to return deterministic results based on params
    def mock_run(sport, *, train_matches, test_matches, params):
        from app.kernel.backtest.runner import BacktestResult
        # Higher elo weight → higher score (simulated)
        elo_w = params.factor_weights.get("elo", 0.25)
        score = 0.5 + elo_w * 0.3  # range [0.5, 0.8]
        return BacktestResult(
            accuracy=score, brier_score=0.25, mae=0.35,
            sample_count=10, score=score, predictions=[],
        )

    # Mock OptimizedParamsStore so the test doesn't write to the real kernel DB.
    with patch("app.kernel.parameter_optimizer.BacktestRunner.run", side_effect=mock_run), \
         patch("app.kernel.optimized_params_store.OptimizedParamsStore") as MockStore:
        mock_store_instance = MockStore.return_value
        mock_store_instance.save_candidate.return_value = {"id": 42, "status": "candidate"}
        result = optimizer.optimize_sync("nba", n_trials=10, train_matches=[], test_matches=[])

    assert "best_score" in result
    assert "best_params" in result
    assert result["trials"] == 10
    assert result["best_score"] > 0.5  # Should find higher elo weight
    # Fix 7: best candidate should be persisted via save_candidate
    assert mock_store_instance.save_candidate.called
    saved_kwargs = mock_store_instance.save_candidate.call_args.kwargs
    assert saved_kwargs["sport"] == "nba"
    assert saved_kwargs["competition"] == "nba"
    assert "elo" in saved_kwargs["factor_weights"]
    assert saved_kwargs["trial_number"] is not None
    assert result["saved_candidate"] == {"id": 42, "status": "candidate"}
    # P3-FE8: metrics for UI visualization
    assert result["accuracy"] is not None
    assert result["brier_score"] == pytest.approx(0.25)
    assert result["mae"] == pytest.approx(0.35)
    assert result["sample_count"] == 10
    assert result["train_count"] == 0
    assert result["test_count"] == 0
    assert isinstance(result["factor_weights"], dict)
    assert "elo" in result["factor_weights"]
    assert "score_formula" in result


def test_search_space_weights_sum_to_one():
    """Verify sampled factor weights always sum to 1.0."""
    optimizer = ParameterOptimizer()
    trial = MagicMock()
    trial.suggest_float = MagicMock(side_effect=lambda name, low, high: 0.3)

    weights = optimizer._sample_factor_weights(trial, "nba")
    assert sum(weights.values()) == pytest.approx(1.0, abs=0.01)
    assert set(weights.keys()) == {"elo", "home_court", "rest", "form"}


def test_search_space_mlb_has_5_factors():
    optimizer = ParameterOptimizer()
    trial = MagicMock()
    trial.suggest_float = MagicMock(side_effect=lambda name, low, high: 0.2)

    weights = optimizer._sample_factor_weights(trial, "mlb")
    assert len(weights) == 5
    assert "starting_pitcher" in weights


def test_sample_elo_params_within_bounds():
    optimizer = ParameterOptimizer()
    trial = MagicMock()
    trial.suggest_float = MagicMock(side_effect=lambda name, low, high: (low + high) / 2)

    params = optimizer._sample_elo_params(trial, "nba")
    assert "hfa" in params
    assert 50 <= params["hfa"] <= 150
    assert "k_regular" in params
    assert 10 <= params["k_regular"] <= 40


def test_multi_objective_score_calculation():
    from app.kernel.backtest.runner import BacktestResult
    optimizer = ParameterOptimizer()
    result = BacktestResult(
        accuracy=0.70, brier_score=0.20, mae=0.30,
        sample_count=100, score=0.0, predictions=[],
    )
    # score = 0.5*0.70 + 0.3*(1-0.20) + 0.2*(1-0.30) = 0.35 + 0.24 + 0.14 = 0.73
    score = optimizer._compute_score(result)
    assert score == pytest.approx(0.73, abs=0.01)
