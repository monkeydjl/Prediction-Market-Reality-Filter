"""Static serving for the built frontend, with the API namespace left alone.

``app.mount("/", StaticFiles(..., html=True))`` is a catch-all: it matches every
request the router did not, which means it also answers for paths the router
refused. Three of its answers are wrong for the API namespace, all measured on
this app before the class below existed:

* ``GET  /api/typo``   -> 404 with 36 300 bytes of ``404.html``, ``text/html``
* ``POST /api/typo``   -> 405 ``Method Not Allowed``, though nothing lives there
* ``POST /api/health`` -> 405, but the ``Allow: GET`` header is gone

The last one is the subtle one. Starlette's router remembers a *partial* match
(the path matched, the method did not) and only raises 405 from it **after** the
whole route table has been scanned without a full match; the mount is a full
match, so it wins the scan and the router's own answer -- status *and* headers --
is never reached. That is also why the parent class is not simply asked for a
404: asking it would inherit the same swallow.

So for the API namespace this class asks the router the question the router
would have asked, in the same order, and otherwise answers with the JSON 404 an
API-only deployment produces (404, ``{"detail": "Not Found"}``,
``content-type: application/json``). The result is that ``/api`` behaves the same
whether or not the frontend was built.

Everything outside ``/api`` keeps the parent behaviour exactly: pages, assets,
the SPA 404 page, and the 405 for a non-GET request to a real file.

The router's trailing-slash redirect is reproduced as well. With a catch-all
mount in the route table, ``/api/health/`` never reached ``Router.app``'s
``redirect_slashes`` branch either, so it was a 404 where an API-only deployment
307s to ``/api/health``. Measuring first was worth it: the plain app answers
``POST /api/health/`` with 307 -> 405, and ``/api/`` with 307 -> the overview.
The mirror skips ``Mount`` routes, because the only mount is this class and it
matches every alternative path -- counting it would turn every unknown
``/api/.../`` into a redirect to its de-slashed twin: a second round trip that
lands on the same 404.
"""

from __future__ import annotations

from collections.abc import Iterable

from starlette._utils import get_route_path
from starlette.datastructures import URL
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import BaseRoute, Match, Mount
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

from app.core.api_namespace import is_api_path


def first_partial(routes: Iterable[BaseRoute], scope: Scope) -> tuple[BaseRoute, Scope] | None:
    """The route whose path matched and whose methods did not, if any.

    ``Router.app`` takes the *first* partial match too, and only after scanning
    the whole table for a full one; no route can full-match here, because the
    router scanned them all and reached the mount instead.
    """
    for route in routes:
        match, child_scope = route.matches(scope)
        if match == Match.PARTIAL:
            return route, child_scope
    return None


def slash_redirect_target(routes: Iterable[BaseRoute], scope: Scope) -> URL | None:
    """Where ``Router.app``'s ``redirect_slashes`` branch would have sent this.

    A mirror of that branch, with one deliberate difference: ``Mount`` routes are
    skipped when probing the alternative path. In this app the only mount is the
    catch-all this class serves, and it matches *every* alternative, so including
    it would make an unknown ``/api/typo/`` redirect to ``/api/typo`` only to 404
    there -- the same answer after an extra round trip, and a redirect for a path
    that has nothing at either spelling.
    """
    route_path = get_route_path(scope)
    if not route_path or route_path == "/":
        return None
    alternative = dict(scope)
    alternative["path"] = (
        alternative["path"].rstrip("/")
        if route_path.endswith("/")
        else alternative["path"] + "/"
    )
    for route in routes:
        if isinstance(route, Mount):
            continue
        match, _ = route.matches(alternative)
        if match != Match.NONE:
            return URL(scope=alternative)
    return None


class ApiAwareStaticFiles(StaticFiles):
    """The frontend mount, except it refuses to answer for ``/api``.

    The mount is expected at ``"/"``, which is what ``app/main.py`` does: the
    paths checked here are read the same way the parent reads them, relative to
    the mount point.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or not is_api_path(get_route_path(scope)):
            await super().__call__(scope, receive, send)
            return

        # The router that dispatched to this mount: Starlette stores it on the
        # scope on the way in, so this is the same route table the request was
        # matched against. Without one (a bare ASGI call, no router in front)
        # there is no answer to reproduce, and the 404 below is still the right
        # shape for an API path.
        router = scope.get("router")
        if router is not None:
            partial = first_partial(router.routes, scope)
            if partial is not None:
                route, child_scope = partial
                scope.update(child_scope)
                await route.handle(scope, receive, send)
                return
            if router.redirect_slashes:
                target = slash_redirect_target(router.routes, scope)
                if target is not None:
                    await RedirectResponse(str(target))(scope, receive, send)
                    return

        response = JSONResponse({"detail": "Not Found"}, status_code=404)
        await response(scope, receive, send)

