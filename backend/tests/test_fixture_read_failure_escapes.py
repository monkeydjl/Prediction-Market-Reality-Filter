"""What an unreadable kernel_match_fixtures / kernel_match_results does to the
serving and learning doors.

`query_fixture` and `query_result` are copy-pasted into five adapter modules
(ten functions), each wrapping `session.get(model_cls, match_id)` in
`except Exception: logger.warning(...); return None`. `None` is also their
normal answer -- "no row carries that id" -- and every consumer restates it as
a fact.

Measured over one seeded epl fixture + result, before the fix:

* fixtures unreadable (renamed `home_team`) or dropped were **identical** to an
  empty-but-readable table at three doors: `get_match_identity` -> stub named
  "Home", `GET /predictions/matches/{id}` -> 404 "Match not found",
  `POST .../predict` -> 404 "Match not found".
* results unreadable was **identical** to an empty table at
  `POST /predictions/outcomes/{id}/process`: **200 {"status": "processed"}**
  with no learning step run at all.
* with a prediction already stored and the fixtures table then broken, that
  same route answered 200 "processed" while `update_calibration` never ran,
  logging "no fixture backs it" about a table holding 18,717 rows.

After the fix the read escapes: the routes report a server fault, and the
empty-but-readable answers are untouched.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy import text

from app.core import config
from app.kernel.kernel_db import (
    KernelMatchFixture,
    KernelMatchResult,
    close_kernel_db,
    get_kernel_session,
    init_kernel_db,
)

MID = "epl-20260301-ARS-CHE"
KICKOFF = datetime(2026, 3, 1, 15, 0)

FIXTURES_UNREADABLE = (
    "ALTER TABLE kernel_match_fixtures RENAME COLUMN home_team TO home_team_x"
)
RESULTS_UNREADABLE = (
    "ALTER TABLE kernel_match_results RENAME COLUMN home_score TO home_score_x"
)

def _fixture_readers():
    """The five (module, query_fixture, query_result) triples, as data.

    ``test_no_adapter_swallows_a_kernel_row_read`` asserts this is exactly the
    set of such helpers under ``app/sports/``, so a sixth copy cannot be added
    with the handler back in it and stay untested.
    """
    from app.sports.baseball import mlb_adapter
    from app.sports.basketball import nba_adapter
    from app.sports.football.adapters import _shared
    from app.sports.hockey import nhl_adapter
    from app.sports.lol import lol_adapter

    return {
        "football/_shared": _shared,
        "basketball/nba": nba_adapter,
        "baseball/mlb": mlb_adapter,
        "hockey/nhl": nhl_adapter,
        "lol/lol": lol_adapter,
    }


def _read_fixture(mod):
    """lol_adapter's pair hardcodes its model class; the other four take it."""
    if mod.__name__.endswith("lol_adapter"):
        return mod.query_fixture(MID)
    return mod.query_fixture(MID, KernelMatchFixture)


def _read_result(mod):
    if mod.__name__.endswith("lol_adapter"):
        return mod.query_result(MID)
    return mod.query_result(MID, KernelMatchResult)


_MODULES = _fixture_readers()


@pytest.fixture
def kernel_db(tmp_path):
    close_kernel_db()
    path = str(tmp_path / "fixture_reads.db")
    with patch.object(config.settings, "KERNEL_DB_FILE", path):
        init_kernel_db(path)
        try:
            _seed()
            yield
        finally:
            close_kernel_db()


def _sql(stmt: str) -> None:
    """Real DDL through the live ORM session, visible to later ORM reads."""
    session = get_kernel_session()
    try:
        session.execute(text(stmt))
        session.commit()
    finally:
        session.close()


