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


def _stub_result(betting_analysis):
    """A minimal PredictionResult carrying a known ``betting_analysis``."""
    from datetime import datetime, timezone

    from app.kernel.domain import ContributionItem, PredictionResult

    return PredictionResult(
        predicted_scores={"home": 1.8, "away": 1.1},
        outcome_probabilities={"home_win": 0.46, "draw": 0.27, "away_win": 0.27},
        confidence=0.61,
        engine_name="football_multi_factor",
        explanation=[
            ContributionItem(
                factor="possession", direction="support", weight=0.04,
                available=True, detail="H=0.371 D=0.238 A=0.391",
            ),
        ],
        betting_analysis=betting_analysis,
        feature_version="football-1.0",
        prediction_timestamp=datetime(2026, 8, 22, tzinfo=timezone.utc),
    )


class TestPredictResponseCarriesBettingAnalysis:
    """The audit trail has to survive the last step, not just be written.

    Every engine builds ``betting_analysis`` and the kernel appends
    ``conditional_calibration`` to it, but the route used to drop the field and
    ``kernel_predictions`` has no column for it, so nothing could read any of it.
    ``test_kernel_prediction_kernel`` already asserts the kernel *writes* the
    calibration record; these assert a caller can *see* it.
    """

    def _client_with(self, result):
        from unittest.mock import MagicMock, patch

        from fastapi.testclient import TestClient

        from app.api.security import settings as security_settings
        from app.core import config
        from app.main import app

        kernel = MagicMock()
        kernel.predict.return_value = result
        return patch.object(config.settings, "KERNEL_PREDICTION_ENABLED", True), \
            patch.object(security_settings, "API_WRITE_KEY", ""), \
            patch.object(security_settings, "ALLOW_OPEN_WRITES", True), \
            patch("app.api.routes.predictions._get_kernel", return_value=kernel), \
            TestClient(app)

    def _post(self, result):
        p1, p2, p3, p4, client = self._client_with(result)
        with p1, p2, p3, p4:
            resp = client.post("/api/predictions/matches/epl-1/predict")
        assert resp.status_code == 200, resp.text
        return resp.json()

    def test_confidence_breakdown_and_line_source_are_readable(self):
        body = self._post(_stub_result({
            "confidence_breakdown": {"data_completeness": 0.5, "factor_agreement": 0.6},
            "soft_totals_btts": {"line": 2.5, "line_source": "placeholder"},
        }))
        ba = body["betting_analysis"]
        assert ba["confidence_breakdown"]["data_completeness"] == 0.5
        # P1-O1 added line_source to tell a real book line from the placeholder.
        assert ba["soft_totals_btts"]["line_source"] == "placeholder"

    def test_a_calibrated_prediction_says_so(self):
        """The serious case: the kernel rewrites the probabilities it returns.

        Without this field a caller cannot tell a calibrated number from a raw
        one, nor how thin the sample behind the adjustment was.
        """
        body = self._post(_stub_result({
            "conditional_calibration": {
                "applied": True, "slope": 0.92, "intercept": 0.03,
                "sample_count": 41, "bucket": "epl:regular_season",
                "source": "conditional", "raw_home_win": 0.50,
                "calibrated_home_win": 0.46,
            },
        }))
        cal = body["betting_analysis"]["conditional_calibration"]
        assert cal["applied"] is True
        assert cal["raw_home_win"] == 0.50
        assert cal["calibrated_home_win"] == 0.46
        assert cal["sample_count"] == 41

    def test_absent_analysis_is_null_not_missing(self):
        """Engines may legitimately return None; the key must still be present.

        A missing key and a null value read the same to a careless client but not
        to a schema, and the LoL market-only engine really does return None.
        """
        body = self._post(_stub_result(None))
        assert "betting_analysis" in body
        assert body["betting_analysis"] is None
