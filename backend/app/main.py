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
    logger.info("EIP v0.3.0 starting - dashboard: /dashboard")
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

# Next.js dashboard (static export). Built with `npm run build` in ../frontend,
# which emits an /app-based export into frontend/out. Mounted only if present so
# the API still boots before the first frontend build.
_FRONTEND_OUT = Path(__file__).parent.parent.parent / "frontend" / "out"
if _FRONTEND_OUT.is_dir():
    app.mount("/app", StaticFiles(directory=str(_FRONTEND_OUT), html=True), name="frontend")

app.include_router(scanner.router, prefix="/scan", tags=["Scanner"])
app.include_router(api_router)


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


@app.get("/")
async def root():
    return {
        "system": "Event Intelligence Platform",
        "version": "0.3.0",
        "dashboard": "/dashboard",
        "dashboard_zh": "/dashboard_zh",
        "docs": "/docs",
        "endpoints": {
            # Event Intelligence
            "event_discovery": "GET  /events/discover",
            "event_analysis": "POST /events/analyze",
            "event_list": "GET  /events/",
            "event_detail": "GET  /events/{event_id}",
            "event_history": "GET  /events/{event_id}/history",
            "event_movers": "GET  /events/movers",
            "event_similar": "GET  /events/{event_id}/similar",
            # Scanner compatibility
            "signal_summary": "GET  /scan/summary",
            "debug_market": "GET  /scan/debug",
            "full_scan": "GET  /scan/",
            "deep_scan": "GET  /scan/deep",
            "cached_results": "GET  /scan/cache",
            # Analysis compatibility
            "manual_analysis": "POST /analysis/",
            # Calibration
            "calibration": "GET  /calibration/",
            "history": "GET  /calibration/history",
            "audit_summary": "GET  /calibration/summary",
            # Backtest
            "backtest_baseline": "GET  /backtest/baseline",
            "backtest_base_rate": "GET  /backtest/base-rate",
            # Resolve
            "auto_resolve": "POST /resolve/auto",
            "manual_resolve": "POST /resolve/manual",
            "pending": "GET  /resolve/pending",
            # Trades are retained for historical records, not product direction.
            "open_trade": "POST /trades/open",
            "close_trade": "POST /trades/close/{id}",
            "trade_summary": "GET  /trades/summary",
            "trade_list": "GET  /trades/",
            # Data
            "markets": "GET  /markets/",
            "news": "GET  /news/",
        },
    }
