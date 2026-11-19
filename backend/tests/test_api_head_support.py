"""HEAD has to answer wherever GET answers -- and has to stay cheap.

Measured on the shipped app before this file existed:

============================  =========================  ===================================
 request                       HEAD                       GET
============================  =========================  ===================================
 ``/api``                      405 ``Allow: GET``          200, 783 B
 ``/api/health``               405                        503, 10 931 B
 ``/api/events/``              405                        200, 684 498 B
 ``/api/typo``                 404                        404, 22 B
============================  =========================  ===================================

The cause is a difference between the two route classes: Starlette's ``Route``
adds ``HEAD`` to a GET route (``if "GET" in self.methods: self.methods.add("HEAD")``)
and FastAPI's ``APIRoute`` does not (``self.methods = {method.upper() for method
in methods}``). So ``curl -I`` worked on a page and failed on every endpoint --
which is backwards, since the 684 KB response is the one whose size a client most
wants before downloading it.

Two things are pinned here: the answer (status, headers, and *no body* -- checked
on the ASGI messages, because httpx never surfaces a HEAD body and would make the
assertion vacuous) and the exception for streaming handlers.
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import unittest
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402
from starlette.types import Message, Receive, Scope, Send  # noqa: E402

from app.core.head_support import (  # noqa: E402
    EXTRA_HEAD_PATHS,
    FALLBACK_ROOT_PATH,
    STREAMING_PATHS,
    ApiHeadAsGetMiddleware,
)

JSON = "application/json"


class RecordingApp:
    """An inner app that records what it was asked and answers 200 + 1 000 bytes."""

    def __init__(self, size: int = 1000) -> None:
        self.size = size
        self.seen: Scope | None = None
        self.messages: list[Message] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.seen = scope
        for message in (
            {"type": "http.response.start", "status": 200,
             "headers": [(b"content-length", str(self.size).encode())]},
            {"type": "http.response.body", "body": b"x" * self.size},
        ):
            self.messages.append(message)
            await send(message)


def _scope(method: str, path: str) -> Scope:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1),
    }


def run_head(inner: RecordingApp, method: str, path: str) -> list[Message]:
    """Drive the middleware over a fake inner app and return what went downstream."""
    forwarded: list[Message] = []

    async def send(message: Message) -> None:
        forwarded.append(message)

    async def receive() -> Message:  # pragma: no cover - never awaited
        return {"type": "http.request", "body": b"", "more_body": False}

    middleware = ApiHeadAsGetMiddleware(inner)
    asyncio.run(middleware(_scope(method, path), receive, send))
    return forwarded


class RewriteTests(unittest.TestCase):
    """The middleware in isolation: what the router sees, what the client gets."""

    def test_head_is_rewritten_and_the_body_is_dropped(self):
        inner = RecordingApp()

        forwarded = run_head(inner, "HEAD", "/api/health")

        self.assertEqual(inner.seen["method"], "GET", "the router still saw HEAD")
        start = forwarded[0]
        self.assertEqual(start["type"], "http.response.start")
        self.assertIn((b"content-length", b"1000"), start["headers"],
                      "the size HEAD exists to report was not passed through")
        bodies = [m for m in forwarded if m["type"] == "http.response.body"]
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0]["body"], b"", "a HEAD response carried a body")
        self.assertFalse(bodies[0].get("more_body", False))

    def test_the_callers_scope_is_not_mutated(self):
        scope = _scope("HEAD", "/api/health")
        inner = RecordingApp()

        async def send(message: Message) -> None:
            return None

        async def receive() -> Message:  # pragma: no cover - never awaited
            return {"type": "http.request", "body": b"", "more_body": False}

        asyncio.run(ApiHeadAsGetMiddleware(inner)(scope, receive, send))

        self.assertEqual(scope["method"], "HEAD", "the shared scope was rewritten in place")
        self.assertEqual(inner.seen["method"], "GET")

    def test_a_streaming_handler_is_left_alone(self):
        """It would start real work: this app's stream endpoint runs a batch job."""
        inner = RecordingApp()

        forwarded = run_head(inner, "HEAD", sorted(STREAMING_PATHS)[0])

        self.assertEqual(inner.seen["method"], "HEAD", "a streaming handler was rewritten")
        self.assertEqual(forwarded[1]["body"], b"x" * 1000, "the stream was truncated")

    def test_outside_the_namespace_nothing_changes(self):
        for path in ("/page", "/apiary", "/next/api/health"):
            with self.subTest(path=path):
                inner = RecordingApp()
                forwarded = run_head(inner, "HEAD", path)
                self.assertEqual(inner.seen["method"], "HEAD")
                self.assertEqual(forwarded[1]["body"], b"x" * 1000)

    def test_get_is_untouched(self):
        inner = RecordingApp()

        forwarded = run_head(inner, "GET", "/api/health")

        self.assertEqual(inner.seen["method"], "GET")
        self.assertEqual(forwarded[1]["body"], b"x" * 1000)


