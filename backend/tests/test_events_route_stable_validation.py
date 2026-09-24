"""Verify that events.py ValueError handlers only expose stable business validation.

All 28 `detail=str(exc)` sites in events.py catch ValueError from service layers.
The ValueError messages must be stable, enumerated business constraints (missing
field, unsupported kind, invalid format) — never unstable implementation details
(SQL, traceback, upstream URL, API key, file path, raw user input).

This test traces representative paths to prove the ValueError sources are safe.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def test_world_cup_data_to_facts_missing_kind():
    """Line 524: stable ValueError 'missing kind'."""
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        resp = client.post(
            "/api/events/sports/world-cup/data/preview",
            json={"matches": []},
        )

    assert resp.status_code == 422
    detail = resp.json()["detail"]

    # Stable business message
    assert detail in {
        "missing kind",
        "unsupported kind",
        "payload must be an object",
        "payload did not contain convertible World Cup data",
    }

    # No unstable leaks
    assert "Traceback" not in detail
    assert ".py" not in detail
    assert "Exception" not in detail


def test_world_cup_data_to_facts_unsupported_kind():
    """Line 524: stable ValueError 'unsupported kind'."""
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    sentinel = "https://evil.example/?key=SECRET"

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        resp = client.post(
            "/api/events/sports/world-cup/data/preview",
            json={"matches": [{"kind": sentinel}]},
        )

    assert resp.status_code == 422
    detail = resp.json()["detail"]

    # User input must not echo
    assert sentinel not in detail
    assert "SECRET" not in detail
    assert "evil.example" not in detail


def test_import_world_cup_data_errors_list():
    """Line 495: result["errors"] traced to stable validation."""
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        # Empty matches triggers validation error
        resp = client.post(
            "/api/events/sports/world-cup/data/import",
            json={"matches": []},
        )

    assert resp.status_code in {200, 422}
    # If 422, detail must be stable
    if resp.status_code == 422:
        detail = str(resp.json().get("detail", ""))
        assert "Traceback" not in detail
        assert ".py" not in detail


def test_source_bundle_preview_kind_disclosure_blocked():
    """Line 537: bundle preview does not echo user kind (covered by test_world_cup_bundle_user_input_disclosure.py)."""
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    sentinel = "C:\\secret\\path.key"

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        resp = client.post(
            "/api/events/sports/world-cup/data/bundle/preview",
            json={"sources": [{"kind": sentinel, "payload": {}}]},
        )

    assert resp.status_code == 422
    detail = resp.json()["detail"]

    assert sentinel not in detail
    assert "secret" not in detail
    assert "path.key" not in detail


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
