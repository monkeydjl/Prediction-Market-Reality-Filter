"""What an unreadable ``kernel_optimized_params`` table says.

``get_applied`` / ``get_candidates`` swallowed query failures into ``None`` /
``[]``, and ``elo_params_resolve`` wrapped them in a second swallow returning
the settings baseline -- which is also the answer for a sport nobody has
optimized yet. Measured on a temp kernel DB seeded from the live applied rows:
a dropped table and a renamed ``elo_params`` column produced identical silent
answers, moving the engines onto un-tuned Elo parameters and answering the
operator's dashboard with an empty list plus the 404 the frontend documents as
"until a candidate is applied".
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.kernel.elo_params_resolve import (
    has_applied_elo_params,
    resolve_elo_params,
    resolve_nba_hfa,
    settings_elo_params,
)
from app.kernel.kernel_db import (
    KernelOptimizedParams, close_kernel_db, get_kernel_session, init_kernel_db,
)
from app.kernel.optimized_params_store import OptimizedParamsStore
from app.main import app
from app.services.historical_data_ingestor import _elo_params_for_sport
from app.sports.hockey.engines.hockey_engine import HockeyEngine
from tests.test_hockey_engine import _make_features

TABLE = "kernel_optimized_params"

#: ``hfa`` / ``k_regular`` from the live applied rows, as measured. Every hfa
#: differs from the settings baseline (nba 100 / mlb 50 / nhl 55), which is what
#: makes a silent revert observable; the remaining keys fill from settings.
APPLIED = {
    "nba": {"hfa": 57.875, "k_regular": 30.587},
    "mlb": {"hfa": 61.293, "k_regular": 13.495},
    "nhl": {"hfa": 83.230, "k_regular": 10.257},
}


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "optimized_params_degraded.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def _sql(stmt: str) -> None:
    """Real DDL through the live ORM session, visible to later ORM reads."""
    session = get_kernel_session()
    try:
        session.execute(text(stmt))
        session.commit()
    finally:
        session.close()


def _seed_applied() -> None:
    """One ``status="applied"`` row per sport, as the live table holds them.

    Written through the ORM rather than ``save_candidate``/``apply`` on purpose:
    ``apply`` also rewrites factor weights and can reseed Elo, none of which is
    under test here, and what these tests need is only the row the reads select.
    """
    session = get_kernel_session()
    try:
        for sport, elo in APPLIED.items():
            session.add(
                KernelOptimizedParams(
                    sport=sport, competition=sport,
                    factor_weights=json.dumps({"elo": 0.45, "form": 0.25}),
                    elo_params=json.dumps(elo),
                    score=0.72, accuracy=0.68, brier_score=0.21, mae=0.32,
                    sample_count=100, trial_number=7, status="applied",
                )
            )
        session.commit()
    finally:
        session.close()


#: Every door that reads the params table, as ``(name, thunk)``. Named rather
#: than ``getattr``-ed off one store because the layers differ: two are store
#: methods, six are resolver functions built on top of them, and one is an
#: engine that consumes the resolved value.
READS = (
    ("get_applied", lambda: OptimizedParamsStore().get_applied("nba", "nba")),
    ("get_candidates", lambda: OptimizedParamsStore().get_candidates()),
    ("resolve_elo_params.nba", lambda: resolve_elo_params("nba")),
    ("resolve_elo_params.mlb", lambda: resolve_elo_params("mlb")),
    ("resolve_elo_params.nhl", lambda: resolve_elo_params("nhl")),
    ("has_applied_elo_params", lambda: has_applied_elo_params("nba")),
    ("resolve_nba_hfa.regular", lambda: resolve_nba_hfa(playoff=False)),
    ("resolve_nba_hfa.playoff", lambda: resolve_nba_hfa(playoff=True)),
    ("_elo_params_for_sport", lambda: _elo_params_for_sport("mlb")),
    ("HockeyEngine.predict", lambda: _hockey_home_win()),
)


def _hockey_home_win() -> float:
    features = _make_features()
    result = HockeyEngine().predict(features, features.match)
    return float(result.outcome_probabilities["home_win"])


def test_a_readable_empty_table_still_answers_the_settings_baseline(kernel_db):
    """Cold start keeps its answer: the fix must not turn "no rows" into an error.

    Without this, "raises on a dropped table" would also pass for reads that
    raised on an empty one, and every sport nobody has optimized yet -- which is
    every sport on a fresh install -- would fail to predict at all.
    """
    assert OptimizedParamsStore().get_applied("nba", "nba") is None
    assert OptimizedParamsStore().get_candidates() == []
    assert has_applied_elo_params("nba") is False
    for sport in APPLIED:
        assert resolve_elo_params(sport) == settings_elo_params(sport), sport
    # No applied row, so NBA keeps the settings playoff/regular split.
    assert resolve_nba_hfa(playoff=False) == 100.0
    assert resolve_nba_hfa(playoff=True) == 90.0
    assert _elo_params_for_sport("mlb") == settings_elo_params("mlb")


def test_an_applied_row_reaches_the_resolvers_and_the_engine(kernel_db):
    """The healthy half: the fitted params are what the reads publish.

    This is what makes the degraded tests below discriminating -- every ``hfa``
    here differs from its settings baseline (nba 100 / mlb 50 / nhl 55), so a
    silent revert is observable rather than a coincidence.
    """
    _seed_applied()
    assert has_applied_elo_params("nba") is True
    for sport, elo in APPLIED.items():
        resolved = resolve_elo_params(sport)
        assert resolved["hfa"] == elo["hfa"], sport
        assert resolved["k_regular"] == elo["k_regular"], sport
        # Keys the row omits still fill from settings.
        assert resolved["k_playoff"] == settings_elo_params(sport)["k_playoff"], sport
    # The applied single hfa replaces the split for both NBA branches.
    assert resolve_nba_hfa(playoff=False) == APPLIED["nba"]["hfa"]
    assert resolve_nba_hfa(playoff=True) == APPLIED["nba"]["hfa"]
    assert _elo_params_for_sport("mlb")["hfa"] == APPLIED["mlb"]["hfa"]
    assert len(OptimizedParamsStore().get_candidates()) == 3


def test_the_engine_publishes_a_different_probability_per_hfa(kernel_db):
    """What a silent revert costs downstream, measured at the engine.

    ``HockeyEngine`` reads ``resolve_elo_params("nhl")["hfa"]`` with no
    try/except, so this pair is the whole point of the fix: the applied hfa
    83.230 and the settings 55 produce different published probabilities, and
    pre-fix an unreadable table chose the second one with no diagnostic.
    """
    baseline = _hockey_home_win()
    _seed_applied()
    applied = _hockey_home_win()
    assert applied != baseline
    assert applied == pytest.approx(0.5714, abs=5e-4)
    assert baseline == pytest.approx(0.559, abs=5e-4)


def test_every_read_raises_when_the_table_is_gone(kernel_db, subtests):
    """A dropped table is not "this sport has never been optimized".

    Each read swallowed the failure into its own cold-start value -- ``None``,
    ``[]``, ``False``, the settings baseline -- so the operator's dashboard
    listed nothing, ``/params/{sport}`` answered the 404 the frontend documents
    as "until a candidate is applied", and the engines quietly published
    un-tuned Elo parameters. ``save_candidate`` on the same broken table
    already raised, so the store disagreed with itself about whether the table
    existed.
    """
    _seed_applied()
    _sql(f"DROP TABLE {TABLE}")
    for name, read in READS:
        with subtests.test(read=name):
            with pytest.raises(OperationalError, match="no such table"):
                read()
    # The asymmetry the fix removes: the write on the same broken table already
    # raised, so pre-fix this store answered "the table is fine, there is just
    # nothing in it" and "the table is gone" about the same table.
    with pytest.raises(OperationalError, match="no such table"):
        OptimizedParamsStore().save_candidate(
            sport="nba", competition="nba",
            factor_weights={"elo": 0.45}, elo_params={"hfa": 100},
            score=0.7, accuracy=0.65, brier_score=0.22, mae=0.35,
            sample_count=100,
        )


def test_every_read_raises_when_the_params_column_drifts(kernel_db, subtests):
    """One renamed column, and the reads are unanimous -- by construction.

    Unlike a store whose reads select different column subsets, every read here
    builds the full row dict (``_row_to_dict``), so a rename of ``elo_params``
    fails all of them rather than leaving a survivor to report a healthy count
    over unreadable rows.
    """
    _seed_applied()
    _sql(f"ALTER TABLE {TABLE} RENAME COLUMN elo_params TO elo_params_old")
    for name, read in READS:
        with subtests.test(read=name):
            with pytest.raises(OperationalError, match="no such column"):
                read()


def test_a_malformed_row_still_falls_back_to_settings(kernel_db):
    """The rival configuration: bad *contents* are not an unreadable table.

    ``resolve_elo_params`` keeps its ``json.JSONDecodeError`` fallback, now
    narrowed to the ``json.loads`` call alone. That statement is about one row,
    which the operator can see and replace; the removed outer ``except
    Exception`` made the same claim about the table itself. Only the parse is
    forgiven, so a readable row with a broken JSON blob still predicts.
    """
    session = get_kernel_session()
    try:
        session.add(
            KernelOptimizedParams(
                sport="nhl", competition="nhl",
                factor_weights=json.dumps({"elo": 0.45}),
                elo_params="not-json{",
                score=0.7, accuracy=0.65, brier_score=0.22, mae=0.35,
                sample_count=100, status="applied",
            )
        )
        session.commit()
    finally:
        session.close()

    assert has_applied_elo_params("nhl") is True
    assert resolve_elo_params("nhl") == settings_elo_params("nhl")


@pytest.fixture
def phase9_client(monkeypatch):
    """Phase 9 routes enabled, server exceptions surfaced as 500 rather than raised.

    ``app.main`` registers no exception handler, so a read error escaping a
    route is a 500 -- which both consuming frontend panels already render
    through their SWR ``error`` branch. ``raise_server_exceptions=False`` is
    what lets the test assert that status instead of catching the exception.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)
    return TestClient(app, raise_server_exceptions=False)


