import time
from collections import defaultdict, deque
from typing import Deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings


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
        client = request.client.host if request.client else "unknown"
        key = f"{client}:{request.method}:{request.url.path}"
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
        return await call_next(request)
