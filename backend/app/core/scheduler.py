"""
scheduler.py
============
APScheduler 定时任务。随 FastAPI 启动自动运行。

任务：
  07:15 UTC — event discover（freeze 预测，让反馈闭环持续积累样本）
  05:20 UTC — World Cup source bundle import（可选，默认关闭）
  每 8 小时  — sentiment refresh（RSS+Reddit 情绪缓存刷新，供 rule engine 使用）
  22:30 UTC — event auto-resolve（匹配已结算预测市场并打分）
"""

import asyncio
import functools
import logging
import os
import sys
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.kernel.sport_market_bridge_service import SportMarketBridgeService
from app.memory import loop_run_store
from app.realtime.connection_manager import get_connection_manager
from app.services.kalshi_sports_source import fetch_kalshi_sport_markets
from app.kernel.futures_market_service import FuturesMarketService
from app.services.polymarket_sports_source import fetch_polymarket_sport_markets
from app.utils import sqlite_db
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
_scheduler_lock_handle: Any | None = None
_scheduler_lock_skipped = False


def _acquire_process_lock(handle: Any) -> None:
    handle.seek(0)
    # `sys.platform` rather than `os.name`: both are equivalent here (Windows is
    # the only "nt"), but a type checker prunes the branch it is not running on
    # only for sys.platform, so os.name made every msvcrt/fcntl attribute look
    # undefined on whichever platform mypy ran.
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_process_lock(handle: Any) -> None:
    handle.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _try_acquire_scheduler_lock() -> bool:
    global _scheduler_lock_handle, _scheduler_lock_skipped

    _scheduler_lock_skipped = False
    if not settings.SCHEDULER_LOCK_ENABLED:
        return True
    if _scheduler_lock_handle is not None:
        return True

    lock_path = os.path.abspath(settings.SCHEDULER_LOCK_FILE)
    lock_dir = os.path.dirname(lock_path)
    if lock_dir:
        os.makedirs(lock_dir, exist_ok=True)
    handle = open(lock_path, "a+", encoding="utf-8")
    try:
        _acquire_process_lock(handle)
    except OSError:
        handle.close()
        _scheduler_lock_skipped = True
        return False

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    _scheduler_lock_handle = handle
    return True


def _release_scheduler_lock() -> None:
    global _scheduler_lock_handle

    if _scheduler_lock_handle is None:
        return
    handle = _scheduler_lock_handle
    _scheduler_lock_handle = None
    try:
        if settings.SCHEDULER_LOCK_ENABLED:
            _release_process_lock(handle)
    finally:
        handle.close()


def scheduler_start_skipped_due_to_lock() -> bool:
    return _scheduler_lock_skipped


# job_defaults apply to every job added via start_scheduler (and to any added
# later that don't override them):
#   - coalesce=True: if the scheduler fell behind and a job would fire more
#     than once to catch up, run it just once (no backlog stampede).
#   - misfire_grace_time=settings.SCHEDULER_MISFIRE_GRACE_SECONDS (default 24h):
#     a missed run still fires if the scheduler catches up inside that window;
#     beyond it the run is dropped and logged instead of using the default 1s
#     grace.
scheduler = AsyncIOScheduler(
    timezone="UTC",
    job_defaults={
        "coalesce": True,
        "misfire_grace_time": settings.SCHEDULER_MISFIRE_GRACE_SECONDS,
    },
)

# In-memory run_id -> job_name mapping so _finish_run can attribute
# Prometheus metrics to the correct job label without reverse lookup.
# Lost on restart, but only needed for the lifetime of the process.
_RUN_TO_JOB: dict[str, str] = {}


def _start_run(job_name: str) -> str | None:
    """Open a ledger row for this run, or None when the ledger write failed.

    The ledger is best-effort: a job whose `loop_runs` insert fails still runs.
    `None` therefore means "no row to update", and nothing more -- see
    `_finish_run`, which used to treat it as "nothing to report" and so silenced
    every alarm the failing job was supposed to raise.
    """
    try:
        run_id = loop_run_store.start_run(job_name)
        if run_id is not None:
            _RUN_TO_JOB[run_id] = job_name
        return run_id
    except Exception as exc:
        logger.error(
            "[Scheduler] Failed to start run ledger for %s: %s",
            job_name,
            type(exc).__name__,
        )
        return None


def _finish_run(
    run_id: str | None,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    exc: BaseException | None = None,
) -> None:
    """Close out a run: update the ledger row, then raise the job's alarms.

    ``run_id is None`` means ``_start_run``'s ledger insert failed, so there is
    no row to update. It does **not** mean nothing happened, and the alarm
    channels below are about the *job*, not the ledger. Returning here gated all
    of them on a ledger write: measured with a ``BEFORE INSERT ... RAISE(ABORT)``
    trigger on ``loop_runs``, a job that ran and failed was identical to a job
    that never ran at every door -- no ledger row, ``runs.event_auto_resolve:
    null`` from ``loop_status``, **no ``SCHEDULER_FAILED_RUNS`` increment, no
    Sentry event and no operator webhook** -- leaving `_start_run`'s log line as
    the only trace of a failure that is supposed to page someone.
    """
    safe_error = error
    exc_type = type(exc).__name__ if exc is not None else None
    if status == "failed" and exc_type is not None:
        safe_error = f"Scheduler job failed: {exc_type}"

    if run_id is not None:
        try:
            loop_run_store.finish_run(
                run_id,
                status,
                result=result,
                error=safe_error,
            )
        except Exception as ledger_exc:
            logger.error(
                "[Scheduler] Failed to finish run ledger for %s: %s",
                run_id,
                type(ledger_exc).__name__,
            )
    # Forward a stable failure event to Sentry (P0-7 §1.2). No-op when
    # SENTRY_DSN is empty (the wrapper handles the disabled case). Called after
    # the run ledger write so local state remains authoritative if ingestion is
    # slow or unavailable. Never forward the original exception or traceback.
    if status == "failed":
        try:
            from app.utils.sentry import capture_message
            capture_message(
                "scheduler job failed",
                level="error",
                code="scheduler_job_failed",
                job_name=_job_name_for_run(run_id) or "unknown",
                run_id=run_id,
                error=safe_error,
                exc_type=exc_type,
            )
        except Exception:  # pragma: no cover - defensive
            logger.debug("[Scheduler] Sentry capture failed", exc_info=True)
        # P0-6 metrics: count failed scheduler runs by job_name. The
        # run_id was started with _start_run(job_name) so we look it up
        # to attribute the failure. Best-effort: a missing job_name
        # (e.g. run ledger write failed) is logged but not fatal.
        try:
            from app.utils.metrics import SCHEDULER_FAILED_RUNS
            # The job_name isn't passed to _finish_run; recover it from
            # the run ledger when possible. We try loop_run_store.last_run
            # but a simpler approach is to track it via run_id → job_name
            # map. For now, use a generic label since run_id is opaque.
            # Phase 1: use a single "unknown" label; Phase 2 will add a
            # _job_name_for_run(run_id) lookup if more granularity is needed.
            job_name = _job_name_for_run(run_id) or "unknown"
            SCHEDULER_FAILED_RUNS.labels(job_name=job_name).inc()
        except Exception:  # pragma: no cover - defensive
            logger.debug("[Scheduler] metrics increment failed", exc_info=True)
        # E8: best-effort operator notification (webhook + Sentry
        # breadcrumb + structured log) gated by
        # SCHEDULER_FAILURE_ALERT_ENABLED. No-op when disabled — the
        # metrics increment and Sentry capture above already cover
        # observability; this only adds a deduplicated alert channel.
        try:
            from app.services.scheduler_failure_alert_dispatcher import (
                dispatch_scheduler_failure_alert,
            )
            dispatch_scheduler_failure_alert(
                job_name=_job_name_for_run(run_id) or "unknown",
                run_id=run_id,
                error=safe_error,
                exc_type=exc_type,
            )
        except Exception:  # pragma: no cover - defensive
            logger.debug("[Scheduler] failure alert dispatch failed", exc_info=True)
    elif status == "success" and run_id is not None:
        # Requires a run_id, unlike the failure path above: a success raises no
        # alarm, the gauge is keyed by job, and with no ledger row the job name
        # is unrecoverable -- a `job_name="unknown"` success series would read as
        # a job that succeeded. The lost row is already reported by _start_run.
        # P0-6 metrics: update last-success gauge so SCHEDULER_LAST_SUCCESS
        # reflects the most recent successful run per job.
        try:
            from datetime import datetime, timezone
            from app.utils.metrics import SCHEDULER_LAST_SUCCESS
            job_name = _job_name_for_run(run_id) or "unknown"
            SCHEDULER_LAST_SUCCESS.labels(job_name=job_name).set(
                datetime.now(timezone.utc).timestamp()
            )
        except Exception:  # pragma: no cover - defensive
            logger.debug("[Scheduler] last-success gauge update failed", exc_info=True)


