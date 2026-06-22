from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable

from app.core.config import settings
from app.core.logging import setup_logging
from app.core.scheduler import start_scheduler, stop_scheduler
from app.services.llm_startup_check_service import validate_primary_llm_startup
from app.utils import sqlite_db

logger = logging.getLogger(__name__)

WaitForShutdown = Callable[[], Awaitable[None]]


async def _wait_for_shutdown_signal() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda _signum, _frame: request_stop())

    await stop.wait()


async def run_scheduler_worker(
    wait_for_shutdown: WaitForShutdown = _wait_for_shutdown_signal,
) -> int:
    setup_logging()
    logger.info("PMRF scheduler worker starting")

    if not settings.SCHEDULER_ENABLED:
        logger.warning("Scheduler worker disabled by SCHEDULER_ENABLED=false")
        return 0

    if settings.LLM_STARTUP_CHECK_ENABLED:
        await validate_primary_llm_startup()
        logger.info("Primary LLM startup check passed.")

    maintenance = sqlite_db.maintain()
    logger.info("Loop DB maintenance passed: %s", maintenance)

    started = start_scheduler()
    if not started:
        logger.error("Scheduler worker could not acquire scheduler ownership.")
        return 1

    try:
        await wait_for_shutdown()
    finally:
        stop_scheduler()

    return 0


def main() -> int:
    return asyncio.run(run_scheduler_worker())


if __name__ == "__main__":
    raise SystemExit(main())
