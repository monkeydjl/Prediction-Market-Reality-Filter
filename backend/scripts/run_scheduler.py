from __future__ import annotations

import asyncio
import logging
import signal
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

# Make backend importable when run as a script. The scheduler systemd unit's
# ExecStart is `python scripts/run_scheduler.py`, which puts `backend/scripts`
# on sys.path[0] rather than `backend`, so without this the worker dies at
# import with `No module named 'app'` and systemd restarts it into the same
# failure every RestartSec.
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

try:
    from app.core.config import settings  # noqa: E402
except RuntimeError as exc:
    print(
        f"Configuration refused startup: {type(exc).__name__}",
        file=sys.stderr,
    )
    raise SystemExit(1) from None

from app.core.logging import setup_logging  # noqa: E402
from app.core.preflight import (  # noqa: E402
    log_realtime_push_posture,
    validate_production_config,
)
from app.core.scheduler import start_scheduler, stop_scheduler  # noqa: E402
from app.services.llm_startup_check_service import (  # noqa: E402
    validate_primary_llm_startup,
)
from app.utils import sqlite_db  # noqa: E402

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

    # This process, not the API, is the one that spends LLM budget unattended and
    # writes the backup archives, so it gets the same production gate. Before the
    # SCHEDULER_ENABLED short-circuit: a misconfigured production deployment is a
    # fact worth reporting even when this unit is the disabled replica.
    validate_production_config()
    log_realtime_push_posture()

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
    try:
        return asyncio.run(run_scheduler_worker())
    except Exception as exc:
        print(f"Scheduler worker failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