def _job_name_for_run(run_id: str | None) -> str | None:
    """Recover the job_name for a given run_id from the in-memory mapping.

    Returns None when the run_id is not in the mapping (e.g. the process
    restarted and lost the in-memory state, or _start_run failed).
    """
    if run_id is None:
        return None
    return _RUN_TO_JOB.get(run_id)


async def _job_translate_titles() -> None:
    """Translate events whose Chinese title is empty or still English."""
    from app.services.probability_engine_service import translate_title
    from app.services.translation_service import looks_chinese
    from app.memory.event_store import list_all_events, save_events

    logger.info("[Scheduler] Title translation starting...")
    run_id = _start_run("translate_titles")
    events = list_all_events()
    translated = 0
    try:
        records_to_save = []
        for event in events:
            record = event.get("record") or event
            zh = str(record.get("event_title_zh") or "").strip()
            if zh and looks_chinese(zh):
                continue
            en = str(record.get("event_title") or "").strip()
            if not en:
                continue
            result = await translate_title(en)
            if result and result != en:
                record["event_title_zh"] = result[:300]
                records_to_save.append(record)
                translated += 1
        if records_to_save:
            save_events(records_to_save)
        _finish_run(run_id, "success", result={"translated": translated})
    except Exception as exc:
        logger.error(
            "[Scheduler] Title translation failed: %s",
            type(exc).__name__,
        )
        _finish_run(run_id, "failed", error=str(exc), exc=exc)


async def _job_event_auto_resolve() -> None:
    """每天 22:30 UTC 自动裁定事件层（匹配已结算预测市场），同时归档已过期源市场事件。"""
    logger.info("[Scheduler] Event auto-resolve starting...")
    run_id = _start_run("event_auto_resolve")
    try:
        from app.memory.event_store import auto_archive_expired
        from app.services.event_resolve_service import auto_resolve_events

        archived = auto_archive_expired()
        result = await auto_resolve_events(resolved_limit=500)
        result["archived_count"] = archived
        _finish_run(run_id, "success", result=result)
        logger.info(
            "[Scheduler] Event auto-resolve: resolved=%d checked=%d archived=%d",
            result.get("resolved_count", 0),
            result.get("checked_count", 0),
            archived,
        )
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.error(
            "[Scheduler] Event auto-resolve failed: %s",
            type(exc).__name__,
        )


async def _job_event_discover() -> None:
    """每天 07:15 UTC 运行事件层发现（freeze 预测），让闭环持续积累样本。

    事件层 discover_events 会为每个市场来源事件冻结一条 point-in-time 预测
    （_persist_events -> freeze_prediction）；当天 22:30 的 event_auto_resolve 在
    市场结算后给它们打分。没有这个发现作业，闭环永远不产数据，校准与 M2 trust
    一直处于 dormant。use_cache=False 强制重新分析，从而每次都记一条新的审计快照
    （这正是 M3 edge 轨迹所需），并捕捉新出现的市场事件。失败被隔离，不影响调度器。
    """
    if not settings.EVENT_DISCOVER_ENABLED:
        return
    logger.info("[Scheduler] Event discover starting...")
    run_id = _start_run("event_discover")
    try:
        # Archive expired events before discovery so closed sources don't
        # get re-scanned with stale tracking status.
        from app.memory.event_store import auto_archive_expired
        archived = auto_archive_expired()
        if archived:
            logger.info("[Scheduler] Auto-archived %d expired events", archived)
        from app.services.event_intelligence_service import discover_events

        result = await discover_events(
            limit=settings.EVENT_DISCOVER_LIMIT, use_cache=False
        )
        _finish_run(run_id, "success", result=result)
        logger.info(
            "[Scheduler] Event discover: count=%d",
            result.get("count", 0),
        )
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] Event discover failed")


async def _job_event_discover_startup() -> None:
    """One-shot startup discover with a fixed small limit.

    Unlike the recurring job (_job_event_discover), which respects the
    configuration setting EVENT_DISCOVER_LIMIT, the startup job always scans
    at most 10 candidates. This keeps the first-run cost bounded regardless
    of the configured limit — a limit of 100 with _CANDIDATE_POOL_FACTOR=3
    would spawn ~300 candidates (600+ LLM calls) and time out long before
    completion, which is wasteful on a fresh deploy where every call counts
    against the daily cost cap.
    """
    if not settings.EVENT_DISCOVER_ENABLED:
        return
    logger.info("[Scheduler] Startup event discover starting (limit=10)...")
    run_id = _start_run("event_discover_startup")
    try:
        from app.memory.event_store import auto_archive_expired
        archived = auto_archive_expired()
        if archived:
            logger.info("[Scheduler] Auto-archived %d expired events", archived)
        from app.services.event_intelligence_service import discover_events

        result = await discover_events(limit=10, use_cache=False)
        _finish_run(run_id, "success", result=result)
        logger.info(
            "[Scheduler] Startup event discover: count=%d",
            result.get("count", 0),
        )
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] Startup event discover failed")


async def _job_loop_db_maintenance() -> None:
    """Daily SQLite maintenance: WAL truncation + integrity check, every store.

    The job name stays `loop_db_maintenance` because six other sites read it
    (`quality_metrics`, `metrics`, `loop_status_service`, the events route) and
    stored history is keyed by it; what changed is the scope. It used to call
    `sqlite_db.maintain()` with no argument, which maintains `LOOP_DB_FILE` only,
    so corruption in the kernel, World Cup or domain-reliability DB was reported
    nowhere.

    A failure in any store fails the run, which is what drives `/api/health` to
    `degraded` — `maintain_all()` deliberately does not raise, so the message can
    name every affected store instead of only the first.
    """
    logger.info("[Scheduler] SQLite maintenance starting...")
    run_id = _start_run("loop_db_maintenance")
    try:
        result = sqlite_db.maintain_all()
        if result["ok"]:
            _finish_run(run_id, "success", result=result)
            logger.info(
                "[Scheduler] SQLite maintenance ok: %d store(s)",
                len(result.get("stores") or {}),
            )
        else:
            failed = result.get("failed") or []
            error = f"SQLite integrity failed for: {', '.join(failed)}"
            _finish_run(run_id, "failed", error=error, result=result)
            logger.error("[Scheduler] SQLite maintenance failed: %s", error)
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] SQLite maintenance failed")


async def _job_loop_run_ledger_maintenance() -> None:
    """Daily reconcile + retention for the loop_runs ledger.

    Measured on the live ledger before this job existed: 123 rows stuck in
    ``running`` (2026-06-29 to 07-23, every owner process long dead), because
    a ``running`` row has exactly two exits -- the job's ``_finish_run``, or
    nobody. No process re-attaches to a stored row, so an abandoned row is
    permanent. The same table had no retention of any kind: 1706 rows over 69
    days, and P0-5's ledger writes make it grow 2.82 MiB/month.

    Two deliberate differences from ``optimization_task_cleanup`` next door:

    * Reconcile is threshold-based, not startup-based. The API and scheduler
      processes share this ledger (the systemd units split them on purpose),
      so "another process started" cannot mean "that row has no owner" -- the
      scheduler can legitimately be mid-run during an API restart.
    * Retention exempts each job's newest row regardless of age, because
      ``/api/health`` reads it through ``latest_run_per_job``. A plain age
      cutoff would delete the only record that a quiet job is healthy (or
      failing), blinding the probe exactly when the job has been quiet long
      enough to look dead.
    """
    logger.info("[Scheduler] loop_runs ledger maintenance starting...")
    run_id = _start_run("loop_run_ledger_maintenance")
    try:
        now = datetime.now(timezone.utc)
        stale_after = (
            now - timedelta(hours=settings.LOOP_RUN_STALE_RUNNING_HOURS)
        ).isoformat()
        cutoff = (now - timedelta(days=settings.LOOP_RUN_RETENTION_DAYS)).isoformat()
        reconciled = loop_run_store.fail_stale_running_rows(stale_after)
        deleted = loop_run_store.delete_terminal_runs_before(cutoff)
        result = {"reconciled_running": reconciled, "deleted_terminal": deleted}
        _finish_run(run_id, "success", result=result)
        logger.info(
            "[Scheduler] loop_runs ledger maintenance: %d stale running row(s) "
            "failed, %d terminal row(s) deleted",
            reconciled, deleted,
        )
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] loop_runs ledger maintenance failed")


