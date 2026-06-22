"""
scheduler.py
============
APScheduler 定时任务。随 FastAPI 启动自动运行。

任务：
  07:15 UTC — event discover（freeze 预测，让反馈闭环持续积累样本）
  05:20 UTC — World Cup source bundle import（可选，默认关闭）
  22:30 UTC — event auto-resolve（匹配已结算预测市场并打分）
"""

import logging
import os
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.memory import loop_run_store
from app.utils import sqlite_db

logger = logging.getLogger(__name__)
_scheduler_lock_handle: Any | None = None
_scheduler_lock_skipped = False


def _acquire_process_lock(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_process_lock(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
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


def _start_run(job_name: str) -> str | None:
    try:
        return loop_run_store.start_run(job_name)
    except Exception:
        logger.exception("[Scheduler] Failed to start run ledger for %s", job_name)
        return None


def _finish_run(
    run_id: str | None,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    if run_id is None:
        return
    try:
        loop_run_store.finish_run(run_id, status, result=result, error=error)
    except Exception:
        logger.exception("[Scheduler] Failed to finish run ledger for %s", run_id)


async def _job_event_auto_resolve():
    """每天 22:30 UTC 自动裁定事件层（匹配已结算预测市场）。"""
    logger.info("[Scheduler] Event auto-resolve starting...")
    run_id = _start_run("event_auto_resolve")
    try:
        from app.services.event_resolve_service import auto_resolve_events

        result = await auto_resolve_events(resolved_limit=200)
        _finish_run(run_id, "success", result=result)
        logger.info(
            "[Scheduler] Event auto-resolve: resolved=%d checked=%d",
            result.get("resolved_count", 0),
            result.get("checked_count", 0),
        )
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc))
        logger.exception("[Scheduler] Event auto-resolve failed")


async def _job_event_discover():
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
        _finish_run(run_id, "failed", error=str(exc))
        logger.exception("[Scheduler] Event discover failed")


async def _job_loop_db_maintenance():
    """Daily SQLite loop-store maintenance: WAL truncation + integrity check."""
    logger.info("[Scheduler] Loop DB maintenance starting...")
    run_id = _start_run("loop_db_maintenance")
    try:
        result = sqlite_db.maintain()
        _finish_run(run_id, "success", result=result)
        checkpoint = result.get("checkpoint", {})
        logger.info(
            "[Scheduler] Loop DB maintenance ok: checkpoint=%s",
            checkpoint,
        )
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc))
        logger.exception("[Scheduler] Loop DB maintenance failed")


async def _job_world_cup_source_bundle_import():
    """Import the configured World Cup source bundle into sports facts."""
    if not settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED:
        return

    mode = settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE.strip().lower()
    logger.info("[Scheduler] World Cup source bundle import starting...")
    run_id = _start_run("world_cup_source_bundle_import")
    try:
        from app.services.world_cup_source_bundle import (
            import_world_cup_source_bundle_file,
            import_world_cup_source_bundle_url,
        )

        if mode == "url":
            result = import_world_cup_source_bundle_url(
                replace=settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_REPLACE
            )
        elif mode == "file":
            result = import_world_cup_source_bundle_file(
                replace=settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_REPLACE
            )
        else:
            raise ValueError(
                "WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE must be 'url' or 'file'"
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
        _finish_run(run_id, "failed", error=str(exc))
        logger.exception("[Scheduler] World Cup source bundle import failed")


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
    if result.get("source_file"):
        summary["source_file"] = result["source_file"]
    if result.get("source_url"):
        summary["source_url"] = result["source_url"]
    if result.get("source_metadata"):
        summary["source_metadata"] = result["source_metadata"]
    return summary


def start_scheduler():
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
            scheduler.add_job(
                _job_event_discover,
                CronTrigger(hour=7, minute=15),
                id="event_discover",
                replace_existing=True,
                max_instances=1,
            )
        scheduler.add_job(
            _job_event_auto_resolve,
            CronTrigger(hour=22, minute=30),
            id="event_auto_resolve",
            replace_existing=True,
            max_instances=1,
        )
        scheduler.add_job(
            _job_loop_db_maintenance,
            CronTrigger(hour=6, minute=45),
            id="loop_db_maintenance",
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
        scheduler.start()
    except Exception:
        _release_scheduler_lock()
        raise
    discover_state = "on" if settings.EVENT_DISCOVER_ENABLED else "off"
    world_cup_state = (
        "on" if settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED else "off"
    )
    logger.info(
        "[Scheduler] Started — event_discover@07:15UTC(%s) | "
        "world_cup_source_bundle_import@%02d:%02dUTC(%s) | "
        "loop_db_maintenance@06:45UTC | event_auto_resolve@22:30UTC",
        discover_state,
        settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_HOUR_UTC,
        settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_MINUTE_UTC,
        world_cup_state,
    )
    return True


def stop_scheduler():
    try:
        if scheduler.running:
            scheduler.shutdown(wait=True)
            logger.info("[Scheduler] Stopped.")
    finally:
        _release_scheduler_lock()
