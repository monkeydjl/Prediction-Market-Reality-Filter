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


def test_save_candidate_upserts_same_sport_candidate(store):
    """UNIQUE (sport, competition, status) — re-run updates candidate in place."""
    first = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.40}, elo_params={"hfa": 90},
        score=0.70, accuracy=0.65, brier_score=0.22, mae=0.35, sample_count=100,
        trial_number=1,
    )
    second = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.50}, elo_params={"hfa": 110},
        score=0.75, accuracy=0.70, brier_score=0.20, mae=0.30, sample_count=100,
        trial_number=2,
    )
    assert second["status"] == "candidate"
    assert second["id"] == first["id"]
    assert second["accuracy"] == 0.70
    assert second["trial_number"] == 2
    assert json.loads(second["factor_weights"])["elo"] == 0.50
    candidates = [c for c in store.get_candidates("nba") if c["status"] == "candidate"]
    assert len(candidates) == 1


def test_get_applied_returns_none_when_no_applied(store):
    result = store.get_applied("nba", "nba")
    assert result is None


def test_apply_marks_candidate_as_applied(store):
    saved = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.50}, elo_params={"hfa": 110},
        score=0.75, accuracy=0.70, brier_score=0.20, mae=0.30, sample_count=100,
    )
    result = store.apply(saved["id"])
    applied = result["applied"]
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
    store.apply(saved["id"], reseed_elo=False)
    # Applying again should not error
    result = store.apply(saved["id"], reseed_elo=False)
    assert result["applied"]["status"] == "applied"


def test_apply_returns_weight_diff_vs_previous(store):
    first = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.40, "form": 0.20}, elo_params={"hfa": 90},
        score=0.70, accuracy=0.65, brier_score=0.22, mae=0.35, sample_count=100,
    )
    store.apply(first["id"], reseed_elo=False)
    second = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.50, "form": 0.15}, elo_params={"hfa": 110},
        score=0.75, accuracy=0.70, brier_score=0.20, mae=0.30, sample_count=100,
    )
    result = store.apply(second["id"], reseed_elo=False)
    assert result["previous_applied"]["id"] == first["id"]
    by_factor = {d["factor"]: d for d in result["weight_diff"]}
    assert by_factor["elo"]["before"] == 0.40
    assert by_factor["elo"]["after"] == 0.50
    assert by_factor["form"]["before"] == 0.20
    assert by_factor["form"]["after"] == 0.15


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
        store.apply(saved["id"], reseed_elo=False)

    # update_weight should be called once per factor weight entry
    assert instance.update_weight.call_count == 4
    # Spot-check the first call: factor_id, competition, weight, source kwarg
    first_call = instance.update_weight.call_args_list[0]
    assert first_call.args[0] == "elo"
    assert first_call.args[1] == "nba"
    assert first_call.args[2] == 0.45
    assert first_call.kwargs.get("source") == "optimized"


def test_apply_reseeds_elo_and_resets_kernel(store):
    from unittest.mock import MagicMock, patch

    saved = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.50}, elo_params={"hfa": 57.8, "k_regular": 30.5},
        score=0.75, accuracy=0.70, brier_score=0.20, mae=0.30, sample_count=100,
    )
    mock_seed = MagicMock(return_value={"teams": 30, "sports": {"nba": {"teams": 30}}, "errors": []})
    mock_reset = MagicMock()
    with patch("app.kernel.factor_registry.FactorRegistry"):
        with patch(
            "app.services.historical_data_ingestor.HistoricalDataIngestor",
        ) as MockIngestor:
            MockIngestor.return_value.seed_elo_ratings = mock_seed
            with patch(
                "app.api.routes.predictions.reset_kernel_singleton",
                mock_reset,
            ):
                result = store.apply(saved["id"], reseed_elo=True)

    mock_seed.assert_called_once_with(sport="nba")
    mock_reset.assert_called_once()
    assert result["elo_params"]["hfa"] == 57.8
    assert result["elo_seed"]["ok"] is True
    assert result["elo_seed"]["teams"] == 30


def test_apply_reseed_failure_does_not_rollback_applied(store):
    from unittest.mock import patch

    saved = store.save_candidate(
        sport="mlb", competition="mlb",
        factor_weights={"elo": 0.40}, elo_params={"hfa": 61.0},
        score=0.70, accuracy=0.65, brier_score=0.22, mae=0.35, sample_count=100,
    )
    with patch("app.kernel.factor_registry.FactorRegistry"):
        with patch(
            "app.services.historical_data_ingestor.HistoricalDataIngestor",
        ) as MockIngestor:
            MockIngestor.return_value.seed_elo_ratings.side_effect = RuntimeError("seed boom")
            with patch("app.api.routes.predictions.reset_kernel_singleton"):
                result = store.apply(saved["id"], reseed_elo=True)

    assert result["applied"]["status"] == "applied"
    assert result["elo_seed"]["ok"] is False
    assert "seed boom" in result["elo_seed"]["error"]
    assert store.get_applied("mlb", "mlb")["id"] == saved["id"]


def test_apply_reseed_elo_false_skips_seed(store):
    from unittest.mock import patch

    saved = store.save_candidate(
        sport="nhl", competition="nhl",
        factor_weights={"elo": 0.40}, elo_params={"hfa": 55.0},
        score=0.70, accuracy=0.65, brier_score=0.22, mae=0.35, sample_count=100,
    )
    with patch("app.kernel.factor_registry.FactorRegistry"):
        with patch(
            "app.services.historical_data_ingestor.HistoricalDataIngestor",
        ) as MockIngestor:
            with patch("app.api.routes.predictions.reset_kernel_singleton"):
                result = store.apply(saved["id"], reseed_elo=False)

    MockIngestor.assert_not_called()
    assert result["elo_seed"] == {"ok": None, "skipped": True}
