"""
scheduler.py
============
APScheduler 定时任务。随 FastAPI 启动自动运行。

任务：
  07:15 UTC — event discover（freeze 预测，让反馈闭环持续积累样本）
  22:30 UTC — event auto-resolve（匹配已结算预测市场并打分）
"""

import logging
import os
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.memory import loop_run_store

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
#   - misfire_grace_time=300: a missed run (e.g. the app was down at 07:15)
#     still fires if the scheduler catches up within 5 minutes; beyond that it
#     is dropped (and logged), instead of the default 1s silent drop.
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
        scheduler.start()
    except Exception:
        _release_scheduler_lock()
        raise
    discover_state = "on" if settings.EVENT_DISCOVER_ENABLED else "off"
    logger.info(
        "[Scheduler] Started — event_discover@07:15UTC(%s) | "
        "event_auto_resolve@22:30UTC",
        discover_state,
    )
    return True


def stop_scheduler():
    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
            logger.info("[Scheduler] Stopped.")
    finally:
        _release_scheduler_lock()
