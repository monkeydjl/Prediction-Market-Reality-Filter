"""Tests for sport_settlement_cli."""
import pytest
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
