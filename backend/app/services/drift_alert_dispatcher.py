"""Drift alert dispatcher (Plan 2 §1.7).

Best-effort alert dispatch for calibration drift. Three outlets:
1. Webhook HTTP POST (when ``DRIFT_ALERT_WEBHOOK_URL`` is set)
2. Sentry ``capture_message`` (breadcrumb-level, level=warning)
3. Structured log line

Gated by ``DRIFT_ALERTS_ENABLED`` (default false). When disabled, all
dispatch is a no-op — drift is still computed and exposed via the
``/quality-metrics/drift`` route and the ``CALIBRATION_DRIFT`` gauge, but
no side effects fire. Detection (``evaluate_scheduler_alerts``) always runs
regardless of the flag, so the ``alerts`` list returned by the drift route
stays consistent with the pure rules 1-3 in ``calibration_drift_service``.

Cooldown: per-alert-code dedup within ``DRIFT_ALERT_COOLDOWN_SECONDS``
prevents webhook spam when a drift condition persists across scrapes.

Rule 4 (scheduler_zero_resolved) is evaluated here because it needs
``loop_run_store`` access (I/O), unlike the pure drift rules in
``calibration_drift_service``.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib import request as urllib_request

from app.core.config import settings
from app.utils.sentry import capture_message

logger = logging.getLogger(__name__)

# Per-code last-dispatch timestamp (unix seconds). In-memory only — resets
# on restart, which is fine: a restart naturally clears transient alert
# pressure and the next scrape re-evaluates fresh.
_last_dispatched: dict[str, float] = {}


def dispatch_drift_alerts(alerts: list[dict[str, Any]], *, force: bool = False) -> None:
    """Dispatch a list of drift alert dicts to configured outlets.

    No-op when ``DRIFT_ALERTS_ENABLED`` is false. Best-effort: any webhook
    or Sentry failure is caught and logged — never raises.
    """
    if not alerts:
        return
    if not getattr(settings, "DRIFT_ALERTS_ENABLED", False):
        return

    cooldown = getattr(settings, "DRIFT_ALERT_COOLDOWN_SECONDS", 3600)
    now = time.time()
    webhook_url = getattr(settings, "DRIFT_ALERT_WEBHOOK_URL", "")

    for alert in alerts:
        code = alert.get("code", "unknown")
        if not force and cooldown > 0 and code in _last_dispatched:
            if now - _last_dispatched[code] < cooldown:
                continue  # within cooldown window — skip
        _last_dispatched[code] = now

        # 1. Log
        logger.warning(
            "[DRIFT-ALERT] %s severity=%s detail=%s",
            code, alert.get("severity", "unknown"), alert.get("detail"),
        )

        # 2. Sentry breadcrumb-level message
        try:
            capture_message(
                f"drift alert: {code}",
                level="warning",
                code=code,
                severity=alert.get("severity"),
                detail=alert.get("detail"),
            )
        except Exception:  # pragma: no cover - defensive
            logger.debug("Sentry capture_message failed for drift alert", exc_info=True)

        # 3. Webhook
        if webhook_url:
            try:
                _post_webhook(webhook_url, alert)
            except Exception:  # pragma: no cover - defensive
                logger.debug("webhook dispatch failed for drift alert", exc_info=True)


def evaluate_scheduler_alerts() -> list[dict[str, Any]]:
    """Rule 4: scheduler succeeded N times but 0 new resolved predictions.

    Returns a list of alert dicts (empty when the condition is not met).
    Reads ``loop_run_store.recent_runs`` — called by the drift route on
    each request. Detection always runs regardless of
    ``DRIFT_ALERTS_ENABLED``; only ``dispatch_drift_alerts`` is gated by
    the flag.
    """
    from app.memory import loop_run_store

    threshold = getattr(settings, "DRIFT_SCHEDULER_ZERO_RESOLVED_RUNS", 3)
    # Query server-side filtered by job_name so other jobs (event_discover,
    # maintenance, World Cup) cannot crowd out the auto-resolve history.
    # The previous global-query + client-side filter approach silently
    # failed when other jobs filled the recent window.
    resolve_runs = loop_run_store.recent_runs(
        limit=threshold, job_name="event_auto_resolve"
    )
    if len(resolve_runs) < threshold:
        return []
    if not all(r.get("status") == "success" for r in resolve_runs):
        return []

    # Sum resolved_count across the recent N successful event_auto_resolve
    # runs. The result dict is stored in loop_runs.result_json and parsed
    # by _row_to_dict into run["result"]. A non-zero sum means the
    # resolution pipeline is making progress; 0 means it's stuck.
    total_resolved = 0
    for r in resolve_runs:
        result = r.get("result") or {}
        if isinstance(result, dict):
            total_resolved += int(result.get("resolved_count", 0))

    if total_resolved > 0:
        return []

    return [{
        "code": "scheduler_zero_resolved",
        "severity": "medium",
        "detail": {
            "consecutive_successes": threshold,
            "total_resolved_count": 0,
            "note": "Scheduler succeeded %d times but 0 new resolved "
                    "predictions across those runs — resolution pipeline "
                    "may be stuck."
                    % threshold,
        },
    }]


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
            logger.warning("drift webhook returned HTTP %s", resp.status)


def _reset_cooldown_state() -> None:
    """Test helper — clear the in-memory cooldown map."""
    _last_dispatched.clear()
