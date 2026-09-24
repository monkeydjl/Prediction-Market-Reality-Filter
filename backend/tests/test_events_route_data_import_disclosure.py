"""Stage 2A RED test: events.py sink #4 — `detail=result["errors"]` (line 511).

Route: POST /api/events/sports/world-cup/data/import  (import_world_cup_data_source)

Same shape as sink #3: after the ValueError branch (sink #4a, line 509, covered
in test_events_route_remaining_sinks_disclosure.py), the handler returns HTTP 422
with `detail=result["errors"]` when nothing imported and errors exist. The errors
array can carry user-controlled field content.

Driven deterministically by patching `import_world_cup_data` to return an errors
array bearing a sentinel. Token checks compute a bool before asserting so the
sentinel never reaches a pytest failure report.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

_S_URL = "https://" + "evil.example/v3/fixtures/1?key=" + "SECRET_K" + "EY&token=BEARER_XYZ"
_S_PATH = "C:\\" + "inject\\path.sql"
SENTINEL = f"bad match {_S_URL} {_S_PATH}"

FORBIDDEN_TOKENS = ("evil.example", "SECRET_K" + "EY", "BEARER_XYZ", "inject\\path.sql")

ROUTE = "/api/events/sports/world-cup/data/import"


def _leaks(text: str) -> bool:
    return any(tok in text for tok in FORBIDDEN_TOKENS)


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def test_data_import_errors_array_does_not_echo_sentinel():
    """Sink #4 (line 511): the errors array must not surface user field content.

    RED (pre-fix): detail=result["errors"] returns raw per-fact error text.
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
            "app.api.routes.events.import_world_cup_data", return_value=injected
        ):
            resp = client.post(ROUTE, json={"matches": [{"match_id": "x"}]})

    assert resp.status_code == 422, resp.status_code
    leaked = _leaks(str(resp.json().get("detail", "")))
    assert not leaked, "data/import errors array echoed a forbidden token (content withheld)"


def test_data_import_success_normal_business():
    """Companion: a valid data payload imports (200) with no errors."""
    from app.api.security import settings as security_settings

    client = _client()
    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        resp = client.post(
            ROUTE,
            json={
                "matches": [
                    {
                        "match_id": "wc2026_001",
                        "home_team": "Brazil",
                        "away_team": "Argentina",
                        "kickoff_at": "2026-06-15T18:00:00Z",
                    }
                ]
            },
        )
    assert resp.status_code == 200
    result = resp.json()
    assert result["imported"] >= 1
    assert result["error_count"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
