# backend/tests/test_predictions_route.py
"""Tests for /api/predictions routes."""
import contextlib
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@contextlib.contextmanager
def _no_upstream_calls():
    """Keep the adapters off clubelo / The Odds API for the duration of a test.

    A predict for a match id that is not in the fixture table falls back to a
    stub identity whose teams are named literally "Home" and "Away", and
    ``fetch_elo_and_odds`` then asks the live upstreams to rate them.  Measured:
    the six league tests below spent 33.5s each -- 201.7s of this file's 203.9s,
    about 30% of the whole backend suite -- waiting out the 30s timeout in
    ``football_data_client``.  It also made them depend on a third-party API
    being reachable, which is what the ``# 500 acceptable if service
    unavailable`` comment used to be apologising for.

    The patch targets are the ones ``adapters/_shared.py`` documents in its own
    header: ``get_club_elo`` is bound at ``_shared`` module scope and must be
    patched there, while ``get_elo_rating`` / ``get_cached_odds`` are imported
    lazily inside the function body and so must be patched at their source
    modules.  Patching the wrong side of that distinction fails silently -- the
    call goes out over the network and the only symptom is a slow test.
    """
    with patch("app.sports.football.adapters._shared.get_club_elo", return_value=None), \
         patch("app.services.elo_ratings_service.get_elo_rating",
               new=AsyncMock(return_value=None)), \
         patch("app.services.odds_cache_service.get_cached_odds",
               new=AsyncMock(return_value=None)):
        yield


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
             patch.object(security_settings, "ALLOW_OPEN_WRITES", True), \
             _no_upstream_calls():
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
        # 500 would be an unhandled crash and must not pass, same as
        # test_predict_match_not_found above.  It was listed as acceptable "if
        # service unavailable", but fetch_elo_and_odds gathers with
        # return_exceptions=True and then swallows the lot, so an unreachable
        # upstream degrades to empty features and still answers 200 -- the one
        # thing the wider tuple allowed was the crash it was meant to catch.
        assert resp.status_code in (200, 404)

    def test_epl_predict_returns_200_or_404(self, client_phase2):
        """EPL match prediction should work (404 if fixture not in DB, not 500)."""
        resp = client_phase2.post(
            "/api/predictions/matches/epl-nonexistent/predict",
            headers={"X-Write-Key": "test"},
        )
        assert resp.status_code in (200, 404)

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
                 patch.object(security_settings, "ALLOW_OPEN_WRITES", True), \
                 _no_upstream_calls():
                client = TestClient(app)
                resp = client.post(
                    "/api/predictions/matches/ucl-nonexistent/predict",
                    headers={"X-Write-Key": "test"},
                )
                # Should still work (falls back to WorldCupAdapter, stub identity)
                assert resp.status_code in (200, 404)
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
             patch.object(security_settings, "ALLOW_OPEN_WRITES", True), \
             _no_upstream_calls():
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
        assert resp.status_code in (200, 404)

    def test_bundesliga_predict_returns_200_or_404(self, client_phase2b):
        """Bundesliga match prediction should work."""
        resp = client_phase2b.post(
            "/api/predictions/matches/bundesliga-nonexistent/predict",
            headers={"X-Write-Key": "test"},
        )
        assert resp.status_code in (200, 404)

    def test_seriea_predict_returns_200_or_404(self, client_phase2b):
        """Serie A match prediction should work."""
        resp = client_phase2b.post(
            "/api/predictions/matches/seriea-nonexistent/predict",
            headers={"X-Write-Key": "test"},
        )
        assert resp.status_code in (200, 404)

    def test_ligue1_predict_returns_200_or_404(self, client_phase2b):
        """Ligue 1 match prediction should work."""
        resp = client_phase2b.post(
            "/api/predictions/matches/ligue1-nonexistent/predict",
            headers={"X-Write-Key": "test"},
        )
        assert resp.status_code in (200, 404)


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


class TestConditionalCalibrationRoute:
    """POST /predictions/calibration/conditional (P1-V5).

    The route had no test at all — and no caller either, which is how it kept a
    missing kernel-flag guard that every neighbouring route has.
    """

    def _patched_learning(self, conf=None, stage=None):
        from unittest.mock import MagicMock, patch
        instance = MagicMock()
        instance.update_calibration_by_confidence.return_value = (
            conf if conf is not None else {"low": 0, "mid": 12, "high": 30}
        )
        instance.update_calibration_by_stage.return_value = (
            stage if stage is not None else {"regular": 25, "knockout": 0, "unknown": 0}
        )
        cls = MagicMock(return_value=instance)
        return patch("app.kernel.learning_service.KernelLearningService", cls), cls, instance

    def test_returns_both_bucket_maps_and_forwards_filters(self, client):
        patcher, _cls, instance = self._patched_learning()
        with patcher:
            resp = client.post(
                "/api/predictions/calibration/conditional",
                params={"competition": "epl", "engine": "elo_odds"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["competition"] == "epl"
        assert body["engine"] == "elo_odds"
        # Both fits must be reported: the confidence buckets alone would hide
        # whether the stage rows were written.
        assert body["confidence_buckets"] == {"low": 0, "mid": 12, "high": 30}
        assert body["stage_buckets"] == {"regular": 25, "knockout": 0, "unknown": 0}
        instance.update_calibration_by_confidence.assert_called_once_with("epl", "elo_odds")
        instance.update_calibration_by_stage.assert_called_once_with("epl", "elo_odds")

    def test_competition_is_required(self, client):
        resp = client.post("/api/predictions/calibration/conditional")
        assert resp.status_code == 422

    def test_returns_503_when_kernel_disabled_without_writing(self, client):
        """The guard must refuse *before* the fit runs.

        Writing calibration rows while KERNEL_PREDICTION_ENABLED is off produces
        rows that GET /calibration then refuses to read back.
        """
        from app.core import config
        from unittest.mock import patch
        patcher, cls, _instance = self._patched_learning()
        with patcher, patch.object(config.settings, "KERNEL_PREDICTION_ENABLED", False):
            resp = client.post(
                "/api/predictions/calibration/conditional",
                params={"competition": "epl"},
            )
        assert resp.status_code == 503
        cls.assert_not_called()

    def test_requires_the_operator_write_key(self):
        """A configured write key must be enforced on this mutation."""
        from app.main import app
        from app.core import config
        from app.api.security import settings as security_settings
        from unittest.mock import patch

        patcher, _cls, _instance = self._patched_learning()
        with patcher, \
             patch.object(config.settings, "KERNEL_PREDICTION_ENABLED", True), \
             patch.object(security_settings, "API_WRITE_KEY", "secret-key"), \
             patch.object(security_settings, "ALLOW_OPEN_WRITES", False):
            client = TestClient(app)
            denied = client.post(
                "/api/predictions/calibration/conditional",
                params={"competition": "epl"},
            )
            allowed = client.post(
                "/api/predictions/calibration/conditional",
                params={"competition": "epl"},
                headers={"X-API-Key": "secret-key"},
            )
        assert denied.status_code == 401
        assert allowed.status_code == 200, allowed.text
