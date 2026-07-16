# backend/tests/test_optimized_params_store.py
"""Tests for OptimizedParamsStore — TDD RED phase."""
import json
import pytest
from datetime import datetime, timezone

from app.kernel.optimized_params_store import OptimizedParamsStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Create a store with an isolated SQLite DB."""
    db_path = str(tmp_path / "test_optimized.db")
    monkeypatch.setenv("KERNEL_DB_PATH", db_path)
    from app.kernel import kernel_db
    kernel_db.KernelBase.metadata.create_all(kernel_db._get_engine(db_path))
    return OptimizedParamsStore(db_path=db_path)


def test_save_candidate_returns_record(store):
    result = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.45, "home_court": 0.15, "rest": 0.15, "form": 0.25},
        elo_params={"hfa": 100, "k_regular": 20, "k_playoff": 30},
        score=0.72, accuracy=0.68, brier_score=0.21, mae=0.32, sample_count=100,
        trial_number=5,
    )
    assert result["id"] is not None
    assert result["sport"] == "nba"
    assert result["status"] == "candidate"
    assert json.loads(result["factor_weights"])["elo"] == 0.45


def test_get_applied_returns_none_when_no_applied(store):
    result = store.get_applied("nba", "nba")
    assert result is None


def test_apply_marks_candidate_as_applied(store):
    saved = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.50}, elo_params={"hfa": 110},
        score=0.75, accuracy=0.70, brier_score=0.20, mae=0.30, sample_count=100,
    )
    applied = store.apply(saved["id"])
    assert applied["status"] == "applied"
    assert applied["applied_at"] is not None
    # Verify get_applied returns it
    again = store.get_applied("nba", "nba")
    assert again["id"] == saved["id"]


def test_apply_archives_previous_applied(store):
    first = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.40}, elo_params={"hfa": 90},
        score=0.70, accuracy=0.65, brier_score=0.22, mae=0.35, sample_count=100,
    )
    store.apply(first["id"])
    second = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.50}, elo_params={"hfa": 110},
        score=0.75, accuracy=0.70, brier_score=0.20, mae=0.30, sample_count=100,
    )
    store.apply(second["id"])
    # First should be archived
    candidates = store.get_candidates("nba")
    statuses = [c["status"] for c in candidates]
    assert "archived" in statuses
    # Only one applied
    applied = store.get_applied("nba", "nba")
    assert applied["id"] == second["id"]


def test_get_candidates_filters_by_sport(store):
    store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.45}, elo_params={"hfa": 100},
        score=0.72, accuracy=0.68, brier_score=0.21, mae=0.32, sample_count=100,
    )
    store.save_candidate(
        sport="mlb", competition="mlb",
        factor_weights={"elo": 0.30}, elo_params={"hfa": 50},
        score=0.68, accuracy=0.63, brier_score=0.24, mae=0.37, sample_count=100,
    )
    nba_only = store.get_candidates("nba")
    assert len(nba_only) == 1
    assert nba_only[0]["sport"] == "nba"


def test_apply_is_idempotent(store):
    saved = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.45}, elo_params={"hfa": 100},
        score=0.72, accuracy=0.68, brier_score=0.21, mae=0.32, sample_count=100,
    )
    store.apply(saved["id"])
    # Applying again should not error
    result = store.apply(saved["id"])
    assert result["status"] == "applied"


def test_apply_updates_factor_registry(store):
    """Verify apply() calls FactorRegistry.update_weight for each factor (spec §7.5)."""
    from unittest.mock import patch
    saved = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.45, "home_court": 0.15, "rest": 0.15, "form": 0.25},
        elo_params={"hfa": 100},
        score=0.72, accuracy=0.68, brier_score=0.21, mae=0.32, sample_count=100,
    )
    # FactorRegistry is imported inside apply(); patch at the source module so
    # the in-function `from ... import FactorRegistry` picks up the mock.
    with patch("app.kernel.factor_registry.FactorRegistry") as MockRegistry:
        instance = MockRegistry.return_value
        store.apply(saved["id"])

    # update_weight should be called once per factor weight entry
    assert instance.update_weight.call_count == 4
    # Spot-check the first call: factor_id, competition, weight, source kwarg
    first_call = instance.update_weight.call_args_list[0]
    assert first_call.args[0] == "elo"
    assert first_call.args[1] == "nba"
    assert first_call.args[2] == 0.45
    assert first_call.kwargs.get("source") == "optimized"
