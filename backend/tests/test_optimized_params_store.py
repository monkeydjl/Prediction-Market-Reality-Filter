# backend/tests/test_optimized_params_store.py
"""Tests for OptimizedParamsStore — TDD RED phase."""
import json
import sqlite3
import pytest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.kernel.optimized_params_store import OptimizedParamsStore


@pytest.fixture
def kernel_dbs(tmp_path):
    """Two *distinct* kernel DBs: the store's own, and the global one.

    The store's ``db_path`` is only meaningful if there is a second, separately
    inspectable database for a write to escape *into*. Pointing the global
    kernel session at its own file is what lets a test tell "wrote to the store"
    apart from "wrote to whatever the global session happened to be".

    The previous fixture did ``monkeypatch.setenv("KERNEL_DB_PATH", db_path)``,
    which was a silent no-op: the setting is ``KERNEL_DB_FILE``, so the env var
    redirected nothing and the global session stayed wherever conftest put it.
    """
    from app.kernel import kernel_db

    store_db = str(tmp_path / "test_optimized.db")
    global_db = str(tmp_path / "global_kernel.db")
    kernel_db.KernelBase.metadata.create_all(kernel_db._get_engine(store_db))
    kernel_db.close_kernel_session()
    kernel_db.init_kernel_db(global_db)
    yield SimpleNamespace(store_db=store_db, global_db=global_db)
    kernel_db.close_kernel_session()


@pytest.fixture
def store(kernel_dbs):
    """Create a store with an isolated SQLite DB."""
    return OptimizedParamsStore(db_path=kernel_dbs.store_db)


def _optimized_weights(db_path):
    """(factor_id, weight) for every source='optimized' row, ordered."""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "select factor_id, weight from kernel_factors "
            "where source = 'optimized' order by factor_id"
        ).fetchall()
    finally:
        conn.close()


def _elo_teams(db_path):
    """Team names in kernel_elo_ratings, ordered."""
    conn = sqlite3.connect(db_path)
    try:
        return [
            r[0]
            for r in conn.execute(
                "select team_name from kernel_elo_ratings order by team_name"
            ).fetchall()
        ]
    finally:
        conn.close()


def _seed_nba_results(db_path):
    """Three finished NBA games written straight into ``db_path``.

    Raw sqlite3 on purpose: seeding through ``get_kernel_session()`` would put
    the rows wherever the global session points, which is the thing these tests
    have to be able to tell apart.
    """
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    games = [
        ("nba-1", "Lakers", "Celtics", 100, 90, base),
        ("nba-2", "Lakers", "Heat", 110, 100, base + timedelta(days=2)),
        ("nba-3", "Celtics", "Lakers", 95, 96, base + timedelta(days=5)),
    ]
    conn = sqlite3.connect(db_path)
    try:
        for match_id, home, away, home_score, away_score, kickoff in games:
            conn.execute(
                "insert into kernel_match_fixtures "
                "(match_id, competition, season, home_team, away_team, "
                "kickoff_utc, stage, status, home_score, away_score) "
                "values (?, 'nba', '2023-24', ?, ?, ?, 'regular', 'finished', ?, ?)",
                (
                    match_id, home, away,
                    kickoff.strftime("%Y-%m-%d %H:%M:%S"),
                    home_score, away_score,
                ),
            )
            conn.execute(
                "insert into kernel_match_results "
                "(match_id, home_score, away_score, outcome, finished_at) "
                "values (?, ?, ?, ?, ?)",
                (
                    match_id, home_score, away_score,
                    "home_win" if home_score > away_score else "away_win",
                    kickoff.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
        conn.commit()
    finally:
        conn.close()


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


# --- db_path containment -------------------------------------------------
#
# apply() writes three things, and only the first went through the store's own
# session factory: the status row (store DB), the factor weights (via a bare
# FactorRegistry()), and the Elo ratings (via a bare HistoricalDataIngestor()).
# Both collaborators default to the *global* kernel session, so a store scoped
# to db_path=X promoted its row into X while writing weights -- and, with
# reseed_elo=True, overwriting kernel_elo_ratings -- inside
# settings.KERNEL_DB_FILE. These tests are deliberately unmocked: patching
# FactorRegistry, as the four tests above do, replaces the very object whose
# session binding is the thing under test.


def test_apply_writes_weights_to_store_db_not_global(store, kernel_dbs):
    saved = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.42, "form": 0.18}, elo_params={"hfa": 100},
        score=0.72, accuracy=0.68, brier_score=0.21, mae=0.32, sample_count=100,
    )
    # Assert the empty state first: otherwise a fixture that seeded these rows
    # would be indistinguishable from apply() having written them.
    assert _optimized_weights(kernel_dbs.store_db) == []
    assert _optimized_weights(kernel_dbs.global_db) == []

    store.apply(saved["id"], reseed_elo=False)

    assert _optimized_weights(kernel_dbs.store_db) == [("elo", 0.42), ("form", 0.18)]
    assert _optimized_weights(kernel_dbs.global_db) == []


def test_apply_reseed_elo_writes_ratings_to_store_db_not_global(store, kernel_dbs):
    """The Elo half of apply() must read *and* write inside the store's DB.

    Seeding the games into the store DB only is what makes this discriminating
    in both directions: a run that read fixtures through the global session
    would find no matches and seed nobody, so a non-empty rating set proves the
    read was redirected too, not just the write.
    """
    from unittest.mock import patch

    _seed_nba_results(kernel_dbs.store_db)
    saved = store.save_candidate(
        sport="nba", competition="nba",
        factor_weights={"elo": 0.42}, elo_params={"hfa": 100},
        score=0.72, accuracy=0.68, brier_score=0.21, mae=0.32, sample_count=100,
    )
    assert _elo_teams(kernel_dbs.store_db) == []
    assert _elo_teams(kernel_dbs.global_db) == []

    with patch("app.api.routes.predictions.reset_kernel_singleton"):
        result = store.apply(saved["id"], reseed_elo=True)

    assert result["elo_seed"]["ok"] is True
    assert result["elo_seed"]["teams"] == 3
    assert _elo_teams(kernel_dbs.store_db) == ["Celtics", "Heat", "Lakers"]
    assert _elo_teams(kernel_dbs.global_db) == []


def test_apply_without_db_path_still_writes_to_global_db(kernel_dbs):
    """The production caller passes no db_path and must keep writing globally.

    The rival configuration to the test above: same code path, no db_path, and
    the weights are expected in the *global* DB. Without this, threading the
    factory could have silently redirected production writes into a store DB
    that no other reader consults.
    """
    global_store = OptimizedParamsStore()
    saved = global_store.save_candidate(
        sport="mlb", competition="mlb",
        factor_weights={"elo": 0.31, "park": 0.09}, elo_params={"hfa": 50},
        score=0.66, accuracy=0.61, brier_score=0.24, mae=0.37, sample_count=100,
    )
    assert _optimized_weights(kernel_dbs.global_db) == []

    global_store.apply(saved["id"], reseed_elo=False)

    assert _optimized_weights(kernel_dbs.global_db) == [("elo", 0.31), ("park", 0.09)]
    assert _optimized_weights(kernel_dbs.store_db) == []
