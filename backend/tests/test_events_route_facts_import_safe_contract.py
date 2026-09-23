"""Regression contract for safe World Cup facts-import validation errors."""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient


ROUTE = "/api/events/sports/world-cup/facts/import"
_SENTINEL = "https://evil.example/v3?api_key=SECRET_KEY&ticket=BEARER_TOKEN C:\\secrets\\facts.sql"
_FORBIDDEN = ("evil.example", "SECRET_KEY", "BEARER_TOKEN", "facts.sql")


def test_facts_import_keeps_error_list_and_sanitizes_user_kind() -> None:
    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        response = client.post(ROUTE, json={"facts": [{"kind": _SENTINEL}]})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert len(detail) == 1
    assert detail[0]["index"] == 0
    leaked = any(token in str(detail) for token in _FORBIDDEN)
    assert not leaked, "facts validation detail echoed a forbidden token (content withheld)"
    assert detail[0]["error"] == "unsupported kind"