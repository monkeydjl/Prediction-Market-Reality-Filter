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
from app.core.preflight import (
    log_realtime_push_posture,
    validate_production_config,
)
from app.core.rate_limit import InMemoryRateLimitMiddleware
from app.core.scheduler import start_scheduler, stop_scheduler
from app.services.llm_gateway_service import has_configured_llm_route
from app.services.llm_startup_check_service import validate_primary_llm_startup
from app.utils import sqlite_db


setup_logging()
logger = logging.getLogger(__name__)


# Ledger job names for the two core startup components that report through
# storage. Deliberately distinct from the scheduler's own `loop_db_maintenance`
# job, which runs the same integrity check on a daily trigger: "the last boot
# found this" and "the last daily check found this" are different facts, and
# collapsing them would let one clear the other without re-testing it.
STARTUP_SQLITE_INTEGRITY_JOB = "startup_sqlite_integrity"
STARTUP_PREDICTION_DB_JOB = "startup_prediction_db_init"

# Startup steps split by what a failure *means*, because the original code did
# not split them at all: all six sat behind `except Exception: logger.warning`,
# so a database too damaged for PRAGMA integrity_check to parse produced the same
# outcome as an unreadable World Cup fact file -- a warning nobody reads and a
# 200 from /api/health.
#
# Core: maintains or initialises state the rest of the system writes through. A
# failure either aborts the boot (the loop DB, which backs the ledger itself) or
# is persisted as a `failed` run so /api/health answers 503.
#
# Optional: degrades one feature. Logged, and the app comes up -- refusing would
# take away the dashboard the operator needs to see the problem, and a false
# "unavailable" is its own outage.
CORE_STARTUP_COMPONENTS = (
    "loop_db_maintenance",         # sqlite_db.maintain(); fail-loud, see below
    STARTUP_SQLITE_INTEGRITY_JOB,  # sqlite_db.maintain_all()
    STARTUP_PREDICTION_DB_JOB,     # init_prediction_db()
)
OPTIONAL_STARTUP_COMPONENTS = (
    "event_prediction_reconcile",   # reconcile_predictions()
    "optimization_task_reconcile",  # reconcile_interrupted_tasks()
    "world_cup_match_scoring",      # score_all_finished_matches()
)


def _safe_error(what: str, exc: BaseException) -> str:
    """A failure description fit for the ledger and for /api/health.

    Names the component and the exception *type*, never `str(exc)`. The row's
    `error` column is returned verbatim to an authenticated /api/health caller,
    and a sqlite3 or OSError message routinely carries the database path.
    Production logging preserves the operation and exception type while its
    handler filter removes traceback and exception details.
    """
    return f"{what}: {type(exc).__name__}"


def _record_core_startup_outcome(
    job: str,
    error: str | None,
    result: dict[str, Any] | None = None,
) -> None:
    """Persist one core startup component's outcome to the loop_runs ledger.

    This is the only thing that carries a startup finding past the log file:
    `/api/health` derives `failed_runs` from `latest_run_per_job`, so a `failed`
    row here is what makes the probe answer 503 -- and it survives the restart an
    operator reaches for first. Retention already exempts each job's newest row,
    so the cleanup job cannot delete the reason health is degraded.

    A success row is written on every clean boot too, not only a failure row.
    Nothing re-checks `init_prediction_db` on a schedule, so without it one bad
    boot would pin /api/health at 503 for the life of the install and restoring
    the store could never clear it.

    A ledger write that fails refuses the boot in *both* directions, and the
    success direction is the less obvious one. The reason is not that the success
    row is precious: the ledger is the only place any startup or scheduler
    failure is durably recorded, and `/api/health` derives its whole verdict from
    it, so a boot that cannot write there has proved the health mechanism is
    dead. Coming up anyway serves 200 indefinitely -- every later failure is
    equally unrecordable, and "an earlier failed row still stands" is not a
    fallback, because writing such a row is precisely what does not work.

    A half-written row is why this cannot be softened to "ignore it if the row is
    a success". `start_run` succeeding and `finish_run` raising leaves a `running`
    row nothing will finish, and the probe degrades only on `status == "failed"`
    -- so the residue is indistinguishable from a job still in progress.
    """
    from app.memory import loop_run_store

    try:
        run_id = loop_run_store.start_run(job)
        loop_run_store.finish_run(
            run_id,
            "failed" if error else "success",
            error=error,
            result=result or {},
        )
    except Exception as exc:
        if error is None:
            raise RuntimeError(
                f"Core startup component {job!r} passed, but its outcome could "
                f"not be recorded in the run ledger ({type(exc).__name__}). The "
                f"ledger is what /api/health reports through, so the probe would "
                f"answer 200 while no failure -- now or later -- could ever be "
                f"recorded. Refusing to start."
            ) from exc
        raise RuntimeError(
            f"Core startup component {job!r} failed and the failure could not be "
            f"recorded in the run ledger ({type(exc).__name__}), so /api/health "
            f"would answer 200 over a broken core component. Refusing to start."
        ) from exc