async def _job_drift_alert_check() -> None:
    """Daily drift-alert evaluation + dispatch, server-side.

    `dispatch_drift_alerts` had exactly one caller before this job: the
    `/quality-metrics/drift` handler, and only on its authenticated branch
    (`can_dispatch` — a valid X-API-Key). Nothing scheduled it, so every
    webhook/Sentry alert in the drift rulebook fired only when an operator
    had already exported the write key into something that polls by hand.
    The route's own docstring called that "the alert heartbeat".

    Runs the same evaluation the route runs — rules 1-3 (samples ->
    build_drift_report -> evaluate_drift_alerts) plus rule 4
    (`evaluate_scheduler_alerts`) — then hands the combined list to
    `dispatch_drift_alerts`, which applies `DRIFT_ALERTS_ENABLED` and the
    per-code cooldown. That flag is why registration itself is gated on
    `DRIFT_ALERTS_ENABLED`: with it off, the job would evaluate and dispatch
    nothing, every day, forever.

    Registered only when `DRIFT_ALERTS_ENABLED` is on. The threshold call
    into `prediction_store` and `event_store` are the route's reads; here
    they run once a day at a fixed hour instead of per request.
    """
    logger.info("[Scheduler] drift alert check starting...")
    run_id = _start_run("drift_alert_check")
    try:
        from app.memory.prediction_store import list_scored_samples_for_drift
        from app.memory.event_store import list_all_events
        from app.services.calibration_drift_service import (
            build_drift_report,
            evaluate_drift_alerts,
        )
        from app.services.drift_alert_dispatcher import (
            dispatch_drift_alerts,
            evaluate_scheduler_alerts,
        )

        recent_n = getattr(settings, "DRIFT_RECENT_WINDOW_N", 50)
        try:
            samples = list_scored_samples_for_drift(recent_n=recent_n)
        except Exception:
            logger.warning("[Scheduler] drift samples unavailable", exc_info=True)
            samples = {"recent": [], "baseline": []}
        recent = samples.get("recent", [])
        if recent:
            degraded_ids: set[str] = set()
            for entry in list_all_events():
                record = entry.get("record") or {}
                lt = record.get("llm_telemetry")
                if isinstance(lt, dict) and lt.get("degraded_mode"):
                    eid = record.get("event_id")
                    if isinstance(eid, str):
                        degraded_ids.add(eid)
            for s in recent:
                if s.get("event_id") in degraded_ids:
                    s["degraded"] = True

        report = build_drift_report(recent, samples.get("baseline", []))
        thresholds = {
            "brier_relative_threshold": getattr(
                settings, "DRIFT_BRIER_RELATIVE_THRESHOLD", 0.30
            ),
            "bucket_deviation_pp": getattr(
                settings, "DRIFT_BUCKET_DEVIATION_PP", 20.0
            ),
            "bucket_min_samples": getattr(settings, "DRIFT_BUCKET_MIN_SAMPLES", 2),
        }
        alerts = evaluate_drift_alerts(report, thresholds)
        alerts.extend(evaluate_scheduler_alerts())

        dispatch_drift_alerts(alerts)
        result = {
            "alerts_detected": len(alerts),
            "recent_window_n": recent_n,
        }
        _finish_run(run_id, "success", result=result)
        logger.info(
            "[Scheduler] drift alert check done: %d alert(s)", len(alerts)
        )
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] drift alert check failed")


async def _job_optimization_task_cleanup() -> None:
    """Daily cleanup of completed/failed optimization tasks older than 24h.

    Auto-tune / batch-optimize tasks are persisted to the loop DB so a restart
    does not 404 the polling frontend (see optimization_task_store). Without
    this job the table grows without bound; the in-memory cache would also
    accumulate stale entries across long-lived processes. A store failure is
    reported into the run ledger so degraded SQLite surfaces loudly rather than
    silently leaking rows.

    This docstring already claimed the store failure was "re-raised into the run
    ledger". It was not: ``cleanup_old_tasks`` caught it, logged it, and returned
    ``None``, and this job then recorded the literal ``{"cleaned": True}``. So the
    one condition the job exists to prevent -- the table growing without bound --
    was the condition it reported as a clean success.
    """
    logger.info("[Scheduler] Optimization task cleanup starting...")
    run_id = _start_run("optimization_task_cleanup")
    try:
        from app.services.optimization_task_manager import get_task_manager

        outcome = await get_task_manager().cleanup_old_tasks(max_age_hours=24) or {}
        store_error = outcome.get("store_error")
        result = {
            "memory_removed": outcome.get("memory_removed"),
            "store_deleted": outcome.get("store_deleted"),
            "store_error": store_error,
        }
        if store_error:
            _finish_run(
                run_id, "failed",
                result=result,
                error="Optimization task store cleanup failed",
            )
            logger.warning(
                "[Scheduler] Optimization task cleanup: store prune failed; "
                "in-memory pruning removed %s",
                outcome.get("memory_removed"),
            )
            return
        _finish_run(run_id, "success", result=result)
        logger.info(
            "[Scheduler] Optimization task cleanup completed "
            "(memory=%s store=%s)",
            outcome.get("memory_removed"), outcome.get("store_deleted"),
        )
    except Exception as exc:
        _finish_run(run_id, "failed", error="Optimization task cleanup failed", exc=exc)
        logger.error(
            "[Scheduler] Optimization task cleanup failed: %s",
            type(exc).__name__,
        )


async def _job_backup_stores() -> None:
    """Daily state-store backup, for deployments with nothing outside to run it.

    A systemd install has `prediction-market-reality-filter-backup.timer`. A
    Docker install has no timer, and before this job nothing in the compose path
    ran `scripts/backup_stores.py` at all -- the volume was mounted at
    `/app/backups` and stayed empty. `BACKUP_SCHEDULE_ENABLED` is therefore off
    by default: two writers would halve the effective retention at `--keep 30`.

    `create_backup` is blocking (measured 1.01s over 28.79 MB of real stores, zip
    deflate on 8 files), so it goes to a worker thread rather than stalling the
    event loop -- under Docker this job runs inside the API process, which is
    also serving requests.

    Imported in the body rather than at module scope, matching the other jobs in
    this file. That import is what puts `scripts/backup_stores.py` into mypy's
    checked set: a body-scope import does not keep it out (measured), which is
    why that file's annotations were completed in the same change.
    """
    logger.info("[Scheduler] Store backup starting...")
    run_id = _start_run("backup_stores")
    try:
        from scripts.backup_stores import create_backup

        archive = await asyncio.to_thread(create_backup)
        size = archive.stat().st_size
        _finish_run(run_id, "success", result={"archive": archive.name, "bytes": size})
        logger.info(
            "[Scheduler] Store backup written: %s (%.2f MB)",
            archive.name, size / 1e6,
        )
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] Store backup failed")


def _run_world_cup_bundle_import(mode: str, replace: bool) -> dict[str, Any]:
    """Shared import-mode dispatch for World Cup source bundles.

    Used by both the scheduled bundle import and the matchday refresh job
    to avoid duplicating the mode-selection logic.
    """
    from app.services.world_cup_api_football_source import (
        import_world_cup_api_football_bundle,
    )
    from app.services.football_data_source import (
        import_world_cup_football_data_standings,
    )
    from app.services.world_cup_sportmonks_source import (
        import_world_cup_sportmonks_bundle,
    )
    from app.services.world_cup_source_bundle import (
        import_world_cup_source_bundle_feeds,
        import_world_cup_source_bundle_file,
        import_world_cup_source_bundle_url,
    )

    if mode == "url":
        return import_world_cup_source_bundle_url(replace=replace)
    elif mode == "file":
        return import_world_cup_source_bundle_file(replace=replace)
    elif mode == "feeds":
        return import_world_cup_source_bundle_feeds(replace=replace)
    elif mode == "api_football":
        return import_world_cup_api_football_bundle(replace=replace)
    elif mode == "football_data":
        return import_world_cup_football_data_standings(replace=replace)
    elif mode == "sportmonks":
        return import_world_cup_sportmonks_bundle(replace=replace)
    else:
        raise ValueError(
            "WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE must be 'url', 'file', 'feeds', "
            "'api_football', 'football_data', or 'sportmonks'"
        )


