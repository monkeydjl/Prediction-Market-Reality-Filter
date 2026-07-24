# backend/tests/test_historical_data_ingestor.py
"""Tests for HistoricalDataIngestor — TDD RED phase."""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.historical_data_ingestor import HistoricalDataIngestor


@pytest.fixture
def ingestor(tmp_path, monkeypatch):
    """Create an ingestor with an isolated SQLite DB.

    Minimal fix from plan: the plan's fixture created the schema via a
    standalone _get_engine call but did not point the module-level
    _SessionLocal (used by get_kernel_session) at the tmp DB. We use
    init_kernel_db(db_path) + close_kernel_db() so HistoricalDataIngestor
    reads/writes the isolated test DB.
    """
    db_path = str(tmp_path / "test_ingestor.db")
    monkeypatch.setenv("KERNEL_DB_PATH", db_path)
    from app.kernel import kernel_db
    kernel_db.close_kernel_db()  # reset any cached engine from prior tests
    kernel_db.init_kernel_db(db_path)  # creates engine + session factory + schema
    yield HistoricalDataIngestor()
    kernel_db.close_kernel_db()  # cleanup


@pytest.mark.asyncio
async def test_ingest_one_season_nba(ingestor):
    mock_games = [
        {"game_id": 1, "home_team": "Lakers", "away_team": "Celtics", "home_score": 110, "away_score": 105, "season": 2024, "date": "2024-01-01"},
        {"game_id": 2, "home_team": "Celtics", "away_team": "Lakers", "home_score": 108, "away_score": 112, "season": 2024, "date": "2024-01-03"},
    ]
    with patch("app.services.historical_data_ingestor.fetch_nba_season_games", new_callable=AsyncMock, return_value=mock_games):
        result = await ingestor.ingest_season("nba", "2024-25")
    assert result["matches"] == 2
    assert result["results"] == 2
    assert len(result["errors"]) == 0


@pytest.mark.asyncio
async def test_ingest_multi_season(ingestor):
    with patch("app.services.historical_data_ingestor.fetch_nba_season_games", new_callable=AsyncMock, return_value=[
        {"game_id": 1, "home_team": "A", "away_team": "B", "home_score": 100, "away_score": 99, "season": 2023, "date": "2023-01-01"},
    ]):
        result1 = await ingestor.ingest_season("nba", "2023-24")
    with patch("app.services.historical_data_ingestor.fetch_nba_season_games", new_callable=AsyncMock, return_value=[
        {"game_id": 2, "home_team": "A", "away_team": "B", "home_score": 101, "away_score": 98, "season": 2024, "date": "2024-01-01"},
    ]):
        result2 = await ingestor.ingest_season("nba", "2024-25")
    assert result1["matches"] == 1
    assert result2["matches"] == 1


@pytest.mark.asyncio
async def test_api_failure_returns_errors(ingestor):
    with patch("app.services.historical_data_ingestor.fetch_nba_season_games", new_callable=AsyncMock, side_effect=Exception("API down")):
        result = await ingestor.ingest_season("nba", "2024-25")
    assert result["matches"] == 0
    assert len(result["errors"]) == 1
    assert "API down" in result["errors"][0]


@pytest.mark.asyncio
async def test_idempotent_re_ingest(ingestor):
    mock_games = [
        {"game_id": 1, "home_team": "Lakers", "away_team": "Celtics", "home_score": 110, "away_score": 105, "season": 2024, "date": "2024-01-01"},
    ]
    with patch("app.services.historical_data_ingestor.fetch_nba_season_games", new_callable=AsyncMock, return_value=mock_games):
        result1 = await ingestor.ingest_season("nba", "2024-25")
        result2 = await ingestor.ingest_season("nba", "2024-25")
    assert result1["matches"] == 1
    # Idempotent: re-ingest stores 0 new matches/results (no duplicates).
    # Plan asserted ==1 here, but the implementation returns the count of
    # newly-stored rows per call (matches test_ingest_one_season_nba semantics),
    # so the correct idempotent expectation is 0.
    assert result2["matches"] == 0  # No duplicate
    assert result2["results"] == 0


