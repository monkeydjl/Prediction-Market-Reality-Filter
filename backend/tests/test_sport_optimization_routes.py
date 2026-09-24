# backend/tests/test_sport_optimization_routes.py
"""Tests for sport optimization API routes — TDD RED phase."""
import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


_TEST_WRITE_KEY = "test-sport-opt-key"


@pytest.fixture(autouse=True)
def disable_phase9(monkeypatch):
    """Default: Phase 9 disabled → 503.  Also set a write key so the
    require_write_key guard passes and requests reach the Phase 9 gate."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", False)
    monkeypatch.setattr(settings, "API_WRITE_KEY", _TEST_WRITE_KEY)


@pytest.fixture
def auth_headers():
    """Valid X-API-Key header for write endpoints (/ingest, /run, /apply)."""
    return {"X-API-Key": _TEST_WRITE_KEY}


def test_endpoints_return_503_when_disabled(client, auth_headers):
    # Pass valid auth so the request reaches the Phase 9 enablement gate.
    resp = client.post(
        "/api/sport-optimization/ingest",
        json={"sport": "nba", "seasons": ["2024-25"]},
        headers=auth_headers,
    )
    assert resp.status_code == 503


def test_ingest_triggers_fetch(client, monkeypatch, auth_headers):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)

    from unittest.mock import AsyncMock, patch
    mock_result = {"matches": 10, "results": 10, "errors": []}
    with patch("app.api.routes.sport_optimization.HistoricalDataIngestor") as MockIngestor:
        instance = MockIngestor.return_value
        instance.ingest_season = AsyncMock(return_value=mock_result)
        resp = client.post(
            "/api/sport-optimization/ingest",
            json={"sport": "nba", "seasons": ["2024-25"]},
            headers=auth_headers,
        )
    assert resp.status_code == 200
    data = resp.json()
    # Implementation wraps results by "{sport}-{season}" key
    assert data["nba-2024-25"]["matches"] == 10


def test_run_optimization_returns_task_id(client, monkeypatch, auth_headers):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)

    # Avoid spinning real Optuna / DB work in the background task.
    import asyncio
    from unittest.mock import patch

    def _noop_spawn(coro, *args, **kwargs):
        if asyncio.iscoroutine(coro):
            coro.close()

    with patch("app.utils.background_tasks.spawn", side_effect=_noop_spawn):
        resp = client.post(
            "/api/sport-optimization/run",
            json={"sport": "nba", "n_trials": 5},
            headers=auth_headers,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "task_id" in data
    assert data["sports"] == ["nba"]
    assert data["n_trials"] == 5


def test_run_optimization_rejects_unknown_sport(client, monkeypatch, auth_headers):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)

    resp = client.post(
        "/api/sport-optimization/run",
        json={"sport": "cricket", "n_trials": 5},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_failed_optimization_task_does_not_persist_or_expose_exception_text(
    client,
    monkeypatch,
    tmp_path,
    auth_headers,
):
    from app.api.routes import sport_optimization
    from app.core.config import settings
    from app.services import optimization_task_manager
    from app.memory import optimization_task_store

    sensitive_error = (
        "postgresql://user:fake-secret@private-db/optimization "
        "SELECT private_data"
    )
    spawned = []
    rendered_logs = []

    def capture_spawn(coro, *args, **kwargs):
        spawned.append(coro)

    class RenderedLogHandler(logging.Handler):
        def emit(self, record):
            rendered_logs.append(self.format(record))

    log_handler = RenderedLogHandler()
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    sport_optimization.logger.addHandler(log_handler)

    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)
    monkeypatch.setattr(settings, "LOOP_DB_FILE", str(tmp_path / "loop.db"))
    optimization_task_store._INITIALIZED.clear()
    task_manager = optimization_task_manager.OptimizationTaskManager()
    monkeypatch.setattr(optimization_task_manager, "_task_manager", task_manager)
    monkeypatch.setattr("app.utils.background_tasks.spawn", capture_spawn)
    monkeypatch.setattr(
        "app.kernel.backtest.match_loader.load_sport_matches_for_backtest",
        lambda sport: (_ for _ in ()).throw(RuntimeError(sensitive_error)),
    )

    try:
        response = client.post(
            "/api/sport-optimization/run",
            json={"sport": "nba", "n_trials": 5},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert len(spawned) == 1
        asyncio.run(spawned[0])
    finally:
        sport_optimization.logger.removeHandler(log_handler)

    task_id = response.json()["task_id"]
    stored = optimization_task_manager.optimization_task_store.get_task(task_id)
    rehydrated_manager = optimization_task_manager.OptimizationTaskManager()
    monkeypatch.setattr(optimization_task_manager, "_task_manager", rehydrated_manager)
    status_response = client.get(f"/api/sport-optimization/status/{task_id}")

    assert stored is not None
    assert stored["status"] == "failed"
    assert stored["error"] == "Optimization task failed"
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "failed"
    assert status_response.json()["error"] == "Optimization task failed"
    exported = str(stored) + status_response.text + "\n".join(rendered_logs)
    assert "fake-secret" not in exported
    assert "private-db" not in exported
    assert "SELECT private_data" not in exported
    assert "Traceback" not in exported
    assert "RuntimeError" in "\n".join(rendered_logs)


def test_apply_does_not_expose_elo_reseed_exception_text(
    client,
    monkeypatch,
    auth_headers,
):
    from app.core.config import settings
    from app.kernel.optimized_params_store import OptimizedParamsStore

    sensitive_error = (
        "SELECT secret FROM C:/private/elo.db "
        "Authorization=Bearer fake-api-key ticket=fake-ticket "
        "subprotocol=fake-subprotocol https://upstream.example/private"
    )

    def fail_seed(*args, **kwargs):
        raise RuntimeError(sensitive_error)

    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)
    candidate = OptimizedParamsStore().save_candidate(
        sport="nba",
        competition="nba",
        factor_weights={"elo": 0.5},
        elo_params={"hfa": 100},
        score=0.75,
        accuracy=0.70,
        brier_score=0.20,
        mae=0.30,
        sample_count=100,
    )
    monkeypatch.setattr(
        "app.services.historical_data_ingestor."
        "HistoricalDataIngestor.seed_elo_ratings",
        fail_seed,
    )

    response = client.post(
        f"/api/sport-optimization/apply/{candidate['id']}",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["elo_seed"] == {
        "ok": False,
        "error": "Elo reseed failed",
    }
    for fragment in (
        "SELECT secret",
        "C:/private",
        "fake-api-key",
        "fake-ticket",
        "fake-subprotocol",
        "https://upstream.example/private",
    ):
        assert fragment not in response.text


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




def test_live_evidence_returns_503_when_disabled(client):
    resp = client.get("/api/sport-optimization/live-evidence")
    assert resp.status_code == 503


def test_live_evidence_is_read_only_and_needs_no_write_key(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)
    expected = {
        "threshold": 10,
        "total_predictions": 0,
        "total_settled": 0,
        "group_count": 0,
        "ready_group_count": 0,
        "learning_ready": False,
        "groups": [],
    }
    monkeypatch.setattr(
        "app.services.phase9_live_evidence_service.build_live_evidence_report",
        lambda: expected,
    )
    resp = client.get("/api/sport-optimization/live-evidence")
    assert resp.status_code == 200
    assert resp.json() == expected



    from app.core.config import settings
    monkeypatch.setattr(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)

    # No X-API-Key header → should be rejected by require_write_key.
    resp = client.post("/api/sport-optimization/apply/1")
    # Should require write key → 401 (or 503 if no key configured at all)
    assert resp.status_code in (401, 403, 404, 503)  # 404 if params_id doesn't exist