async def _job_world_cup_source_bundle_import() -> None:
    """Import the configured World Cup source bundle into sports facts."""
    if not settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED:
        return

    mode = settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE.strip().lower()
    logger.info("[Scheduler] World Cup source bundle import starting...")
    run_id = _start_run("world_cup_source_bundle_import")
    try:
        result = _run_world_cup_bundle_import(
            mode, replace=settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_REPLACE
        )

        summary = _world_cup_bundle_import_summary(result, mode)
        _finish_run(run_id, "success", result=summary)
        logger.info(
            "[Scheduler] World Cup source bundle import: facts=%d sources=%d mode=%s",
            summary.get("converted_fact_count", 0),
            summary.get("source_count", 0),
            mode,
        )
    except Exception as exc:
        _finish_run(run_id, "failed", result={"mode": mode}, error=str(exc), exc=exc)
        logger.exception("[Scheduler] World Cup source bundle import failed")


async def _job_world_cup_matchday_refresh() -> None:
    """Refresh World Cup data during active match windows."""
    if not settings.WORLD_CUP_MATCHDAY_REFRESH_ENABLED:
        return
    if not settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED:
        return

    from datetime import datetime, timezone
    from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts

    now = datetime.now(timezone.utc)
    window_hours = settings.WORLD_CUP_MATCHDAY_REFRESH_WINDOW_HOURS
    facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

    has_live_match = False
    for fact in facts:
        if fact.get("kind") != "match_result":
            continue
        status = (fact.get("status") or "").upper()
        if status in ("FT", "AET", "PEN"):
            continue
        kickoff_str = fact.get("kickoff_at")
        if not kickoff_str:
            continue
        try:
            kickoff = datetime.fromisoformat(kickoff_str)
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if abs((now - kickoff).total_seconds()) <= window_hours * 3600:
            has_live_match = True
            break

    if not has_live_match:
        logger.debug("[Scheduler] Matchday refresh: no active matches in window, skipping.")
        return

    logger.info("[Scheduler] Matchday refresh: active match detected, importing...")
    run_id = _start_run("world_cup_matchday_refresh")
    try:
        mode = settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE.strip().lower()
        result = _run_world_cup_bundle_import(mode, replace=True)
        from app.services.world_cup_post_match_backfill_service import run_post_match_backfill

        post_match_result = run_post_match_backfill(dry_run=False, sync_first=False)

        summary = _world_cup_bundle_import_summary(result, mode)
        summary["post_match_backfill"] = _world_cup_post_match_backfill_summary(
            post_match_result
        )
        _finish_run(run_id, "success", result=summary)
        logger.info(
            "[Scheduler] Matchday refresh: facts=%d mode=%s scored=%d",
            summary.get("converted_fact_count", 0),
            mode,
            summary["post_match_backfill"].get("scored", 0),
        )
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] Matchday refresh failed")


async def _job_world_cup_prediction_update() -> None:
    """Daily World Cup score prediction update at 06:00 UTC."""
    logger.info("[Scheduler] World Cup prediction update starting...")
    run_id = _start_run("world_cup_prediction_update")
    try:
        from app.services.world_cup_prediction_scheduler import run_daily_prediction_update

        result = await run_daily_prediction_update()
        summary = _summarize_prediction_update(result)
        if result.get("status") == "error":
            _finish_run(
                run_id,
                "failed",
                result=summary,
                error="World Cup prediction update failed",
            )
            logger.error("[Scheduler] World Cup prediction update failed")
            return
        _finish_run(run_id, "success", result=summary)
        logger.info("[Scheduler] World Cup prediction update completed")
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] World Cup prediction update failed")


async def _job_world_cup_live_update() -> None:
    """Live World Cup prediction updates during active matches (every 2 minutes)."""
    from app.services.world_cup_live_update_service import update_live_predictions

    run_id = _start_run("world_cup_live_update")
    try:
        result = await update_live_predictions()

        _finish_run(run_id, "success", result=_summarize_live_update(result))
        # Only log if there were matches to update. The guard used to read
        # `matches_checked`, which `update_live_predictions` does not return --
        # nor `live_count` or `updated` -- so it was always 0 and this line never
        # emitted once. The ledger row above is what covers the quiet runs, which
        # are 718 of the 720 this job makes a day.
        if any(
            result.get(key) for key in
            ("in_play_count", "pre_match_updated", "newly_finished_scored")
        ):
            logger.info(
                "[Scheduler] Live update: in_play=%d pre_match_updated=%d newly_scored=%d",
                result.get("in_play_count", 0),
                result.get("pre_match_updated", 0),
                result.get("newly_finished_scored", 0),
            )
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] Live update failed: %s", exc)


async def _job_sentiment_refresh() -> None:
    """Refresh sentiment cache for World Cup teams with recent/upcoming matches.

    Runs every 8 hours so the rule engine's sentiment_factor (mapped from
    cached TeamSentiment rows) stays fresh.  Teams are discovered dynamically
    from the fixture table — no hard-coded list to maintain.
    """
    logger.info("[Scheduler] Sentiment refresh starting...")
    run_id = _start_run("sentiment_refresh")
    try:
        from datetime import datetime, timezone, timedelta
        from app.services.sentiment_aggregator import fetch_team_sentiment, cache_sentiment
        from app.models.world_cup_prediction import MatchFixture
        from app.utils.prediction_db import get_prediction_session, close_prediction_session

        session = get_prediction_session()
        teams: set[str] = set()
        try:
            now = datetime.now(timezone.utc)
            window = timedelta(days=7)
            matches = (
                session.query(MatchFixture)
                .filter(
                    MatchFixture.kickoff_utc.between(
                        now - window, now + window
                    )
                )
                .all()
            )
            for m in matches:
                if m.home_team:
                    teams.add(m.home_team)
                if m.away_team:
                    teams.add(m.away_team)
        finally:
            close_prediction_session(session)

        fetched = 0
        errors = 0
        for team in sorted(teams):
            try:
                data = await fetch_team_sentiment(team)
                cache_sentiment(data)
                fetched += 1
            except Exception as exc:
                errors += 1
                logger.warning(
                    "[Scheduler] Sentiment fetch failed for %s: %s", team, exc
                )

        result = {"teams_found": len(teams), "fetched": fetched, "errors": errors}
        _finish_run(run_id, "success", result=result)
        logger.info(
            "[Scheduler] Sentiment refresh: teams=%d fetched=%d errors=%d",
            len(teams), fetched, errors,
        )
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] Sentiment refresh failed")


async def _job_discover_sport_markets() -> None:
    """Hourly: discover Polymarket and Kalshi sports markets and link via bridge service."""
    if not settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED:
        return
    logger.info("[Scheduler] Sport market discovery starting...")
    run_id = _start_run("sport_market_discover")
    try:
        from app.kernel.kernel_db import init_kernel_db
        init_kernel_db()
        bridge = SportMarketBridgeService()

        polymarket_count = 0
        if settings.PHASE7_POLYMARKET_SPORTS_SOURCE_ENABLED:
            polymarket_candidates = await fetch_polymarket_sport_markets(limit=100)
            polymarket_count = len(polymarket_candidates)

        kalshi_count = 0
        kalshi_linked = 0
        kalshi_unresolved = 0
        kalshi_errors = 0
        if settings.PHASE11_KALSHI_SPORTS_ENABLED:
            try:
                kalshi_candidates = await fetch_kalshi_sport_markets(limit=100)
                kalshi_count = len(kalshi_candidates)
                for candidate in kalshi_candidates:
                    try:
                        result = await bridge.link_kalshi_market(candidate)
                        if result.get("linked"):
                            kalshi_linked += 1
                        else:
                            kalshi_unresolved += 1
                    except Exception:
                        kalshi_errors += 1
                        logger.warning("Failed to link Kalshi market", exc_info=True)
            except Exception:
                logger.warning("Kalshi sports discovery failed", exc_info=True)

        # Record link outcomes, not just candidate counts: a run that fetched
        # 100 candidates and linked none is not a healthy run, and the ledger
        # should say so.
        _finish_run(run_id, "success", result={
            "polymarket_candidates": polymarket_count,
            "kalshi_candidates": kalshi_count,
            "kalshi_linked": kalshi_linked,
            "kalshi_unresolved": kalshi_unresolved,
            "kalshi_errors": kalshi_errors,
        })
    except Exception as exc:
        logger.exception("[Scheduler] Sport market discovery failed")
        _finish_run(run_id, "failed", error=str(exc), exc=exc)


