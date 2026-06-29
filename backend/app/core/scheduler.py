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

import logging
import os
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.memory import loop_run_store
from app.utils import sqlite_db
from datetime import datetime, timedelta, timezone

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
    exc: BaseException | None = None,
) -> None:
    if run_id is None:
        return
    try:
        loop_run_store.finish_run(run_id, status, result=result, error=error)
    except Exception:
        logger.exception("[Scheduler] Failed to finish run ledger for %s", run_id)
    # Forward scheduler failures to Sentry (P0-7 §1.2). No-op when SENTRY_DSN
    # is empty (the wrapper handles the disabled case). Called *after* the run
    # ledger is written so the local SQLite record is authoritative even if
    # Sentry ingestion is slow / down. Pass ``exc`` explicitly so Sentry gets
    # the full stack trace even though we are outside the except block.
    if status == "failed":
        try:
            from app.utils.sentry import capture_exception
            capture_exception(
                exc,
                job_run_id=run_id,
                job_error=error,
            )
        except Exception:  # pragma: no cover - defensive
            logger.debug("[Scheduler] Sentry capture failed", exc_info=True)


async def _job_event_auto_resolve():
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
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
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
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] Loop DB maintenance failed")


async def _job_optimization_task_cleanup():
    """Daily cleanup of completed/failed optimization tasks older than 24h.

    Auto-tune / batch-optimize tasks are persisted to the loop DB so a restart
    does not 404 the polling frontend (see optimization_task_store). Without
    this job the table grows without bound; the in-memory cache would also
    accumulate stale entries across long-lived processes. Cleanup is best-
    effort — a store failure is logged and re-raised into the run ledger so
    degraded SQLite surfaces loudly rather than silently leaking rows.
    """
    logger.info("[Scheduler] Optimization task cleanup starting...")
    run_id = _start_run("optimization_task_cleanup")
    try:
        from app.services.optimization_task_manager import get_task_manager

        await get_task_manager().cleanup_old_tasks(max_age_hours=24)
        _finish_run(run_id, "success", result={"cleaned": True})
        logger.info("[Scheduler] Optimization task cleanup completed")
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] Optimization task cleanup failed")


def _run_world_cup_bundle_import(mode: str, replace: bool):
    """Shared import-mode dispatch for World Cup source bundles.

    Used by both the scheduled bundle import and the matchday refresh job
    to avoid duplicating the mode-selection logic.
    """
    from app.services.world_cup_api_football_source import (
        import_world_cup_api_football_bundle,
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
    elif mode == "sportmonks":
        return import_world_cup_sportmonks_bundle(replace=replace)
    else:
        raise ValueError(
            "WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE must be 'url', 'file', 'feeds', "
            "'api_football', or 'sportmonks'"
        )


async def _job_world_cup_source_bundle_import():
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
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] World Cup source bundle import failed")


async def _job_world_cup_matchday_refresh():
    """Refresh World Cup data during active match windows."""
    if not settings.WORLD_CUP_MATCHDAY_REFRESH_ENABLED:
        return
    if not settings.WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED:
        return

    from datetime import datetime, timezone, timedelta
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

        summary = _world_cup_bundle_import_summary(result, mode)
        _finish_run(run_id, "success", result=summary)
        logger.info(
            "[Scheduler] Matchday refresh: facts=%d mode=%s",
            summary.get("converted_fact_count", 0),
            mode,
        )
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] Matchday refresh failed")


async def _job_world_cup_prediction_update():
    """Daily World Cup score prediction update at 06:00 UTC."""
    logger.info("[Scheduler] World Cup prediction update starting...")
    run_id = _start_run("world_cup_prediction_update")
    try:
        from app.services.world_cup_prediction_scheduler import run_daily_prediction_update

        result = await run_daily_prediction_update()
        _finish_run(run_id, "success", result=_summarize_prediction_update(result))
        logger.info("[Scheduler] World Cup prediction update completed")
    except Exception as exc:
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
        logger.exception("[Scheduler] World Cup prediction update failed")


async def _job_world_cup_live_update():
    """Live World Cup prediction updates during active matches (every 2 minutes)."""
    from app.services.world_cup_live_update_service import update_live_predictions

    try:
        result = await update_live_predictions()

        # Only log if there were matches to update
        if result.get("matches_checked", 0) > 0:
            logger.info(
                "[Scheduler] Live update: checked=%d live=%d updated=%d",
                result.get("matches_checked", 0),
                result.get("live_count", 0),
                result.get("updated", 0),
            )
    except Exception as exc:
        logger.exception("[Scheduler] Live update failed: %s", exc)


async def _job_sentiment_refresh():
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
    if result.get("source_feeds"):
        summary["source_feeds"] = result["source_feeds"]
    if result.get("provider"):
        summary["provider"] = result["provider"]
    if result.get("skipped_source_count") is not None:
        summary["skipped_source_count"] = result["skipped_source_count"]
    if result.get("skipped_sources"):
        summary["skipped_sources"] = result["skipped_sources"]
    if result.get("source_fetch_count") is not None:
        summary["source_fetch_count"] = result["source_fetch_count"]
    if result.get("source_fetches"):
        summary["source_fetches"] = result["source_fetches"]
    if result.get("call_budget"):
        summary["call_budget"] = result["call_budget"]
    if result.get("run"):
        summary["run"] = result["run"]
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
            # Run discovery every 4 hours so the system accumulates samples faster.
            scheduler.add_job(
                _job_event_discover,
                IntervalTrigger(hours=4),
                id="event_discover",
                replace_existing=True,
                max_instances=1,
            )
            # Also fire once 30 seconds after startup for immediate population.
            scheduler.add_job(
                _job_event_discover,
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
        scheduler.add_job(
            _job_loop_db_maintenance,
            CronTrigger(hour=6, minute=45),
            id="loop_db_maintenance",
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


def stop_scheduler():
    try:
        if scheduler.running:
            scheduler.shutdown(wait=True)
            logger.info("[Scheduler] Stopped.")
    finally:
        _release_scheduler_lock()
