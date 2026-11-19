"""An /api path must answer like an API, with or without the built frontend.

Measured on the shipped app before this file existed, with ``frontend/out``
present and ``StaticFiles(html=True)`` mounted at ``/``:

* ``GET  /api/definitely-not-a-route`` -> 404, 36 300 bytes, ``text/html``
  (``frontend/out/404.html``), so ``fetch(...).json()`` throws on a body that
  starts with ``<!DOCTYPE``;
* ``POST /api/definitely-not-a-route`` -> 405 ``Method Not Allowed``, claiming a
  resource that does not exist;
* ``POST /api/health`` -> 405, but without the ``Allow: GET`` header the same
  app returns when no frontend is built;
* ``GET  /api/health/`` -> 404, where the same app without a frontend build
  307s to ``/api/health``.

The third one is Starlette's: the router keeps a partial match (path matched,
method did not) and only raises 405 from it after scanning the whole table
without a full match, and the catch-all mount is a full match. So the mount was
replacing the router's answer, not just its body. The fourth is the same
mechanism one branch further down (``redirect_slashes``).

The fence is therefore tested two ways: behaviour against a synthetic app, and
parity against the same app without the mount -- redirects not followed, because
following them hides whether the deployment gave a 307 or a 404.
"""
from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.static_serving import ApiAwareStaticFiles  # noqa: E402

PAGE_404 = "<html>the SPA 404 page</html>"
HTML_404 = "text/html; charset=utf-8"
JSON = "application/json"
NOT_FOUND_BODY = '{"detail":"Not Found"}'

#: Every method a client may reach the mount with. ``OPTIONS`` is left out: CORS
#: preflights are answered by middleware before routing, so the mount never sees
#: one.
METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")


def build_app(mounted: bool, directory: str) -> FastAPI:
    """The same two routes, with or without the catch-all frontend mount.

    Deliberately not the real app: this has to be able to assert a difference,
    which means the two sides may not share anything but the routes.
    """
    app = FastAPI()

    @app.get("/api")
    async def overview() -> dict[str, str]:
        return {"system": "probe"}

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    if mounted:
        app.mount("/", ApiAwareStaticFiles(directory=directory, html=True), name="frontend")
    return app


def dotted_name(node: ast.expr) -> str:
    """``Name``/``Attribute`` chain as text, for diagnosing a wiring change."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


class FencedAppTestCase(unittest.TestCase):
    """A real export directory, mounted at ``/`` exactly like ``app/main.py``."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)
        (self.out / "404.html").write_text(PAGE_404, encoding="utf-8")
        (self.out / "index.html").write_text("<html>home</html>", encoding="utf-8")
        self.client = TestClient(build_app(True, str(self.out)))


class ApiPathAnswersJsonTests(FencedAppTestCase):
    def test_an_unrouted_api_path_is_a_json_404(self):
        resp = self.client.get("/api/definitely-not-a-route")

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.headers["content-type"], JSON)
        # Byte for byte what FastAPI answers when no frontend is built, so a
        # client cannot tell which deployment it is talking to.
        self.assertEqual(resp.text, NOT_FOUND_BODY)

    def test_no_method_gets_the_page_back(self):
        """The matrix, because the fence is on the namespace, not on GET."""
        answered = {}
        for method in METHODS:
            for path in ("/api", "/api/", "/api/typo", "/api/typo/deeper"):
                resp = self.client.request(method, path)
                answered[f"{method} {path}"] = (
                    resp.status_code,
                    resp.headers["content-type"],
                )

        html = {case: seen for case, seen in answered.items() if seen[1] != JSON}
        self.assertEqual(html, {}, "these API requests were answered with a page")

    def test_the_fence_stops_at_a_path_segment(self):
        """``/apiary`` is a page, not an API path.

        A prefix test on ``"/api"`` alone would fence it off, and the next static
        asset or page whose name starts with those three letters with it.
        """
        for path in ("/apiary", "/apix/y", "/API/x", "/api-page"):
            with self.subTest(path=path):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 404)
                self.assertEqual(resp.headers["content-type"], HTML_404)
                self.assertEqual(resp.text, PAGE_404)

    def test_a_wrong_method_on_a_routed_path_keeps_its_allow_header(self):
        """405 is the router's answer, and the header is the useful half of it."""
        resp = self.client.post("/api/health")

        self.assertEqual(resp.status_code, 405)
        self.assertEqual(resp.headers["allow"], "GET")
        self.assertEqual(resp.headers["content-type"], JSON)

    def test_a_wrong_method_on_an_unrouted_path_is_a_404(self):
        """405 would send the caller looking for a verb for a resource that is not there."""
        resp = self.client.post("/api/typo")

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.text, NOT_FOUND_BODY)

    def test_pages_and_assets_keep_the_parent_behaviour(self):
        self.assertEqual(self.client.get("/").text, "<html>home</html>")

        missing = self.client.get("/no-such-page")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.text, PAGE_404, "the SPA 404 page is still the SPA 404 page")

        # Starlette's own 405 for a non-GET request to a file that does exist:
        # outside the fence, and unchanged by it.
        self.assertEqual(self.client.post("/index.html").status_code, 405)