async def _job_fetch_traditional_odds() -> None:
    """Every ODDS_FETCH_INTERVAL_MIN: fetch traditional sportsbook odds."""
    if not settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED:
        return
    if not settings.ODDS_API_ENABLED:
        return
    run_id = _start_run("sport_market_odds_fetch")
    try:
        from app.kernel.kernel_db import init_kernel_db
        from app.kernel.traditional_odds_store import TraditionalOddsStore
        from app.kernel.sport_market_link_store import SportMarketLinkStore
        from app.services.odds_api_service import fetch_all_sports_odds

        init_kernel_db()
        odds_store = TraditionalOddsStore()
        link_store = SportMarketLinkStore()

        # Get all matches with verified links (need odds)
        matches = link_store.get_matches_with_verified_links()
        if not matches:
            _finish_run(run_id, "success", result={
                "matches_total": 0, "captured": 0, "errors": 0,
            })
            return

        # Don't spend quota on a run that cannot attribute anything.
        # fetch_all_sports_odds() is a /sports discovery call plus one
        # /sports/{key}/odds per sport key, all metered against
        # x-requests-remaining. _match_odds_to_match needs team names parsed out
        # of the match_id, and no production writer puts them there, so this used
        # to buy N+1 paid requests and then report captured=0 with "success".
        attributable = [m for m in matches if _odds_lookup_key(m) is not None]
        if not attributable:
            _finish_run(
                run_id, "failed",
                result={
                    "matches_total": len(matches),
                    "attributable": 0,
                    "captured": 0,
                    "errors": 0,
                    "skipped_reason": "no_match_id_yields_team_tokens",
                },
                error=(
                    f"none of {len(matches)} linked match id(s) can be matched to "
                    "an Odds API fixture; ids carry no team tokens, so the fetch "
                    "was skipped rather than spending quota for zero captures"
                ),
            )
            logger.warning(
                "[Scheduler] Traditional odds fetch skipped: 0 of %d linked "
                "match id(s) are attributable (ids carry no team tokens)",
                len(matches),
            )
            return

        # Fetch all available sports odds from The Odds API
        all_odds = await fetch_all_sports_odds()

        # Match odds to matches and store
        captured = 0
        errors = 0
        for match_id in matches:
            try:
                odds_list = _match_odds_to_match(match_id, all_odds)
                if odds_list:
                    now = datetime.now(timezone.utc)
                    competition = match_id.split("-")[0]
                    for outcome, implied_prob, decimal_odds, bookmaker, book_count in odds_list:
                        odds_store.append_snapshot(
                            match_id=match_id,
                            mapped_outcome=outcome,
                            competition=competition,
                            implied_prob=implied_prob,
                            decimal_odds=decimal_odds,
                            bookmaker=bookmaker,
                            bookmakers_count=book_count,
                            captured_at=now,
                        )
                        captured += 1
                        if settings.PHASE10_REALTIME_PUSH_ENABLED:
                            try:
                                _manager = get_connection_manager()
                                await _manager.broadcast_to_match(match_id, {
                                    "type": "odds_snapshot",
                                    "match_id": match_id,
                                    "outcome": outcome,
                                    "implied_prob": implied_prob,
                                    "decimal_odds": decimal_odds,
                                    "bookmaker": bookmaker,
                                    "captured_at": now.isoformat(),
                                })
                            except Exception:
                                logger.warning(
                                    "Failed to broadcast odds snapshot via WebSocket",
                                    exc_info=True,
                                )
            except Exception as exc:
                errors += 1
                logger.warning("Odds fetch failed for %s: %s", match_id, exc)

        _finish_run(run_id, "success", result={
            "matches_total": len(matches),
            "attributable": len(attributable),
            "captured": captured,
            "errors": errors,
        })
    except Exception as exc:
        logger.exception("[Scheduler] Traditional odds fetch failed")
        _finish_run(run_id, "failed", error=str(exc), exc=exc)


def _odds_lookup_key(match_id: str) -> str | None:
    """The Odds API sport key for ``match_id``, or None if it cannot be attributed.

    ``_match_odds_to_match`` needs **team names parsed out of the match_id**:
    ``{comp}-{YYYYMMDD}-{HOME}-{AWAY}`` or ``{comp}-{YYYY}-{MM}-{DD}-{HOME}-{AWAY}``.
    Every production writer builds ids as ``{sport}-{provider_game_id}`` instead
    (``historical_data_ingestor`` line ~250, and the football adapters), which
    carries no team name at all — measured 2026-09-01: **all 18,717 rows** in
    ``kernel_match_fixtures`` bail out here, none yields two team tokens. The
    dated format exists only in test fixtures.

    This predicate is shared with the caller on purpose. The job uses it to decide
    whether ``fetch_all_sports_odds()`` — a quota-metered ``/sports`` call plus one
    ``/sports/{key}/odds`` per sport — is worth spending at all, and a second
    hand-typed copy of the rule would be free to disagree with the matcher it is
    supposed to predict.
    """
    from app.services.odds_api_service import COMPETITION_TO_ODDS_API_SPORT
    from app.kernel.sport_market_bridge_service import SportMarketBridgeService

    competition, _date_str, team_tokens = (
        SportMarketBridgeService._parse_match_id_static(match_id)
    )
    # An unparseable competition could not be in COMPETITION_TO_ODDS_API_SPORT
    # anyway, so fold it into the existing bail-out instead of looking it up.
    if not competition or not team_tokens or len(team_tokens) < 2:
        return None
    # A token that normalizes to "" would compare equal to a fixture missing its
    # home_team/away_team, attributing another fixture's odds to this match.
    if not team_tokens[0].strip() or not team_tokens[1].strip():
        return None
    return COMPETITION_TO_ODDS_API_SPORT.get(competition)


def _match_odds_to_match(
    match_id: str, all_odds: dict[str, list[dict]]
) -> list[tuple[str, float, float, str, int]]:
    """Match The Odds API fixtures to a match_id.

    Parses match_id to extract competition, date, and team tokens, then
    finds the matching fixture in all_odds by team-name normalization.

    Returns:
        [(mapped_outcome, implied_prob, decimal_odds, bookmaker, bookmakers_count), ...]
        Empty list if no match found.
    """
    from app.services.odds_api_service import (
        normalize_team_name, extract_best_odds,
    )
    from app.utils.implied_prob import odds_api_to_implied
    from app.kernel.sport_market_bridge_service import SportMarketBridgeService

    sport_key = _odds_lookup_key(match_id)
    if not sport_key:
        return []

    _competition, _date_str, team_tokens = (
        SportMarketBridgeService._parse_match_id_static(match_id)
    )

    fixtures = all_odds.get(sport_key, [])
    if not fixtures:
        return []

    home_token = team_tokens[0]
    away_token = team_tokens[1]
    home_normalized = normalize_team_name(home_token)
    away_normalized = normalize_team_name(away_token)

    # Find matching fixture
    for fixture in fixtures:
        fixture_home = normalize_team_name(fixture.get("home_team", ""))
        fixture_away = normalize_team_name(fixture.get("away_team", ""))
        if fixture_home == home_normalized and fixture_away == away_normalized:
            # Extract best odds
            odds = extract_best_odds(fixture)
            if not odds:
                continue

            home_decimal = odds.get("home")
            away_decimal = odds.get("away")
            draw_decimal = odds.get("draw")
            bookmaker = odds.get("source", "average")
            book_count = odds.get("bookmakers_count", 0)

            # Convert to implied probabilities
            decimals = []
            mapping = []
            if home_decimal is not None:
                decimals.append(home_decimal)
                mapping.append("home_win")
            if draw_decimal is not None:
                decimals.append(draw_decimal)
                mapping.append("draw")
            if away_decimal is not None:
                decimals.append(away_decimal)
                mapping.append("away_win")

            if not decimals:
                continue

            implied = odds_api_to_implied(decimals)

            return [
                (mapping[i], implied[i], decimals[i], bookmaker, book_count)
                for i in range(len(mapping))
            ]

    return []


