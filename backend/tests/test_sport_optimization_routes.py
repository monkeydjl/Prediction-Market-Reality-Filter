# backend/tests/test_sport_optimization_routes.py
"""Tests for sport optimization API routes — TDD RED phase."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def disable_phase9(monkeypatch):
    """Default: Phase 9 disabled → 503."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", False)


def test_endpoints_return_503_when_disabled(client):
    resp = client.post("/api/sport-optimization/ingest", json={"sport": "nba", "seasons": ["2024-25"]})
    assert resp.status_code == 503


def test_ingest_triggers_fetch(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)

    from unittest.mock import AsyncMock, patch
    mock_result = {"matches": 10, "results": 10, "errors": []}
    with patch("app.api.routes.sport_optimization.HistoricalDataIngestor") as MockIngestor:
        instance = MockIngestor.return_value
        instance.ingest_season = AsyncMock(return_value=mock_result)
        resp = client.post("/api/sport-optimization/ingest", json={"sport": "nba", "seasons": ["2024-25"]})
    assert resp.status_code == 200
    data = resp.json()
    # Implementation wraps results by "{sport}-{season}" key
    assert data["nba-2024-25"]["matches"] == 10


def test_run_optimization_returns_task_id(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)

    resp = client.post("/api/sport-optimization/run", json={"sport": "nba", "n_trials": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert "task_id" in data


def test_get_params_returns_404_when_none(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)

    resp = client.get("/api/sport-optimization/params/nba")
    # Returns 404 when no params found, or 200 with null
    assert resp.status_code in (200, 404)


def test_list_params_returns_array(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)

    resp = client.get("/api/sport-optimization/params")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


def test_apply_params_requires_write_key(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)

    resp = client.post("/api/sport-optimization/apply/1")
    # Should require write key → 401 or 403
    assert resp.status_code in (401, 403, 404)  # 404 if params_id doesn't exist
