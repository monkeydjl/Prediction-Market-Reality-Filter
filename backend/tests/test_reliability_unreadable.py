# backend/tests/test_reliability_unreadable.py
"""A reliability query that could not run must not look like an empty store.

Both exception branches used to return ``{engine, competition, bins: [],
total_samples: 0}``. A healthy store with nothing settled yet reports
``total_samples: 0`` as well, so the two were indistinguishable and a broken
calibration dashboard rendered as an idle one.

The branches also returned a *different shape* from the success path, omitting
``ece``/``max_calibration_error``/``sample_count`` (plus the three confidence
fields), which is what the optional markers on the frontend's
``ReliabilityData`` were accommodating.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.kernel.kernel_db import (
    close_kernel_session,
    compute_confidence_reliability_bins,
    compute_reliability_bins,
    get_kernel_session,
    init_kernel_db,
)
from tests.test_confidence_reliability import _seed

_TARGET = "app.kernel.kernel_db.get_kernel_session"


@pytest.fixture
def db(tmp_path):
    """Fresh SQLite DB per test, never the live kernel store."""
    init_kernel_db(str(tmp_path / "test_kernel.db"))
    session = get_kernel_session()
    yield session
    session.close()
    close_kernel_session()

_CURVE_KEYS = {
    "engine",
    "competition",
    "bins",
    "total_samples",
    "sample_count",
    "ece",
    "max_calibration_error",
}
_CONF_ONLY = {"mean_confidence", "mean_accuracy", "signed_gap"}


def _query_raises():
    """A session whose ``query`` blows up — the branch that was already caught."""
    session = MagicMock()
    session.query.side_effect = RuntimeError("db down")
    return patch(_TARGET, return_value=session)


def _open_raises():
    """The store cannot be opened — this used to propagate as a 500."""
    return patch(_TARGET, side_effect=RuntimeError("cannot open database file"))


class TestTheTwoZeroesAreDistinguishable:
    """The whole point: `total_samples == 0` is no longer one state."""

    def test_an_empty_store_carries_no_error_key(self, db):
        result = compute_confidence_reliability_bins(engine="no-such-engine")
        assert result["total_samples"] == 0
        assert "error" not in result

    @pytest.mark.parametrize("failure", [_query_raises, _open_raises])
    def test_an_unreadable_store_is_marked(self, failure):
        with failure():
            result = compute_confidence_reliability_bins()
        assert result["total_samples"] == 0
        assert result["error"] == "query_failed"

    def test_the_two_are_otherwise_identical(self, db):
        """Asserting only that the error path has `error` would not show that
        the *rest* of the payload is now the same — that is the half a consumer
        reading `.ece` depends on."""
        args = {"engine": "no-such-engine", "competition": "nba"}
        empty = compute_confidence_reliability_bins(**args)
        with _query_raises():
            broken = compute_confidence_reliability_bins(**args)
        assert set(broken) - set(empty) == {"error"}
        assert set(empty) - set(broken) == set()
        for key in set(empty) - {"bins"}:
            assert empty[key] == broken[key], key


class TestTheShapeMatchesTheSuccessPath:
    def test_probability_curve_error_has_every_success_key(self):
        with _query_raises():
            broken = compute_reliability_bins()
        assert _CURVE_KEYS <= set(broken)
        # And not the confidence-only fields, which this curve never computes.
        assert not (_CONF_ONLY & set(broken))

    def test_confidence_curve_error_has_every_success_key(self):
        with _query_raises():
            broken = compute_confidence_reliability_bins()
        assert (_CURVE_KEYS | _CONF_ONLY) <= set(broken)

    @pytest.mark.parametrize(
        "fn,extra",
        [
            (compute_reliability_bins, set()),
            (compute_confidence_reliability_bins, _CONF_ONLY),
        ],
    )
    def test_the_error_keys_equal_the_success_keys_plus_error(self, db, fn, extra):
        """Derived from the success path rather than hand-listed, so a new field
        added to the curve cannot be forgotten on the error branch."""
        ok = fn(engine="no-such-engine")
        with _query_raises():
            broken = fn()
        assert set(broken) == set(ok) | {"error"}
        assert _CURVE_KEYS | extra <= set(ok)

    def test_no_measurement_is_invented(self):
        """An unreadable store must not produce a number that reads as a result."""
        with _query_raises():
            broken = compute_confidence_reliability_bins()
        assert broken["ece"] is None
        assert broken["max_calibration_error"] is None
        assert broken["mean_confidence"] is None
        assert broken["mean_accuracy"] is None
        assert broken["signed_gap"] is None
        assert broken["total_samples"] == 0
        assert broken["sample_count"] == 0

    def test_the_bin_axis_is_the_full_curve_not_an_empty_list(self):
        """`bins: []` collapsed the chart; the empty curve renders an axis."""
        with _query_raises():
            broken = compute_confidence_reliability_bins(bins=10)
        assert len(broken["bins"]) == 10
        assert all(b["count"] == 0 for b in broken["bins"])
        assert all(b["avg_predicted"] is None for b in broken["bins"])

    def test_the_bin_count_argument_is_honoured_on_the_error_path(self):
        for n in (5, 10, 20):
            with _query_raises():
                broken = compute_reliability_bins(bins=n)
            assert len(broken["bins"]) == n

    @pytest.mark.parametrize(
        "fn", [compute_reliability_bins, compute_confidence_reliability_bins]
    )
    def test_the_filters_are_echoed_back_on_the_error_path(self, fn):
        """The dashboard labels the panel from these two fields, so a failure
        that blanked them would relabel whichever slice the operator asked for."""
        with _query_raises():
            broken = fn(engine="basketball", competition="nba")
        assert broken["engine"] == "basketball"
        assert broken["competition"] == "nba"
        with _query_raises():
            unfiltered = fn()
        assert unfiltered["engine"] is None
        assert unfiltered["competition"] is None


class TestAnOpenFailureNoLongerPropagates:
    """`get_kernel_session()` was outside the try, so the two most likely DB
    failures produced different HTTP behaviour: 500 versus 200-and-empty."""

    @pytest.mark.parametrize(
        "fn", [compute_reliability_bins, compute_confidence_reliability_bins]
    )
    def test_it_returns_the_documented_shape(self, fn):
        with _open_raises():
            result = fn()
        assert result["error"] == "query_failed"
        assert result["total_samples"] == 0

    @pytest.mark.parametrize(
        "fn", [compute_reliability_bins, compute_confidence_reliability_bins]
    )
    def test_an_open_failure_and_a_query_failure_agree(self, fn):
        with _open_raises():
            opened = fn()
        with _query_raises():
            queried = fn()
        assert opened == queried

    @pytest.mark.parametrize(
        "fn", [compute_reliability_bins, compute_confidence_reliability_bins]
    )
    def test_the_session_is_still_closed_when_the_query_fails(self, fn):
        """The guard added for the unbound-session case must not skip cleanup.

        Parametrized over both functions on purpose: an injection that deleted
        the ``finally`` from only one of them read GREEN when this covered just
        the confidence curve. Each has its own copy of the block.
        """
        session = MagicMock()
        session.query.side_effect = RuntimeError("db down")
        with patch(_TARGET, return_value=session):
            fn()
        session.close.assert_called_once()

    @pytest.mark.parametrize(
        "fn", [compute_reliability_bins, compute_confidence_reliability_bins]
    )
    def test_the_session_is_closed_on_the_success_path_too(self, fn):
        """A leaked Session shows up on Windows as a PermissionError at rmtree,
        two test files away. Both exits go through the same finally."""
        session = MagicMock()
        session.query.return_value.join.return_value.filter.return_value.all.return_value = []
        with patch(_TARGET, return_value=session):
            result = fn()
        assert "error" not in result
        session.close.assert_called_once()

    def test_no_close_is_attempted_when_the_open_failed(self):
        """`session` is unbound there; an unguarded finally would raise
        NameError and mask the real failure."""
        with _open_raises():
            result = compute_reliability_bins()
        assert result["error"] == "query_failed"


_ROUTES = [
    "/api/predictions/calibration/reliability",
    "/api/predictions/calibration/confidence-reliability",
]


class TestThroughTheRealApp:
    """Mounted on ``app.main``, not a bare ``FastAPI()``: a test that builds its
    own app cannot tell whether the router is reachable in production."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from app.core import config
        from app.main import app

        original = config.settings.KERNEL_PREDICTION_ENABLED
        config.settings.KERNEL_PREDICTION_ENABLED = True
        try:
            yield TestClient(app)
        finally:
            config.settings.KERNEL_PREDICTION_ENABLED = original

    @pytest.mark.parametrize("path", _ROUTES)
    def test_the_disabled_gate_still_precedes_the_store(self, path):
        """The fixture above turns the flag on, so pin that the flag is live:
        otherwise every assertion below could be passing for the wrong reason."""
        from fastapi.testclient import TestClient

        from app.core import config
        from app.main import app

        original = config.settings.KERNEL_PREDICTION_ENABLED
        config.settings.KERNEL_PREDICTION_ENABLED = False
        try:
            with _query_raises():
                resp = TestClient(app).get(path)
        finally:
            config.settings.KERNEL_PREDICTION_ENABLED = original
        assert resp.status_code == 503
        assert "error" not in resp.json()

    @pytest.mark.parametrize("path", _ROUTES)
    def test_both_paths_are_mounted(self, client, path):
        from app.main import app

        assert path in {r.path for r in app.routes}  # type: ignore[attr-defined]

    @pytest.mark.parametrize("path", _ROUTES)
    def test_a_broken_store_answers_200_with_the_marker(self, client, path):
        """Not a 500: the panel is one of several on the page, and the operator
        needs the rest of it. But it must say it failed."""
        with _query_raises():
            resp = client.get(path)
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] == "query_failed"
        assert body["total_samples"] == 0
        assert body["ece"] is None

    @pytest.mark.parametrize("path", _ROUTES)
    def test_an_open_failure_is_no_longer_a_500(self, client, path):
        with _open_raises():
            resp = client.get(path)
        assert resp.status_code == 200
        assert resp.json()["error"] == "query_failed"

    @pytest.mark.parametrize("path", _ROUTES)
    def test_a_healthy_response_carries_no_marker(self, client, db, path):
        resp = client.get(path, params={"engine": "no-such-engine"})
        assert resp.status_code == 200
        body = resp.json()
        assert "error" not in body
        assert body["total_samples"] == 0
        assert len(body["bins"]) == 10

    @pytest.mark.parametrize("path", _ROUTES)
    def test_the_route_forwards_the_bins_argument_unchanged(self, client, path):
        """The removed normalization block sat between the helper and the
        response; this pins that nothing else was rewriting the payload."""
        with _query_raises():
            resp = client.get(path, params={"bins": 5})
        assert len(resp.json()["bins"]) == 5

    @pytest.mark.parametrize("path", _ROUTES)
    def test_the_response_is_the_helper_result_verbatim(self, client, path):
        """No post-processing at all — assert equality with the helper, so a
        re-added normalization step would be red."""
        module = (
            "compute_reliability_bins"
            if path.endswith("/reliability")
            else "compute_confidence_reliability_bins"
        )
        sentinel = {"engine": None, "competition": None, "marker": "verbatim"}
        with patch(f"app.kernel.kernel_db.{module}", return_value=sentinel):
            resp = client.get(path)
        assert resp.json() == sentinel


class TestTheSuccessPathIsUnchanged:
    def test_a_real_curve_still_has_no_error_key(self, db):
        _seed(db, "m1", confidence=0.80, max_prob=0.60, correct=1)
        result = compute_confidence_reliability_bins()
        assert result["total_samples"] == 1
        assert "error" not in result
        assert result["mean_confidence"] == pytest.approx(0.80)

    def test_the_probability_curve_still_has_no_error_key(self, db):
        _seed(db, "m1", confidence=0.80, max_prob=0.60, correct=1)
        result = compute_reliability_bins()
        assert result["total_samples"] == 1
        assert "error" not in result
