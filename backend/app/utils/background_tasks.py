"""Fire-and-forget task launcher that keeps a reference and logs the outcome.

`asyncio.create_task(coro)` without storing the returned task has two problems:

1. The event loop only keeps a strong reference to a task while it is scheduled
   to run a step. Between steps the task can be garbage collected, and it then
   stops mid-execution with no error anywhere. This is what ruff's RUF006 flags.
2. If the coroutine raises, nothing retrieves the exception. Python reports it
   as a "Task exception was never retrieved" message at garbage-collection time
   — on stderr, outside logging, with no request context and no Sentry event.

`spawn` fixes both: the task is held in a module-level set until it finishes,
and a done callback logs whatever it ended with.

This is for work whose result the caller genuinely does not await (a long
optimization run behind a task-id poll endpoint). When the coroutine is cheap
and non-blocking — a status-dict update, say — just `await` it instead; a task
buys nothing and defers the effect past the caller's return.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# Strong references to in-flight tasks. Entries are discarded by the done
# callback, so this stays bounded by the number of concurrently running tasks.
_PENDING: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task[Any]:
    """Run `coro` in the background, holding a reference and logging the result.

    `name` identifies the work in the log line; include the task/job id when
    there is one so a failure can be traced back to the record the caller
    handed to the client.
    """
    task = asyncio.create_task(coro, name=name)
    _PENDING.add(task)
    task.add_done_callback(_on_done)
    return task


def _on_done(task: asyncio.Task[Any]) -> None:
    _PENDING.discard(task)
    name = task.get_name()
    if task.cancelled():
        # Normal at shutdown; noteworthy otherwise, because the coroutine's own
        # `except Exception` handlers did not run (CancelledError is a
        # BaseException) so any state it owns was left mid-transition.
        logger.warning("Background task cancelled: %s", name)
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Background task failed: %s", name, exc_info=exc)


def pending_count() -> int:
    """Number of in-flight spawned tasks. For tests and diagnostics."""
    return len(_PENDING)
