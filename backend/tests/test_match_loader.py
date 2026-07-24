"""Tests for backtest match_loader."""
from datetime import datetime, timezone

import pytest

from app.kernel.backtest.match_loader import (
    load_sport_matches_for_backtest,
    time_series_split,
)


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


@pytest.fixture
def kernel_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "loader.db")
    monkeypatch.setenv("KERNEL_DB_PATH", db_path)
    from app.kernel import kernel_db

    kernel_db.close_kernel_db()
    kernel_db.init_kernel_db(db_path)
    yield db_path
    kernel_db.close_kernel_db()


def _seed_three_nba_games(kernel_db):
    from app.kernel.kernel_db import (
        KernelMatchFixture,
        KernelMatchResult,
        get_kernel_session,
    )

    UTC = timezone.utc
    session = get_kernel_session()
    try:
        rows = [
            ("nba-1", "Lakers", "Celtics", 100, 90, datetime(2024, 1, 1, 0, 0, tzinfo=UTC)),
            ("nba-2", "Lakers", "Heat", 110, 100, datetime(2024, 1, 3, 0, 0, tzinfo=UTC)),
            ("nba-3", "Celtics", "Lakers", 95, 96, datetime(2024, 1, 6, 0, 0, tzinfo=UTC)),
        ]
        for mid, home, away, hs, aws, ko in rows:
            session.add(
                KernelMatchFixture(
                    match_id=mid,
                    competition="nba",
                    home_team=home,
                    away_team=away,
                    kickoff_utc=ko,
                    season="2023-24",
                    stage="regular",
                    status="finished",
                    home_score=hs,
                    away_score=aws,
                )
            )
            session.add(
                KernelMatchResult(
                    match_id=mid,
                    home_score=hs,
                    away_score=aws,
                    outcome="home_win" if hs > aws else "away_win",
                    finished_at=ko,
                )
            )
        session.commit()
    finally:
        session.close()


def test_load_sport_matches_real_rest_form(kernel_db):
    _seed_three_nba_games(kernel_db)
    matches = load_sport_matches_for_backtest("nba")
    assert len(matches) == 3
    by_id = {m["match_id"]: m for m in matches}
    assert by_id["nba-1"]["rest_days_home"] is None
    assert by_id["nba-1"]["form_home"] == 0.5
    assert by_id["nba-2"]["rest_days_home"] == 2.0
    assert by_id["nba-2"]["form_home"] == 1.0
    rests = [m["rest_days_home"] for m in matches]
    assert not all(r == 2.0 for r in rests)
    forms = [m["form_home"] for m in matches]
    assert not all(f == 0.5 for f in forms)
    assert all("kickoff_utc" not in m for m in matches)