class StreamCensusTests(unittest.TestCase):
    """`STREAMING_PATHS` is memory; the route table is the fact.

    A streaming handler added without updating the set would be rewritten for
    HEAD and would start its work from a probe. The detector is here, not in the
    middleware: the middleware should not pay an `inspect` per request to find out
    something fixed at import time.
    """

    STREAMING_ANNOTATIONS = {"StreamingResponse", "EventSourceResponse"}

    @staticmethod
    def returns_a_stream(route: Any) -> bool:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            return False
        annotation = inspect.signature(endpoint).return_annotation
        if isinstance(annotation, str):
            name = annotation
        else:
            name = getattr(annotation, "__name__", type(annotation).__name__)
        return name.rsplit(".", 1)[-1] in StreamCensusTests.STREAMING_ANNOTATIONS

    def test_the_streaming_list_matches_the_route_table(self):
        from app.main import app as real_app

        found = {route.path for route in real_app.routes if self.returns_a_stream(route)}

        self.assertTrue(found, "the detector found no streaming route at all")
        self.assertEqual(
            found,
            set(STREAMING_PATHS),
            "a handler that returns a stream must be listed, or HEAD will run it",
        )


class EndpointCensusTests(unittest.TestCase):
    """Every ``APIRoute`` outside ``/api`` needs HEAD too -- and none may appear unseen.

    ``EXTRA_HEAD_PATHS`` is memory; the route table is the fact. An endpoint
    registered directly on ``app`` (``@app.get`` instead of an ``APIRouter``)
    carries the same ``APIRoute`` defect while sitting outside the fence's
    predicate, so adding one has to be a decision rather than an oversight.

    §53.136: the route table is not the same in every deployment. ``main.py``
    registers ``GET /`` only when the frontend build is absent, and mounts
    ``frontend/out`` at ``/`` when it is present. This test passed on the machine
    where ``out/`` had just been built (§53.130) and turned CI red, which has no
    build: a census that reads a deployment-dependent table has to say which
    deployment it is looking at. It reads that from the route table itself -- a
    mount at ``/`` means the build is being served, so the fallback route is not
    registered -- instead of re-deriving the condition from settings, so a
    changed branch fails here rather than silently shifting the expectation.
    """

    def test_the_list_outside_the_namespace_matches_the_route_table(self):
        from fastapi.routing import APIRoute
        from starlette.routing import Mount

        from app.core.api_namespace import is_api_path
        from app.main import app as real_app

        found = {
            route.path
            for route in real_app.routes
            if isinstance(route, APIRoute) and not is_api_path(route.path)
        }

        # ``Mount("/")`` keeps ``path == ""``: Starlette strips the trailing
        # slash, so testing for ``"/"`` finds nothing and the expectation below
        # would silently keep the fallback path in both deployments -- the same
        # shape of mistake as the one this whole section is about.
        static_build_mounted = any(
            isinstance(route, Mount) and route.path in ("", "/") for route in real_app.routes
        )
        expected = set(EXTRA_HEAD_PATHS)
        if static_build_mounted:
            expected.discard(FALLBACK_ROOT_PATH)

        self.assertTrue(found, "the census found no APIRoute outside /api at all")
        self.assertEqual(
            found,
            expected,
            "an APIRoute outside /api is not covered by the HEAD rewrite",
        )


