"""
scheduler.py
============
APScheduler 定时任务。随 FastAPI 启动自动运行。

任务：
  07:00 UTC — 扫描市场（最多 50 个候选，通过过滤后分析）
  22:00 UTC — auto-resolve + 输出校准摘要到日志
  22:30 UTC — event auto-resolve（匹配已结算 Polymarket 市场）
"""

import asyncio
import logging
import re

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.utils.market_utils import safe_float

logger = logging.getLogger(__name__)
# job_defaults apply to every job added via start_scheduler (and to any added
# later that don't override them):
#   - coalesce=True: if the scheduler fell behind and a job would fire more
#     than once to catch up, run it just once (no backlog stampede).
#   - misfire_grace_time=300: a missed run (e.g. the app was down at 07:00)
#     still fires if the scheduler catches up within 5 minutes; beyond that it
#     is dropped (and logged), instead of the default 1s silent drop.
scheduler = AsyncIOScheduler(
    timezone="UTC",
    job_defaults={"coalesce": True, "misfire_grace_time": 300},
)

# ── 市场过滤常量（与 scanner.py 保持一致）────────────────────────────
_MIN_LIQUIDITY = 5_000
_MIN_VOLUME    = 1_000
_ABSURD_RE = re.compile(
    r"(jesus.*return|alien.*contact|bigfoot|flat.?earth|time.travel)",
    re.I,
)


def _is_valid_market(market) -> bool:
    if safe_float(market.liquidity, 0) < _MIN_LIQUIDITY:
        return False
    if safe_float(market.volume, 0) < _MIN_VOLUME:
        return False
    prob = safe_float(market.yes_price, 0.5) * 100
    if prob >= 92 or prob <= 8:
        return False
    if _ABSURD_RE.search(market.question):
        return False
    return True


async def _job_morning_scan():
    """每天 07:00 UTC 扫描并记录信号摘要。"""
    logger.info("[Scheduler] Morning scan starting...")
    try:
        from app.services.polymarket_service import fetch_markets
        from app.services.gnews_service import fetch_google_news
        from app.services.rss_service import fetch_news
        from app.services.news_filter_service import filter_news_for_market
        from app.services.ai_analysis_service import analyze_market
        from app.services.analysis_audit_service import record_analysis
        from app.memory.market_memory import get_cached_analysis, set_cached_analysis
        from app.memory.agent_memory import add_prediction

        markets = await fetch_markets(limit=50)
        rss_news = await fetch_news(limit=5)
        rss_articles = [
            {"title": a.title, "description": a.summary,
             "source": a.source, "published": a.published}
            for a in rss_news
        ]

        # Process each market concurrently (Semaphore(4) caps LLM concurrency,
        # mirroring /scan). Each _process_market returns a small result so the
        # shared signal_counts / actionable are aggregated by the main task
        # after gather - no concurrent mutation of shared state.
        semaphore = asyncio.Semaphore(4)

        async def _process_market(market):
            """Return (signal_label, analysis_or_none).

            signal_label is the signal string for counting, or "skipped" when
            the market was filtered / had no relevant news / failed. analysis
            is the full analysis dict when one was produced (also for cache
            hits), else None.
            """
            async with semaphore:
                try:
                    if not _is_valid_market(market):
                        return "skipped", None

                    cached = get_cached_analysis(market.question)
                    if cached:
                        return cached.get("signal", "WATCHLIST"), cached

                    google_news = await fetch_google_news(market.question)
                    filtered = filter_news_for_market(
                        market_question=market.question,
                        articles=rss_articles + google_news,
                    )
                    if filtered["summary"]["selected_count"] == 0:
                        return "skipped", None

                    analysis = await analyze_market(
                        market_question=market.question,
                        market_probability=safe_float(market.yes_price, 0.5) * 100,
                        news_context=filtered["context"],
                        volume=safe_float(market.volume, 0),
                        liquidity=safe_float(market.liquidity, 0),
                    )
                    analysis["market_id"] = market.id
                    record_analysis(analysis)
                    add_prediction(
                        market_question=market.question,
                        market_probability=safe_float(market.yes_price, 0.5) * 100,
                        final_probability=safe_float(analysis.get("ai_probability"), 50),
                        agent_results=[{
                            "agent_name": "ai_analysis_service",
                            "probability": safe_float(analysis.get("ai_probability"), 50),
                            "confidence": safe_float(analysis.get("confidence_score"), 0),
                        }],
                    )
                    set_cached_analysis(market.question, analysis)
                    return analysis.get("signal", "WATCHLIST"), analysis
                except Exception as exc:
                    logger.warning(
                        "[Scheduler] market processing failed [%s]: %s",
                        str(getattr(market, "question", ""))[:80], exc,
                    )
                    return "skipped", None

        results = await asyncio.gather(*(_process_market(m) for m in markets))

        signal_counts: dict[str, int] = {
            "STRONG_LONG": 0, "LONG": 0, "SHORT": 0,
            "STRONG_SHORT": 0, "WATCHLIST": 0, "skipped": 0,
        }
        actionable = []
        for signal_label, analysis in results:
            signal_counts[signal_label] = signal_counts.get(signal_label, 0) + 1
            if analysis is not None and signal_label not in ("WATCHLIST", "skipped"):
                actionable.append(analysis)

        logger.info(
            "[Scheduler] Morning scan done. signals=%s actionable=%d",
            signal_counts, len(actionable),
        )
        for r in actionable:
            logger.info(
                "  ⚡ %s | div=%+.1f%% | conf=%.2f | %s",
                r.get("signal"),
                safe_float(r.get("divergence"), 0),
                safe_float(r.get("confidence_score"), 0),
                r.get("market_question", "")[:72],
            )

    except Exception:
        logger.exception("[Scheduler] Morning scan failed")


async def _job_evening_resolve():
    """每天 22:00 UTC 自动解决预测并打印校准摘要。"""
    logger.info("[Scheduler] Evening resolve starting...")
    try:
        from app.services.auto_resolve_service import run_auto_resolve
        from app.services.calibration_service import get_calibration_report

        result = await run_auto_resolve(resolved_limit=200)
        logger.info(
            "[Scheduler] Auto-resolve: resolved=%d checked=%d",
            result.get("resolved_count", 0),
            result.get("checked_count", 0),
        )

        report = get_calibration_report()
        overall = report.get("overall", {})
        n = overall.get("n", 0)
        if n > 0:
            logger.info(
                "[Scheduler] Calibration → Brier=%.4f | Skill=%.3f | Grade=%s | n=%d",
                overall.get("score", 0) or 0,
                overall.get("skill_score", 0) or 0,
                overall.get("grade", "N/A"),
                n,
            )
        else:
            logger.info("[Scheduler] No resolved predictions yet.")

    except Exception:
        logger.exception("[Scheduler] Evening resolve failed")


async def _job_event_auto_resolve():
    """每天 22:30 UTC 自动裁定事件层（匹配已结算 Polymarket 市场）。

    与市场层 evening_resolve（22:00）错开 30 分钟，避免同时拉取 Polymarket。
    """
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


def start_scheduler():
    scheduler.add_job(
        _job_morning_scan,
        CronTrigger(hour=7, minute=0),
        id="morning_scan",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        _job_evening_resolve,
        CronTrigger(hour=22, minute=0),
        id="evening_resolve",
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
    logger.info(
        "[Scheduler] Started — morning_scan@07:00UTC | "
        "evening_resolve@22:00UTC | event_auto_resolve@22:30UTC"
    )


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[Scheduler] Stopped.")