@pytest.mark.asyncio
async def test_mlb_ingest(ingestor):
    mock_games = [
        {"game_id": 1, "home_team": "Yankees", "away_team": "Red Sox", "home_score": 5, "away_score": 3, "season": 2024, "date": "2024-06-01"},
    ]
    with patch("app.services.historical_data_ingestor.fetch_mlb_season_games", new_callable=AsyncMock, return_value=mock_games):
        result = await ingestor.ingest_season("mlb", "2024")
    assert result["matches"] == 1
    assert result["results"] == 1


@pytest.mark.asyncio
async def test_ingest_match_id_matches_adapter_format(ingestor):
    """match_id must be sport-game_id (same as NBA/MLB/NHL adapters)."""
    from app.kernel.kernel_db import KernelMatchFixture, KernelMatchResult, get_kernel_session

    mock_games = [
        {
            "game_id": 18446819,
            "home_team": "Oklahoma City Thunder",
            "away_team": "Indiana Pacers",
            "home_score": 125,
            "away_score": 124,
            "date": "2025-10-22T00:00:00+00:00",
            "stage": "regular_season",
            "status": "finished",
        },
    ]
    with patch(
        "app.services.historical_data_ingestor.fetch_nba_season_games",
        new_callable=AsyncMock,
        return_value=mock_games,
    ):
        await ingestor.ingest_season("nba", "2025-26")

    session = get_kernel_session()
    try:
        fix = session.get(KernelMatchFixture, "nba-18446819")
        res = session.get(KernelMatchResult, "nba-18446819")
        assert fix is not None
        assert res is not None
        assert res.outcome == "home_win"
        assert res.home_score == 125
    finally:
        session.close()


def test_backfill_results_from_fixtures(ingestor):
    """Scores on fixtures only → KernelMatchResult rows."""
    from datetime import datetime, timezone

    from app.kernel.kernel_db import (
        KernelMatchFixture,
        KernelMatchResult,
        get_kernel_session,
    )

    session = get_kernel_session()
    try:
        now = datetime.now(timezone.utc)
        session.add(
            KernelMatchFixture(
                match_id="nba-99",
                competition="nba",
                season="2025-26",
                home_team="Lakers",
                away_team="Celtics",
                kickoff_utc=now,
                stage="regular_season",
                status="finished",
                home_score=110,
                away_score=100,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    finally:
        session.close()

    out = ingestor.backfill_results_from_fixtures(sport="nba")
    assert out["results"] == 1
    assert len(out["errors"]) == 0

    session = get_kernel_session()
    try:
        res = session.get(KernelMatchResult, "nba-99")
        assert res is not None
        assert res.home_score == 110
        assert res.outcome == "home_win"
    finally:
        session.close()

    # Idempotent
    out2 = ingestor.backfill_results_from_fixtures(sport="nba")
    assert out2["results"] == 0


def test_seed_elo_ratings(ingestor):
    """After results exist, seed_elo_ratings populates kernel_elo_ratings."""
    from datetime import datetime, timezone

    from app.kernel.kernel_db import (
        KernelEloRating,
        KernelMatchFixture,
        KernelMatchResult,
        get_kernel_session,
    )

    session = get_kernel_session()
    try:
        now = datetime.now(timezone.utc)
        for mid, home, away, hs, aws in [
            ("nba-1", "Lakers", "Celtics", 110, 100),
            ("nba-2", "Celtics", "Lakers", 105, 100),
        ]:
            session.add(
                KernelMatchFixture(
                    match_id=mid,
                    competition="nba",
                    season="2025-26",
                    home_team=home,
                    away_team=away,
                    kickoff_utc=now,
                    stage="regular_season",
                    status="finished",
                    home_score=hs,
                    away_score=aws,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                KernelMatchResult(
                    match_id=mid,
                    home_score=hs,
                    away_score=aws,
                    outcome="home_win" if hs > aws else "away_win",
                    finished_at=now,
                    created_at=now,
                )
            )
        session.commit()
    finally:
        session.close()

    out = ingestor.seed_elo_ratings(sport="nba")
    assert out["teams"] == 2
    assert len(out["errors"]) == 0
    assert out["sports"]["nba"]["matches"] == 2

    session = get_kernel_session()
    try:
        lakers = session.get(KernelEloRating, "Lakers")
        celtics = session.get(KernelEloRating, "Celtics")
        assert lakers is not None
        assert celtics is not None
        assert lakers.competition == "nba"
        assert lakers.sport == "basketball"
        assert 1400 < lakers.elo_rating < 1600
        assert 1400 < celtics.elo_rating < 1600
    finally:
        session.close()