async def _job_capture_market_snapshots() -> None:
    """Every MARKET_SNAPSHOT_INTERVAL_MIN: capture Polymarket price snapshots."""
    if not settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED:
        return
    run_id = _start_run("sport_market_snapshots")
    try:
        from app.kernel.kernel_db import init_kernel_db
        from app.kernel.sport_market_bridge_service import SportMarketBridgeService
        from app.kernel.sport_market_link_store import SportMarketLinkStore
        from app.kernel.market_snapshot_store import MarketSnapshotStore

        init_kernel_db()
        bridge = SportMarketBridgeService()
        link_store = SportMarketLinkStore()
        snap_store = MarketSnapshotStore()

        # Get all verified links across all matches
        matches = link_store.get_matches_with_verified_links()
        captured = 0
        errors = 0
        for match_id in matches:
            try:
                links = link_store.get_verified_links(match_id=match_id)
                for link in links:
                    price = await bridge.fetch_link_price(link)
                    if price is not None:
                        captured_at = datetime.now(timezone.utc)
                        snap_store.append_snapshot(
                            link_id=link["id"],
                            implied_prob=price["implied_prob"],
                            price=price["price"],
                            liquidity=price.get("liquidity"),
                            volume=price.get("volume"),
                            captured_at=captured_at,
                        )
                        captured += 1
                        if settings.PHASE10_REALTIME_PUSH_ENABLED:
                            try:
                                _manager = get_connection_manager()
                                await _manager.broadcast_to_match(match_id, {
                                    "type": "market_snapshot",
                                    "match_id": match_id,
                                    "link_id": link["id"],
                                    "implied_prob": price["implied_prob"],
                                    "price": price["price"],
                                    "captured_at": captured_at.isoformat(),
                                })
                            except Exception:
                                logger.warning(
                                    "Failed to broadcast market snapshot via WebSocket",
                                    exc_info=True,
                                )
            except Exception as exc:
                errors += 1
                logger.warning(
                    "Snapshot capture failed for match %s: %s",
                    match_id,
                    exc,
                )

        _finish_run(run_id, "success", result={
            "matches_total": len(matches),
            "captured": captured,
            "errors": errors,
        })
    except Exception as exc:
        logger.exception("[Scheduler] Market snapshot capture failed")
        _finish_run(run_id, "failed", error=str(exc), exc=exc)


async def _job_detect_sport_edges() -> None:
    """Every EDGE_DETECTION_INTERVAL_MIN: compute edges for matches with verified links."""
    if not settings.PHASE7_EDGE_DETECTOR_ENABLED:
        return
    run_id = _start_run("sport_edge_detect")
    try:
        from app.kernel.kernel_db import init_kernel_db
        from app.kernel.edge_detector_service import EdgeDetectorService
        from app.kernel.sport_market_link_store import SportMarketLinkStore
        init_kernel_db()
        store = SportMarketLinkStore()
        matches = store.get_matches_with_verified_links()
        service = EdgeDetectorService()
        processed = 0
        errors = 0
        for match_id in matches:
            try:
                summary = service.detect_edges(match_id)
                if not summary.skipped:
                    processed += 1
            except Exception as exc:
                errors += 1
                logger.warning(
                    "[Scheduler] Edge detection failed for %s: %s", match_id, exc
                )
        # Count the failures too: a per-match raise used to leave only a log
        # line, so a run whose every match was unreadable reported
        # matches_total=1 matches_processed=0 — the same ledger row as a match
        # deliberately skipped for having no prediction.
        _finish_run(run_id, "success", result={
            "matches_total": len(matches),
            "matches_processed": processed,
            "errors": errors,
        })
    except Exception as exc:
        logger.exception("[Scheduler] Sport edge detection failed")
        _finish_run(run_id, "failed", error=str(exc), exc=exc)


async def _job_process_market_settlements() -> None:
    """Scan for finished matches without settlements, process them."""
    if not settings.PHASE7_MARKET_SETTLEMENT_SCHEDULER_ENABLED:
        return
    run_id = _start_run("market_settlement_feedback")
    try:
        from app.kernel.kernel_db import init_kernel_db
        from app.kernel.market_settlement_service import MarketSettlementService
        init_kernel_db()
        svc = MarketSettlementService()
        result = svc.scan_and_process(limit=settings.MARKET_SETTLEMENT_BATCH_LIMIT)
        logger.info(
            f"[Scheduler] Market settlements: scanned={result.scanned} "
            f"processed={result.processed} skipped={result.skipped} "
            f"already={result.already_processed} errors={result.errors}"
        )
        _finish_run(run_id, "success", result={
            "scanned": result.scanned,
            "processed": result.processed,
            "skipped": result.skipped,
            "already_processed": result.already_processed,
            "errors": result.errors,
        })
    except Exception as exc:
        logger.exception("[Scheduler] Market settlement job failed")
        _finish_run(run_id, "failed", error=str(exc), exc=exc)


async def _job_update_weights_weekly() -> None:
    """Weekly weight update via Phase 3 learning loop (Phase 9)."""
    if not settings.PHASE9_LEARNING_ACTIVATED:
        return
    if not settings.PHASE9_WEEKLY_WEIGHT_UPDATE_INTERVAL_MIN:
        return
    logger.info("[Scheduler] Weekly weight update starting...")
    run_id = _start_run("update_weights_weekly")
    try:
        from app.kernel.learning_service import KernelLearningService

        learning = KernelLearningService()
        updated: list[str] = []
        skipped: dict[str, str] = {}
        for competition in ["nba", "mlb", "nhl"]:
            try:
                # update_weights has four reachable conditions that decline to
                # learn (plus a defensive private-state guard): too few samples,
                # no factor samples, a factor with zero samples, and zero total
                # accuracy. They all used to be a bare `return`, so this logged
                # "Updated weights" for each of them and the ledger recorded the
                # hardcoded input list below as the result -- three failures still
                # read as a successful weekly update.
                outcome = learning.update_weights(competition) or {}
                if outcome.get("updated"):
                    updated.append(competition)
                    logger.info(
                        "[Scheduler] Updated weights for %s (factors=%s samples=%s)",
                        competition,
                        outcome.get("factors"),
                        outcome.get("samples"),
                    )
                else:
                    reason = str(outcome.get("reason") or "unknown")
                    skipped[competition] = reason
                    logger.info(
                        "[Scheduler] Weights unchanged for %s: %s", competition, reason,
                    )
            except Exception as exc:
                skipped[competition] = f"error:{type(exc).__name__}"
                logger.warning(
                    "[Scheduler] Weight update failed for %s: %s",
                    competition,
                    type(exc).__name__,
                )
        _finish_run(
            run_id,
            "success" if updated else "failed",
            result={"competitions": updated, "skipped": skipped},
            error=None if updated else "No competition weights updated",
        )
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] Weekly weight update failed")


async def _job_reoptimize_monthly() -> None:
    """Monthly re-optimization of parameters (Phase 9, spec §8.2).

    The job is only registered when PHASE9_LEARNING_ACTIVATED is on, so this
    function assumes it is enabled and triggers ParameterOptimizer.optimize_sync
    for each configured sport.
    """
    logger.info("[Scheduler] Monthly re-optimization starting...")
    run_id = _start_run("reoptimize_monthly")
    try:
        from app.kernel.backtest.match_loader import (
            load_sport_matches_for_backtest,
            time_series_split,
        )
        from app.kernel.kernel_db import init_kernel_db
        from app.kernel.parameter_optimizer import ParameterOptimizer

        init_kernel_db()
        optimizer = ParameterOptimizer()
        n_trials = settings.PHASE9_OPTIMIZATION_TRIALS
        sports = ["nba", "mlb", "nhl"]
        completed: list[str] = []
        skipped: dict[str, str] = {}
        for sport in sports:
            try:
                # The matches have to be loaded here. Passing empty lists ran the
                # full Optuna search against nothing: BacktestRunner returns
                # sample_count=0 with every metric 0.0, so all n_trials tied and
                # the "best" candidate was whichever trial happened to run first.
                # Same loader and same minimum as POST /sport-optimization/run,
                # so the scheduled path and the manual one measure the same way.
                matches = await asyncio.to_thread(
                    load_sport_matches_for_backtest, sport,
                )
                if len(matches) < 5:
                    skipped[sport] = f"insufficient_matches:{len(matches)}"
                    logger.warning(
                        "[Scheduler] Re-optimization skipped for %s: only %d "
                        "matches; ingest history first",
                        sport, len(matches),
                    )
                    continue
                train, test = time_series_split(matches, test_ratio=0.2)
                result = await asyncio.to_thread(
                    functools.partial(
                        optimizer.optimize_sync,
                        sport,
                        n_trials=n_trials,
                        train_matches=train,
                        test_matches=test,
                    )
                )
                reason = result.get("not_persisted_reason")
                if reason:
                    skipped[sport] = reason
                    logger.warning(
                        "[Scheduler] Re-optimization for %s persisted nothing: %s",
                        sport, reason,
                    )
                    continue
                completed.append(sport)
                logger.info(
                    "[Scheduler] Re-optimization completed for %s "
                    "(train=%d test=%d score=%.4f)",
                    sport, len(train), len(test), result.get("best_score") or 0.0,
                )
            except Exception as exc:
                skipped[sport] = f"error:{type(exc).__name__}"
                logger.warning(
                    "[Scheduler] Re-optimization failed for %s: %s",
                    sport,
                    type(exc).__name__,
                )
        # A run that optimized nothing is not a success. Reporting one made an
        # empty kernel DB indistinguishable from a completed monthly re-optimization
        # in the run ledger.
        status = "success" if completed else "failed"
        _finish_run(
            run_id,
            status,
            result={"sports": completed, "skipped": skipped},
            error=None if completed else "No sport re-optimized",
        )
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] Monthly re-optimization failed")


