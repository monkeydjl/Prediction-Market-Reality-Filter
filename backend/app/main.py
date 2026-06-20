import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.rate_limit import InMemoryRateLimitMiddleware
from app.core.scheduler import start_scheduler, stop_scheduler


setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("EIP v0.3.0 starting - app: /")
    if not settings.OPENAI_API_KEY:
        logger.critical("OPENAI_API_KEY is empty — LLM calls will fail at runtime")
    else:
        logger.info("OPENAI_API_KEY is configured (%.3s...)", settings.OPENAI_API_KEY[:3])
    if settings.API_WRITE_KEY:
        logger.info("API_WRITE_KEY is configured (len=%d)", len(settings.API_WRITE_KEY))
    else:
        logger.warning("API_WRITE_KEY is empty — write endpoints are public")
    start_scheduler()
    try:
        yield
    finally:
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
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
async def api_health():
    from app.core.scheduler import scheduler
    from app.services.loop_status_service import loop_status

    status = loop_status(scheduler_running=scheduler.running)
    failed_runs = [
        job
        for job, run in status.get("runs", {}).items()
        if run and run.get("status") == "failed"
    ]
    return {
        "status": "degraded" if failed_runs else "ok",
        "version": "0.3.0",
        "scheduler_running": scheduler.running,
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
