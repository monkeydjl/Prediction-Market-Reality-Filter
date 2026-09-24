"""Stage 2A RED test: events.py sink #3 — `detail=result["errors"]` (line 495).

Route: POST /api/events/sports/world-cup/facts/import  (import_world_cup_facts)

The handler, after catching ValueError separately (sink #1, line 493, covered in
test_events_route_remaining_sinks_disclosure.py), returns HTTP 422 with
`detail=result["errors"]` when `imported == 0 and error_count > 0`. Each error
entry is `{"index": i, "error": str(exc)}` built in sports_fact_service from the
per-fact normalizer, so a user-controlled field value can ride inside it.

This drives the sink deterministically by patching `import_sports_facts` to
RETURN an errors array carrying a sentinel (the sink is a return path, not an
exception path). Forbidden-token checks compute a bool before asserting so the
sentinel never lands in a pytest failure report or diff.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

_S_URL = "https://" + "evil.example/f?key=" + "SECRET_K" + "EY_9&token=BEARER_ABC"
_S_PATH = "C:\\" + "paths\\file.sql"
SENTINEL = f"unsupported fact {_S_URL} {_S_PATH}"

FORBIDDEN_TOKENS = ("evil.example", "SECRET_K" + "EY", "BEARER_ABC", "paths\\file.sql")

ROUTE = "/api/events/sports/world-cup/facts/import"


def _leaks(text: str) -> bool:
    return any(tok in text for tok in FORBIDDEN_TOKENS)


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def test_facts_import_errors_array_does_not_echo_sentinel():
    """Sink #3 (line 495): the errors array must not surface user field content.

    RED (pre-fix): detail=result["errors"] returns the raw per-fact error text.
    GREEN (post-fix): detail carries counts/indices, not error strings.
    """
    from app.api.security import settings as security_settings

    injected = {
        "imported": 0,
        "error_count": 1,
        "errors": [{"index": 0, "error": SENTINEL}],
    }
    client = _client()
    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        with patch(
            "app.api.routes.events.import_sports_facts", return_value=injected
        ):
            resp = client.post(ROUTE, json={"facts": [{"kind": "x"}]})

    assert resp.status_code == 422, resp.status_code
    leaked = _leaks(str(resp.json().get("detail", "")))
    assert not leaked, "facts/import errors array echoed a forbidden token (content withheld)"


def test_facts_import_success_normal_business():
    """Companion: a valid fact imports (200) and reports no errors."""
    from app.api.security import settings as security_settings

    client = _client()
    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        resp = client.post(
            ROUTE,
            json={"facts": [{"kind": "injury", "team": "Brazil", "player": "Neymar"}]},
        )
    assert resp.status_code == 200
    result = resp.json()
    assert result["imported"] >= 1
    assert result["error_count"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
