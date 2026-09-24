"""Verify bundle feeds routes reject URLError/HTTPError without echoing URLs.

Lines 619, 633, 646, 661 in events.py catch ValueError from bundle feed
operations and use detail=str(exc). The ValueError sources must not echo URLs,
HTTP codes, or other unstable details that could leak configuration.
"""
from __future__ import annotations

from unittest.mock import patch
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient


def test_preview_api_football_http_error_stable():
    """Line 646: preview_api_football_world_cup_source_bundle_route ValueError must not echo URL."""
    from app.api.security import settings as security_settings
    from app.core.config import settings as app_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    sentinel_url = "https://api.api-football.com/v3/fixtures?league=SECRET_LEAGUE&season=2026&key=SECRET_API_KEY"

    # Mock configuration values to trigger actual fetch
    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True), \
            patch.object(app_settings, "WORLD_CUP_API_FOOTBALL_API_KEY", "test_key"), \
            patch.object(app_settings, "WORLD_CUP_API_FOOTBALL_BASE_URL", "https://api.api-football.com/v3"), \
            patch.object(app_settings, "WORLD_CUP_API_FOOTBALL_LEAGUE_ID", "SECRET_LEAGUE"), \
            patch.object(app_settings, "WORLD_CUP_API_FOOTBALL_SEASON", "2026"), \
            patch("app.services.world_cup_api_football_source.urlopen",
                  side_effect=HTTPError(sentinel_url, 403, "Forbidden", {}, None)):
        resp = client.post("/api/events/sports/world-cup/data/bundle/api-football/preview")

    assert resp.status_code == 422
    detail = resp.json()["detail"]

    # Current ValueError says "API-Football returned HTTP 403"
    # Must not echo URL or query params
    assert "SECRET_LEAGUE" not in detail
    assert "SECRET_API_KEY" not in detail
    assert "api.api-football.com" not in detail
    assert "fixtures" not in detail


def test_import_api_football_http_error_stable():
    """Line 661: import_api_football_world_cup_source_bundle_route ValueError must not echo URL."""
    from app.api.security import settings as security_settings
    from app.core.config import settings as app_settings
    from app.main import app
    from app.memory import loop_run_store

    client = TestClient(app, raise_server_exceptions=False)

    sentinel_url = "https://api.api-football.com/v3/standings?league=PROD_LEAGUE&season=2026&key=PRODUCTION_KEY"

    # Mock successful validation run (required before import)
    validation_result = {
        "status": "success",
        "result": {"ok": True},
    }
    with patch.object(loop_run_store, "last_run", return_value=validation_result):
        with patch.object(security_settings, "ALLOW_OPEN_WRITES", True), \
                patch.object(app_settings, "WORLD_CUP_API_FOOTBALL_API_KEY", "prod_key"), \
                patch.object(app_settings, "WORLD_CUP_API_FOOTBALL_BASE_URL", "https://api.api-football.com/v3"), \
                patch.object(app_settings, "WORLD_CUP_API_FOOTBALL_LEAGUE_ID", "PROD_LEAGUE"), \
                patch.object(app_settings, "WORLD_CUP_API_FOOTBALL_SEASON", "2026"), \
                patch("app.services.world_cup_api_football_source.urlopen",
                      side_effect=HTTPError(sentinel_url, 500, "Internal Server Error", {}, None)):
            resp = client.post(
                "/api/events/sports/world-cup/data/bundle/api-football/import",
                params={"replace": False},
            )

    assert resp.status_code == 422
    detail = resp.json()["detail"]

    # Current ValueError says "API-Football returned HTTP 500"
    # Must not echo URL or query params
    assert "PROD_LEAGUE" not in detail
    assert "PRODUCTION_KEY" not in detail
    assert "api.api-football.com" not in detail
    assert "standings" not in detail


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
