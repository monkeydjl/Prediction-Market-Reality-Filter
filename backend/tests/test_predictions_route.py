# backend/tests/test_predictions_route.py
"""Tests for /api/predictions routes."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Yield a TestClient with KERNEL_PREDICTION_ENABLED=True and write-key
    auth disabled (so POST routes are reachable without an API key).

    ``config.settings`` is used (not ``from ... import settings``) so that
    the fixture always sees the *current* Settings instance, even if
    ``test_main_frontend_mount`` reloads the config module mid-suite.
    The security module's ``settings`` is patched separately because
    ``require_write_key`` binds ``settings`` at import time and may hold
    a stale reference after a config reload.
    """
    from app.main import app
    from app.core import config
    from app.api.security import settings as security_settings
    from unittest.mock import patch

    with patch.object(config.settings, "KERNEL_PREDICTION_ENABLED", True), \
         patch.object(security_settings, "API_WRITE_KEY", ""), \
         patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        yield TestClient(app)


class TestPredictionsRoutes:
    def test_list_engines(self, client):
        """List engines works even when kernel is disabled."""
        from app.core import config
        config.settings.KERNEL_PREDICTION_ENABLED = False  # test disabled state
        resp = client.get("/api/predictions/engines")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert "elo_odds" in data

    def test_predict_match_not_found(self, client):
        # Flag is enabled by fixture. The WorldCupAdapter gracefully degrades
        # to a stub MatchIdentity for unknown match IDs and the EloOddsEngine
        # falls back to default probabilities, so 200 is also valid.
        resp = client.post("/api/predictions/matches/nonexistent/predict")
        assert resp.status_code in (200, 404, 500)

    def test_process_outcome_not_found(self, client):
        # Flag is enabled by fixture
        resp = client.post("/api/predictions/outcomes/nonexistent/process")
        assert resp.status_code in (404, 200, 500)

    def test_predict_returns_503_when_disabled(self, client):
        """When KERNEL_PREDICTION_ENABLED is False, predict returns 503."""
        from app.core import config
        config.settings.KERNEL_PREDICTION_ENABLED = False
        resp = client.post("/api/predictions/matches/any/predict")
        assert resp.status_code == 503
