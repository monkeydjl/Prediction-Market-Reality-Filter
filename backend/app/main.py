import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, Request, Response, status as http_status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.api.security import is_write_key_valid
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.rate_limit import InMemoryRateLimitMiddleware
from app.core.scheduler import start_scheduler, stop_scheduler
from app.services.llm_startup_check_service import validate_primary_llm_startup


setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("EIP v0.3.0 starting - app: /")
    if not settings.OPENAI_API_KEY:
        logger.critical("OPENAI_API_KEY is empty — LLM calls will fail at runtime")
    else:
        logger.info("OPENAI_API_KEY is configured (len=%d)", len(settings.OPENAI_API_KEY))
    if settings.LLM_STARTUP_CHECK_ENABLED:
        await validate_primary_llm_startup()
        logger.info("Primary LLM startup check passed.")
    if settings.API_WRITE_KEY:
        logger.info("API_WRITE_KEY is configured (len=%d)", len(settings.API_WRITE_KEY))
    elif settings.ALLOW_OPEN_WRITES:
        logger.warning(
            "API_WRITE_KEY is empty and ALLOW_OPEN_WRITES=true — write endpoints "
            "are PUBLIC. Never use this in production."
        )
    else:
        # Fail closed: an empty key with no explicit opt-in would silently expose
        # every mutating endpoint (manual resolve, auto-resolve, discover/analyze
        # LLM spend). Refuse to boot rather than run wide open.
        raise RuntimeError(
            "API_WRITE_KEY is empty. Set API_WRITE_KEY to protect write endpoints, "
            "or set ALLOW_OPEN_WRITES=true to explicitly run with public writes "
            "(local dev only)."
        )
    scheduler_started = False
    if settings.SCHEDULER_ENABLED:
        scheduler_started = start_scheduler()
    else:
        logger.warning("Scheduler disabled by SCHEDULER_ENABLED=false")
    try:
        yield
    finally:
        if scheduler_started:
            stop_scheduler()


app = FastAPI(
    title="Event Intelligence Platform",
    version="0.3.0",
    description=(
        "AI-powered event discovery and probability analysis. "
        "Collects public information, extracts evidence, scores credibility, "
        "and estimates probability change."
    ),
    lifespan=lifespan,
)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    ),
}


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        if name not in response.headers:
            response.headers[name] = value
    return response


def _validate_cors_settings() -> None:
    if settings.CORS_ALLOW_CREDENTIALS and "*" in settings.CORS_ALLOWED_ORIGINS:
        raise RuntimeError(
            "CORS_ALLOW_CREDENTIALS=true cannot be used with "
            "CORS_ALLOWED_ORIGINS=*."
        )
    for name, values in (
        ("CORS_ALLOWED_METHODS", settings.CORS_ALLOWED_METHODS),
        ("CORS_ALLOWED_HEADERS", settings.CORS_ALLOWED_HEADERS),
    ):
        if "*" in values:
            raise RuntimeError(f"{name} must list explicit values, not '*'.")


_validate_cors_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=settings.CORS_ALLOWED_METHODS,
    allow_headers=settings.CORS_ALLOWED_HEADERS,
)
app.add_middleware(InMemoryRateLimitMiddleware)

# All JSON API routers live under /api so the single-page app can own the root
# paths. Without this the frontend's /events page would collide with the events
# API at /events.
app.include_router(api_router, prefix="/api")


@app.get("/api")
async def api_overview():
    return {
        "system": "Event Intelligence Platform",
        "version": "0.3.0",
        "app": "/",
        "docs": "/docs",
        "endpoints": {
            # Event discovery & analysis
            "event_discovery": "GET  /api/events/discover",
            "event_analysis": "POST /api/events/analyze",
            "event_list": "GET  /api/events/",
            "event_detail": "GET  /api/events/{event_id}",
            "event_history": "GET  /api/events/{event_id}/history",
            "event_movers": "GET  /api/events/movers",
            "event_similar": "GET  /api/events/{event_id}/similar",
            # Reality-feedback loop
            "event_auto_resolve": "POST /api/events/resolve/auto",
            "open_decisions": "GET  /api/events/decisions/open",
            "event_decision": "GET  /api/events/{event_id}/decision",
            "fresh_edges": "GET  /api/events/edges/fresh",
            "loop_status": "GET  /api/events/loop/status",
            # Calibration
            "event_calibration": "GET  /api/events/calibration",
            "prediction_calibration": "GET  /api/events/predictions/calibration",
        },
    }


@app.get("/api/health")
async def api_health(
    response: Response,
    x_api_key: str | None = Header(default=None),
):
    from app.core.scheduler import scheduler, scheduler_start_skipped_due_to_lock
    from app.services.loop_status_service import loop_status

    status = loop_status(
        scheduler_running=scheduler.running,
        include_run_details=is_write_key_valid(x_api_key),
    )
    failed_runs = [
        job
        for job, run in status.get("runs", {}).items()
        if run and run.get("status") == "failed"
    ]
    degraded = bool(failed_runs) or (
        settings.SCHEDULER_ENABLED
        and not scheduler.running
        and not scheduler_start_skipped_due_to_lock()
    )
    # Return 503 when degraded so container/systemd healthchecks and external
    # uptime monitors actually trip instead of seeing a perpetual 200. The body
    # still carries the detail for a human reading the response.
    response.status_code = (
        http_status.HTTP_503_SERVICE_UNAVAILABLE if degraded else http_status.HTTP_200_OK
    )
    return {
        "status": "degraded" if degraded else "ok",
        "version": "0.3.0",
        "scheduler_running": scheduler.running,
        "scheduler_enabled": settings.SCHEDULER_ENABLED,
        "scheduler_lock_skipped": scheduler_start_skipped_due_to_lock(),
        "failed_runs": failed_runs,
        "loop": status,
    }


# Next.js dashboard (static export). Built with `npm run build` in ../frontend,
# which emits a root-based export into frontend/out. Served at the site root so
# the homepage is the app itself (no /app prefix). Mounted LAST so the explicit
# /api, /dashboard and /docs routes above win, and the SPA catch-all only serves
# the frontend's own pages and assets. Mounted only if present so the API still
# boots before the first frontend build.
_FRONTEND_OUT = Path(__file__).parent.parent.parent / "frontend" / "out"
if _FRONTEND_OUT.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_OUT), html=True), name="frontend")
