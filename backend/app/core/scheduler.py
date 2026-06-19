"""
scheduler.py
============
APScheduler 定时任务。随 FastAPI 启动自动运行。

任务：
  07:15 UTC — event discover（freeze 预测，让反馈闭环持续积累样本）
  22:30 UTC — event auto-resolve（匹配已结算预测市场并打分）
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings

logger = logging.getLogger(__name__)
# job_defaults apply to every job added via start_scheduler (and to any added
# later that don't override them):
#   - coalesce=True: if the scheduler fell behind and a job would fire more
#     than once to catch up, run it just once (no backlog stampede).
#   - misfire_grace_time=300: a missed run (e.g. the app was down at 07:15)
#     still fires if the scheduler catches up within 5 minutes; beyond that it
#     is dropped (and logged), instead of the default 1s silent drop.
scheduler = AsyncIOScheduler(
    timezone="UTC",
    job_defaults={"coalesce": True, "misfire_grace_time": 300},
)


async def _job_event_auto_resolve():
    """每天 22:30 UTC 自动裁定事件层（匹配已结算预测市场）。"""
    logger.info("[Scheduler] Event auto-resolve starting...")
    try:
        from app.services.event_resolve_service import auto_resolve_events

        result = await auto_resolve_events(resolved_limit=200)
        logger.info(
            "[Scheduler] Event auto-resolve: resolved=%d checked=%d",
            result.get("resolved_count", 0),
            result.get("checked_count", 0),
        )
    except Exception:
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
    try:
        from app.services.event_intelligence_service import discover_events

        result = await discover_events(
            limit=settings.EVENT_DISCOVER_LIMIT, use_cache=False
        )
        logger.info(
            "[Scheduler] Event discover: count=%d",
            result.get("count", 0),
        )
    except Exception:
        logger.exception("[Scheduler] Event discover failed")


def start_scheduler():
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
    discover_state = "on" if settings.EVENT_DISCOVER_ENABLED else "off"
    logger.info(
        "[Scheduler] Started — event_discover@07:15UTC(%s) | "
        "event_auto_resolve@22:30UTC",
        discover_state,
    )


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[Scheduler] Stopped.")