class TrailingSlashTests(FencedAppTestCase):
    """The router's slash redirect, which the catch-all mount also swallowed."""

    def test_a_trailing_slash_goes_where_the_router_would_have_sent_it(self):
        for path, target in (
            ("/api/health/", "http://testserver/api/health"),
            ("/api/", "http://testserver/api"),
            # Two slashes are one extra segment too many; the router strips them
            # all before retrying, and so does this.
            ("/api/health//", "http://testserver/api/health"),
        ):
            with self.subTest(path=path):
                resp = self.client.get(path, follow_redirects=False)
                self.assertEqual(resp.status_code, 307)
                self.assertEqual(resp.headers["location"], target)
                # 307, not 301/302: the method must survive the hop.
                self.assertEqual(self.client.post(path, follow_redirects=False).status_code, 307)

    def test_the_redirect_never_invents_a_target(self):
        """Skipping ``Mount`` while probing the alternative path is what this pins.

        The catch-all matches every alternative, so counting it would send
        ``/api/typo/`` to ``/api/typo`` only to 404 there: a redirect for a path
        that has nothing at either spelling, plus a round trip.
        """
        for path in ("/api/typo/", "/api/typo/deeper/"):
            with self.subTest(path=path):
                resp = self.client.get(path, follow_redirects=False)
                self.assertEqual(resp.status_code, 404)
                self.assertEqual(resp.text, NOT_FOUND_BODY)


class MountChangesNothingTests(unittest.TestCase):
    """The mount must not be able to change what ``/api`` answers.

    Every row is compared with the same app *without* the mount -- the shape a
    deployment with no frontend build serves. Comparing against a fixed table
    would only pin today's answers; comparing against the other app also catches
    a framework upgrade that changes them.

    Redirects are not followed on either side. Following them turns "307 then
    405" into "405" and "307 then 200" into "200", which is exactly the
    difference a mount with a swallowed ``redirect_slashes`` branch produces.
    """

    CASES = tuple(
        (method, path)
        for method in METHODS
        for path in (
            "/api", "/api/", "/api/health", "/api/health/", "/api/health//",
            "/api/typo", "/api/typo/", "/api/typo/deeper", "/api/health/extra",
        )
    )

    def test_the_mount_does_not_change_any_api_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "404.html").write_text(PAGE_404, encoding="utf-8")
            plain = TestClient(build_app(False, tmp))
            mounted = TestClient(build_app(True, tmp))

            differences = {}
            for method, path in self.CASES:
                answers = []
                for client in (plain, mounted):
                    resp = client.request(method, path, follow_redirects=False)
                    answers.append((
                        resp.status_code,
                        # A 307 carries no content-type at all (empty body), so
                        # every header has to be read with `.get`.
                        resp.headers.get("content-type"),
                        resp.headers.get("allow"),
                        resp.headers.get("location"),
                        resp.text,
                    ))
                if answers[0] != answers[1]:
                    differences[f"{method} {path}"] = tuple(answers)

        self.assertEqual(
            differences, {},
            "the frontend mount changed these answers (plain, mounted): "
            f"{differences}",
        )


class ShippedAppTests(unittest.TestCase):
    """The real app, whether or not ``frontend/out`` was built."""

    def test_an_unknown_api_path_is_json(self):
        from app.main import app as real_app

        resp = TestClient(real_app).get("/api/__definitely_not_a_route__")

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(
            (resp.headers["content-type"], resp.text),
            (JSON, NOT_FOUND_BODY),
            "an API client cannot parse a page-shaped body",
        )

    def test_a_trailing_slash_still_reaches_the_endpoint(self):
        """The one answer here that must not depend on the build at all.

        ``/api/health`` itself may be 503 (that is the health verdict), so only
        the hop is asserted: a 307 to the canonical path, not a 404.
        """
        from app.main import app as real_app

        resp = TestClient(real_app).get("/api/health/", follow_redirects=False)

        self.assertEqual(resp.status_code, 307)
        self.assertEqual(resp.headers["location"], "http://testserver/api/health")

    def test_the_frontend_mount_is_the_api_aware_class(self):
        """The class above is only worth anything if the mount uses it.

        Read from the source rather than from ``app.routes``: the mount exists
        only when ``frontend/out`` was built, so a route-table check would pass
        vacuously on a fresh checkout.
        """
        source = (_BACKEND / "app" / "main.py").read_text(encoding="utf-8")
        mounts = [
            node for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "mount"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "/"
        ]

        self.assertEqual(len(mounts), 1, "expected exactly one mount at /")
        handler = mounts[0].args[1]
        if isinstance(handler, ast.Call):
            handler = handler.func
        self.assertTrue(
            dotted_name(handler).endswith("ApiAwareStaticFiles"),
            "this mount answers every path the router refused, so it has to be "
            f"ApiAwareStaticFiles; app/main.py mounts {dotted_name(handler)}",
        )


if __name__ == "__main__":
    unittest.main()
