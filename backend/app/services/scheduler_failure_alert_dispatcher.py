"""Scheduler failure alert dispatcher (E8).

Best-effort alert dispatch for scheduler job failures. Three outlets:
1. Webhook HTTP POST (when ``SCHEDULER_FAILURE_ALERT_WEBHOOK_URL`` is set)
2. Sentry ``capture_exception`` (also forwarded by the scheduler itself —
   this dispatcher adds a structured ``capture_message`` breadcrumb so
   the alert is visible even when ``SENTRY_DSN`` is the only channel)
3. Structured log line

Gated by ``SCHEDULER_FAILURE_ALERT_ENABLED`` (default false). When
disabled, dispatch is a no-op — the scheduler still records the failure
in the loop-run ledger, increments the Prometheus counter, and forwards
the exception to Sentry via the existing ``_finish_run`` path. This
dispatcher only adds an explicit notification channel for operators who
want webhook + cooldown-deduplicated alerts.

Cooldown: per-job_name dedup within
``SCHEDULER_FAILURE_ALERT_COOLDOWN_SECONDS`` prevents webhook spam when
a job fails repeatedly across scrapes. Set to 0 to disable cooldown.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib import request as urllib_request

from app.core.config import settings

logger = logging.getLogger(__name__)

# Per-job_name last-dispatch timestamp (unix seconds). In-memory only —
# resets on restart, which is acceptable: a restart naturally clears
# transient alert pressure and the next failure re-evaluates fresh.
_last_dispatched: dict[str, float] = {}


def dispatch_scheduler_failure_alert(
    *,
    job_name: str,
    run_id: str | None,
    error: str | None,
    exc: BaseException | None = None,
    force: bool = False,
) -> None:
    """Dispatch a scheduler failure alert to configured outlets.

    No-op when ``SCHEDULER_FAILURE_ALERT_ENABLED`` is false. Best-effort:
    any webhook or Sentry failure is caught and logged — never raises.
    """
    if not job_name:
        job_name = "unknown"
    if not getattr(settings, "SCHEDULER_FAILURE_ALERT_ENABLED", False):
        return

    cooldown = getattr(settings, "SCHEDULER_FAILURE_ALERT_COOLDOWN_SECONDS", 1800)
    now = time.time()
    webhook_url = getattr(settings, "SCHEDULER_FAILURE_ALERT_WEBHOOK_URL", "")

    if not force and cooldown > 0 and job_name in _last_dispatched:
        if now - _last_dispatched[job_name] < cooldown:
            return  # within cooldown window — skip
    _last_dispatched[job_name] = now

    alert: dict[str, Any] = {
        "code": "scheduler_job_failed",
        "severity": "warning",
        "job_name": job_name,
        "run_id": run_id,
        "error": error,
        "exc_type": type(exc).__name__ if exc is not None else None,
    }

    # 1. Log
    logger.warning(
        "[SCHEDULER-FAILURE-ALERT] job=%s run=%s error=%s exc=%s",
        job_name, run_id, error,
        type(exc).__name__ if exc is not None else "n/a",
    )

    # 2. Sentry breadcrumb-level message. The scheduler already calls
    # capture_exception(exc, ...) in _finish_run; this adds a structured
    # breadcrumb so the alert is searchable in Sentry by code/severity
    # even when the exception capture fails or is sampled.
    try:
        from app.utils.sentry import capture_message
        capture_message(
            f"scheduler job failed: {job_name}",
            level="warning",
            code=alert["code"],
            severity=alert["severity"],
            job_name=job_name,
            run_id=run_id,
            error=error,
            exc_type=alert["exc_type"],
        )
    except Exception:  # pragma: no cover - defensive
        logger.debug("Sentry capture_message failed for scheduler failure alert", exc_info=True)

    # 3. Webhook
    if webhook_url:
        try:
            _post_webhook(webhook_url, alert)
        except Exception:  # pragma: no cover - defensive
            logger.debug("webhook dispatch failed for scheduler failure alert", exc_info=True)


def _post_webhook(url: str, alert: dict[str, Any]) -> None:
    """POST a single alert as JSON to the webhook URL. Raises on failure."""
    payload = json.dumps(alert).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib_request.urlopen(req, timeout=5) as resp:  # noqa: S310 - operator-configured URL
        if resp.status >= 400:
            logger.warning("scheduler failure webhook returned HTTP %s", resp.status)


def _reset_cooldown_state() -> None:
    """Test helper — clear the in-memory cooldown map."""
    _last_dispatched.clear()
