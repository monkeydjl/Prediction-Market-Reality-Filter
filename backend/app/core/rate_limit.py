import time
from collections import defaultdict, deque
from typing import Deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings


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
    def __init__(self, app):
        super().__init__(app)
        self._hits: dict[str, Deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):
        if not settings.RATE_LIMIT_ENABLED:
            return await call_next(request)

        window = max(1, settings.RATE_LIMIT_WINDOW_SECONDS)
        limit = max(1, settings.RATE_LIMIT_MAX_REQUESTS)
        now = time.monotonic()
        self._prune(now, window)
        client = _client_host(request)
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


def _client_host(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
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
