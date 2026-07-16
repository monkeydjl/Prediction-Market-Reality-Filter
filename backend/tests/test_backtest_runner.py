# backend/tests/test_backtest_runner.py
"""Tests for BacktestRunner — TDD RED phase."""
import pytest

from app.kernel.backtest.elo_time_machine import EloParams
from app.kernel.backtest.runner import BacktestParams, BacktestRunner


def _make_match(match_id, home, away, home_score, away_score, season, rest_home=2, rest_away=2, form_home=0.5, form_away=0.5):
    return {
        "match_id": match_id,
        "home_team": home,
        "away_team": away,
        "home_score": home_score,
        "away_score": away_score,
        "season": season,
        "is_playoff": False,
        "rest_days_home": rest_home,
        "rest_days_away": rest_away,
        "form_home": form_home,
        "form_away": form_away,
    }


def test_baseline_backtest_produces_result():
    matches = [
        _make_match("m1", "Lakers", "Celtics", 110, 105, 2024),
        _make_match("m2", "Celtics", "Lakers", 108, 112, 2024),
    ]
    params = BacktestParams(
        factor_weights={"elo": 0.45, "home_court": 0.15, "rest": 0.15, "form": 0.25},
        elo_params={"hfa": 100, "k_regular": 20, "k_playoff": 30, "season_carry": 0.75, "initial": 1500},
    )
    runner = BacktestRunner()
    result = runner.run("nba", train_matches=matches[:1], test_matches=matches[1:], params=params)
    assert 0.0 <= result.accuracy <= 1.0
    assert 0.0 <= result.brier_score <= 1.0
    assert 0.0 <= result.mae <= 1.0
    assert result.sample_count == 1
    assert result.score == pytest.approx(0.5 * result.accuracy + 0.3 * (1 - result.brier_score) + 0.2 * (1 - result.mae))


def test_parameter_change_affects_predictions():
    matches = [
        _make_match("m1", "A", "B", 100, 99, 2024),
        _make_match("m2", "A", "B", 100, 99, 2024),
    ]
    params_high_elo = BacktestParams(
        factor_weights={"elo": 0.90, "home_court": 0.04, "rest": 0.03, "form": 0.03},
        elo_params={"hfa": 100, "k_regular": 20, "k_playoff": 30, "season_carry": 0.75, "initial": 1500},
    )
    params_low_elo = BacktestParams(
        factor_weights={"elo": 0.10, "home_court": 0.30, "rest": 0.30, "form": 0.30},
        elo_params={"hfa": 100, "k_regular": 20, "k_playoff": 30, "season_carry": 0.75, "initial": 1500},
    )
    runner = BacktestRunner()
    result_high = runner.run("nba", train_matches=matches[:1], test_matches=matches[1:], params=params_high_elo)
    result_low = runner.run("nba", train_matches=matches[:1], test_matches=matches[1:], params=params_low_elo)
    # With high elo weight, predictions should differ from low elo weight
    # (Different probabilities → different scores)
    # They might not differ in accuracy (both 1/1 or 0/1) but brier/mae should differ
    assert result_high.brier_score != result_low.brier_score or result_high.mae != result_low.mae


def test_time_series_split_no_data_leakage():
    train = [_make_match(f"t{i}", "A", "B", 100+i, 99+i, 2023) for i in range(10)]
    test = [_make_match(f"e{i}", "A", "B", 100+i, 99+i, 2024) for i in range(5)]
    params = BacktestParams(
        factor_weights={"elo": 0.45, "home_court": 0.15, "rest": 0.15, "form": 0.25},
        elo_params={"hfa": 100, "k_regular": 20, "k_playoff": 30, "season_carry": 0.75, "initial": 1500},
    )
    runner = BacktestRunner()
    result = runner.run("nba", train_matches=train, test_matches=test, params=params)
    assert result.sample_count == 5


def test_empty_test_matches_returns_zero_sample():
    train = [_make_match("t1", "A", "B", 100, 99, 2024)]
    params = BacktestParams(
        factor_weights={"elo": 0.45, "home_court": 0.15, "rest": 0.15, "form": 0.25},
        elo_params={"hfa": 100, "k_regular": 20, "k_playoff": 30, "season_carry": 0.75, "initial": 1500},
    )
    runner = BacktestRunner()
    result = runner.run("nba", train_matches=train, test_matches=[], params=params)
    assert result.sample_count == 0
    assert result.accuracy == 0.0


def test_single_match_backtest():
    train = [_make_match("t1", "A", "B", 100, 99, 2024)]
    test = [_make_match("e1", "A", "B", 100, 99, 2024)]
    params = BacktestParams(
        factor_weights={"elo": 0.45, "home_court": 0.15, "rest": 0.15, "form": 0.25},
        elo_params={"hfa": 100, "k_regular": 20, "k_playoff": 30, "season_carry": 0.75, "initial": 1500},
    )
    runner = BacktestRunner()
    result = runner.run("nba", train_matches=train, test_matches=test, params=params)
    assert result.sample_count == 1


def test_multi_season_backtest():
    train = [_make_match(f"t{i}", "A", "B", 100, 99, 2023) for i in range(5)]
    test = [_make_match(f"e{i}", "A", "B", 100, 99, 2024) for i in range(5)]
    params = BacktestParams(
        factor_weights={"elo": 0.45, "home_court": 0.15, "rest": 0.15, "form": 0.25},
        elo_params={"hfa": 100, "k_regular": 20, "k_playoff": 30, "season_carry": 0.75, "initial": 1500},
    )
    runner = BacktestRunner()
    result = runner.run("nba", train_matches=train, test_matches=test, params=params)
    assert result.sample_count == 5


def test_mlb_with_starting_pitcher():
    train = [{"match_id": "t1", "home_team": "A", "away_team": "B", "home_score": 5, "away_score": 3, "season": 2024, "is_playoff": False, "rest_days_home": 2, "rest_days_away": 2, "form_home": 0.5, "form_away": 0.5, "pitcher_era_home": 3.5, "pitcher_era_away": 4.0}]
    test = [{"match_id": "e1", "home_team": "A", "away_team": "B", "home_score": 4, "away_score": 2, "season": 2024, "is_playoff": False, "rest_days_home": 2, "rest_days_away": 2, "form_home": 0.5, "form_away": 0.5, "pitcher_era_home": 3.0, "pitcher_era_away": 4.5}]
    params = BacktestParams(
        factor_weights={"elo": 0.30, "home_court": 0.10, "rest": 0.15, "form": 0.20, "starting_pitcher": 0.25},
        elo_params={"hfa": 50, "k_regular": 20, "k_playoff": 30, "season_carry": 0.75, "initial": 1500},
    )
    runner = BacktestRunner()
    result = runner.run("mlb", train_matches=train, test_matches=test, params=params)
    assert result.sample_count == 1
    assert 0.0 <= result.accuracy <= 1.0
