# backend/tests/test_sports_api_type_contract.py
"""Pin the prediction response against the frontend interface that reads it.

CI's ``Type Sync Check`` runs ``scripts.generate_types --check``, which covers
only the 14 Pydantic root models in ``app/models/_frontend_export.py`` — the
events domain. Every sports route returns a bare ``dict[str, Any]``, so
``frontend/src/lib/sports-api/types.ts`` is a hand-maintained mirror with no
checker at all on either side.

That gap is how ``betting_analysis`` came to be declared on ``PredictionResult``
and never sent: nine engines built it, the kernel appended its calibration audit
to it, three panels read it, and the route omitted it. TypeScript could not
complain, because an always-absent ``?:`` field is indistinguishable at the type
level from a legitimately-sometimes-absent one, and each consumer had a silent
fallback.

So these tests compare the *actual* response keys against the field list parsed
out of the ``.ts`` file. Reading a frontend file from a backend test follows
``test_generate_types.py``, which resolves ``frontend/src/lib`` the same way; no
Node is involved here, only text.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_TYPES_TS = _REPO_ROOT / "frontend" / "src" / "lib" / "sports-api" / "types.ts"

# Fields the POST response carries that the persisted row cannot, each with the
# reason it is absent rather than forgotten. Asserted to be *exactly* the
# difference, so a newly-dropped field cannot hide behind this list.
_GET_PATH_EXEMPT = {
    # `kernel_predictions` keys the row by match, so the serializer takes the
    # match id from the caller's URL instead of repeating it in the body.
    "match_id",
    # `kernel_predictions` has no `betting_analysis` column and kernel tables
    # have no ALTER TABLE path in this repo, so the stored row cannot carry it.
    "betting_analysis",
}


def _interface_fields(name: str) -> tuple[set[str], set[str]]:
    """Return (required, optional) field names of a top-level TS interface.

    Only fields at the interface's own indentation level count; nested object
    literals are indented further and are not part of this contract.
    """
    src = _TYPES_TS.read_text(encoding="utf-8")
    start = re.search(rf"export interface {name}[^{{]*\{{", src)
    assert start is not None, f"{name} not found in {_TYPES_TS}"
    rest = src[start.end():]
    end = rest.index("\n}")
    body = rest[:end]

    required = set(re.findall(r"^ {2}(\w+):", body, re.M))
    optional = set(re.findall(r"^ {2}(\w+)\?:", body, re.M))
    assert required or optional, f"parsed no fields out of {name}"
    return required, optional


def _post_predict_keys() -> set[str]:
    """Keys of the real ``POST /predictions/matches/{id}/predict`` response."""
    from datetime import datetime, timezone
    from unittest.mock import MagicMock, patch

    from fastapi.testclient import TestClient

    from app.api.security import settings as security_settings
    from app.core import config
    from app.kernel.domain import PredictionResult
    from app.main import app

    kernel = MagicMock()
    kernel.predict.return_value = PredictionResult(
        predicted_scores={"home": 1.4, "away": 1.1},
        outcome_probabilities={"home_win": 0.44, "draw": 0.29, "away_win": 0.27},
        confidence=0.58,
        engine_name="elo_odds",
        explanation=[],
        betting_analysis={"confidence_breakdown": {}},
        feature_version="football-1.0",
        prediction_timestamp=datetime(2026, 8, 22, tzinfo=timezone.utc),
    )
    with patch.object(config.settings, "KERNEL_PREDICTION_ENABLED", True), \
            patch.object(security_settings, "API_WRITE_KEY", ""), \
            patch.object(security_settings, "ALLOW_OPEN_WRITES", True), \
            patch("app.api.routes.predictions._get_kernel", return_value=kernel):
        resp = TestClient(app).post("/api/predictions/matches/epl-1/predict")
    assert resp.status_code == 200, resp.text
    return set(resp.json())


def _stored_prediction_keys() -> set[str]:
    """Keys of the persisted-row serializer behind ``GET /matches/{id}``."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.api.routes.predictions import _prediction_to_dict

    row = SimpleNamespace(
        engine="elo_odds",
        predicted_scores={"home": 1.4, "away": 1.1},
        outcome_probabilities={"home_win": 0.44, "draw": 0.29, "away_win": 0.27},
        confidence=0.58,
        explanation="[]",
        feature_version="football-1.0",
        created_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
    )
    return set(_prediction_to_dict(row))  # type: ignore[arg-type]


class TestPredictionResultContract:
    def test_the_parse_actually_finds_the_fields(self):
        """Guard against the whole suite passing on an empty field set.

        A regex that silently stops matching would make every assertion below
        vacuously true, which is the failure mode this file exists to prevent.
        """
        required, optional = _interface_fields("PredictionResult")
        assert "engine" in required
        assert "outcome_probabilities" in required
        assert {"match_id", "betting_analysis"} <= optional

    def test_post_response_carries_every_declared_field(self):
        """The POST response is the complete surface the interface describes."""
        required, optional = _interface_fields("PredictionResult")
        assert _post_predict_keys() == required | optional

    def test_stored_row_lacks_exactly_the_fields_it_cannot_hold(self):
        """The GET path is allowed to be narrower — but only by known fields.

        Equality, not a subset check: a field quietly dropped from the
        serializer fails here instead of becoming a second silent gap.
        """
        required, _ = _interface_fields("PredictionResult")
        keys = _stored_prediction_keys()
        assert keys == required
        assert (required | _GET_PATH_EXEMPT) - keys == _GET_PATH_EXEMPT

    @pytest.mark.parametrize("field", sorted(_GET_PATH_EXEMPT))
    def test_every_exemption_is_a_real_interface_field(self, field):
        """An exemption for a field that no longer exists hides a rename."""
        required, optional = _interface_fields("PredictionResult")
        assert field in required | optional
