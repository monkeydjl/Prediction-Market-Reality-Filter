import logging
import time
from collections import defaultdict, deque
from typing import Deque

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import settings


logger = logging.getLogger(__name__)

_MAX_BUCKETS = 4096
_STATIC_PATH_SEGMENTS = {
    "_next",
    "api",
    "auto",
    "calibration",
    "decision",
    "decisions",
    "discover",
    "docs",
    "edges",
    "events",
    "fresh",
    "health",
    "history",
    "link",
    "links",
    "loop",
    "movers",
    "open",
    "pending",
    "predictions",
    "recent",
    "redoc",
    "resolve",
    "similar",
    "status",
    "tracking",
    "verify",
}


class InMemoryRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._hits: dict[str, Deque[float]] = defaultdict(deque)
        # Instance state, not a module global, so each app gets its own and the
        # conftest singleton-reset census has nothing to track.
        self._warned_proxy_mismatch = False

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not settings.RATE_LIMIT_ENABLED:
            return await call_next(request)

        window = max(1, settings.RATE_LIMIT_WINDOW_SECONDS)
        limit = max(1, settings.RATE_LIMIT_MAX_REQUESTS)
        now = time.monotonic()
        self._prune(now, window)
        client = _client_host(request)
        self._warn_once_on_proxy_mismatch(request)
        key = f"{client}:{request.method}:{_route_key(request)}"
        hits = self._hits[key]

        while hits and now - hits[0] >= window:
            hits.popleft()

        if len(hits) >= limit:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded",
                    "retry_after_seconds": window,
                },
                headers={"Retry-After": str(window)},
            )

        hits.append(now)
        self._enforce_bucket_cap()
        return await call_next(request)

    def _warn_once_on_proxy_mismatch(self, request: Request) -> None:
        """Log once when the traffic contradicts TRUSTED_PROXY_HEADER.

        Both mismatches silently destroy per-client throttling, and neither is
        visible from outside the process:

        * headers present, flag off — the documented default. `request.client`
          is then the proxy's own address on every request, so every caller
          shares one bucket and RATE_LIMIT_MAX_REQUESTS is a global cap. One
          busy client 429s everybody.
        * headers absent, flag on — the app is not behind the proxy it was told
          to trust, so every caller collapses onto the socket peer just the
          same.

        Once per process is the right cadence: the setting only changes on
        restart, and a per-request log would be its own denial of service.
        """
        if self._warned_proxy_mismatch:
            return
        has_proxy_headers = bool(
            _forwarded_chain(request) or request.headers.get("x-real-ip", "").strip()
        )
        trusted = bool(settings.TRUSTED_PROXY_HEADER)
        if has_proxy_headers == trusted:
            return
        self._warned_proxy_mismatch = True
        if has_proxy_headers:
            logger.warning(
                "Rate limiting is keyed off the socket peer but this request "
                "carried proxy headers: the app looks like it sits behind a "
                "reverse proxy while TRUSTED_PROXY_HEADER is false, so every "
                "caller shares one bucket and RATE_LIMIT_MAX_REQUESTS (%s per "
                "%ss) is a global cap rather than a per-client one. Set "
                "TRUSTED_PROXY_HEADER=true (and RATE_LIMIT_TRUSTED_PROXY_HOPS "
                "to the number of proxies in front) if that proxy is trusted.",
                settings.RATE_LIMIT_MAX_REQUESTS,
                settings.RATE_LIMIT_WINDOW_SECONDS,
            )
        else:
            logger.warning(
                "TRUSTED_PROXY_HEADER is true but this request carried no "
                "X-Forwarded-For or X-Real-IP header, so the app is not behind "
                "the trusted proxy it was configured for and every caller is "
                "keyed to the socket peer. Set TRUSTED_PROXY_HEADER=false for "
                "direct-to-internet deploys."
            )

    def _prune(self, now: float, window: int) -> None:
        for key, hits in list(self._hits.items()):
            while hits and now - hits[0] >= window:
                hits.popleft()
            if not hits:
                self._hits.pop(key, None)

    def _enforce_bucket_cap(self) -> None:
        overflow = len(self._hits) - _MAX_BUCKETS
        if overflow <= 0:
            return
        oldest = sorted(
            self._hits,
            key=lambda key: self._hits[key][-1] if self._hits[key] else 0,
        )
        for key in oldest[:overflow]:
            self._hits.pop(key, None)


def _forwarded_chain(request: Request) -> list[str]:
    """Every X-Forwarded-For address, left to right, blanks dropped.

    All instances of the header are joined before splitting: a caller may send
    the header more than once, and reading only the first would let them push
    our own proxy's entry out of the slice we trust.
    """
    raw = ",".join(request.headers.getlist("x-forwarded-for"))
    return [part.strip() for part in raw.split(",") if part.strip()]


def _client_host(request: Request) -> str:
    """The rate-limit identity for this request.

    The socket peer, unless the deployment declares itself to be behind trusted
    proxies — in which case the client address is read out of X-Forwarded-For
    **from the right**.

    Reading from the right is the whole point. X-Forwarded-For is a chain that
    grows left to right: each proxy appends the address it accepted the
    connection from, so the trailing entries are the ones our own
    infrastructure wrote and the leading entry is whatever the caller sent.
    `deploy/nginx.conf.example` forwards `$proxy_add_x_forwarded_for`, which
    *appends* to the client-supplied header rather than replacing it, so a
    caller sending `X-Forwarded-For: 10.0.0.1` makes this app see
    `10.0.0.1, <real peer>`. Trusting the leftmost entry there hands the
    rate-limit key to the caller: rotate the spoofed value and every request
    lands in a fresh bucket, which defeats per-IP throttling and any
    key-bruteforce detection built on it.

    RATE_LIMIT_TRUSTED_PROXY_HOPS says how many trailing entries we own, so the
    client is that many addresses from the right. One hop — the default — is
    correct for both shipped proxy examples: nginx appends the real peer, and
    Caddy replaces the header with it.

    A chain shorter than the declared hop count means the request did not
    traverse the proxies we were told to expect (the header was stripped, the
    hop count is wrong, or someone reached the app directly). We then fall back
    to X-Real-IP — which both shipped examples *replace*, so it is not
    caller-controlled — and finally to the socket peer. Never to the leftmost
    entry: a short chain is exactly the shape a spoofing caller produces, and
    falling back to it would reopen the hole this function exists to close.
    """
    if settings.TRUSTED_PROXY_HEADER:
        hops = max(1, settings.RATE_LIMIT_TRUSTED_PROXY_HOPS)
        chain = _forwarded_chain(request)
        if len(chain) >= hops:
            return chain[-hops]
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip
    return request.client.host if request.client else "unknown"


def _route_key(request: Request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if isinstance(route_path, str) and route_path:
        return route_path
    return _normalize_path(request.url.path)


def _normalize_path(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if not parts:
        return "/"
    normalized = []
    for part in parts:
        lower = part.lower()
        if lower in _STATIC_PATH_SEGMENTS or "." in part:
            normalized.append(part)
        else:
            normalized.append("{param}")
    return "/" + "/".join(normalized)