def _seed() -> None:
    session = get_kernel_session()
    try:
        session.add(
            KernelMatchFixture(
                match_id=MID,
                competition="epl",
                season="2025-26",
                home_team="Arsenal",
                away_team="Chelsea",
                kickoff_utc=KICKOFF,
                status="finished",
            )
        )
        session.add(
            KernelMatchResult(
                match_id=MID,
                home_score=2,
                away_score=1,
                outcome="home_win",
                finished_at=KICKOFF,
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    finally:
        session.close()


@pytest.fixture
def client(kernel_db):
    """The production route stack over the seeded temp kernel DB.

    ``reset_kernel_singleton`` matters both ways: ``_get_kernel`` caches the
    assembled kernel on a function attribute, so a leftover instance would hold
    adapters bound to another test's DB.
    """
    from app.api.routes.predictions import reset_kernel_singleton
    from app.api.security import settings as security_settings
    from app.main import app

    reset_kernel_singleton()
    with patch.object(config.settings, "KERNEL_PREDICTION_ENABLED", True), \
            patch.object(config.settings, "PHASE2_LEAGUES_ENABLED", True), \
            patch.object(config.settings, "PHASE3_LEARNING_ENABLED", True), \
            patch.object(security_settings, "API_WRITE_KEY", ""), \
            patch.object(security_settings, "ALLOW_OPEN_WRITES", True), \
            patch("app.sports.football.adapters._shared.get_club_elo",
                  return_value=None), \
            patch("app.services.elo_ratings_service.get_elo_rating",
                  new=AsyncMock(return_value=None)), \
            patch("app.services.odds_cache_service.get_cached_odds",
                  new=AsyncMock(return_value=None)):
        # raise_server_exceptions=False so the GET route -- which has no generic
        # handler -- reports the status a real HTTP client would see.
        yield TestClient(app, raise_server_exceptions=False)
    reset_kernel_singleton()


@pytest.mark.parametrize("name", list(_MODULES), ids=list(_MODULES))
def test_a_seeded_row_is_returned_by_every_family(kernel_db, name):
    mod = _MODULES[name]

    assert _read_fixture(mod).home_team == "Arsenal"
    assert _read_result(mod).home_score == 2


@pytest.mark.parametrize("name", list(_MODULES), ids=list(_MODULES))
def test_a_readable_but_empty_table_still_answers_none(kernel_db, name):
    """The reverse test: no row is a normal answer and must not raise."""
    mod = _MODULES[name]
    _sql("DELETE FROM kernel_match_fixtures")
    _sql("DELETE FROM kernel_match_results")

    assert _read_fixture(mod) is None
    assert _read_result(mod) is None


@pytest.mark.parametrize(
    "ddl",
    [FIXTURES_UNREADABLE, "DROP TABLE kernel_match_fixtures"],
    ids=["renamed_column", "dropped_table"],
)
@pytest.mark.parametrize("name", list(_MODULES), ids=list(_MODULES))
def test_an_unreadable_fixtures_table_escapes_every_family(kernel_db, name, ddl):
    mod = _MODULES[name]
    _sql(ddl)

    with pytest.raises(OperationalError):
        _read_fixture(mod)
    # The other table is untouched, so this is a read failure and not an outage.
    assert _read_result(mod).home_score == 2


@pytest.mark.parametrize(
    "ddl",
    [RESULTS_UNREADABLE, "DROP TABLE kernel_match_results"],
    ids=["renamed_column", "dropped_table"],
)
@pytest.mark.parametrize("name", list(_MODULES), ids=list(_MODULES))
def test_an_unreadable_results_table_escapes_every_family(kernel_db, name, ddl):
    mod = _MODULES[name]
    _sql(ddl)

    with pytest.raises(OperationalError):
        _read_result(mod)
    assert _read_fixture(mod).home_team == "Arsenal"


def test_the_match_routes_answer_404_for_a_match_that_is_merely_absent(client):
    """The contrast row. This behaviour must survive the fix unchanged."""
    _sql("DELETE FROM kernel_match_fixtures")

    assert client.get(f"/api/predictions/matches/{MID}").status_code == 404
    assert client.post(f"/api/predictions/matches/{MID}/predict").status_code == 404


@pytest.mark.parametrize(
    "ddl",
    [FIXTURES_UNREADABLE, "DROP TABLE kernel_match_fixtures"],
    ids=["renamed_column", "dropped_table"],
)
def test_the_match_routes_report_a_server_fault_not_a_missing_match(client, ddl):
    """404 said the match does not exist. Both doors used to answer it."""
    _sql(ddl)

    assert client.get(f"/api/predictions/matches/{MID}").status_code == 500
    predict = client.post(f"/api/predictions/matches/{MID}/predict")
    assert predict.status_code == 500
    assert "kernel_match_fixtures" in predict.text


def test_the_outcome_route_reports_processed_for_a_match_with_no_result(client):
    """The other contrast row: nothing to process is a 200, and must stay one."""
    _sql("DELETE FROM kernel_match_results")

    resp = client.post(f"/api/predictions/outcomes/{MID}/process")

    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"


@pytest.mark.parametrize(
    "ddl",
    [RESULTS_UNREADABLE, "DROP TABLE kernel_match_results"],
    ids=["renamed_column", "dropped_table"],
)
def test_the_outcome_route_no_longer_reports_processed_over_a_broken_table(client, ddl):
    _sql(ddl)

    resp = client.post(f"/api/predictions/outcomes/{MID}/process")

    assert resp.status_code == 500
    assert "kernel_match_results" in resp.text


def test_the_learning_loop_is_no_longer_skipped_under_a_quiet_200(client):
    """The sequence that reaches process_outcome's own is_stub branch.

    Predict while the table is readable so an error can be computed, then break
    it. Before the fix this answered 200 {"status": "processed"} with
    ``update_calibration`` never called, logging "no fixture backs it" about a
    table holding 18,717 rows.
    """
    from app.api.routes.predictions import _get_kernel

    assert client.post(f"/api/predictions/matches/{MID}/predict").status_code == 200

    kernel = _get_kernel()
    ran: list[str] = []
    real = kernel._learning.update_calibration

    def _spy(*args, **kwargs):
        ran.append("update_calibration")
        return real(*args, **kwargs)

    with patch.object(kernel._learning, "update_calibration", _spy):
        healthy = client.post(f"/api/predictions/outcomes/{MID}/process")
        assert healthy.status_code == 200
        assert ran == ["update_calibration"]

        ran.clear()
        _sql(FIXTURES_UNREADABLE)
        broken = client.post(f"/api/predictions/outcomes/{MID}/process")

    assert broken.status_code == 500
    assert ran == []


def test_no_adapter_swallows_a_kernel_row_read():
    """An exact partition, so a sixth copy cannot reintroduce the handler.

    Scans ``app/sports/`` rather than trusting the module table above: a new
    adapter carrying its own copy fails here before anything else notices.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "sports"
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name not in ("query_fixture", "query_result"):
                continue
            key = path.relative_to(root).as_posix()
            found.setdefault(key, []).append(node.name)
            assert not [
                h for h in ast.walk(node) if isinstance(h, ast.ExceptHandler)
            ], f"{key}:{node.lineno} {node.name} swallows its kernel read again"

    assert found == {
        "baseball/mlb_adapter.py": ["query_fixture", "query_result"],
        "basketball/nba_adapter.py": ["query_fixture", "query_result"],
        "football/adapters/_shared.py": ["query_fixture", "query_result"],
        "hockey/nhl_adapter.py": ["query_fixture", "query_result"],
        "lol/lol_adapter.py": ["query_fixture", "query_result"],
    }
    assert len(found) == len(_MODULES)
