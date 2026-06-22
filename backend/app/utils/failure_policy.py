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
        exc,
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
