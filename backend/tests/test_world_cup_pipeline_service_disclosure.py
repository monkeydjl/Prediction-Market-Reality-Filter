"""Stage 2A tests: service-layer disclosure in world_cup_prediction_pipeline.py
and world_cup_ai_optimization_service.py (audit section 三 A / B / C).

Section A — line 730: `{"status":"error","error": f"Unsupported engine: {engine}"}`
  `engine` is typed `PredictionEngine = Literal[6 values]` at every HTTP
  entrypoint, so Pydantic rejects an out-of-set engine with 422 BEFORE the
  pipeline runs — line 730 is NOT reachable with user-controlled free-form
  input. Evidence:
    - test_http_rejects_unknown_engine_before_pipeline (422, boundary proof)
    - test_pipeline_result_echoes_engine_argument_directly (direct unit call
      shows the result dict echoes whatever `engine` string it is handed —
      RED, justifies fixing 730 as defence-in-depth so the public result dict
      is safe even for a future internal caller).

Section B — line 1471: `{"status":"error","error": f"Prediction failed: {e}"}`
  Raw exception text in the PUBLIC result dict. Every HTTP consumer sanitises
  it today (/predict maps non-"Match not found" to a fixed message; /batch
  rewrites each error to "Prediction generation failed"), but the service
  result itself is unsafe and is also logged raw at pipeline.py:1467. Per audit
  section 四.3 the public result type must be safe on its own. Evidence:
    - test_pipeline_result_dict_carries_raw_exception (RED).

Section C — ai_opt line 129: `f"AI优化失败: {error_type}"`, error_type =
  type(exc).__name__. GREEN: only the class name, never exception args/user
  input. Evidence: test_ai_optimization_emits_only_exception_type_name.

Token checks compute a bool before asserting; sentinels are module constants,
never parametrize args.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Section A: an engine value shaped like hostile input (only reachable by a
# direct unit call; HTTP validation rejects it first).
_A_URL = "https://" + "evil.example/engine?key=" + "SECRET_1" + "23&token=BEARER_XYZ"
_A_PATH = "C:\\" + "inject\\path.sql"
ENGINE_SENTINEL = f"{_A_URL} {_A_PATH}"

# Section B: a raw exception carrying a DSN-like secret.
_B_DSN = "postgresql://user:" + "PASSWORD_SEC" + "RET@db.example:5432/prod?key=" + "API_KEY_XYZ"
EXC_SENTINEL = f"Database error: {_B_DSN}"

FORBIDDEN_TOKENS = (
    "evil.example",
    "SECRET_1" + "23",
    "BEARER_XYZ",
    "inject\\path.sql",
    "postgresql://",
    "PASSWORD_SEC" + "RET",
    "db.example",
    "API_KEY_XYZ",
)

PIPE = "app.services.world_cup_prediction_pipeline"


def _leaks(text: str) -> bool:
    return any(tok in text for tok in FORBIDDEN_TOKENS)


# --- Section A ---------------------------------------------------------------


# (route, engine_binding): the /predict engine is a PredictionRequest body
# field; the /batch-predict engine is a Literal query param. Both reject an
# out-of-set value with 422 before run_prediction_pipeline runs.
@pytest.mark.parametrize(
    "route,as_query",
    [
        ("/api/world-cup/predictions/matches/m1/predict", False),
        ("/api/world-cup/predictions/batch-predict", True),
    ],
    ids=["predict_body", "batch_query"],
)
def test_http_rejects_unknown_engine_before_pipeline(route: str, as_query: bool):
    """Section A boundary proof: an out-of-Literal engine is a 422, not a 730 echo.

    `engine` is `PredictionEngine` (a Literal) at both entrypoints, so an unknown
    engine never reaches run_prediction_pipeline. Stays GREEN even before any
    fix — it documents that line 730 takes no user-controlled input.
    """
    from fastapi.testclient import TestClient

    from app.api.security import settings as security_settings
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    with patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        if as_query:
            resp = client.post(f"{route}?engine=totally_unknown_engine")
        else:
            resp = client.post(route, json={"engine": "totally_unknown_engine"})
    # Pydantic validation → 422 Unprocessable Entity, before the pipeline.
    assert resp.status_code == 422, f"{route}: expected 422 validation, got {resp.status_code}"


def test_pipeline_result_echoes_engine_argument_directly():
    """Section A (line 730): the result dict echoes the engine argument verbatim.

    RED (pre-fix): error == f"Unsupported engine: {engine}". Reached only by a
    direct call (HTTP validation blocks it), but the public result dict should
    not echo its argument regardless of who calls it.
    """
    from app.models.world_cup_prediction import MatchFixture

    fixture = MagicMock(spec=MatchFixture)
    fixture.match_id = "m1"
    fixture.home_team = "Brazil"
    fixture.away_team = "Argentina"
    fixture.status = "scheduled"
    fixture.kickoff_utc = None

    with patch(f"{PIPE}.get_prediction_session") as mock_session, \
         patch(f"{PIPE}._has_match_started", return_value=False), \
         patch(f"{PIPE}.get_elo_rating", new=AsyncMock(return_value={"rating": 2000})), \
         patch(f"{PIPE}.get_cached_odds", new=AsyncMock(return_value={"home_win": 2.0, "draw": 3.0, "away_win": 3.5})):
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = fixture
        mock_session.return_value = db

        from app.services.world_cup_prediction_pipeline import run_prediction_pipeline

        result = asyncio.run(
            run_prediction_pipeline(match_id="m1", engine=ENGINE_SENTINEL)
        )

    # Reaching line 730 (not an earlier skip/error) is required for this to mean
    # anything; assert we actually hit the unsupported-engine branch.
    assert result.get("status") == "error", f"did not reach engine check: {result.get('status')}"
    leaked = _leaks(str(result.get("error", "")))
    assert not leaked, "pipeline result echoed the engine argument (content withheld)"


# --- Section B ---------------------------------------------------------------


def test_pipeline_result_dict_carries_raw_exception():
    """Section B (line 1471): the public result dict must not carry raw exc text.

    RED (pre-fix): error == f"Prediction failed: {e}". An early query failure is
    the minimal robust trigger — the except at 1465 shapes the result.
    """
    import logging

    # pipeline.py:1467 logs the raw exception with exc_info=True. That is itself
    # a section-D disclosure surface, but pytest would capture the traceback and
    # print the sentinel; disable this logger for the call so the RED evidence
    # (the result dict) stays content-safe.
    pipe_logger = logging.getLogger("app.services.world_cup_prediction_pipeline")
    prev_disabled = pipe_logger.disabled
    pipe_logger.disabled = True
    try:
        with patch(f"{PIPE}.get_prediction_session") as mock_session:
            db = MagicMock()
            db.query.side_effect = Exception(EXC_SENTINEL)
            mock_session.return_value = db

            from app.services.world_cup_prediction_pipeline import run_prediction_pipeline

            result = asyncio.run(
                run_prediction_pipeline(match_id="m1", engine="elo_odds")
            )
    finally:
        pipe_logger.disabled = prev_disabled

    assert result.get("status") == "error"
    leaked = _leaks(str(result.get("error", "")))
    assert not leaked, "pipeline result dict carried the raw exception (content withheld)"


def test_pipeline_match_not_found_stays_safe():
    """Section B companion: the normal 'Match not found' path is unaffected."""
    with patch(f"{PIPE}.get_prediction_session") as mock_session:
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = None
        mock_session.return_value = db

        from app.services.world_cup_prediction_pipeline import run_prediction_pipeline

        result = asyncio.run(
            run_prediction_pipeline(match_id="m1", engine="elo_odds")
        )

    assert result.get("status") == "error"
    assert result.get("error") == "Match not found"


# --- Section C ---------------------------------------------------------------


def test_ai_optimization_emits_only_exception_type_name():
    """Section C (ai_opt line 129): message carries only type(exc).__name__.

    GREEN: even when the underlying gateway raises an exception whose args carry
    a sentinel, the returned message contains only the class name.
    """
    from app.services import world_cup_ai_optimization_service as svc

    class _SecretBearingError(Exception):
        pass

    current_prediction = {
        "predicted_score": {"home": 2.0, "away": 1.0},
        "outcome_probabilities": {"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
        "confidence": 0.68,
        "elo_ratings": None,
    }

    # Gate open (route configured) + force the real gateway call (complete_json)
    # to raise an exception whose text is the sentinel. Both are module-top
    # imports, so patch them on the service module.
    with patch.object(svc, "has_configured_llm_route", return_value=True), \
         patch.object(svc, "complete_json", new=AsyncMock(side_effect=_SecretBearingError(EXC_SENTINEL))):
        result = asyncio.run(
            svc.optimize_prediction_with_ai(
                home_team="Brazil",
                away_team="Argentina",
                current_prediction=current_prediction,
                prediction_method="elo_odds",
                match_context={},
            )
        )

    assert result.get("status") == "error"
    message = str(result.get("message", ""))
    leaked = _leaks(message)
    assert not leaked, "AI optimization message leaked exception content (content withheld)"
    # Positive assertion: it names the exception class, nothing more.
    assert "_SecretBearingError" in message


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
