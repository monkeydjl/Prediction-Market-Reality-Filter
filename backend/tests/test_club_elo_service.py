# backend/tests/test_club_elo_service.py
"""Tests for club_elo_service — ClubElo.com CSV fetcher + cache."""
from unittest.mock import patch, MagicMock
from io import StringIO
import pytest

from app.services.club_elo_service import (
    get_club_elo,
    fetch_club_elo_snapshot,
    get_club_elo_by_country,
    _normalize_team_name,
)


SAMPLE_CSV = """Rank,Club,Country,Level,Elo,From,To
1,Arsenal,ENG,1,2063.76,2026-05-31,2026-08-21
2,Man City,ENG,1,1970.85,2026-07-05,2026-08-23
3,Paris SG,FRA,1,1967.88,2026-07-05,2026-08-23
4,Real Madrid,ESP,1,1955.12,2026-07-05,2026-08-23
5,Bayern Munich,GER,1,1940.33,2026-07-05,2026-08-23
"""


class TestNormalizeTeamName:
    def test_removes_spaces(self):
        assert _normalize_team_name("Man City") == "mancity"

    def test_lowercases(self):
        assert _normalize_team_name("Arsenal") == "arsenal"

    def test_removes_common_suffixes(self):
        assert _normalize_team_name("Arsenal FC") == "arsenal"
        assert _normalize_team_name("Real Madrid CF") == "realmadrid"
        assert _normalize_team_name("FC Bayern München") == "bayernmünchen"

    def test_handles_none(self):
        assert _normalize_team_name("") == ""


class TestFetchClubEloSnapshot:
    @patch("app.services.club_elo_service.httpx.get")
    def test_fetch_snapshot_parses_csv(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_CSV
        mock_get.return_value = mock_response

        result = fetch_club_elo_snapshot("2026-07-13")
        assert len(result) == 5
        assert result[0]["Club"] == "Arsenal"
        assert result[0]["Country"] == "ENG"
        assert result[0]["Elo"] == "2063.76"

    @patch("app.services.club_elo_service.httpx.get")
    def test_fetch_snapshot_network_error_returns_empty(self, mock_get):
        import httpx
        mock_get.side_effect = httpx.RequestError("Network error")
        result = fetch_club_elo_snapshot("2026-07-13")
        assert result == []


class TestGetClubEloByCountry:
    @patch("app.services.club_elo_service.httpx.get")
    def test_filter_england_level1(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_CSV
        mock_get.return_value = mock_response

        result = get_club_elo_by_country("ENG", level=1)
        assert "Arsenal" in result
        assert "Man City" in result
        assert "Real Madrid" not in result
        assert result["Arsenal"] == 2063.76


class TestGetClubElo:
    @patch("app.services.club_elo_service.httpx.get")
    @patch("app.services.club_elo_service._check_cache")
    def test_cache_hit_returns_cached(self, mock_cache, mock_get):
        mock_cache.return_value = {"elo_rating": 1900.0, "source": "clubelo"}
        result = get_club_elo("Arsenal")
        assert result is not None
        assert result["elo_rating"] == 1900.0
        mock_get.assert_not_called()

    @patch("app.services.club_elo_service.httpx.get")
    @patch("app.services.club_elo_service._check_cache")
    @patch("app.services.club_elo_service._save_cache")
    def test_cache_miss_fetches_and_saves(self, mock_save, mock_cache, mock_get):
        mock_cache.return_value = None
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_CSV
        mock_get.return_value = mock_response

        result = get_club_elo("Arsenal")
        assert result is not None
        assert result["elo_rating"] == 2063.76
        assert result["source"] == "clubelo"
        mock_save.assert_called_once()

    @patch("app.services.club_elo_service.httpx.get")
    @patch("app.services.club_elo_service._check_cache")
    def test_team_not_found_returns_none(self, mock_cache, mock_get):
        mock_cache.return_value = None
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_CSV
        mock_get.return_value = mock_response

        result = get_club_elo("Nonexistent United")
        assert result is None

    @patch("app.services.club_elo_service.httpx.get")
    @patch("app.services.club_elo_service._check_cache")
    def test_network_error_returns_none(self, mock_cache, mock_get):
        mock_cache.return_value = None
        import httpx
        mock_get.side_effect = httpx.RequestError("Network error")

        result = get_club_elo("Arsenal")
        assert result is None
