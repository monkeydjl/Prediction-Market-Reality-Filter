"""Tests for sport_settlement_cli."""
import pytest
from sqlalchemy.exc import OperationalError

from app.kernel.kernel_db import init_kernel_db, close_kernel_db


@pytest.fixture
def kernel_db(tmp_path, monkeypatch):
    db_path = tmp_path / "settlement_cli_test.db"
    monkeypatch.setenv("KERNEL_DB_PATH", str(db_path))
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def _seed_data(match_id="m1"):
    from datetime import datetime, timezone, timedelta
    from app.kernel.kernel_db import KernelPrediction, KernelMatchOutcome, get_kernel_session
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    from app.kernel.edge_store import EdgeStore
    now = datetime.now(timezone.utc)
    # Backdate snapshot by 1s so it precedes the match's finished_at (avoids a
    # timing race where captured_at > finished_at causes skipped_no_snapshot).
    snapshot_at = now - timedelta(seconds=1)
    session = get_kernel_session()
    try:
        session.add(KernelPrediction(
            match_id=match_id, sport="basketball", competition="nba",
            season="2025-26", engine="BasketballEngine", predicted_scores={},
            outcome_probabilities={"home_win": 0.65, "away_win": 0.35}, confidence=0.7,
            feature_version="nba-1.0", explanation={}, created_at=now, updated_at=now,
        ))
        session.add(KernelMatchOutcome(
            match_id=match_id, home_score=2, away_score=1, outcome="home_win",
            engine=None, score_mae=None, outcome_correct=None, brier_score=None,
            finished_at=now, created_at=now,
        ))
        session.commit()
    finally:
        session.close()
    link_store = SportMarketLinkStore()
    link = link_store.upsert_link(
        match_id=match_id, contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=True, market_question="q", implied_prob=0.6,
    )
    snap_store = MarketSnapshotStore()
    snap_store.append_snapshot(
        link_id=link["id"], implied_prob=0.9, price=0.9,
        liquidity=None, volume=None, captured_at=snapshot_at,
    )
    edge_store = EdgeStore()
    edge_store.append_edge(
        match_id=match_id, mapped_outcome="home_win",
        model_prob=0.65, market_prob=0.6, raw_edge=0.05,
        trust=0.8, liquidity_factor=1.0, adjusted_edge=0.04,
        spread=None, sources_count=1, stale=False,
    )


def test_cli_process_command(kernel_db, capsys):
    _seed_data("m1")
    from scripts.sport_settlement_cli import main
    rc = main(["process", "--match-id", "m1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[OK]" in out
    assert "m1" in out


def test_cli_scan_command(kernel_db, capsys):
    from app.core import config
    # Lower threshold so calibration writes
    _seed_data("m1")
    from scripts.sport_settlement_cli import main
    rc = main(["scan", "--limit", "10"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "scanned" in out or "processed" in out


def test_cli_scan_does_not_print_ok_when_the_queue_cannot_be_read(kernel_db, monkeypatch, capsys):
    """A degraded kernel DB must not surface as ``[OK] scanned=0``.

    ``_find_finished_matches_without_settlements`` used to swallow query errors
    into ``[]``, so this command printed the same line an idle run prints and
    exited 0 — the one outcome an operator would read as "nothing to settle".
    """
    from app.kernel import market_settlement_service as mss

    class _BrokenSession:
        def query(self, *a, **k):
            raise RuntimeError("disk I/O error")

        def close(self):
            pass

    monkeypatch.setattr(mss, "get_kernel_session", lambda: _BrokenSession())
    from scripts.sport_settlement_cli import main

    with pytest.raises(RuntimeError, match="disk I/O error"):
        main(["scan", "--limit", "10"])

    assert "[OK]" not in capsys.readouterr().out


def test_cli_calibrations_command(kernel_db, capsys):
    from scripts.sport_settlement_cli import main
    rc = main(["calibrations"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "calibration" in out.lower() or "no calibration" in out.lower()


def test_cli_history_command(kernel_db, capsys):
    from scripts.sport_settlement_cli import main
    rc = main(["history", "--limit", "5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "settlement" in out.lower() or "no settlement" in out.lower()


def _drop_settlement_tables():
    """Drop both D tables through the live ORM session.

    ``_migrate_dormant_tables`` issues DROP TABLE at init, so a missing kernel
    table is a state this repo already produces.
    """
    from sqlalchemy import text
    from app.kernel.kernel_db import get_kernel_session
    session = get_kernel_session()
    try:
        session.execute(text("DROP TABLE kernel_market_settlements"))
        session.execute(text("DROP TABLE kernel_market_calibrations"))
        session.commit()
    finally:
        session.close()


def test_cli_calibrations_does_not_print_no_data_when_the_table_cannot_be_read(
    kernel_db, capsys
):
    """A degraded kernel DB must not surface as ``[INFO] no market calibrations found``.

    ``get_calibrations`` used to swallow query failures into ``[]``, so this
    command printed the line an empty table prints and exited 0 — the one
    outcome an operator reads as "the channel has not fitted anything yet",
    which is also this table's normal state.
    """
    _drop_settlement_tables()
    from scripts.sport_settlement_cli import main

    with pytest.raises(OperationalError, match="no such table"):
        main(["calibrations"])

    out = capsys.readouterr().out
    assert "no market calibrations found" not in out
    assert "[OK]" not in out


def test_cli_history_does_not_print_no_data_when_the_table_cannot_be_read(
    kernel_db, capsys
):
    """Same for ``history``: ``[INFO] no settlements found`` and exit 0."""
    _drop_settlement_tables()
    from scripts.sport_settlement_cli import main

    with pytest.raises(OperationalError, match="no such table"):
        main(["history", "--limit", "5"])

    out = capsys.readouterr().out
    assert "no settlements found" not in out
    assert "[OK]" not in out
