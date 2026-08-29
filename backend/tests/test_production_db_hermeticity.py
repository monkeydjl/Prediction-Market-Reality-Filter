"""No test may write to a production database.

``tests/conftest.py`` promises hermetic runs by pointing every database path
setting at a session temp directory "so no test reads real production data".
The kernel database escaped that promise: ``init_kernel_db()`` hardcoded

    Path(__file__).resolve().parents[2] / "kernel_predictions.db"

instead of reading a setting, and a hardcoded default is invisible to a redirect
that works by overwriting settings.  Every other database in this codebase is a
setting (``LOOP_DB_FILE``, ``WORLD_CUP_PREDICTION_DB_FILE``,
``DOMAIN_RELIABILITY_DB_PATH``); the kernel DB was the sole exception, so the
nine no-arg ``init_kernel_db()`` call sites in ``app/`` -- the predict route,
seven scheduler jobs, and the lazy fallback in ``get_kernel_session`` -- opened
the real file during test runs.

Measured before the fix, on the developer machine:

* one run of ``tests/test_predictions_route.py`` added **8 rows** to
  ``kernel_prediction_history`` in ``backend/kernel_predictions.db``;
* **1208 of that table's 1224 rows** were predictions for seven match ids that
  do not exist (``epl-nonexistent``, ``laliga-nonexistent``, ...), i.e. 98.7% of
  the kernel's entire prediction history was test exhaust;
* ``GET /predictions/history`` (``app/api/routes/predictions.py``) serves that
  table straight to the dashboard.

The production paths below are derived from the repo layout and NOT from
``settings``, deliberately.  Reading ``settings.KERNEL_DB_FILE`` here would
compare the temp file against itself and pass no matter what the code does --
the vacuous shape this whole module exists to prevent.
"""
import hashlib
import pathlib
from unittest.mock import AsyncMock, patch

import pytest

import app.core.config as config_module
from app.kernel import kernel_db

from tests import conftest

# backend/app/core/config.py -> backend/
_BACKEND_ROOT = pathlib.Path(config_module.__file__).resolve().parents[2]

AUTH = {"X-Write-Key": "test"}


def _repo_db_state() -> dict[str, str]:
    """Digest every SQLite file sitting in the backend directory.

    Keyed by filename so that *creating* a database is a change too, not just
    growing one: in CI none of these files exist, and a leak that creates
    ``kernel_predictions.db`` from scratch has to fail the same way it fails on
    a developer machine where the file is already 6 MB.  ``*.db*`` also catches
    the ``-wal`` / ``-shm`` sidecars SQLite writes in WAL mode.
    """
    return {
        path.name: hashlib.md5(path.read_bytes()).hexdigest()
        for path in sorted(_BACKEND_ROOT.glob("*.db*"))
    }


def _stubbed_upstreams():
    """Patch the three upstream fetches at the points ``_shared`` documents.

    ``get_club_elo`` is patched on ``_shared`` because ``_shared`` binds it at
    module scope; ``get_elo_rating`` and ``get_cached_odds`` are patched at their
    source modules because ``_shared`` imports those lazily inside the function
    body, which a source-module patch still honors.  Patching the wrong one of
    the two is silent -- the call goes out over the network and the test only
    looks slow.  Without these, six tests in test_predictions_route.py spend
    33.5s each asking a third-party API for the Elo rating of a team that the
    stub identity names "Home".
    """
    return (
        patch("app.sports.football.adapters._shared.get_club_elo", return_value=None),
        patch("app.services.elo_ratings_service.get_elo_rating", new=AsyncMock(return_value=None)),
        patch("app.services.odds_cache_service.get_cached_odds", new=AsyncMock(return_value=None)),
    )


@pytest.fixture
def kernel_client():
    """A TestClient with the kernel enabled and write auth open."""
    from fastapi.testclient import TestClient

    from app.api.security import settings as security_settings
    from app.core import config
    from app.main import app

    with patch.object(config.settings, "KERNEL_PREDICTION_ENABLED", True), \
         patch.object(security_settings, "API_WRITE_KEY", ""), \
         patch.object(security_settings, "ALLOW_OPEN_WRITES", True):
        yield TestClient(app)


