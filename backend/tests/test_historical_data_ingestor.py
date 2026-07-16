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
