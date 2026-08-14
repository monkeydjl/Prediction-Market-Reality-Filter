"""Sentry SDK integration (P0-7 §1.2).

When ``SENTRY_DSN`` is non-empty, the FastAPI app and scheduler both report
exceptions to Sentry. When empty (default), all functions here degrade to
no-ops so the app still boots without a Sentry backend configured.

The init hook is called from ``app.main.lifespan`` so the SDK is active
before any route handler or scheduler job runs. The ``capture_exception``
helper is called from ``scheduler._finish_run`` (and may be called from
any other component that wants to forward an exception to Sentry).

Why a wrapper module instead of using ``sentry_sdk`` directly?
- A single import boundary means tests can patch ``app.utils.sentry`` to
  a stub without depending on the real SDK.
- The ``SENTRY_DSN empty? -> no-op`` check lives in one place.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

# The only levels sentry_sdk.capture_message accepts. Spelled out here so the
# wrapper's own signature is the boundary that rejects a bad level, rather than
# passing it through to the SDK.
SentryLevel = Literal["fatal", "critical", "error", "warning", "info", "debug"]

try:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    _SENTRY_AVAILABLE = True
except ImportError:  # pragma: no cover - env misconfig
    sentry_sdk = None  # type: ignore[assignment]
    _SENTRY_AVAILABLE = False


def init_sentry(
    *,
    dsn: str,
    environment: str = "production",
    release: str | None = None,
    traces_sample_rate: float = 0.0,
    attach_stacktrace: bool = True,
) -> bool:
    """Initialize the Sentry SDK. Returns True if initialized, False if no-op.

    Called once from ``app.main.lifespan``. Idempotent — calling again with
    the same DSN is a no-op (sentry_sdk.init guards against double-init).

    Args:
        dsn: Sentry project DSN. Empty string disables Sentry entirely
            (the common dev / test case).
        environment: Sentry environment tag (production / staging / dev).
        release: Optional release identifier (e.g. ``pmrf@0.3.0``).
        traces_sample_rate: Performance transaction sample rate. Default 0
            (no perf monitoring — keep P0 minimal). Set to 0.01-0.1 in prod
            if performance tracing is wanted.
        attach_stacktrace: Always attach stack traces to messages, even when
            no exception is in flight. Default True for parity with the
            existing logger.exception behavior.
    """
    if not dsn:
        logger.info("Sentry disabled: SENTRY_DSN is empty.")
        return False
    if not _SENTRY_AVAILABLE:
        logger.warning(
            "Sentry DSN is set but sentry_sdk is not installed; "
            "install with: pip install sentry-sdk[fastapi]"
        )
        return False

    integrations: list[Any] = []
    try:
        # FastApiIntegration auto-instruments request handlers + errors.
        integrations.append(FastApiIntegration())
    except Exception:  # pragma: no cover - defensive
        logger.warning("FastApiIntegration init failed", exc_info=True)

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        traces_sample_rate=traces_sample_rate,
        attach_stacktrace=attach_stacktrace,
        integrations=integrations,
        # Send default PII off — we never want operator API keys or
        # request bodies (which may contain user-supplied news context)
        # in Sentry. The existing logging already redacts secrets; mirror
        # that posture here.
        send_default_pii=False,
    )
    logger.info(
        "Sentry initialized (env=%s, release=%s, traces_sample_rate=%s)",
        environment,
        release,
        traces_sample_rate,
    )
    return True


def capture_exception(exc: BaseException | None = None, **context: Any) -> None:
    """Forward an exception to Sentry. No-op when Sentry is disabled.

    Use this from scheduler job except blocks and any other catch site
    that wants Sentry reporting. The exception's traceback is captured
    automatically when called inside an ``except`` block; pass ``exc``
    explicitly when calling outside one.

    Extra context can be passed as kwargs; it shows up in the Sentry event
    under "extra".
    """
    if not _SENTRY_AVAILABLE:
        return
    try:
        if context:
            sentry_sdk.set_context("pmrf", context)
        if exc is not None:
            sentry_sdk.capture_exception(exc)
        else:
            # Re-raise + capture to grab the active traceback
            sentry_sdk.capture_exception()
    except Exception:  # pragma: no cover - defensive
        logger.debug("Sentry capture_exception failed", exc_info=True)


def capture_message(message: str, level: SentryLevel = "info", **context: Any) -> None:
    """Forward a free-form message to Sentry. No-op when disabled."""
    if not _SENTRY_AVAILABLE:
        return
    try:
        if context:
            sentry_sdk.set_context("pmrf", context)
        sentry_sdk.capture_message(message, level=level)
    except Exception:  # pragma: no cover - defensive
        logger.debug("Sentry capture_message failed", exc_info=True)


def is_enabled() -> bool:
    """Return True if Sentry SDK is initialized and capturing events."""
    if not _SENTRY_AVAILABLE:
        return False
    try:
        return sentry_sdk.is_initialized()  # type: ignore[union-attr]
    except Exception:  # pragma: no cover - defensive
        return False
