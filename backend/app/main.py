import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.api.routes import scanner
from app.core.logging import setup_logging
from app.core.scheduler import start_scheduler, stop_scheduler


setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("EIP v0.3.0 starting - app: /, dashboard: /dashboard")
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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_STATIC = Path(__file__).parent.parent / "static"
_STATIC.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

# All JSON API routers live under /api so the single-page app can own the root
# paths. Without this the frontend's /events page would collide with the events
# API at /events. /api/scan keeps the legacy scanner reachable under the prefix.
app.include_router(scanner.router, prefix="/api/scan", tags=["Scanner"])
app.include_router(api_router, prefix="/api")


@app.get("/dashboard", include_in_schema=False)
async def serve_dashboard():
    dashboard = _STATIC / "index.html"
    if dashboard.exists():
        return FileResponse(str(dashboard))
    return {"error": "Dashboard not found"}


@app.get("/dashboard/zh", include_in_schema=False)
async def serve_dashboard_zh():
    dashboard = _STATIC / "index_zh.html"
    if dashboard.exists():
        return FileResponse(str(dashboard))
    return {"error": "Dashboard not found"}


@app.get("/dashboard_zh", include_in_schema=False)
async def serve_dashboard_zh_compat():
    return await serve_dashboard_zh()


@app.get("/api")
async def api_overview():
    return {
        "system": "Event Intelligence Platform",
        "version": "0.3.0",
        "app": "/",
        "dashboard": "/dashboard",
        "dashboard_zh": "/dashboard_zh",
        "docs": "/docs",
        "endpoints": {
            # Event Intelligence
            "event_discovery": "GET  /api/events/discover",
            "event_analysis": "POST /api/events/analyze",
            "event_list": "GET  /api/events/",
            "event_detail": "GET  /api/events/{event_id}",
            "event_history": "GET  /api/events/{event_id}/history",
            "event_movers": "GET  /api/events/movers",
            "event_similar": "GET  /api/events/{event_id}/similar",
            # Scanner compatibility
            "signal_summary": "GET  /api/scan/summary",
            "debug_market": "GET  /api/scan/debug",
            "full_scan": "GET  /api/scan/",
            "deep_scan": "GET  /api/scan/deep",
            "cached_results": "GET  /api/scan/cache",
            # Analysis compatibility
            "manual_analysis": "POST /api/analysis/",
            # Calibration
            "calibration": "GET  /api/calibration/",
            "history": "GET  /api/calibration/history",
            "audit_summary": "GET  /api/calibration/summary",
            # Backtest
            "backtest_baseline": "GET  /api/backtest/baseline",
            "backtest_base_rate": "GET  /api/backtest/base-rate",
            # Resolve
            "auto_resolve": "POST /api/resolve/auto",
            "manual_resolve": "POST /api/resolve/manual",
            "pending": "GET  /api/resolve/pending",
            # Trades are retained for historical records, not product direction.
            "open_trade": "POST /api/trades/open",
            "close_trade": "POST /api/trades/close/{id}",
            "trade_summary": "GET  /api/trades/summary",
            "trade_list": "GET  /api/trades/",
            # Data
            "markets": "GET  /api/markets/",
            "news": "GET  /api/news/",
        },
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
