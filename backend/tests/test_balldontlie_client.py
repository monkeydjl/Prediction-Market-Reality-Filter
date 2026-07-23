from unittest.mock import MagicMock, patch

from app.sports.basketball.balldontlie_client import (
    BalldontlieClientError,
    fetch_nba_games,
)


def _resp(status: int, payload: dict | None = None, text: str = ""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.json.return_value = payload or {}
    return r


@patch("app.sports.basketball.balldontlie_client.time.sleep", return_value=None)
@patch("app.sports.basketball.balldontlie_client.httpx.get")
@patch("app.sports.basketball.balldontlie_client.config")
def test_fetch_retries_429_then_succeeds(mock_config, mock_get, _sleep):
    mock_config.settings.BALLDONTLIE_API_KEY = "k"
    mock_get.side_effect = [
        _resp(429, text="slow down"),
        _resp(
            200,
            {"data": [{"id": 1}], "meta": {}},
        ),
    ]
    games = fetch_nba_games(2025)
    assert len(games) == 1
    assert mock_get.call_count == 2


@patch("app.sports.basketball.balldontlie_client.time.sleep", return_value=None)
@patch("app.sports.basketball.balldontlie_client.httpx.get")
@patch("app.sports.basketball.balldontlie_client.config")
def test_fetch_returns_partial_after_persistent_429(mock_config, mock_get, _sleep):
    mock_config.settings.BALLDONTLIE_API_KEY = "k"
    page1 = _resp(
        200,
        {"data": [{"id": 1}, {"id": 2}], "meta": {"next_cursor": 99}},
    )
    # Exhaust retries on second page
    mock_get.side_effect = [page1] + [_resp(429)] * 10
    games = fetch_nba_games(2025)
    assert len(games) == 2


@patch("app.sports.basketball.balldontlie_client.config")
def test_fetch_requires_key(mock_config):
    mock_config.settings.BALLDONTLIE_API_KEY = ""
    try:
        fetch_nba_games(2025)
        assert False, "expected error"
    except BalldontlieClientError as exc:
        assert "not configured" in str(exc)
