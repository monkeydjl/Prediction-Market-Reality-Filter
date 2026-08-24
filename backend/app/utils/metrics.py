"""Prometheus metric definitions for the EIP backend (P0-6).

All metrics are exported via the default ``prometheus_client`` registry
and exposed at ``/metrics``. Components import these symbols and call
``.inc()`` / ``.observe()`` / ``.set()`` as needed. The ``/metrics``
endpoint is wired in ``app.main``.

Metric naming follows the ``pmrf_*`` prefix convention (Project:
Prediction Market Reality Filter). Prometheus metric names must NOT
contain hyphens — only ``[a-zA-Z0-9_:]``.

Two metric families are defined:

1. **Counters / Histograms** — incremented at the source (overlay
   builds, scheduler runs, LLM calls). These are accurate at every
   scrape.

2. **Gauges** — recomputed from ``event_store`` /
   ``prediction_store`` on each ``/metrics`` scrape via
   :func:`refresh_aggregate_gauges`. This is cheaper than maintaining
   them eagerly on every ``analyze_event`` and stays accurate even
   when events are deleted or restored outside the API.

When ``prometheus_client`` is not installed (e.g. local dev), all
symbols degrade to no-op stubs so the rest of the app still imports
cleanly. The ``/metrics`` endpoint will then return an empty payload.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Gauge, Histogram, Info
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover - env misconfig
    logger.warning(
        "prometheus_client not installed; metrics are no-ops. "
        "Install with: pip install prometheus_client"
    )
    _PROMETHEUS_AVAILABLE = False

    class _NoOpMetric:
        """Drop-in stand-in for a Prometheus metric. All operations are no-ops."""

        def labels(self, *args: Any, **kwargs: Any) -> _NoOpMetric:  # noqa: D401 - mirrors prom API
            return self

        def inc(self, *args: Any, **kwargs: Any) -> None:
            pass

        def observe(self, *args: Any, **kwargs: Any) -> None:
            pass

        def set(self, *args: Any, **kwargs: Any) -> None:
            pass

        def info(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - Info only
            pass

    def Counter(*args: Any, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        return _NoOpMetric()

    def Gauge(*args: Any, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        return _NoOpMetric()

    def Histogram(*args: Any, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        return _NoOpMetric()

    def Info(*args: Any, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        return _NoOpMetric()

    # *args/**kwargs like the stubs above, so a caller that ever passes a
    # registry still works. [misc] rather than the [no-redef] the class stubs
    # above need: for a *function*, mypy demands that conditional variants have
    # identical signatures and reports only that - impossible to satisfy here,
    # since the real one is annotated with a prometheus_client type that does
    # not exist on this branch.
    def generate_latest(*args: Any, **kwargs: Any) -> bytes:  # type: ignore[misc]
        return b""

    CONTENT_TYPE_LATEST = "text/plain; charset=utf-8"


# ---------------------------------------------------------------------------
# Spec §1.1 — five core metrics
# ---------------------------------------------------------------------------

# 1. decision_quality_downgrade_rate (Counter; Prometheus computes rate on scrape)
DECISION_QUALITY_DOWNGRADE = Counter(
    "pmrf_decision_quality_downgrade_total",
    "Number of YES/NO recommendations downgraded by decision_quality overlay, "
    "by downgrade reason category.",
    ["reason"],
)

# 2. consensus_distribution (Gauge; recomputed on scrape)
CONSENSUS_DISTRIBUTION = Gauge(
    "pmrf_consensus_distribution",
    "Count of events by decision_quality.consensus_level.",
    ["level"],  # level ∈ {none, low, medium, high}
)

# 3. rule_fire_count (Counter; incremented on each downgrade-rule fire)
RULE_FIRE = Counter(
    "pmrf_rule_fire_total",
    "Number of times each decision_quality downgrade rule fired.",
    ["rule"],  # rule ∈ {stage_a_1, stage_a_2, stage_a_3, stage_a_4, stage_b_risk}
)

# 4. build_failure_count (Counter; incremented when overlay build raises)
OVERLAY_BUILD_FAILURE = Counter(
    "pmrf_overlay_build_failure_total",
    "Number of overlay build failures (best-effort fallback engaged).",
    ["phase"],  # phase ∈ {decision_quality, market_quality, source_reliability, llm_telemetry, merge}
)

# 5. latency_ms (Histogram; observed around each overlay build)
OVERLAY_LATENCY = Histogram(
    "pmrf_overlay_latency_ms",
    "Time spent building each overlay block (milliseconds).",
    ["phase"],
    buckets=(1, 5, 10, 20, 50, 100, 250, 500),
)


# ---------------------------------------------------------------------------
# Scheduler metrics
# ---------------------------------------------------------------------------

SCHEDULER_LAST_SUCCESS = Gauge(
    "pmrf_scheduler_last_success_timestamp",
    "Unix timestamp of the most recent successful scheduler job completion.",
    ["job_name"],
)

SCHEDULER_FAILED_RUNS = Counter(
    "pmrf_scheduler_failed_runs_total",
    "Number of failed scheduler job runs.",
    ["job_name"],
)


# ---------------------------------------------------------------------------
# LLM cost telemetry
# ---------------------------------------------------------------------------

LLM_TOKEN_COST = Counter(
    "pmrf_llm_token_cost_total",
    "Estimated LLM token cost in USD (sum of input+output across all calls).",
    ["model"],
)

LLM_TOKEN_USAGE = Counter(
    "pmrf_llm_token_usage_total",
    "LLM token usage count by token kind (input/output).",
    ["model", "kind"],
)


# ---------------------------------------------------------------------------
# Calibration drift
# ---------------------------------------------------------------------------

CALIBRATION_BRIER = Gauge(
    "pmrf_calibration_brier_score",
    "Mean Brier score over resolved 'act' predictions (lower=better; "
    "0=perfect, 0.33=random). NaN when no scored samples.",
)

CALIBRATION_DRIFT = Gauge(
    "pmrf_calibration_drift_score",
    "Recent-vs-baseline Brier delta (positive = recent worse than baseline). "
    "Phase 1 placeholder: 0.0 until drift algorithm is implemented.",
)


# ---------------------------------------------------------------------------
# Final direction change (event-level)
# ---------------------------------------------------------------------------

FINAL_DIRECTION_CHANGE = Counter(
    "pmrf_final_direction_change_total",
    "Number of times an event's final_displayed_direction changed across "
    "save_events updates.",
)

FINAL_DIRECTION = Gauge(
    "pmrf_final_direction_count",
    "Count of events by final_displayed_direction (recomputed on scrape).",
    ["direction"],  # direction ∈ {YES, NO, WAIT, AVOID}
)


# ---------------------------------------------------------------------------
# Event store size (E1: scale debt)
# ---------------------------------------------------------------------------

EVENT_STORE_BYTES = Gauge(
    "pmrf_event_store_bytes",
    "Size of event_store.json on disk. Every mutating call rewrites the whole "
    "file, so one write costs roughly this many bytes of serialize + fsync; "
    "watch this to see when the JSON store has to become a real database.",
)

EVENT_STORE_RECORDS = Gauge(
    "pmrf_event_store_records",
    "Number of event records in event_store.json. Nothing removes a record, so "
    "this only grows.",
)


# ---------------------------------------------------------------------------
# Build info
# ---------------------------------------------------------------------------

APP_INFO = Info(
    "pmrf_app",
    "Application build info (version, environment).",
)


def record_overlay_build_failure(phase: str) -> None:
    """Convenience wrapper to record an overlay build failure."""
    OVERLAY_BUILD_FAILURE.labels(phase=phase).inc()


def record_overlay_latency(phase: str, seconds: float) -> None:
    """Convenience wrapper to record overlay latency. Accepts seconds,
    converts to ms for the histogram."""
    OVERLAY_LATENCY.labels(phase=phase).observe(seconds * 1000.0)


# ---------------------------------------------------------------------------
# Aggregate gauge refresh (called on each /metrics scrape)
# ---------------------------------------------------------------------------

def refresh_aggregate_gauges() -> None:
    """Recompute the gauges that aggregate over event_store / prediction_store.

    Called by the ``/metrics`` endpoint handler before serving the registry.
    Idempotent and safe to call repeatedly. Best-effort: any store failure is
    logged and the gauge keeps its previous value.
    """
    try:
        _refresh_event_store_gauges()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("refresh_aggregate_gauges: event_store failed: %s", exc)
    try:
        _refresh_scheduler_gauges()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("refresh_aggregate_gauges: scheduler failed: %s", exc)
    try:
        _refresh_calibration_gauges()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("refresh_aggregate_gauges: calibration failed: %s", exc)


def _refresh_event_store_gauges() -> None:
    """Walk event_store and update direction + consensus gauges.

    Also reports the store's size on disk (E1: every mutating call rewrites the
    whole file, so one write costs roughly that many bytes). The record count
    comes from the one walk below rather than from the store: asking it would
    re-read and re-parse the whole file for a number this function already has
    in hand.
    """
    from app.memory.event_store import list_all_events, store_bytes

    direction_counts: dict[str, int] = {"YES": 0, "NO": 0, "WAIT": 0, "AVOID": 0}
    consensus_counts: dict[str, int] = {"none": 0, "low": 0, "medium": 0, "high": 0}
    other_direction = 0
    other_consensus = 0

    records = 0
    for entry in list_all_events():
        records += 1
        record = entry.get("record") or {}
        final_dir = record.get("final_displayed_direction")
        if isinstance(final_dir, str) and final_dir in direction_counts:
            direction_counts[final_dir] += 1
        elif final_dir is not None:
            other_direction += 1

        dq = record.get("decision_quality")
        if isinstance(dq, dict):
            level = dq.get("consensus_level")
            if isinstance(level, str) and level in consensus_counts:
                consensus_counts[level] += 1
            elif level is not None:
                other_consensus += 1

    for direction, count in direction_counts.items():
        FINAL_DIRECTION.labels(direction=direction).set(count)
    if other_direction:
        FINAL_DIRECTION.labels(direction="other").set(other_direction)

    for level, count in consensus_counts.items():
        CONSENSUS_DISTRIBUTION.labels(level=level).set(count)
    if other_consensus:
        CONSENSUS_DISTRIBUTION.labels(level="other").set(other_consensus)

    EVENT_STORE_RECORDS.set(records)
    # store_bytes() reports 0 for a missing file (fresh deploy). Zero is the
    # honest reading; leaving the gauge unset would look like the scrape failed.
    EVENT_STORE_BYTES.set(store_bytes())


def _refresh_scheduler_gauges() -> None:
    """Update scheduler last-success gauges from loop_run_store."""
    from app.memory import loop_run_store

    for job_name in ("event_discover", "event_auto_resolve", "loop_db_maintenance"):
        run = loop_run_store.last_run(job_name)
        if not run:
            continue
        if run.get("status") != "success":
            continue
        finished_at = run.get("finished_at")
        ts = _to_unix_timestamp(finished_at)
        if ts is not None:
            SCHEDULER_LAST_SUCCESS.labels(job_name=job_name).set(ts)


def _refresh_calibration_gauges() -> None:
    """Update calibration Brier gauge from prediction_store."""
    from app.memory.prediction_store import calibration_summary

    summary = calibration_summary() or {}
    brier = summary.get("brier_score")
    if brier is None:
        # No scored samples — set to NaN so the gauge renders as NaN rather
        # than 0 (0 would be misleadingly "perfect").
        CALIBRATION_BRIER.set(float("nan"))
    else:
        try:
            CALIBRATION_BRIER.set(float(brier))
        except (TypeError, ValueError):
            CALIBRATION_BRIER.set(float("nan"))

    # Drift score: recent-vs-baseline Brier delta. Computed on each scrape
    # from prediction_store scored samples (read-only, cheap). Best-effort:
    # any failure leaves the gauge at its previous value. Uses the default
    # recent-window size (50) directly — DRIFT_RECENT_WINDOW_N is a
    # best-effort default and the gauge refresh must not import settings
    # into this hot path; the /quality-metrics/drift route honors the
    # configured value for the authoritative report.
    try:
        from app.memory.prediction_store import list_scored_samples_for_drift
        from app.services.calibration_drift_service import compute_drift_score
        samples = list_scored_samples_for_drift(recent_n=50)
        drift = compute_drift_score(
            [s["brier_score"] for s in samples.get("recent", []) if s.get("brier_score") is not None],
            [s["brier_score"] for s in samples.get("baseline", []) if s.get("brier_score") is not None],
        )
        score = drift.get("drift_score")
        if score is None:
            CALIBRATION_DRIFT.set(float("nan"))
        else:
            CALIBRATION_DRIFT.set(float(score))
    except Exception:  # pragma: no cover - defensive
        # Keep previous value on failure — do not clobber with 0.0.
        pass


def _to_unix_timestamp(iso_str: str | None) -> float | None:
    """Parse an ISO-8601 timestamp string to a unix timestamp (seconds)."""
    if not iso_str:
        return None
    try:
        from datetime import datetime
        # Accept both timezone-aware and naive (assume UTC if naive).
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            from datetime import timezone
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def render_metrics() -> tuple[bytes, str]:
    """Refresh aggregate gauges and return (body, content_type) for /metrics."""
    refresh_aggregate_gauges()
    return generate_latest(), CONTENT_TYPE_LATEST
