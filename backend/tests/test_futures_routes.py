# backend/tests/test_futures_routes.py
"""Tests for futures API routes — TDD RED phase."""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def disable_phase12(monkeypatch):
    """Default: Phase 12 disabled -> 503."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE12_FUTURES_MARKETS_ENABLED", False)


def test_endpoints_return_503_when_disabled(client):
    resp = client.get("/api/futures/nba/2024-25")
    assert resp.status_code == 503


def test_get_futures_returns_links_when_enabled(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE12_FUTURES_MARKETS_ENABLED", True)

    mock_store = MagicMock()
    mock_store.get_links = MagicMock(return_value=[
        {
            "id": 1, "competition": "nba", "season": "2024-25",
            "team": "LAL", "contract_id": "KXNBACHAMP-LAL",
            "source": "kalshi", "market_question": "championship - LAL",
            "implied_prob": 0.18, "verified": True,
        },
    ])
    with patch(
        "app.api.routes.futures.FuturesLinkStore",
        return_value=mock_store,
    ):
        resp = client.get("/api/futures/nba/2024-25")
    assert resp.status_code == 200
    data = resp.json()
    assert data["competition"] == "nba"
    assert data["season"] == "2024-25"
    assert len(data["links"]) == 1
    assert data["links"][0]["team"] == "LAL"


def test_get_latest_snapshots_returns_data_when_enabled(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE12_FUTURES_MARKETS_ENABLED", True)

    mock_store = MagicMock()
    mock_store.get_latest_snapshots = MagicMock(return_value=[
        {
            "id": 100, "link_id": 1, "team": "LAL",
            "implied_prob": 0.22, "price": 0.22,
            "liquidity": 51000.0, "volume": 12100.0,
            "captured_at": "2026-07-16T11:00:00Z",
        },
    ])
    with patch(
        "app.api.routes.futures.FuturesLinkStore",
        return_value=mock_store,
    ):
        resp = client.get("/api/futures/nba/2024-25/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["competition"] == "nba"
    assert data["season"] == "2024-25"
    assert len(data["snapshots"]) == 1
    assert data["snapshots"][0]["team"] == "LAL"
    assert data["snapshots"][0]["implied_prob"] == 0.22
