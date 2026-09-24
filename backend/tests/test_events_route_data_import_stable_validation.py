"""Verify data import routes expose only stable business validation in detail.

The /sports/world-cup/facts/import and /sports/world-cup/data/import routes
catch ValueError and expose detail=str(exc). The ValueError sources must be
stable business constraints (missing field, unsupported kind, invalid format)
— never unstable implementation details (SQL, traceback, upstream URL, file
path, raw user input).

This test traces the ValueError sources from normalize_sports_fact(),
world_cup_data_to_facts(), and their helpers to prove all messages are stable
enumerated business rules.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def test_facts_import_missing_kind():
    """Line 495: import_sports_facts returns errors list with stable 'missing kind'."""
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        resp = client.post(
            "/api/events/sports/world-cup/facts/import",
            json={"facts": [{}]},
        )

    assert resp.status_code == 422
    detail = resp.json()["detail"]

    # detail is errors list from import_sports_facts (line 495)
    assert isinstance(detail, list)
    assert len(detail) == 1
    assert detail[0]["error"] == "missing kind"

    # No unstable leaks
    detail_str = str(detail)
    assert "Traceback" not in detail_str
    assert ".py" not in detail_str


def test_facts_import_unsupported_kind():
    """Line 495: import_sports_facts returns errors list with stable 'unsupported kind'."""
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        resp = client.post(
            "/api/events/sports/world-cup/facts/import",
            json={"facts": [{"kind": "unknown_kind"}]},
        )

    assert resp.status_code == 422
    detail = resp.json()["detail"]

    # detail is errors list from import_sports_facts (line 495)
    assert isinstance(detail, list)
    assert len(detail) == 1
    assert "unsupported kind" in detail[0]["error"]

    # User-controlled kind values must never be echoed.
    assert detail[0]["error"] == "unsupported kind"
    assert "unknown_kind" not in detail[0]["error"]

    # No unstable leaks
    detail_str = str(detail)
    assert "Traceback" not in detail_str
    assert ".py" not in detail_str


def test_facts_import_missing_tournament():
    """Line 495: import_sports_facts with empty tournament uses default, imports successfully."""
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        resp = client.post(
            "/api/events/sports/world-cup/facts/import",
            json={"facts": [{"kind": "injury", "tournament": ""}]},
        )

    # Empty tournament falls back to default_tournament='world-cup-2026'
    assert resp.status_code == 200
    result = resp.json()
    assert result["imported"] == 1
    assert result["error_count"] == 0


def test_data_import_payload_not_object():
    """Line 509: world_cup_data_to_facts raises stable 'payload must be an object'.

    FastAPI validates DictPayload type before handler, so list body triggers
    Pydantic validation error, not the service ValueError.
    """
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        resp = client.post(
            "/api/events/sports/world-cup/data/import",
            json=[],
        )

    assert resp.status_code == 422
    detail = resp.json()["detail"]

    # FastAPI/Pydantic validation error - list is not DictPayload
    assert isinstance(detail, list)
    assert detail[0]["type"] == "dict_type"


def test_data_import_no_convertible_data():
    """Line 509: world_cup_data_to_facts raises stable 'payload did not contain convertible World Cup data'."""
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        resp = client.post(
            "/api/events/sports/world-cup/data/import",
            json={},
        )

    assert resp.status_code == 422
    detail = resp.json()["detail"]

    # Stable business message
    assert detail == "payload did not contain convertible World Cup data"


def test_data_import_matches_not_list():
    """Line 509: _require_list raises stable 'matches must be a list'."""
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        resp = client.post(
            "/api/events/sports/world-cup/data/import",
            json={"matches": "not a list"},
        )

    assert resp.status_code == 422
    detail = resp.json()["detail"]

    # Stable business message
    assert detail == "matches must be a list"


def test_data_import_match_not_object():
    """Line 509: _match_facts raises stable 'matches[0] must be an object'."""
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        resp = client.post(
            "/api/events/sports/world-cup/data/import",
            json={"matches": ["not an object"]},
        )

    assert resp.status_code == 422
    detail = resp.json()["detail"]

    # Stable business message
    assert detail == "matches[0] must be an object"


def test_data_import_missing_match_id():
    """Line 509: _match_facts raises stable 'matches[0] missing match_id'."""
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        resp = client.post(
            "/api/events/sports/world-cup/data/import",
            json={"matches": [{}]},
        )

    assert resp.status_code == 422
    detail = resp.json()["detail"]

    # Stable business message
    assert detail == "matches[0] missing match_id"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
