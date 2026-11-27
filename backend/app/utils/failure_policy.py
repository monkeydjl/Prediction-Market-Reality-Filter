"""Shared service failure policy helpers.

These helpers make intentional degradation explicit while preserving each
caller's existing return contract.
"""

from __future__ import annotations

from logging import Logger
from typing import Any, Mapping, TypeVar

T = TypeVar("T")

FAIL_CLOSED_EMPTY_LIST = "fail_closed_empty_list"
FAIL_CLOSED_NONE = "fail_closed_none"
DETERMINISTIC_FALLBACK = "deterministic_fallback"

# The third zero, named once (audit 53.180).
#
# A body that answers "nothing to show" can mean three different things, and a
# client that cannot separate them acts on the wrong one: the feature is off
# (``enabled: false``, or a 503 where the router gates), there is genuinely no
# data (``available: false`` / ``skip_reason: "no_odds"``), or the read failed.
# Only the third one is the server's fault, and only the third one means "ask
# again later" -- so it gets its own word rather than reusing either of the
# other two. One body uses one mechanism; this constant is the reason value
# (``skip_reason``) and the boolean key (``read_failed``) for that meaning.
READ_FAILED = "read_failed"


def log_service_failure(
    logger: Logger,
    source: str,
    exc: BaseException,
    *,
    policy: str,
    context: Mapping[str, Any] | None = None,
) -> None:
    suffix = _format_context(context)
    logger.warning(
        "Service failure [source=%s policy=%s%s]: %s",
        source,
        policy,
        suffix,
        type(exc).__name__,
    )


def fail_closed_empty_list(
    logger: Logger,
    source: str,
    exc: BaseException,
    *,
    context: Mapping[str, Any] | None = None,
) -> list[Any]:
    log_service_failure(
        logger,
        source,
        exc,
        policy=FAIL_CLOSED_EMPTY_LIST,
        context=context,
    )
    return []


def fail_closed_none(
    logger: Logger,
    source: str,
    exc: BaseException,
    *,
    context: Mapping[str, Any] | None = None,
) -> None:
    log_service_failure(
        logger,
        source,
        exc,
        policy=FAIL_CLOSED_NONE,
        context=context,
    )
    return None


def deterministic_fallback(
    logger: Logger,
    source: str,
    exc: BaseException,
    fallback: T,
    *,
    context: Mapping[str, Any] | None = None,
) -> T:
    log_service_failure(
        logger,
        source,
        exc,
        policy=DETERMINISTIC_FALLBACK,
        context=context,
    )
    return fallback


def _format_context(context: Mapping[str, Any] | None) -> str:
    if not context:
        return ""
    parts = [f"{key}={value}" for key, value in sorted(context.items())]
    return " " + " ".join(parts)
