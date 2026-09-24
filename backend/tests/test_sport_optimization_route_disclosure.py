"""Stage 2A RED tests: sport_optimization.py disclosure sinks.

HTTP sink #32 (line 246): `detail=str(e)` in apply_params — a ValueError from
OptimizedParamsStore.apply reaches the client.

User-input reflection sinks (missed by the earlier inventory):
  - line 83  `detail=f"Unsupported sport: {sport}"`      (POST /backfill-seed)
  - line 87  `detail=f"...unsupported sport: {sport}"`    (POST /backfill-seed)
  - line 116 `detail=f"Unsupported sport: {sport}"`      (POST /run)
`sport` is `request.sport`, a free-form string on the request body, echoed
verbatim. That reflects arbitrary user input into the error message.

All routes are gated by PHASE9_ACCURACY_SPRINT_ENABLED (503 when false), so the
flag is enabled for the duration. Token checks compute a bool before asserting.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

BASE = "/api/sport-optimization"

# Sink #32 sentinel rides inside a ValueError raised by the store.
_S_URL = "https://" + "evil.example/task?key=" + "SECRET_1" + "23&token=BEARER_XYZ"
_S_PATH = "C:\\" + "path\\to\\file.sql"
APPLY_SENTINEL = f"Params not found: {_S_URL} {_S_PATH}"

# Reflection sinks echo request.sport. A hostile "sport" value carries tokens.
SPORT_SENTINEL = "https://" + "evil.example/x?key=" + "SECRET_1" + "23;C:\\p\\q.sql"

FORBIDDEN_TOKENS = ("evil.example", "SECRET_1" + "23", "BEARER_XYZ", "path\\to\\file.sql", "\\p\\q.sql")


def _leaks(text: str) -> bool:
    return any(tok in text for tok in FORBIDDEN_TOKENS)


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def test_apply_params_valueerror_does_not_leak():
    """Sink #32 (line 246): ValueError text must not reach the 404 detail."""
    from app.api.security import settings as security_settings
    from app.core.config import settings

    client = _client()
    with patch.object(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True):
        with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
            with patch(
                "app.kernel.optimized_params_store.OptimizedParamsStore"
            ) as mock_store_cls:
                mock_store = MagicMock()
                mock_store.apply.side_effect = ValueError(APPLY_SENTINEL)
                mock_store_cls.return_value = mock_store
                resp = client.post(f"{BASE}/apply/999999")

    assert resp.status_code == 404, resp.status_code
    leaked = _leaks(str(resp.json().get("detail", "")))
    assert not leaked, "apply/{id} echoed the ValueError text (content withheld)"


def test_apply_params_success_business_case():
    """Sink #32 companion: a valid apply returns 200 with the store result."""
    from app.api.security import settings as security_settings
    from app.core.config import settings

    client = _client()
    with patch.object(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True):
        with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
            with patch(
                "app.kernel.optimized_params_store.OptimizedParamsStore"
            ) as mock_store_cls:
                mock_store = MagicMock()
                mock_store.apply.return_value = {"params_id": 1, "applied": True}
                mock_store_cls.return_value = mock_store
                resp = client.post(f"{BASE}/apply/1")

    assert resp.status_code == 200
    assert resp.json()["params_id"] == 1


# Only line/route/flags are parametrized. The sentinel sport value is injected
# inside the test body from a module constant, NEVER through a parametrize arg —
# pytest prints parametrize args verbatim, which would leak the sentinel.
@pytest.mark.parametrize(
    "line,route,extra",
    [
        (83, "/backfill-seed", {"backfill": True, "seed_elo": False}),
        (87, "/backfill-seed", {"backfill": False, "seed_elo": True}),
        (116, "/run", {"n_trials": 1}),
    ],
    ids=["line83_backfill", "line87_seed_elo", "line116_run"],
)
def test_unsupported_sport_does_not_reflect_user_input(line: int, route: str, extra: dict):
    """Reflection sinks: an unsupported sport must not echo the raw sport value.

    RED (pre-fix): detail=f"Unsupported sport: {sport}" returns the user string.
    GREEN (post-fix): the invalid value is not reflected verbatim.
    """
    from app.api.security import settings as security_settings
    from app.core.config import settings

    body = {"sport": SPORT_SENTINEL, **extra}
    client = _client()
    with patch.object(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True):
        with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
            resp = client.post(f"{BASE}{route}", json=body)

    assert resp.status_code == 400, f"line {line}: status {resp.status_code}"
    leaked = _leaks(str(resp.json().get("detail", "")))
    assert not leaked, f"line {line} ({route}) reflected the raw sport value (content withheld)"


def test_run_valid_sport_business_case():
    """Reflection companion: a supported sport is accepted (not rejected as 400).

    The task is scheduled in a worker thread; the route returns a task envelope.
    """
    from app.api.security import settings as security_settings
    from app.core.config import settings

    client = _client()
    with patch.object(settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True):
        with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
            with patch("app.services.optimization_task_manager.get_task_manager") as mgr, \
                 patch("app.utils.background_tasks.spawn"):
                mgr.return_value = MagicMock(
                    create_task=MagicMock(return_value={"task_id": "t1", "status": "queued"})
                )
                resp = client.post(f"{BASE}/run", json={"sport": "nba", "n_trials": 1})

    # Must NOT be the 400 unsupported-sport path; a valid sport passes the guard.
    assert resp.status_code != 400, resp.status_code


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