def _live_alert_channels(sentry_live: bool) -> list[str]:
    """Push channels that would actually reach somebody outside this process.

    Each channel needs two things, and counting one of them is what would make
    the startup line a lie: a DSN with no importable `sentry_sdk` captures
    nothing (`init_sentry` says so and returns False, which is the `sentry_live`
    argument), and an enabled webhook with no URL only writes the local WARNING
    the dispatcher already writes. The loop-run ledger, the Prometheus counter
    and `/api/health` are all pull-only, so none of them counts here.
    """
    channels = []
    if sentry_live:
        channels.append("Sentry")
    if (settings.SCHEDULER_FAILURE_ALERT_ENABLED
            and settings.SCHEDULER_FAILURE_ALERT_WEBHOOK_URL):
        channels.append("scheduler-failure webhook")
    if settings.DRIFT_ALERTS_ENABLED and settings.DRIFT_ALERT_WEBHOOK_URL:
        channels.append("calibration-drift webhook")
    return channels


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("EIP v0.3.0 starting - app: /")
    # Before anything opens a database, spends a token or binds a port: a
    # production process whose configuration does not meet the production
    # requirements must not come up at all. No-op outside PMRF_ENV=production.
    validate_production_config()
    log_realtime_push_posture()
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
    sentry_live = init_sentry(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT,
        release=settings.SENTRY_RELEASE or "pmrf@0.3.0",
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        attach_stacktrace=settings.SENTRY_ATTACH_STACKTRACES,
    )
    # Every push channel ships off, and none of the five settings behind them is
    # named in either overlay template, in deploy/docker-compose.yml or in the
    # systemd unit -- so the documented production deploy has none live and
    # nothing said so. A failed job is still recorded (ledger row, Prometheus
    # counter, /api/health 503); what is absent is anything that goes looking for
    # an operator. Warn like the cost cap rather than refuse to boot: missing
    # alerting breaks nothing that already works.
    alert_channels = _live_alert_channels(sentry_live)
    if alert_channels:
        logger.info("Alert push channels live: %s", ", ".join(alert_channels))
    else:
        logger.warning(
            "No alert push channel is configured — a failed job is recorded "
            "(loop_runs ledger, pmrf_scheduler_failed_runs_total, /api/health "
            "503) but nothing notifies anybody. Set SENTRY_DSN, or "
            "SCHEDULER_FAILURE_ALERT_ENABLED=true with a webhook URL."
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
    # `maintain_all` has three outcomes, and only two of them used to be handled.
    # It reports a corrupt store by *returning* ok=False, which was recorded
    # correctly. It also *raises* -- sqlite3.DatabaseError from a file too damaged
    # for PRAGMA integrity_check to parse, OSError from the volume underneath it --
    # and that went to `logger.warning` beside the optional steps, so the app came
    # up and /api/health answered 200. The more damaged the database, the healthier
    # it looked. "Could not run" is not a pass.
    try:
        other_stores = sqlite_db.maintain_all()
    except Exception as exc:
        logger.error(
            "SQLite store maintenance could not run at startup — /api/health "
            "will report degraded. Check the data volume and restore from a "
            "backup if a store is damaged.",
            exc_info=True,
        )
        _record_core_startup_outcome(
            STARTUP_SQLITE_INTEGRITY_JOB,
            _safe_error("SQLite store maintenance could not run", exc),
        )
    else:
        if not other_stores["ok"]:
            failed = other_stores.get("failed") or []
            logger.error(
                "SQLite integrity failed at startup for: %s — /api/health will "
                "report degraded. Restore these stores from a backup.",
                ", ".join(failed),
            )
            _record_core_startup_outcome(
                STARTUP_SQLITE_INTEGRITY_JOB,
                f"SQLite integrity failed for: {', '.join(failed)}",
                # Setting names and pass/fail only. `maintain_all` puts a full
                # `f"{type(exc).__name__}: {exc}"` in each store's `error`, and
                # this dict is returned verbatim to an authenticated /api/health.
                result={
                    "failed": failed,
                    "checked": sorted(other_stores.get("stores") or {}),
                },
            )
        else:
            logger.info(
                "SQLite maintenance passed for %d store(s)",
                len(other_stores.get("stores") or {}),
            )
            _record_core_startup_outcome(STARTUP_SQLITE_INTEGRITY_JOB, None)

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

    # Initialize World Cup prediction database (creates tables if missing).
    # Core: this is the DDL every match prediction and every scoring pass writes
    # through, so a failure here means later writes fail one at a time inside
    # scheduler jobs rather than once, visibly, at boot. Nothing re-checks it on a
    # schedule, which is why the clean path records a success row -- otherwise one
    # bad boot would pin /api/health at 503 forever.
    try:
        from app.utils.prediction_db import init_prediction_db
        init_prediction_db()
    except Exception as exc:
        logger.error(
            "World Cup prediction DB init failed — /api/health will report "
            "degraded.",
            exc_info=True,
        )
        _record_core_startup_outcome(
            STARTUP_PREDICTION_DB_JOB,
            _safe_error("World Cup prediction DB init failed", exc),
        )
    else:
        logger.info("World Cup prediction DB initialized.")
        _record_core_startup_outcome(STARTUP_PREDICTION_DB_JOB, None)

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
    # None removes the route entirely, so the schema and both doc pages 404
    # rather than rendering an empty page. FastAPI gates /docs and /redoc on
    # openapi_url too, so all three go together.
    openapi_url="/openapi.json" if settings.OPENAPI_ENABLED else None,
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
        # None when OPENAPI_ENABLED is false. `docs_url` keeps its default even
        # then -- FastAPI gates the route on `openapi_url and docs_url` -- so the
        # pointer has to ask the same question the router asked, or it advertises
        # a 404 in exactly the deployment that setting is for.
        "docs": app.docs_url if app.openapi_url else None,
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

    Offloaded like ``api_health`` above: the refresh reads the whole event store
    (68 ms of a 78 ms scrape, over a 3.6 MB file) and Prometheus scrapes every
    15s, so running it inline stalled the loop 5,760 times a day.
    """
    from app.utils.metrics import render_metrics

    body, content_type = await asyncio.to_thread(render_metrics)
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
        # /docs does not exist when OPENAPI_ENABLED is false, so the fallback is
        # the machine-readable overview -- which is also the only endpoint that
        # still describes the API in that deployment.
        return RedirectResponse(url="/docs" if app.openapi_url else "/api")