class TestKernelDbHermeticity:
    @staticmethod
    def _seed_fixture(match_id: str) -> None:
        """Insert a real fixture row so the predict below is a real predict.

        Seeded into ``match_fixtures`` in the World Cup prediction database,
        because that is the table the adapter this id actually routes to will
        read: ``PHASE2_LEAGUES_ENABLED`` is false in this fixture, so every
        prefix falls back to ``WorldCupAdapter``.  Both that path and
        ``init_kernel_db()`` are redirected into the session temp dir by
        conftest, so the seeding itself stays inside the guarantee this module
        asserts -- ``_repo_db_state()`` digests ``backend/*.db*``, and a seed
        that landed in the repo would fail the comparison below rather than
        being excused by it.
        """
        from datetime import datetime, timezone

        from app.models.world_cup_prediction import MatchFixture
        from app.utils.prediction_db import get_prediction_session

        kernel_db.init_kernel_db()
        session = get_prediction_session()
        try:
            MatchFixture.metadata.create_all(session.get_bind())
            session.merge(MatchFixture(
                match_id=match_id,
                fixture_id=999001,
                home_team="Brazil",
                away_team="Morocco",
                kickoff_utc=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
                stage="group_stage",
                status="scheduled",
            ))
            session.commit()
        finally:
            session.close()

    def test_no_arg_init_resolves_inside_the_test_temp_dir(self):
        """``init_kernel_db()`` is how all nine app/ call sites open the DB."""
        kernel_db.close_kernel_db()
        kernel_db.init_kernel_db()

        assert kernel_db._engine is not None
        url = str(kernel_db._engine.url).replace("\\", "/")
        temp_dir = conftest._TEST_DATA_DIR.replace("\\", "/")
        assert temp_dir in url, (
            f"init_kernel_db() opened {url!r}, outside the test temp dir "
            f"{temp_dir!r} -- the harness redirect does not reach it"
        )

    def test_the_setting_is_what_decides_the_path(self, tmp_path):
        """Behavioural guard against re-hardcoding the path.

        Asserting on the *source* of init_kernel_db would pass the moment
        someone spelled the constant differently.  Moving the setting and
        checking where the engine actually lands cannot be fooled that way.
        """
        target = tmp_path / "relocated_kernel.db"
        from app.core import config

        kernel_db.close_kernel_db()
        with patch.object(config.settings, "KERNEL_DB_FILE", str(target)):
            kernel_db.init_kernel_db()

        assert kernel_db._engine is not None
        assert str(target).replace("\\", "/") in str(kernel_db._engine.url).replace("\\", "/")
        assert target.exists(), "the relocated database was never created"

    def test_predicting_writes_a_row_but_touches_no_repo_database(self, kernel_client):
        """The end-to-end regression: this is the write that leaked.

        Both halves are load-bearing.  Without the "a row was written"
        assertion the test passes when the route silently does nothing, which is
        the failure mode that would make it useless as a guard.

        The probe id is now **seeded as a real fixture**.  It previously had no
        fixture row at all, and the write it asserted was an invented one: the
        adapters substitute a placeholder identity for an unknown id, so predict
        ran to completion on defaults and persisted the result.  That behaviour
        is now refused with a 404, which would have left this test asserting a
        row that no longer gets written -- it was passing *because of* the defect
        it sits next to.  Seeding the fixture exercises the real write path, so
        the hermeticity claim is now made about a write that production performs.
        """
        match_id = "hermeticity-probe"
        self._seed_fixture(match_id)
        before = _repo_db_state()

        elo_club, elo_national, odds = _stubbed_upstreams()
        with elo_club, elo_national, odds:
            resp = kernel_client.post(
                f"/api/predictions/matches/{match_id}/predict", headers=AUTH
            )

        assert resp.status_code == 200, resp.text
        assert kernel_db.get_latest_prediction(match_id) is not None, (
            "no prediction was persisted, so this test proves nothing about "
            "where predictions get persisted"
        )

        after = _repo_db_state()
        assert after == before, (
            "a prediction write reached a database inside the repo; "
            f"changed: {sorted(set(after.items()) ^ set(before.items()))}"
        )
