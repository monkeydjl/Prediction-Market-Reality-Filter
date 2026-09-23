"""``OPENAPI_ENABLED`` has to actually close /openapi.json, /docs and /redoc.

Both shipped proxy examples tell the operator to disable those paths "in prod via
``OPENAPI_ENABLED=false``" and block them at the proxy only "as defense in
depth" — so the app is documented as the primary control. There was no such
setting: ``hasattr(settings, "OPENAPI_ENABLED")`` was ``False``, nothing in
``backend/`` read the name, and an operator who set it got 200 on all three
paths. What that served was not a stub: a 189 KB schema covering 184 paths and
79 write operations (``POST /api/events/reset``,
``POST /api/analytics/verified-result-correction``, …) plus the ``X-API-Key`` and
``X-Operator`` header names each one expects.

The app shape is decided at import time, so each case reloads ``app.core.config``
and ``app.main`` under a patched environment — the same pattern as
``test_main_frontend_mount.py``.
"""
import importlib
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient


@contextmanager
def _reloaded(**env):
    """Reload the app with these env vars; a value of ``None`` removes the name.

    ``patch.dict`` restores the environment on exit, and both modules are
    reloaded again in ``finally`` so a later test sees the process-wide app it
    expects.
    """
    from app.core import config as config_module
    from app import main as main_module

    with patch.dict(os.environ, {k: v for k, v in env.items() if v is not None}):
        for name, value in env.items():
            if value is None:
                os.environ.pop(name, None)
        importlib.reload(config_module)
        try:
            yield importlib.reload(main_module)
        finally:
            importlib.reload(config_module)
            importlib.reload(main_module)


class OpenAPIExposureSwitchTests(unittest.TestCase):
    PATHS = ("/openapi.json", "/docs", "/redoc")

    def test_the_schema_and_both_doc_pages_are_gone_when_disabled(self):
        with _reloaded(OPENAPI_ENABLED="false") as main_module:
            client = TestClient(main_module.app)
            statuses = {path: client.get(path).status_code for path in self.PATHS}

        self.assertEqual(
            statuses,
            dict.fromkeys(self.PATHS, 404),
            "OPENAPI_ENABLED=false left the API documentation reachable; both "
            "deploy/nginx.conf.example and deploy/Caddyfile.example tell the "
            "operator this setting closes it",
        )

    def test_they_are_served_when_enabled(self):
        """The reverse half: a global 404 would satisfy the test above.

        Asserting the status alone would also pass if the routes were gone for
        some unrelated reason, so each response is checked for the thing only
        that endpoint can produce.
        """
        with _reloaded(OPENAPI_ENABLED="true") as main_module:
            client = TestClient(main_module.app)
            schema = client.get("/openapi.json")
            swagger = client.get("/docs")
            redoc = client.get("/redoc")

        self.assertEqual(
            (schema.status_code, swagger.status_code, redoc.status_code),
            (200, 200, 200),
        )
        self.assertGreater(len(schema.json()["paths"]), 100)
        self.assertIn("swagger-ui", swagger.text.lower())
        self.assertIn("redoc", redoc.text.lower())

    def test_the_documentation_is_available_by_default(self):
        """Absent means on, so this change cannot close a dev box's /docs.

        Checked with the name *removed* rather than set to "true": a default of
        false would still pass the enabled case above.
        """
        with _reloaded(OPENAPI_ENABLED=None) as main_module:
            self.assertTrue(main_module.settings.OPENAPI_ENABLED)
            self.assertEqual(
                TestClient(main_module.app).get("/docs").status_code, 200
            )

    def test_the_api_overview_does_not_advertise_a_page_that_is_gone(self):
        """``GET /api`` publishes a ``docs`` pointer; it must track the switch.

        The pointer was the string ``"/docs"``, written independently of the
        constructor argument that decides whether the page exists — two claims
        about one fact, one of which would be false in exactly the deployment
        this setting is for.
        """
        with _reloaded(OPENAPI_ENABLED="false") as main_module:
            disabled = TestClient(main_module.app).get("/api").json()
        with _reloaded(OPENAPI_ENABLED="true") as main_module:
            enabled = TestClient(main_module.app).get("/api").json()

        self.assertIsNone(disabled["docs"])
        self.assertEqual(enabled["docs"], "/docs")

    def test_the_backend_root_redirect_does_not_point_at_a_disabled_page(self):
        """With no frontend to serve, ``/`` redirects — but not into a 404.

        ``BACKEND_SERVE_FRONTEND=false`` is the branch that sends the root at
        ``/docs``. Both halves run so the assertion cannot pass by the redirect
        target being constant.
        """
        with _reloaded(
            BACKEND_SERVE_FRONTEND="false", OPENAPI_ENABLED="false"
        ) as main_module:
            disabled = TestClient(main_module.app).get("/", follow_redirects=False)
        with _reloaded(
            BACKEND_SERVE_FRONTEND="false", OPENAPI_ENABLED="true"
        ) as main_module:
            enabled = TestClient(main_module.app).get("/", follow_redirects=False)

        self.assertEqual(disabled.status_code, 307)
        self.assertEqual(disabled.headers["location"], "/api")
        self.assertEqual(enabled.status_code, 307)
        self.assertEqual(enabled.headers["location"], "/docs")


if __name__ == "__main__":
    unittest.main()