def test_the_operator_doors_answer_500_not_empty_and_404(kernel_db, phase9_client):
    """The dashboard must not read an unreadable table as "nothing applied yet".

    Measured pre-fix on a dropped table: ``GET /params`` answered ``200 []``
    and ``GET /params/nba`` answered ``404 "No applied params for nba"`` -- the
    exact pair the frontend documents for a sport awaiting its first candidate,
    so the panel rendered its own empty state over a broken table.
    """
    _seed_applied()
    assert phase9_client.get("/api/sport-optimization/params").status_code == 200
    assert len(phase9_client.get("/api/sport-optimization/params").json()) == 3
    assert phase9_client.get("/api/sport-optimization/params/nba").json()["sport"] == "nba"

    _sql(f"DROP TABLE {TABLE}")

    assert phase9_client.get("/api/sport-optimization/params").status_code == 500
    assert phase9_client.get("/api/sport-optimization/params/nba").status_code == 500


def test_the_cold_start_404_survives_a_readable_empty_table(kernel_db, phase9_client):
    """``/params/{sport}`` keeps its documented 404 when nothing is applied.

    The rival configuration for the test above: same two doors, readable table,
    and the answers stay ``200 []`` / ``404``. Without this, raising on an
    unreadable table could have been achieved by raising on an empty one.
    """
    resp = phase9_client.get("/api/sport-optimization/params")
    assert resp.status_code == 200
    assert resp.json() == []
    detail = phase9_client.get("/api/sport-optimization/params/nba")
    assert detail.status_code == 404
    assert detail.json()["detail"] == "No applied params for nba"
