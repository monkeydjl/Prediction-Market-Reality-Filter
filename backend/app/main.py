import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, Request, Response, status as http_status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from app.api.router import api_router
from app.api.security import is_write_key_valid
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.rate_limit import InMemoryRateLimitMiddleware
from app.core.scheduler import start_scheduler, stop_scheduler
from app.services.llm_gateway_service import has_configured_llm_route
from app.services.llm_startup_check_service import validate_primary_llm_startup
from app.utils import sqlite_db


setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("EIP v0.3.0 starting - app: /")
    if has_configured_llm_route("default"):
        logger.info("LLM Gateway route is configured")
    elif not settings.OPENAI_API_KEY:
        logger.critical("No configured LLM route/API key — LLM calls will fail at runtime")
    else:
        logger.info("OPENAI_API_KEY is configured (len=%d)", len(settings.OPENAI_API_KEY))
    # A cap of 0 disables the guard entirely (see _cost_cap_exceeded), which is
    # the shipped default. That is fine for a keyless dev box but means a paid
    # key runs with no ceiling, so say so out loud rather than only in
    # .env.example. Not fail-closed like API_WRITE_KEY: spend is recoverable,
    # and refusing to boot would break every existing deployment.
    if settings.LLM_DAILY_COST_CAP_USD <= 0:
        logger.warning(
            "LLM_DAILY_COST_CAP_USD=%s — daily LLM spend is UNLIMITED. Set a "
            "positive number to cap it.",
            settings.LLM_DAILY_COST_CAP_USD,
        )
    else:
        logger.info(
            "Daily LLM spend cap: $%.2f", settings.LLM_DAILY_COST_CAP_USD
        )
    # Initialize Sentry before any route / scheduler runs so failures during
    # startup (e.g., LLM check) get captured. No-op when SENTRY_DSN is empty.
    from app.utils.sentry import init_sentry
    init_sentry(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT,
        release=settings.SENTRY_RELEASE or "pmrf@0.3.0",
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        attach_stacktrace=settings.SENTRY_ATTACH_STACKTRACES,
    )
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
    # The loop DB keeps its fail-loud boot semantics: it backs the run ledger and
    # the calibration history every other job writes through, so starting on a
    # corrupt one would append to a broken file.
    sqlite_maintenance = sqlite_db.maintain()
    logger.info("Loop DB maintenance passed: %s", sqlite_maintenance)

    # The other SQLite state stores get checked too, but do not block startup: a
    # corrupt domain-reliability DB degrades source scoring, it does not make the
    # app unusable, and refusing to boot would take away the dashboard the
    # operator needs to see the problem. Silence was the actual defect —
    # measured, a corrupt kernel DB (33,882 prediction rows) booted clean and
    # /api/health answered 200 "ok". A failed run row is what makes health report
    # `degraded`, so the finding survives the log scrollback nobody reads.
    try:
        other_stores = sqlite_db.maintain_all()
        if not other_stores["ok"]:
            failed = other_stores.get("failed") or []
            logger.error(
                "SQLite integrity failed at startup for: %s — /api/health will "
                "report degraded. Restore these stores from a backup.",
                ", ".join(failed),
            )
            from app.memory import loop_run_store

            run_id = loop_run_store.start_run("loop_db_maintenance")
            loop_run_store.finish_run(
                run_id,
                "failed",
                error=f"SQLite integrity failed for: {', '.join(failed)}",
                result=other_stores,
            )
        else:
            logger.info(
                "SQLite maintenance passed for %d store(s)",
                len(other_stores.get("stores") or {}),
            )
    except Exception as exc:
        logger.warning("SQLite store maintenance skipped: %s", exc, exc_info=True)

    # Heal orphan predictions left by crashes during resolve_with_calibration()
    # (event resolved in JSON but prediction still 'open' in SQLite).
    try:
        from app.services.event_resolve_service import reconcile_predictions
        healed = reconcile_predictions()
        if healed:
            logger.info("Startup reconciliation healed %d orphan predictions", healed)
    except Exception as exc:
        logger.warning("Startup reconciliation skipped: %s", exc, exc_info=True)

    # Same shape for background optimization tasks: a 'pending'/'running' row in
    # SQLite after a restart has no asyncio.Task behind it, so /auto-tune/status
    # would report it running forever and cleanup_old_tasks (terminal statuses
    # only) would never prune it.
    try:
        from app.services.optimization_task_manager import get_task_manager
        interrupted = await get_task_manager().reconcile_interrupted_tasks()
        if interrupted:
            logger.info(
                "Startup reconciliation failed %d interrupted optimization task(s)",
                interrupted,
            )
    except Exception as exc:
        logger.warning(
            "Optimization task reconciliation skipped: %s", exc, exc_info=True
        )

    # Initialize World Cup prediction database (creates tables if missing)
    try:
        from app.utils.prediction_db import init_prediction_db
        init_prediction_db()
        logger.info("World Cup prediction DB initialized.")
    except Exception as exc:
        logger.warning("World Cup prediction DB init skipped: %s", exc, exc_info=True)

    # Score finished matches (feedback loop reconciliation)
    try:
        from app.services.world_cup_scoring_service import score_all_finished_matches
        scoring_result = score_all_finished_matches()
        logger.info("Match scoring reconciliation: %s", scoring_result)
    except Exception as exc:
        logger.warning("Match scoring reconciliation skipped: %s", exc, exc_info=True)

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
    # CSP hardening for the operator-key XSS threat model (P-9 mitigation).
    #
    # The frontend stores API_WRITE_KEY in sessionStorage and sends it on
    # every write request as X-API-Key. An XSS could read it and exfiltrate.
    # The complete fix is nonce-based CSP, but this app ships a Next.js static
    # export (frontend/out/, no SSR runtime), so per-request nonces are not
    # possible without major refactoring. The directives below harden what we
    # can without breaking the app:
    #
    #   - script-src still carries 'unsafe-inline' because Next.js's static
    #     export emits inline <script> tags with hydration data (__NEXT_DATA__).
    #     Removing it breaks every page. The connect-src 'self' directive
    #     limits the blast radius: an injected inline script cannot fetch()
    #     to an attacker-controlled origin, so it cannot directly exfiltrate
    #     the operator key.
    #   - img-src tightened to 'self' data: — the app loads no external images,
    #     so the previous 'https:' wildcard (which allowed any HTTPS image) is
    #     unnecessary attack surface.
    #   - form-action 'self' prevents XSS-driven <form> exfiltration (an
    #     attacker can't POST the operator key to their server via a form).
    #   - frame-src 'none' explicitly forbids iframes (frame-ancestors only
    #     restricts who can frame US; frame-src restricts who WE can frame).
    #   - upgrade-insecure-requests + block-all-mixed-content ensure no
    #     HTTP subresource can be injected on an HTTPS page.
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-src 'none'; "
        "frame-ancestors 'none'; "
        "upgrade-insecure-requests; "
        "block-all-mixed-content"
    ),
}


