"""Tests for backtest match_loader."""
from app.kernel.backtest.match_loader import time_series_split


def test_time_series_split_keeps_order():
    matches = [{"match_id": f"m{i}", "season": 2024} for i in range(10)]
    train, test = time_series_split(matches, test_ratio=0.2)
    assert len(train) == 8
    assert len(test) == 2
    assert train[-1]["match_id"] == "m7"
    assert test[0]["match_id"] == "m8"


def test_time_series_split_empty():
    assert time_series_split([]) == ([], [])


def test_time_series_split_small():
    matches = [{"match_id": "only"}]
    train, test = time_series_split(matches)
    assert train == []
    assert len(test) == 1
