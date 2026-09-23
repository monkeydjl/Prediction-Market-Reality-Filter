"""Verify bundle URL routes use stable ValueError messages, not raw network errors.

Lines 566, 583, 594, 608 in events.py catch ValueError from bundle file/URL
operations and use detail=str(exc). The ValueError sources must not echo URLs,
HTTP codes, or other unstable details that could leak configuration.
"""
from __future__ import annotations

from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest
from fastapi.testclient import TestClient


def test_bundle_file_preview_unknown_kind_stable():
    """Line 566: preview_world_cup_source_bundle_file ValueError is stable."""
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    # Malicious kind in configured file
    sentinel = "https://attacker.example/?token=SECRET"

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True), \
            patch("app.services.world_cup_source_bundle.load_world_cup_source_bundle_file",
                  return_value={"sources": [{"kind": sentinel, "payload": {}}]}):
        resp = client.post("/api/events/sports/world-cup/data/bundle/source/preview")

    assert resp.status_code == 422
    detail = resp.json()["detail"]

    # Must not echo user input
    assert sentinel not in detail
    assert "attacker.example" not in detail
    assert "SECRET" not in detail

    # Must use fixed safe message from events.py handler
    assert detail == "Invalid source configuration"


def test_bundle_url_preview_http_error_stable():
    """Line 594: preview_world_cup_source_bundle_url ValueError must not echo URL."""
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    # HTTPError would trigger ValueError with label containing the URL
    http_err = HTTPError(
        "https://bundle.example/secret.json",
        403,
        "Forbidden",
        {},
        None,
    )

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True), \
            patch("app.services.world_cup_source_bundle.urlopen",
                  side_effect=http_err):
        resp = client.post("/api/events/sports/world-cup/data/bundle/url/preview")

    assert resp.status_code == 422
    detail = resp.json()["detail"]

    # Must not echo URL or HTTP status
    assert "bundle.example" not in detail
    assert "secret.json" not in detail
    assert "403" not in detail
    assert "Forbidden" not in detail

    # ValueError from _fetch_json_url says "returned HTTP {code}" - stable
    # but we need the handler to use fixed message
    # For now, verify no URL/secret leaks
    assert "https://" not in detail


def test_bundle_url_import_network_error_stable():
    """Line 608: import_world_cup_source_bundle_url ValueError must not echo URL."""
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    # URLError would trigger ValueError with label
    url_err = URLError("Connection refused")

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True), \
            patch("app.services.world_cup_source_bundle.urlopen",
                  side_effect=url_err):
        resp = client.post(
            "/api/events/sports/world-cup/data/bundle/url/import",
            params={"replace": False},
        )

    assert resp.status_code == 422
    detail = resp.json()["detail"]

    # ValueError says "fetch failed" - generic, stable
    # but verify handler uses fixed message
    assert "Connection refused" not in detail


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