@app.middleware("http")
async def add_security_headers(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
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
async def api_overview() -> dict[str, Any]:
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
) -> dict[str, Any]:
    from app.core.scheduler import scheduler, scheduler_start_skipped_due_to_lock
    from app.services.loop_status_service import loop_status

    status = await asyncio.to_thread(
        loop_status,
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


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
    """Prometheus metrics endpoint (P0-6 §1.1).

    Exposes the default ``prometheus_client`` registry in text exposition
    format. Refreshes aggregate gauges (direction counts, consensus
    distribution, scheduler last-success, calibration Brier) before each
    scrape so values stay fresh without per-event instrumentation.

    Public endpoint — no ``X-API-Key`` required (Prometheus scrapers cannot
    authenticate, and the metrics are aggregate counters only, no per-event
    operator-grade intelligence).
    """
    from app.utils.metrics import render_metrics

    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


# Next.js dashboard (static export). Built with `npm run build` in ../frontend,
# which emits a root-based export into frontend/out. Served at the site root so
# the homepage is the app itself (no /app prefix). Mounted LAST so the explicit
# /api, /dashboard and /docs routes above win, and the SPA catch-all only serves
# the frontend's own pages and assets. Mounted only if present so the API still
# boots before the first frontend build.
_FRONTEND_OUT = Path(__file__).parent.parent.parent / "frontend" / "out"
if settings.BACKEND_SERVE_FRONTEND and _FRONTEND_OUT.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_OUT), html=True), name="frontend")
else:
    @app.get("/", include_in_schema=False)
    async def backend_root() -> RedirectResponse:
        return RedirectResponse(url="/docs")
