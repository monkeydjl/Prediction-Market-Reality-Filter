"""Verify bundle preview does not echo user-controlled kind in HTTP responses.

User request specifies bundle preview must block disclosure of:
- URL, query, fragment
- Paths, SQL
- API key, ticket, Authorization
- Raw exception text

This test reproduces the reported issue: _source_kind() puts the raw kind into
ValueError, events.py uses detail=str(exc), so malicious kind values echo.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def test_bundle_preview_rejects_unknown_kind_without_echoing():
    """POST /api/events/sports/world-cup/data/bundle/preview must not echo kind.

    Before fix: detail contained the full sentinel URL.
    After fix: detail uses fixed safe message.
    """
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    # Sentinel carrying all forbidden patterns
    sentinel = (
        "https://evil.example/path?key=SECRET&auth=BEARER_TOKEN"
        "#fragment=C:\\paths\\file.sql"
    )

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        resp = client.post(
            "/api/events/sports/world-cup/data/bundle/preview",
            json={"sources": [{"kind": sentinel, "payload": {}}]},
        )

    assert resp.status_code == 422
    body = resp.json()
    detail = body["detail"]

    # Must not echo user input
    assert sentinel not in detail
    assert sentinel not in str(body)

    # Must not leak any forbidden pattern
    assert "evil.example" not in detail
    assert "SECRET" not in detail
    assert "BEARER_TOKEN" not in detail
    assert "C:\\" not in detail
    assert ".sql" not in detail
    assert "fragment" not in detail

    # Must not include raw ValueError text that could echo index/position
    assert "sources[0]" not in detail

    # Must use fixed safe message
    assert detail == "Invalid source configuration"


def test_bundle_preview_rejects_empty_sources_without_echoing_internal_validation():
    """An empty bundle remains invalid under the stable source-bundle contract."""
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        resp = client.post(
            "/api/events/sports/world-cup/data/bundle/preview",
            json={"sources": []},
        )

    assert resp.status_code == 422
    body = resp.json()
    detail = body["detail"]
    assert detail == "Invalid source configuration"
    assert "at least one source" not in str(body)


def test_bundle_import_rejects_unknown_kind_without_echoing():
    """POST /api/events/sports/world-cup/data/bundle/import has same contract."""
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    sentinel = "sql://user:password@host/db?table=DROP%20TABLE"

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        resp = client.post(
            "/api/events/sports/world-cup/data/bundle/import",
            json={"sources": [{"kind": sentinel, "payload": {}}]},
        )

    assert resp.status_code == 422
    detail = resp.json()["detail"]

    assert sentinel not in detail
    assert "password" not in detail
    assert "DROP" not in detail
    assert detail == "Invalid source configuration"


def test_statistics_preview_rejects_unknown_kind_without_echoing():
    """Statistics source has same user input path."""
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    sentinel = "/etc/passwd?token=ADMIN_KEY"

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        resp = client.post(
            "/api/events/sports/world-cup/statistics/preview",
            json={"kind": sentinel, "payload": {}},
        )

    assert resp.status_code == 422
    detail = resp.json()["detail"]

    assert sentinel not in detail
    assert "/etc/passwd" not in detail
    assert "ADMIN_KEY" not in detail
    assert detail == "Invalid source configuration"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