async def _job_discover_futures_markets() -> None:
    """Every FUTURES_DISCOVERY_INTERVAL_MIN: discover and link Kalshi futures markets."""
    if not settings.PHASE12_FUTURES_MARKETS_ENABLED:
        return
    logger.info("[Scheduler] Futures market discovery starting...")
    run_id = _start_run("futures_market_discover")
    try:
        from app.kernel.kernel_db import init_kernel_db
        init_kernel_db()
        service = FuturesMarketService()
        result = await service.discover_and_link()
        _finish_run(run_id, "success", result=result)
        logger.info(
            "[Scheduler] Futures market discovery: discovered=%d linked=%d errors=%d",
            result.get("discovered", 0),
            result.get("linked", 0),
            result.get("errors", 0),
        )
    except Exception as exc:
        logger.exception("[Scheduler] Futures market discovery failed")
        _finish_run(run_id, "failed", error=str(exc), exc=exc)


async def _job_capture_futures_snapshots() -> None:
    """Every FUTURES_SNAPSHOT_INTERVAL_MIN: capture price snapshots for verified futures links."""
    if not settings.PHASE12_FUTURES_MARKETS_ENABLED:
        return
    logger.info("[Scheduler] Futures snapshot capture starting...")
    run_id = _start_run("futures_snapshots_capture")
    try:
        from app.kernel.kernel_db import init_kernel_db
        init_kernel_db()
        service = FuturesMarketService()
        result = await service.capture_snapshots()
        _finish_run(run_id, "success", result=result)
        logger.info(
            "[Scheduler] Futures snapshot capture: captured=%d errors=%d",
            result.get("captured", 0),
            result.get("errors", 0),
        )
    except Exception as exc:
        logger.exception("[Scheduler] Futures snapshot capture failed")
        _finish_run(run_id, "failed", error=str(exc), exc=exc)


def _summarize_prediction_update(result: dict[str, Any]) -> dict[str, Any]:
    """Summarize prediction update result for scheduler run log."""
    if result.get("status") == "error":
        return {
            "status": "error",
            "error": result.get("error"),
            "step": result.get("step")
        }

    summary = {
        "status": "ok",
        "timestamp": result.get("timestamp"),
    }

    if result.get("fixture_sync"):
        sync = result["fixture_sync"]
        summary["fixtures_synced"] = sync.get("fixtures_parsed", 0)
        summary["remaining_matches"] = sync.get("remaining_matches", 0)

    if result.get("predictions"):
        pred = result["predictions"]
        summary["predictions_total"] = pred.get("total", 0)
        summary["predictions_succeeded"] = pred.get("succeeded", 0)
        summary["predictions_failed"] = pred.get("failed", 0)
        summary["predictions_skipped"] = pred.get("skipped", 0)

    if result.get("post_match_backfill"):
        backfill = result["post_match_backfill"]
        scoring = backfill.get("scoring", {})
        summary["post_match_candidates"] = backfill.get("candidate_count", 0)
        summary["post_match_scored"] = scoring.get("scored", 0)
        summary["post_match_errors"] = scoring.get("errors", 0)

    return summary


def _summarize_live_update(result: dict[str, Any]) -> dict[str, Any]:
    """Summarize a live-update run for the scheduler run log.

    Drops `actions`, which repeats the same three counts under other names, so
    the stored row stays small: this job runs 720 times a day, more than every
    other interval job combined.
    """
    return {
        "status": result.get("status"),
        "timestamp": result.get("timestamp"),
        "in_play_count": result.get("in_play_count", 0),
        "pre_match_updated": result.get("pre_match_updated", 0),
        "newly_finished_scored": result.get("newly_finished_scored", 0),
    }


def _world_cup_bundle_import_summary(result: dict[str, Any], mode: str) -> dict[str, Any]:
    summary = {
        "mode": mode,
        "source_count": result.get("source_count", 0),
        "converted_fact_count": result.get("converted_fact_count", 0),
        "imported": result.get("imported", 0),
        "error_count": result.get("error_count", 0),
        "total": result.get("total", 0),
        "replace": result.get("replace", False),
    }
    if result.get("provider"):
        summary["provider"] = result["provider"]
    if result.get("skipped_source_count") is not None:
        summary["skipped_source_count"] = result["skipped_source_count"]
    if result.get("source_fetch_count") is not None:
        summary["source_fetch_count"] = result["source_fetch_count"]
    if result.get("call_budget"):
        summary["call_budget"] = result["call_budget"]
    if result.get("run"):
        summary["run"] = result["run"]
    return summary


def _world_cup_post_match_backfill_summary(result: dict[str, Any]) -> dict[str, Any]:
    scoring = result.get("scoring") or {}
    result_fact_backfill = result.get("result_fact_backfill") or {}
    return {
        "status": result.get("status"),
        "candidate_count": result.get("candidate_count", 0),
        "scored": scoring.get("scored", 0),
        "skipped": scoring.get("skipped", 0),
        "errors": scoring.get("errors", 0),
        "result_facts_imported": result_fact_backfill.get("imported", 0),
    }


