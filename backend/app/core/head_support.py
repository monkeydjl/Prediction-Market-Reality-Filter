"""HEAD for the API namespace: answered as GET, without the body.

FastAPI's ``APIRoute`` never adds ``HEAD`` to a GET route -- ``self.methods =
{method.upper() for method in methods}`` -- while Starlette's own ``Route`` does
(``if "GET" in self.methods: self.methods.add("HEAD")``). The same-shaped route
therefore answers ``curl -I`` differently depending on which class registered it:
measured on this app, ``HEAD /api`` was 405 ``Allow: GET`` while ``HEAD
/nope-page`` was an ordinary 404 carrying the SPA page's headers.

That matters for the one thing HEAD is for. ``GET /api/events/`` is a 684 KB
response (measured); a client that wants the status, the type and the size before
deciding to download it had no way to ask, and ``HEAD /api/health`` -- the obvious
monitor probe -- was a 405 as well.

Why a middleware and not a route class: ``include_router`` keeps each route's own
class, so adding HEAD to ``methods`` would mean touching every ``APIRouter`` under
``app/api``; and a response class would still emit the body, because
``Response.__call__`` sends ``self.body`` whatever the method is (only
``FileResponse`` implements ``send_header_only``).

Scope is "the endpoints this app answers itself": the API namespace -- the same
predicate the static fence uses -- plus the ones registered outside it
(``EXTRA_HEAD_PATHS``, measured: ``/metrics`` is the only one). Outside that
nothing changes: pages and assets are served by ``FileResponse``, which already
answers HEAD correctly, and ``/docs``, ``/redoc``, ``/openapi.json`` are
Starlette ``Route`` objects, which add HEAD themselves.

``/metrics`` was the one gap left after §53.113: ``HEAD`` there answered 404 with
``content-length: 36300`` -- the SPA page's size, for an endpoint that serves
6 340 bytes of text. A monitor asking "how big is this scrape" was told about a
page the route does not serve.

🔴 HEAD on a handler that returns a stream is left alone (405, as before), and
that is deliberate: ``GET .../batch-switch-engine-stream`` starts a batch job and
then streams its progress, so rewriting HEAD would make a probe start real work.
``STREAMING_PATHS`` names the exceptions; the test that pins it re-derives the
expected set from the route table's return annotations, so a streaming handler
cannot join the app unnoticed.
"""

from __future__ import annotations

from starlette._utils import get_route_path
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.api_namespace import is_api_path

#: GET handlers that start a stream (and, here, real work). A HEAD must not run
#: one: it stays the 405 it is today. Pinned by `test_head_support` against the
#: route table, not by memory.
STREAMING_PATHS = frozenset({
    "/api/world-cup/predictions/batch-switch-engine-stream",
})

#: `main.py` registers `GET /` only when the frontend build is absent; with a
#: build, `frontend/out` is mounted at `/` instead. So `/` is an `APIRoute` in
#: one deployment and a mount in the other -- which is why the census test reads
#: that decision from the route table instead of re-deriving it (§53.136).
FALLBACK_ROOT_PATH = "/"

#: Endpoints registered outside the ``/api`` namespace that need HEAD answered
#: from their GET. They have the same defect -- ``APIRoute`` never adds HEAD --
#: but they are not API paths, so the fence's predicate does not reach them.
#:
#: ``/metrics`` is present in every deployment. ``/`` is present only when there
#: is no frontend build (``FALLBACK_ROOT_PATH``); listing it unconditionally is
#: what makes ``HEAD /`` answer like its GET in the pre-build deployment instead
#: of a 405, and the census subtracts it when the static mount is present so both
#: deployments agree. The census re-derives this set from the route table, so a
#: third one cannot appear without turning that test red. A hand-kept list
#: nobody re-counts would rot silently (§53.105).
EXTRA_HEAD_PATHS = frozenset({"/metrics", FALLBACK_ROOT_PATH})


def answers_head_as_get(route_path: str) -> bool:
    """Whether HEAD on this path should be answered by running its GET."""
    return is_api_path(route_path) or route_path in EXTRA_HEAD_PATHS


class ApiHeadAsGetMiddleware:
    """Answer ``HEAD`` in the API namespace by running the ``GET``, minus the body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "HEAD":
            await self.app(scope, receive, send)
            return

        route_path = get_route_path(scope)
        if not answers_head_as_get(route_path) or route_path in STREAMING_PATHS:
            await self.app(scope, receive, send)
            return

        # A copy: the scope is shared with every layer below, and rewriting a
        # mapping other code may re-read is the kind of thing that only shows up
        # somewhere else.
        rewritten = dict(scope)
        rewritten["method"] = "GET"

        body_sent = False

        async def send_headers_only(message: Message) -> None:
            nonlocal body_sent
            if message["type"] != "http.response.body":
                await send(message)
                return
            if body_sent:
                return
            body_sent = True
            # Content-Length stays whatever GET announced: HEAD describes the
            # GET response's representation, it does not send it. A second body
            # message (a stream) is dropped -- the non-streaming paths that reach
            # here never produce one.
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        await self.app(rewritten, receive, send_headers_only)
