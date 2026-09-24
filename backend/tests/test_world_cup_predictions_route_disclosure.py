"""Stage 2A RED tests: world_cup_predictions.py sinks #33 and #34.

Route: POST /api/world-cup/predictions/matches/{match_id}/optimize
       (optimize_match_prediction)

  - line 986: `detail=optimization_result.get("message")` when status == "error"   -> 500
  - line 989: `detail=optimization_result.get("message")` when status == "unavailable" -> 503

`optimization_result` comes from `optimize_prediction_with_ai`. The route
forwards its `message` verbatim into the HTTP detail with no sanitisation — that
is the boundary defect. (The only production PRODUCER of that message today,
world_cup_ai_optimization_service.py:129, emits `type(exc).__name__`, i.e. safe
content; so this proves the boundary is unguarded, not that production currently
leaks. See the service-layer test for the producer side.)

The route imports the service function inside the handler, so it is patched at
its source module (re-bound at call time). Token checks compute a bool before
asserting; the sentinel is a module constant, never a parametrize arg.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

_S_URL = "https://" + "evil.example/llm?key=" + "SECRET_1" + "23&auth=BEARER_XYZ"
_S_PATH = "C:\\" + "path\\to\\model.sql"
SENTINEL = f"AI optimization failed {_S_URL} {_S_PATH}"

FORBIDDEN_TOKENS = ("evil.example", "SECRET_1" + "23", "BEARER_XYZ", "path\\to\\model.sql")

ROUTE = "/api/world-cup/predictions/matches/test_match_001/optimize"
SERVICE_FN = "app.services.world_cup_ai_optimization_service.optimize_prediction_with_ai"


def _leaks(text: str) -> bool:
    return any(tok in text for tok in FORBIDDEN_TOKENS)


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def _mock_fixture_and_prediction():
    from app.models.world_cup_prediction import MatchFixture, MatchPrediction

    fixture = MagicMock(spec=MatchFixture)
    fixture.match_id = "test_match_001"
    fixture.home_team = "Brazil"
    fixture.away_team = "Argentina"
    fixture.status = "scheduled"
    fixture.stage = "Group Stage"
    fixture.group = "A"
    fixture.venue = "Stadium"

    prediction = MagicMock(spec=MatchPrediction)
    prediction.match_id = "test_match_001"
    prediction.predicted_home_score = 2.0
    prediction.predicted_away_score = 1.0
    prediction.home_win_prob = 0.55
    prediction.draw_prob = 0.25
    prediction.away_win_prob = 0.20
    prediction.confidence = 0.68
    prediction.prediction_method = "elo_odds"
    prediction.factors = {}
    prediction.key_factors = []
    return fixture, prediction


def _call_optimize(service_return: dict):
    """Drive the optimize route with the AI service returning `service_return`.

    Returns the response. The service mock is asserted to have been called so a
    silently-ineffective patch cannot masquerade as evidence.
    """
    from app.api.security import settings as security_settings

    fixture, prediction = _mock_fixture_and_prediction()
    client = _client()
    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        with patch(
            "app.api.routes.world_cup_predictions.get_prediction_session"
        ) as mock_session:
            mock_db = MagicMock()
            mock_db.query.return_value.filter_by.return_value.first.side_effect = [
                fixture,
                prediction,
            ]
            mock_session.return_value = mock_db
            with patch(SERVICE_FN, return_value=service_return) as mock_ai:
                resp = client.post(ROUTE)
            # Guard against a no-op patch: the sink is only reached if the AI
            # service was actually invoked with the mocked result.
            assert mock_ai.called, "AI service was not called; route diverged before the sink"
    return resp


@pytest.mark.parametrize(
    "status,expected_code",
    [("error", 500), ("unavailable", 503)],
    ids=["sink33_error_500", "sink34_unavailable_503"],
)
def test_optimize_forwards_service_message_unsanitised(status: str, expected_code: int):
    """Sinks #33/#34: the service `message` must not be forwarded verbatim.

    RED (pre-fix): detail = optimization_result["message"] (raw).
    GREEN (post-fix): detail is a fixed message; no token surfaces; code preserved.
    """
    resp = _call_optimize({"status": status, "message": SENTINEL})
    assert resp.status_code == expected_code, resp.status_code
    # HTTPException detail is rendered into the JSON body; check the whole body
    # text so a str OR structured detail is covered.
    leaked = _leaks(resp.text)
    assert not leaked, f"optimize forwarded the service message for status={status} (content withheld)"


def test_optimize_success_returns_ok():
    """Companion: an 'optimized' result returns 200 with the optimization body."""
    resp = _call_optimize(
        {"status": "optimized", "optimization": {"suggested_adjustments": [], "confidence_boost": 0.05}}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["match_id"] == "test_match_001"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