def start_scheduler() -> bool:
    if scheduler.running is True:
        logger.info("[Scheduler] Already running; startup skipped.")
        return True
    if not _try_acquire_scheduler_lock():
        logger.warning(
            "[Scheduler] Another process holds %s; startup skipped.",
            settings.SCHEDULER_LOCK_FILE,
        )
        return False
    try:
        if settings.EVENT_DISCOVER_ENABLED:
            # Run discovery every 4 hours so the system accumulates samples faster.
            scheduler.add_job(
                _job_event_discover,
                IntervalTrigger(hours=4),
                id="event_discover",
                replace_existing=True,
                max_instances=1,
            )
            # Also fire once 30 seconds after startup for immediate population.
            # Uses a fixed limit of 10 independent of EVENT_DISCOVER_LIMIT, so
            # the first-run cost stays bounded (P0 cost-cap guardrail).
            scheduler.add_job(
                _job_event_discover_startup,
                "date",
                run_date=datetime.now(timezone.utc) + timedelta(seconds=30),
                id="event_discover_startup",
                replace_existing=True,
            )
        scheduler.add_job(
            _job_event_auto_resolve,
            CronTrigger(hour=22, minute=30),
            id="event_auto_resolve",
            replace_existing=True,
            max_instances=1,
        )
        if settings.AUTO_TRANSLATE_TITLES:
            scheduler.add_job(
                _job_translate_titles,
                IntervalTrigger(hours=6),
                id="translate_titles",
                replace_existing=True,
                max_instances=1,
            )
            # Fire once 120 seconds after startup (after discover finishes).
            scheduler.add_job(
                _job_translate_titles,
                "date",
                run_date=datetime.now(timezone.utc) + timedelta(seconds=120),
                id="translate_titles_startup",
                replace_existing=True,
            )
        scheduler.add_job(
            _job_loop_db_maintenance,
            CronTrigger(hour=6, minute=45),
            id="loop_db_maintenance",
            replace_existing=True,
            max_instances=1,
        )
        # Reconcile stale `running` rows and prune terminal rows in the same
        # ledger this scheduler writes. After loop_db_maintenance (its integrity
        # check must pass before this job deletes anything) and before the
        # optimization-task cleanup that shares the DB file.
        scheduler.add_job(
            _job_loop_run_ledger_maintenance,
            CronTrigger(hour=6, minute=48),
            id="loop_run_ledger_maintenance",
            replace_existing=True,
            max_instances=1,
        )
        # Server-side drift alert evaluation + dispatch. Gated by the same
        # flag the dispatcher itself checks: with DRIFT_ALERTS_ENABLED off,
        # the job would evaluate and dispatch nothing every day.
        if settings.DRIFT_ALERTS_ENABLED:
            scheduler.add_job(
                _job_drift_alert_check,
                CronTrigger(hour=7, minute=30),
                id="drift_alert_check",
                replace_existing=True,
                max_instances=1,
            )
        # Prune completed/failed optimization tasks older than 24h so the
        # persisted task table (and the in-memory cache) stay bounded. Runs
        # shortly after loop_db_maintenance so a degraded DB surfaces first.
        scheduler.add_job(
            _job_optimization_task_cleanup,
            CronTrigger(hour=6, minute=50),
            id="optimization_task_cleanup",
            replace_existing=True,
            max_instances=1,
        )
        if settings.BACKUP_SCHEDULE_ENABLED:
            # 07:00 UTC, i.e. after loop_db_maintenance (06:45) has truncated
            # every WAL and its integrity check has passed, so each .db archived
            # below is self-contained and known-good; and before event_discover
            # (07:15) starts writing again.
            scheduler.add_job(
                _job_backup_stores,
                CronTrigger(hour=7, minute=0),
                id="backup_stores",
                replace_existing=True,
                max_instances=1,
            )
        if settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED:
            scheduler.add_job(
                _job_world_cup_source_bundle_import,
                CronTrigger(
                    hour=settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_HOUR_UTC,
                    minute=settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_MINUTE_UTC,
                ),
                id="world_cup_source_bundle_import",
                replace_existing=True,
                max_instances=1,
            )
        if settings.WORLD_CUP_MATCHDAY_REFRESH_ENABLED:
            scheduler.add_job(
                _job_world_cup_matchday_refresh,
                IntervalTrigger(
                    minutes=settings.WORLD_CUP_MATCHDAY_REFRESH_INTERVAL_MINUTES,
                ),
                id="world_cup_matchday_refresh",
                replace_existing=True,
                max_instances=1,
            )
        # World Cup daily prediction update at 06:00 UTC
        scheduler.add_job(
            _job_world_cup_prediction_update,
            CronTrigger(hour=6, minute=0),
            id="world_cup_prediction_update",
            replace_existing=True,
            max_instances=1,
        )
        # World Cup live prediction updates (every 2 minutes)
        scheduler.add_job(
            _job_world_cup_live_update,
            IntervalTrigger(minutes=2),
            id="world_cup_live_update",
            replace_existing=True,
            max_instances=1,
        )
        # Sentiment cache refresh for World Cup teams (every 8 hours).
        # Keeps the rule engine's sentiment_factor populated with fresh data
        # from RSS news + Reddit.  Without this job, get_cached_sentiment()
        # always returns None (TTL expired) and sentiment_factor stays at
        # the neutral default of 1.0.
        scheduler.add_job(
            _job_sentiment_refresh,
            IntervalTrigger(hours=8),
            id="sentiment_refresh",
            replace_existing=True,
            max_instances=1,
        )
        if settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED:
            scheduler.add_job(
                _job_discover_sport_markets,
                IntervalTrigger(minutes=settings.POLYMARKET_SPORTS_DISCOVERY_INTERVAL_MIN),
                id="sport_market_discover",
                replace_existing=True,
                max_instances=1,
            )
            scheduler.add_job(
                _job_fetch_traditional_odds,
                IntervalTrigger(minutes=settings.ODDS_FETCH_INTERVAL_MIN),
                id="sport_market_odds_fetch",
                replace_existing=True,
                max_instances=1,
            )
            scheduler.add_job(
                _job_capture_market_snapshots,
                IntervalTrigger(minutes=settings.MARKET_SNAPSHOT_INTERVAL_MIN),
                id="sport_market_snapshots",
                replace_existing=True,
                max_instances=1,
            )
        if settings.PHASE7_EDGE_DETECTOR_ENABLED:
            scheduler.add_job(
                _job_detect_sport_edges,
                IntervalTrigger(minutes=settings.EDGE_DETECTION_INTERVAL_MIN),
                id="sport_edge_detect",
                replace_existing=True,
                max_instances=1,
            )
        if settings.PHASE7_MARKET_SETTLEMENT_SCHEDULER_ENABLED:
            scheduler.add_job(
                _job_process_market_settlements,
                IntervalTrigger(minutes=settings.MARKET_SETTLEMENT_INTERVAL_MIN),
                id="market_settlement_feedback",
                replace_existing=True,
                max_instances=1,
            )
        if settings.PHASE9_LEARNING_ACTIVATED and settings.PHASE9_WEEKLY_WEIGHT_UPDATE_INTERVAL_MIN > 0:
            scheduler.add_job(
                _job_update_weights_weekly,
                IntervalTrigger(minutes=settings.PHASE9_WEEKLY_WEIGHT_UPDATE_INTERVAL_MIN),
                id="update_weights_weekly",
                replace_existing=True,
                max_instances=1,
            )
            logger.info("[Scheduler] Registered weekly weight update job (interval=%d min)", settings.PHASE9_WEEKLY_WEIGHT_UPDATE_INTERVAL_MIN)

        if settings.PHASE9_LEARNING_ACTIVATED and settings.PHASE9_OPTIMIZATION_INTERVAL_MIN > 0:
            scheduler.add_job(
                _job_reoptimize_monthly,
                IntervalTrigger(minutes=settings.PHASE9_OPTIMIZATION_INTERVAL_MIN),
                id="reoptimize_monthly",
                replace_existing=True,
                max_instances=1,
            )
            logger.info("[Scheduler] Registered monthly re-optimization job (interval=%d min)", settings.PHASE9_OPTIMIZATION_INTERVAL_MIN)
        if settings.PHASE12_FUTURES_MARKETS_ENABLED:
            scheduler.add_job(
                _job_discover_futures_markets,
                IntervalTrigger(minutes=settings.FUTURES_DISCOVERY_INTERVAL_MIN),
                id="futures_market_discover",
                replace_existing=True,
                max_instances=1,
            )
            scheduler.add_job(
                _job_capture_futures_snapshots,
                IntervalTrigger(minutes=settings.FUTURES_SNAPSHOT_INTERVAL_MIN),
                id="futures_snapshots_capture",
                replace_existing=True,
                max_instances=1,
            )
            logger.info(
                "[Scheduler] Registered futures jobs (discover@%dmin, snapshots@%dmin)",
                settings.FUTURES_DISCOVERY_INTERVAL_MIN,
                settings.FUTURES_SNAPSHOT_INTERVAL_MIN,
            )
        scheduler.start()
    except Exception:
        _release_scheduler_lock()
        raise
    discover_state = "on" if settings.EVENT_DISCOVER_ENABLED else "off"
    world_cup_state = (
        "on" if settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED else "off"
    )
    matchday_state = "on" if settings.WORLD_CUP_MATCHDAY_REFRESH_ENABLED else "off"
    logger.info(
        "[Scheduler] Started — event_discover@07:15UTC(%s) | "
        "world_cup_source_bundle_import@%02d:%02dUTC(%s) | "
        "world_cup_matchday_refresh@%dmin(%s) | "
        "world_cup_prediction_update@06:00UTC | "
        "world_cup_live_update@2min | "
        "sentiment_refresh@8h | "
        "loop_db_maintenance@06:45UTC | "
        "loop_run_ledger_maintenance@06:48UTC | "
        "optimization_task_cleanup@06:50UTC | "
        "event_auto_resolve@22:30UTC",
        discover_state,
        settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_HOUR_UTC,
        settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_MINUTE_UTC,
        world_cup_state,
        settings.WORLD_CUP_MATCHDAY_REFRESH_INTERVAL_MINUTES,
        matchday_state,
    )
    return True


def stop_scheduler() -> None:
    try:
        if scheduler.running:
            scheduler.shutdown(wait=True)
            logger.info("[Scheduler] Stopped.")
    finally:
        _release_scheduler_lock()
