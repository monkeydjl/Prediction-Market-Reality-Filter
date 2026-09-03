"""The three scheduler jobs that enumerate matches from the links table.

Each one asks ``SportMarketLinkStore.get_matches_with_verified_links()`` for its
work-list, and the run-ledger row it writes is the operator's whole view of the
run. That read used to swallow query failures into ``[]``, which is also the
answer for "nothing is linked yet", so a run over an unreadable table was
recorded as a healthy run with nothing to do.

Real DDL through the live ORM session provides the failure (no fake session, no
patched store): ``DROP TABLE`` for the whole table, and
``ALTER TABLE ... RENAME COLUMN implied_prob`` for the partial drift where the
match_id-only enumeration survives and every per-match read does not.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import text

from app.core.config import settings
from app.core.scheduler import (
    _job_capture_market_snapshots,
    _job_detect_sport_edges,
    _job_fetch_traditional_odds,
)
from app.kernel.kernel_db import (
    KernelPrediction,
    close_kernel_db,
    get_kernel_session,
    init_kernel_db,
)
from app.kernel.sport_market_link_store import SportMarketLinkStore

TABLE = "kernel_sport_market_links"


@pytest.fixture
def kernel_db(tmp_path):
    """A temp kernel DB. The jobs' no-arg ``init_kernel_db()`` returns early
    because the engine is already set, so they read this one."""
    db_path = tmp_path / "scheduler_links_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


@pytest.fixture
def ledger(kernel_db, monkeypatch):
    """Enable all three jobs and capture what they write to the run ledger."""
    monkeypatch.setattr(settings, "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", True)
    monkeypatch.setattr(settings, "PHASE7_EDGE_DETECTOR_ENABLED", True)
    monkeypatch.setattr(settings, "ODDS_API_ENABLED", True)
    monkeypatch.setattr(settings, "PHASE10_REALTIME_PUSH_ENABLED", False)
    rows = []

    def fake_finish(run_id, status, *, result=None, error=None, exc=None):
        rows.append({"status": status, "result": result, "error": error})

    with patch("app.core.scheduler._start_run", return_value="run-1"), \
         patch("app.core.scheduler._finish_run", side_effect=fake_finish):
        yield rows


def _sql(stmt: str) -> None:
    session = get_kernel_session()
    try:
        session.execute(text(stmt))
        session.commit()
    finally:
        session.close()


def _seed_match(match_id="nba-20250101-LAL-BOS"):
    """One verified link plus the prediction the edge detector needs.

    The match_id uses the attributable dated format so the odds job reaches its
    fetch decision rather than bailing out on attribution.
    """
    now = datetime.now(timezone.utc)
    session = get_kernel_session()
    try:
        session.add(KernelPrediction(
            match_id=match_id, sport="basketball", competition="nba",
            season="2025-26", engine="BasketballEngine",
            predicted_scores={"home": 110, "away": 102},
            outcome_probabilities={"home_win": 0.65, "away_win": 0.35},
            confidence=0.7, feature_version="nba-1.0", explanation=[],
            created_at=now, updated_at=now,
        ))
        session.commit()
    finally:
        session.close()
    return SportMarketLinkStore().upsert_link(
        match_id=match_id, contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=True, market_question="q",
        implied_prob=0.58,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("job", [
    _job_fetch_traditional_odds,
    _job_capture_market_snapshots,
    _job_detect_sport_edges,
])
async def test_an_unreadable_links_table_fails_the_run(ledger, job):
    """A dropped table must not be reported as "nothing is linked".

    Pre-fix all three wrote ``status="success"`` with ``matches_total=0`` — and
    for the odds job that was worse than losing information: its healthy answer
    for this seed is ``status="failed"`` carrying the real
    ``no_match_id_yields_team_tokens`` diagnostic, so the degraded read
    *silenced an alarm that was already firing*.
    """
    _seed_match()
    _sql(f"DROP TABLE {TABLE}")

    await job()

    final = ledger[-1]
    assert final["status"] == "failed"
    assert "no such table" in (final["error"] or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("job,expected", [
    (_job_fetch_traditional_odds,
     {"matches_total": 0, "captured": 0, "errors": 0}),
    (_job_capture_market_snapshots,
     {"matches_total": 0, "captured": 0, "errors": 0}),
    (_job_detect_sport_edges,
     {"matches_total": 0, "matches_processed": 0, "errors": 0}),
])
async def test_a_readable_empty_table_still_reports_a_healthy_idle_run(
    ledger, job, expected,
):
    """Cold start keeps its ledger row: no links yet is not an error."""
    await job()
    final = ledger[-1]
    assert final["status"] == "success"
    assert final["result"] == expected
    assert final["error"] is None


@pytest.mark.asyncio
async def test_partial_drift_is_counted_as_an_error_not_a_quiet_zero(ledger):
    """The sharpest shape: the work-list survives, every per-match read fails.

    ``get_matches_with_verified_links`` selects only ``match_id``, so one renamed
    column leaves it answering ``["nba-..."]`` while every read that builds a row
    dict raises. Pre-fix that produced ``success matches_total=1 captured=0
    errors=0`` and ``success matches_total=1 matches_processed=0`` — a green run
    over a match that exists, with nothing in the row to distinguish it from a
    match deliberately skipped. The per-match handlers still absorb the failure
    by design; what changed is that the ledger now counts it.
    """
    _seed_match()
    _sql(f"ALTER TABLE {TABLE} RENAME COLUMN implied_prob TO implied_prob_old")

    await _job_capture_market_snapshots()
    snapshots = ledger[-1]
    assert snapshots["status"] == "success"
    assert snapshots["result"] == {
        "matches_total": 1, "captured": 0, "errors": 1,
    }

    await _job_detect_sport_edges()
    edges = ledger[-1]
    assert edges["status"] == "success"
    assert edges["result"] == {
        "matches_total": 1, "matches_processed": 0, "errors": 1,
    }
