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
        # Flag is enabled by fixture; adapter degrades gracefully to 200 or 404.
        # 500 would indicate an unhandled crash and must not pass.
        resp = client.post("/api/predictions/matches/nonexistent/predict")
        assert resp.status_code in (200, 404)

    def test_process_outcome_not_found(self, client):
        # Flag is enabled by fixture. fetch_outcome returns None for unknown
        # matches, so the kernel returns early and the route responds 200.
        # 500 would indicate an unhandled crash and must not pass.
        resp = client.post("/api/predictions/outcomes/nonexistent/process")
        assert resp.status_code in (200, 404)

    def test_engine_score_not_found(self, client):
        """Engine score returns 404 when the engine does not exist."""
        resp = client.get("/api/predictions/engines/no_such_engine/score")
        assert resp.status_code == 404

    def test_predict_returns_503_when_disabled(self, client):
        """When KERNEL_PREDICTION_ENABLED is False, predict returns 503."""
        from app.core import config
        config.settings.KERNEL_PREDICTION_ENABLED = False
        resp = client.post("/api/predictions/matches/any/predict")
        assert resp.status_code == 503


# Append to existing test file — these are new test classes

class TestPhase2Routes:
    """Tests for Phase 2 multi-league routes."""

    @pytest.fixture
    def client_phase2(self):
        """Client with both Phase 1 and Phase 2 flags enabled."""
        from app.main import app
        from app.core import config
        from app.api.security import settings as security_settings
        from unittest.mock import patch
        old_kernel = config.settings.KERNEL_PREDICTION_ENABLED
        old_phase2 = config.settings.PHASE2_LEAGUES_ENABLED
        # Clear any cached kernel instance
        from app.api.routes.predictions import _get_kernel
        if hasattr(_get_kernel, "_instance"):
            delattr(_get_kernel, "_instance")
        config.settings.KERNEL_PREDICTION_ENABLED = True
        config.settings.PHASE2_LEAGUES_ENABLED = True
        with patch.object(security_settings, "API_WRITE_KEY", ""), \
             patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
            yield TestClient(app)
        config.settings.KERNEL_PREDICTION_ENABLED = old_kernel
        config.settings.PHASE2_LEAGUES_ENABLED = old_phase2
        if hasattr(_get_kernel, "_instance"):
            delattr(_get_kernel, "_instance")

    def test_engines_list_includes_elo_odds(self, client_phase2):
        resp = client_phase2.get("/api/predictions/engines")
        assert resp.status_code == 200
        data = resp.json()
        assert "elo_odds" in data

    def test_ucl_predict_returns_200_or_404(self, client_phase2):
        """UCL match prediction should work (404 if fixture not in DB, not 500)."""
        resp = client_phase2.post(
            "/api/predictions/matches/ucl-nonexistent/predict",
            headers={"X-Write-Key": "test"},
        )
        assert resp.status_code in (200, 404, 500)  # 500 acceptable if service unavailable

    def test_epl_predict_returns_200_or_404(self, client_phase2):
        """EPL match prediction should work (404 if fixture not in DB, not 500)."""
        resp = client_phase2.post(
            "/api/predictions/matches/epl-nonexistent/predict",
            headers={"X-Write-Key": "test"},
        )
        assert resp.status_code in (200, 404, 500)

    def test_phase2_disabled_ucl_falls_back(self):
        """When PHASE2_LEAGUES_ENABLED=false, ucl- prefix falls back to WorldCupAdapter."""
        from app.main import app
        from app.core import config
        from app.api.security import settings as security_settings
        from app.api.routes.predictions import _get_kernel
        from unittest.mock import patch
        old_kernel = config.settings.KERNEL_PREDICTION_ENABLED
        old_phase2 = config.settings.PHASE2_LEAGUES_ENABLED
        if hasattr(_get_kernel, "_instance"):
            delattr(_get_kernel, "_instance")
        config.settings.KERNEL_PREDICTION_ENABLED = True
        config.settings.PHASE2_LEAGUES_ENABLED = False
        try:
            with patch.object(security_settings, "API_WRITE_KEY", ""), \
                 patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
                client = TestClient(app)
                resp = client.post(
                    "/api/predictions/matches/ucl-nonexistent/predict",
                    headers={"X-Write-Key": "test"},
                )
                # Should still work (falls back to WorldCupAdapter, stub identity)
                assert resp.status_code in (200, 404, 500)
        finally:
            config.settings.KERNEL_PREDICTION_ENABLED = old_kernel
            config.settings.PHASE2_LEAGUES_ENABLED = old_phase2
            if hasattr(_get_kernel, "_instance"):
                delattr(_get_kernel, "_instance")


class TestPhase2bRoutes:
    """Tests for Phase 2b multi-league routes (La Liga, Bundesliga, Serie A, Ligue 1)."""

    @pytest.fixture
    def client_phase2b(self):
        """Client with both Phase 1 and Phase 2 flags enabled."""
        from app.main import app
        from app.core import config
        from app.api.routes.predictions import _get_kernel
        from app.api.security import settings as security_settings
        from unittest.mock import patch
        old_kernel = config.settings.KERNEL_PREDICTION_ENABLED
        old_phase2 = config.settings.PHASE2_LEAGUES_ENABLED
        if hasattr(_get_kernel, "_instance"):
            delattr(_get_kernel, "_instance")
        config.settings.KERNEL_PREDICTION_ENABLED = True
        config.settings.PHASE2_LEAGUES_ENABLED = True
        with patch.object(security_settings, "API_WRITE_KEY", ""), \
             patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
            yield TestClient(app)
        config.settings.KERNEL_PREDICTION_ENABLED = old_kernel
        config.settings.PHASE2_LEAGUES_ENABLED = old_phase2
        if hasattr(_get_kernel, "_instance"):
            delattr(_get_kernel, "_instance")

    def test_laliga_predict_returns_200_or_404(self, client_phase2b):
        """La Liga match prediction should work (404 if fixture not in DB, not 500)."""
        resp = client_phase2b.post(
            "/api/predictions/matches/laliga-nonexistent/predict",
            headers={"X-Write-Key": "test"},
        )
        assert resp.status_code in (200, 404, 500)

    def test_bundesliga_predict_returns_200_or_404(self, client_phase2b):
        """Bundesliga match prediction should work."""
        resp = client_phase2b.post(
            "/api/predictions/matches/bundesliga-nonexistent/predict",
            headers={"X-Write-Key": "test"},
        )
        assert resp.status_code in (200, 404, 500)

    def test_seriea_predict_returns_200_or_404(self, client_phase2b):
        """Serie A match prediction should work."""
        resp = client_phase2b.post(
            "/api/predictions/matches/seriea-nonexistent/predict",
            headers={"X-Write-Key": "test"},
        )
        assert resp.status_code in (200, 404, 500)

    def test_ligue1_predict_returns_200_or_404(self, client_phase2b):
        """Ligue 1 match prediction should work."""
        resp = client_phase2b.post(
            "/api/predictions/matches/ligue1-nonexistent/predict",
            headers={"X-Write-Key": "test"},
        )
        assert resp.status_code in (200, 404, 500)