class ShippedHeadTests(unittest.TestCase):
    """The real app: the answers a monitor and a curious client actually get."""

    def setUp(self) -> None:
        from app.main import app as real_app

        self.client = TestClient(real_app)

    def test_metrics_answers_head_with_the_get_headers(self):
        """`/metrics` is the one ``APIRoute`` outside ``/api`` (§53.115).

        Before: HEAD answered **404** with ``content-length: 36 300`` -- the SPA
        page's size, for an endpoint that serves 6 339 bytes of text. A monitor
        asking "how big is this scrape" was told about a page the route does not
        serve.

        The length is compared within a band rather than exactly: this payload is
        the live prometheus registry, and its GC counters grow between two
        renders (measured ``python_gc_collections_total 646 -> 672``), which can
        shift a digit. The band is two orders of magnitude narrower than the
        defect it replaces, so a real mismatch still fails.
        """
        head = self.client.head("/metrics")
        get = self.client.get("/metrics")

        self.assertEqual(head.status_code, 200, "HEAD /metrics is not answered by its GET")
        self.assertTrue(
            (head.headers.get("content-type") or "").startswith("text/plain"),
            "HEAD /metrics answered something other than the metrics body",
        )
        self.assertEqual(head.headers["content-type"], get.headers["content-type"])
        self.assertLessEqual(
            abs(int(head.headers["content-length"]) - len(get.content)),
            128,
            "HEAD advertised a size the GET does not produce",
        )

    def test_the_overview_answers_head_with_the_get_headers(self):
        head = self.client.head("/api")
        get = self.client.get("/api")

        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.headers["content-type"], JSON)
        self.assertEqual(head.headers["content-length"], get.headers["content-length"])

    def test_health_reports_the_same_verdict_and_no_body(self):
        """503 while degraded, 200 when not -- but never 405, which said nothing.

        Only the verdict is compared here. The payload *is* stable now
        (§53.114 made it a function of state rather than of how many times it
        had been polled), and the same-length assertion lives in
        ``tests/test_health_payload_stability.py`` against a cold loop database,
        where it is deterministic. It is not asserted here because this client
        shares a session-wide sandbox: ``review_queue.oldest_age_hours`` is a
        clock-derived float rounded to four decimals of an hour (0.36 s), so a
        non-empty queue would make two calls differ by a digit and this test
        would flake rather than fail.
        """
        head = self.client.head("/api/health")
        get = self.client.get("/api/health")

        self.assertIn(head.status_code, {200, 503})
        self.assertEqual(head.status_code, get.status_code)
        self.assertEqual(head.headers["content-type"], JSON)

    def test_an_unrouted_api_path_is_the_same_404(self):
        head = self.client.head("/api/__definitely_not_a_route__")

        self.assertEqual(head.status_code, 404)
        self.assertEqual(head.headers["content-type"], JSON)

    def test_the_streaming_job_is_not_started_by_a_probe(self):
        head = self.client.head(next(iter(STREAMING_PATHS)))

        self.assertEqual(head.status_code, 405)
        self.assertEqual(head.headers["allow"], "GET")

    def test_the_root_answers_head_in_whichever_deployment_this_is(self):
        """``/`` is a mount with a build and an ``APIRoute`` without one; neither is a 405.

        This assertion is deliberately deployment-blind, because the value of it
        is the same in both: an ``APIRoute`` outside the namespace answers HEAD
        with 405 until it is listed in ``EXTRA_HEAD_PATHS``, and ``/`` is the one
        outside both predicates (``is_api_path`` says no, and before §53.136 the
        extra list said no as well). Which status is correct depends on the
        deployment -- 200 from ``index.html`` with a build, 307 to ``/api`` or
        ``/docs`` without -- so only "answered, with no body" is pinned here.

        ``follow_redirects=False`` is load-bearing: with the client's default the
        307 is followed and the build-less deployment reports the redirected
        GET's 200, which reads as "the mount answered" -- the opposite of what
        happened (§53.112 made the same point about 307 -> 200 looking like one
        200).
        """
        head = self.client.head(FALLBACK_ROOT_PATH, follow_redirects=False)

        self.assertIn(
            head.status_code,
            {200, 307},
            "HEAD / must be answered by its GET, not refused as a method",
        )
        self.assertEqual(head.content, b"")
        if head.status_code == 307:
            self.assertTrue(
                head.headers.get("location"), "the fallback redirect lost its target"
            )
        else:
            self.assertTrue(
                (head.headers.get("content-type") or "").startswith("text/html"),
                "the mount did not answer with the built page",
            )


if __name__ == "__main__":
    unittest.main()
