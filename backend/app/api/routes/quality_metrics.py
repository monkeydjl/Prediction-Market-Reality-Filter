"""Quality metrics aggregation routes (P0-6 §1.1).

Exposes a JSON summary of the same quality data that ``/metrics`` exposes
in Prometheus format, plus a lightweight anomalies endpoint. The summary
is computed on demand from ``event_store`` + ``prediction_store`` +
``loop_run_store`` — no caching layer, since the underlying stores are
already in-memory JSON / SQLite reads.

Authorization: read-only. No ``X-API-Key`` required (same as
``/api/health``). The data is aggregate counts only; no per-event
detail that would expose operator-grade intelligence. The ``/drift``
route is read-only for unauthenticated callers — alert *dispatch*
(webhook/Sentry) requires a valid write key (``optional_write_key``) so
that a read endpoint cannot be turned into a side-effect surface by any
visitor.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.security import optional_write_key
from app.memory.event_store import list_all_events, list_resolved_events
from app.memory import loop_run_store
from app.memory.prediction_store import (
    calibration_bucket_summary,
    calibration_summary,
    list_scored_samples_for_drift,
)
from app.services.calibration_drift_service import build_drift_report, evaluate_drift_alerts
from app.services.drift_alert_dispatcher import dispatch_drift_alerts, evaluate_scheduler_alerts

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# /api/quality-metrics/summary
# ---------------------------------------------------------------------------

@router.get("/quality-metrics/summary")
async def quality_metrics_summary(
    timeframe: str = Query(
        default="24h",
        description="Time window for the scheduler run slice (24h|7d|all).",
    ),
) -> dict[str, Any]:
    """Aggregate quality metrics across all overlays + scheduler + calibration.

    Returns a single JSON object with sections:

    - ``counts`` — totals (events, resolved, scored predictions)
    - ``final_direction`` — distribution by YES/NO/WAIT/AVOID
    - ``consensus`` — distribution by decision_quality.consensus_level
    - ``downgrade`` — count by overlay (decision_quality / market_quality /
      source_reliability), plus a breakdown of final_downgrade_reason presence
    - ``market_quality`` — wide_spread / thin_market flags, score distribution
    - ``source_reliability`` — avg overall_score, source_count, domain_diversity
    - ``llm_telemetry`` — total estimated cost, degraded_mode count
    - ``calibration`` — passthrough of ``calibration_summary()`` (Brier etc.)
    - ``scheduler`` — last-success timestamps + recent failed runs

    The ``timeframe`` parameter only filters the scheduler recent_runs slice;
    event aggregates cover the full store (no per-event timestamp filtering
    to keep the read cheap).
    """
    timeframe = timeframe if timeframe in ("24h", "7d", "all") else "24h"

    events = list_all_events()
    resolved = list_resolved_events()

    direction_dist: dict[str, int] = {"YES": 0, "NO": 0, "WAIT": 0, "AVOID": 0}
    consensus_dist: dict[str, int] = {"none": 0, "low": 0, "medium": 0, "high": 0}
    downgrade_reason_present = 0
    market_quality_count = 0
    wide_spread_count = 0
    thin_market_count = 0
    market_quality_scores: list[float] = []
    source_reliability_count = 0
    sr_overall_scores: list[float] = []
    sr_source_counts: list[int] = []
    sr_domain_diversities: list[int] = []
    llm_telemetry_count = 0
    llm_degraded_count = 0
    llm_total_cost = 0.0
    dq_error_count = 0
    mq_error_count = 0
    sr_error_count = 0

    for entry in events:
        record = entry.get("record") or {}

        final_dir = record.get("final_displayed_direction")
        if isinstance(final_dir, str):
            direction_dist[final_dir] = direction_dist.get(final_dir, 0) + 1

        dq = record.get("decision_quality")
        if isinstance(dq, dict):
            level = dq.get("consensus_level")
            if isinstance(level, str):
                consensus_dist[level] = consensus_dist.get(level, 0) + 1
            if dq.get("error"):
                dq_error_count += 1

        if record.get("final_downgrade_reason"):
            downgrade_reason_present += 1

        mq = record.get("market_quality")
        if isinstance(mq, dict):
            market_quality_count += 1
            if mq.get("error"):
                mq_error_count += 1
            if mq.get("wide_spread_flag"):
                wide_spread_count += 1
            if mq.get("thin_market_flag"):
                thin_market_count += 1
            score = mq.get("score")
            if isinstance(score, (int, float)):
                market_quality_scores.append(float(score))

        sr = record.get("source_reliability")
        if isinstance(sr, dict):
            source_reliability_count += 1
            if sr.get("error"):
                sr_error_count += 1
            score = sr.get("overall_score")
            if isinstance(score, (int, float)):
                sr_overall_scores.append(float(score))
            sc = sr.get("source_count")
            if isinstance(sc, int):
                sr_source_counts.append(sc)
            dd = sr.get("domain_diversity")
            if isinstance(dd, int):
                sr_domain_diversities.append(dd)

        lt = record.get("llm_telemetry")
        if isinstance(lt, dict):
            llm_telemetry_count += 1
            if lt.get("degraded_mode"):
                llm_degraded_count += 1
            cost = lt.get("estimated_token_cost")
            if isinstance(cost, (int, float)):
                llm_total_cost += float(cost)

    # Scheduler slice (limited by timeframe)
    recent_runs_limit = {"24h": 50, "7d": 200, "all": 500}[timeframe]
    scheduler_block = _scheduler_summary(recent_runs_limit)

    # Calibration
    try:
        calibration = calibration_summary()
    except Exception as exc:
        logger.warning("calibration_summary failed: %s", exc)
        calibration = {"error": str(exc)}

    try:
        buckets = calibration_bucket_summary()
    except Exception as exc:
        logger.warning("calibration_bucket_summary failed: %s", exc)
        buckets = {"error": str(exc)}

    return {
        "timeframe": timeframe,
        "counts": {
            "events": len(events),
            "resolved_events": len(resolved),
            "with_decision_quality": sum(
                1 for e in events if isinstance((e.get("record") or {}).get("decision_quality"), dict)
            ),
            "with_market_quality": market_quality_count,
            "with_source_reliability": source_reliability_count,
            "with_llm_telemetry": llm_telemetry_count,
        },
        "final_direction": direction_dist,
        "consensus": consensus_dist,
        "downgrade": {
            "final_downgrade_reason_present": downgrade_reason_present,
            "build_errors": {
                "decision_quality": dq_error_count,
                "market_quality": mq_error_count,
                "source_reliability": sr_error_count,
            },
        },
        "market_quality": {
            "count": market_quality_count,
            "wide_spread_flag_count": wide_spread_count,
            "thin_market_flag_count": thin_market_count,
            "score_avg": _avg(market_quality_scores),
            "score_min": min(market_quality_scores) if market_quality_scores else None,
            "score_max": max(market_quality_scores) if market_quality_scores else None,
        },
        "source_reliability": {
            "count": source_reliability_count,
            "overall_score_avg": _avg(sr_overall_scores),
            "source_count_avg": _avg_int(sr_source_counts),
            "domain_diversity_avg": _avg_int(sr_domain_diversities),
        },
        "llm_telemetry": {
            "count": llm_telemetry_count,
            "degraded_mode_count": llm_degraded_count,
            "estimated_token_cost_total": round(llm_total_cost, 6),
        },
        "calibration": calibration,
        "calibration_buckets": buckets,
        "scheduler": scheduler_block,
    }


# ---------------------------------------------------------------------------
# /api/quality-metrics/timeseries
# ---------------------------------------------------------------------------

@router.get("/quality-metrics/timeseries")
async def quality_metrics_timeseries(
    window: str = Query(
        default="7d",
        description="Time window for the scheduler run slice (24h|7d|30d).",
    ),
) -> dict[str, Any]:
    """Return a coarse scheduler-run timeseries.

    Each point is one ``loop_runs`` row (status, duration_ms, started_at).
    Coarse-grained on purpose: the Prometheus ``/metrics`` endpoint is the
    high-resolution signal; this JSON endpoint exists for the operational
    dashboard's "did the loop keep running?" view.
    """
    window = window if window in ("24h", "7d", "30d") else "7d"
    limit = {"24h": 100, "7d": 500, "30d": 2000}[window]
    runs = loop_run_store.recent_runs(limit=limit)
    points: list[dict[str, Any]] = []
    for run in runs:
        points.append({
            "job_name": run.get("job_name"),
            "status": run.get("status"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "duration_ms": run.get("duration_ms"),
        })
    return {
        "window": window,
        "points": points,
    }


# ---------------------------------------------------------------------------
# /api/quality-metrics/anomalies
# ---------------------------------------------------------------------------

@router.get("/quality-metrics/anomalies")
async def quality_metrics_anomalies() -> dict[str, Any]:
    """Surface operational anomalies detected from the live stores.

    Anomalies are conservative: each is a clear "something is wrong" signal
    that a human should investigate. The endpoint does NOT raise on anomalies
    — it returns them as a list so the dashboard can render them as a banner.

    Checks (Phase 1 — minimum set):
    - scheduler_enabled but no recent successful run
    - any scheduler job with last status == 'failed'
    - scored predictions with very high Brier (>= 0.40)
    - events where market_quality.wide_spread_flag=True but
      final_displayed_direction is YES/NO (not downgraded)
    - events with llm_telemetry.degraded_mode=True
    """
    from app.core.config import settings
    from app.core.scheduler import scheduler, scheduler_start_skipped_due_to_lock

    anomalies: list[dict[str, Any]] = []

    # Scheduler anomaly: enabled but not running
    try:
        if settings.SCHEDULER_ENABLED and not scheduler.running:
            anomalies.append({
                "code": "scheduler_not_running",
                "severity": "high",
                "detail": (
                    "SCHEDULER_ENABLED=true but scheduler.running=false "
                    "(skipped_due_to_lock=%s)" % scheduler_start_skipped_due_to_lock()
                ),
            })
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("scheduler anomaly check failed: %s", exc)

    # Scheduler failed runs (last per job)
    try:
        for job_name in ("event_discover", "event_auto_resolve", "loop_db_maintenance"):
            run = loop_run_store.last_run(job_name)
            if run and run.get("status") == "failed":
                anomalies.append({
                    "code": "scheduler_job_failed",
                    "severity": "high",
                    "detail": {
                        "job_name": job_name,
                        "started_at": run.get("started_at"),
                        "error": run.get("error"),
                    },
                })
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("failed-run anomaly check failed: %s", exc)

    # Calibration Brier anomaly
    try:
        calibration = calibration_summary()
        brier = calibration.get("brier_score")
        if brier is not None and float(brier) >= 0.40:
            anomalies.append({
                "code": "calibration_brier_high",
                "severity": "medium",
                "detail": {
                    "brier_score": brier,
                    "n": calibration.get("n"),
                    "grade": calibration.get("grade"),
                    "threshold": 0.40,
                },
            })
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("brier anomaly check failed: %s", exc)

    # Per-event overlay anomalies (collect sample event_ids for operator jump links)
    wide_spread_ids: list[str] = []
    degraded_ids: list[str] = []
    for entry in list_all_events():
        record = entry.get("record") or {}
        eid = str(entry.get("event_id") or record.get("event_id") or "")
        mq = record.get("market_quality")
        if isinstance(mq, dict) and mq.get("wide_spread_flag"):
            final_dir = record.get("final_displayed_direction")
            if final_dir in ("YES", "NO") and eid:
                wide_spread_ids.append(eid)
        lt = record.get("llm_telemetry")
        if isinstance(lt, dict) and lt.get("degraded_mode") and eid:
            degraded_ids.append(eid)

    if wide_spread_ids:
        anomalies.append({
            "code": "wide_spread_not_downgraded",
            "severity": "medium",
            "event_ids": wide_spread_ids[:8],
            "href": "/history",
            "detail": {
                "count": len(wide_spread_ids),
                "event_ids": wide_spread_ids[:8],
                "note": "Events with market_quality.wide_spread_flag=True but "
                        "final_displayed_direction is YES/NO (should be WAIT/AVOID).",
            },
        })

    if degraded_ids:
        anomalies.append({
            "code": "llm_degraded_mode_events",
            "severity": "low",
            "event_ids": degraded_ids[:8],
            "href": "/history",
            "detail": {
                "count": len(degraded_ids),
                "event_ids": degraded_ids[:8],
                "note": "Events whose llm_telemetry.degraded_mode=True.",
            },
        })

    # Navigation hints for aggregate anomalies (no single event)
    for a in anomalies:
        if a.get("href"):
            continue
        code = a.get("code")
        if code == "calibration_brier_high":
            a["href"] = "/history"
        elif str(code or "").startswith("scheduler"):
            a["href"] = "/quality"

    return {
        "count": len(anomalies),
        "anomalies": anomalies,
    }


# ---------------------------------------------------------------------------
# /api/quality-metrics/drift
# ---------------------------------------------------------------------------

@router.get("/quality-metrics/drift")
async def quality_metrics_drift(
    can_dispatch: bool = Depends(optional_write_key),
) -> dict[str, Any]:
    """Calibration drift report + triggered alerts (Plan 2 §1.7).

    Returns:
        - ``drift`` — recent-vs-baseline Brier delta (positive = recent worse)
        - ``ece`` — Expected Calibration Error for recent + baseline windows
        - ``degraded_mixing`` — whether recent window contains LLM-degraded samples
        - ``buckets`` — per-cell (edge|confidence) recent vs baseline stats
        - ``alerts`` — list of triggered alert dicts (rules 1-4)

    The drift *computation* and alert *detection* always run (read-only).
    Alert *dispatch* (webhook/Sentry) is gated by both
    ``DRIFT_ALERTS_ENABLED`` and a valid write key (``X-API-Key`` header).
    Unauthenticated callers — including the dashboard's periodic poll —
    receive the detection result but cannot trigger side effects.
    Operators can use an authenticated request (``X-API-Key``) as the
    alert heartbeat.
    """
    from app.core.config import settings

    recent_n = getattr(settings, "DRIFT_RECENT_WINDOW_N", 50)
    try:
        samples = list_scored_samples_for_drift(recent_n=recent_n)
    except Exception as exc:
        logger.warning("list_scored_samples_for_drift failed: %s", exc)
        samples = {"recent": [], "baseline": []}

    # Join recent samples with event_store to set the ``degraded`` flag
    # (the predictions table does not store LLM degradation state).
    recent = samples.get("recent", [])
    if recent:
        degraded_ids: set[str] = set()
        for entry in list_all_events():
            record = entry.get("record") or {}
            lt = record.get("llm_telemetry")
            if isinstance(lt, dict) and lt.get("degraded_mode"):
                eid = record.get("event_id")
                if isinstance(eid, str):
                    degraded_ids.add(eid)
        for s in recent:
            if s.get("event_id") in degraded_ids:
                s["degraded"] = True

    report = build_drift_report(recent, samples.get("baseline", []))

    thresholds = {
        "brier_relative_threshold": getattr(settings, "DRIFT_BRIER_RELATIVE_THRESHOLD", 0.30),
        "bucket_deviation_pp": getattr(settings, "DRIFT_BUCKET_DEVIATION_PP", 20.0),
        "bucket_min_samples": getattr(settings, "DRIFT_BUCKET_MIN_SAMPLES", 2),
    }
    alerts = evaluate_drift_alerts(report, thresholds)
    alerts.extend(evaluate_scheduler_alerts())

    # Best-effort dispatch. Gated by write key (``can_dispatch``) so that
    # an unauthenticated GET cannot trigger Sentry/webhook side effects.
    # Also gated by DRIFT_ALERTS_ENABLED inside dispatch_drift_alerts.
    if can_dispatch:
        try:
            dispatch_drift_alerts(alerts)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("drift alert dispatch failed: %s", exc)

    return {
        "recent_window_n": recent_n,
        "drift": report.get("drift"),
        "ece": report.get("ece"),
        "degraded_mixing": report.get("degraded_mixing"),
        "buckets": report.get("buckets"),
        "alerts": alerts,
        "alerts_enabled": getattr(settings, "DRIFT_ALERTS_ENABLED", False),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scheduler_summary(recent_limit: int) -> dict[str, Any]:
    """Build the scheduler block for /quality-metrics/summary."""
    last_runs: dict[str, dict[str, Any] | None] = {}
    for job_name in ("event_discover", "event_auto_resolve", "loop_db_maintenance"):
        run = loop_run_store.last_run(job_name)
        if not run:
            last_runs[job_name] = None
            continue
        last_runs[job_name] = {
            "status": run.get("status"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "duration_ms": run.get("duration_ms"),
        }

    failed_count = 0
    recent_runs: list[dict[str, Any]] = []
    try:
        for run in loop_run_store.recent_runs(limit=recent_limit):
            status = run.get("status")
            if status == "failed":
                failed_count += 1
            recent_runs.append({
                "job_name": run.get("job_name"),
                "status": status,
                "started_at": run.get("started_at"),
                "duration_ms": run.get("duration_ms"),
            })
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("recent_runs fetch failed: %s", exc)

    return {
        "last_runs": last_runs,
        "recent_failed_count": failed_count,
        "recent_runs_count": len(recent_runs),
    }


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _avg_int(values: list[int]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


# ---------------------------------------------------------------------------
# /api/quality-metrics/report
# ---------------------------------------------------------------------------

@router.get("/quality-metrics/report")
async def quality_metrics_report(
    limit: int | None = Query(
        default=None,
        description="Report on only the first N resolved events (default: all).",
    ),
    sample: int | None = Query(
        default=None,
        description="Randomly sample N resolved events (reproducible seed=42).",
    ),
) -> dict[str, Any]:
    """Sliced quality metrics report over resolved events.

    Returns a single JSON object with sections:

    - ``overview`` — totals (resolved, with_calibration, missing_calibration)
    - ``by_source_type`` — slice by ``record.source.type``
    - ``by_analysis_quality`` — slice by ``record.llm_telemetry.analysis_quality``
      (engine proxy: llm vs deterministic_fallback vs unknown)
    - ``by_edge_bucket`` — slice by ``compute_edge_bucket(edge)``
    - ``by_source_reliability_bucket`` — slice by bucketed
      ``source_reliability.overall_score`` (low/medium/high/very_high/missing)
    - ``calibration_deviation`` — per probability bucket [0,20)/.../[80,100],
      mean predicted vs mean actual and their deviation
    - ``report_errors`` — per-event extraction failures (resilient)

    Per slice: ``n``, ``direction_correct_true/false/none`` counts,
    ``direction_accuracy`` (true/(true+false), None when no directional),
    ``brier`` block (mean/skill/grade/n aggregated from
    ``record.calibration.brier_score``).

    Pure read-only: no writes, no LLM calls, no network. Same authorization
    as ``/summary`` — read-only, no ``X-API-Key`` required.
    """
    import random

    from app.services.quality_metrics_report_service import build_report, extract_metrics

    entries = list_resolved_events()
    if sample is not None and sample < len(entries):
        # Reproducible sample — seed fixed (same convention as the CLI
        # script and sweep_event_quality.py).
        rng = random.Random(42)
        entries = rng.sample(entries, sample)
    if limit is not None:
        entries = entries[:limit]

    items: list[dict[str, Any]] = []
    report_errors: list[dict[str, str]] = []
    for entry in entries:
        record = entry.get("record")
        if not isinstance(record, dict):
            report_errors.append({
                "event_id": entry.get("event_id", "?"),
                "error": "record missing or not a dict",
            })
            continue
        try:
            items.append(extract_metrics(record))
        except Exception as exc:
            report_errors.append({
                "event_id": record.get("event_id", "?"),
                "error": str(exc),
            })

    return build_report(items, report_errors)


# ---------------------------------------------------------------------------
# /api/quality-metrics/alerts
# ---------------------------------------------------------------------------

@router.get("/quality-metrics/alerts")
async def quality_metrics_alerts(
    limit: int | None = Query(
        default=None, ge=1,
        description="Report on only the first N resolved events (default: all).",
    ),
    sample: int | None = Query(
        default=None, ge=1,
        description="Randomly sample N resolved events (reproducible seed=42).",
    ),
    include_insufficient_samples: bool = Query(
        default=False,
        description="Include diagnostics for low-sample slices.",
    ),
) -> dict[str, Any]:
    """Evaluate quality alerts on the current resolved-event set.

    Returns a single JSON object with:

    - ``alerts`` — list of triggered alert dicts (codes: direction_accuracy_low,
      brier_score_high, missing_calibration_rate_high, report_errors_high)
    - ``alert_count`` — len(alerts)
    - ``diagnostics`` — present only when ``include_insufficient_samples=true``;
      contains ``insufficient_samples`` (slices with n < min_samples)

    Pure read-only: no writes, no dispatch, no LLM calls. Same authorization
    as ``/report`` — read-only, no ``X-API-Key`` required. ``sample`` is
    applied first (seed=42), then ``limit`` — same semantics as
    ``/quality-metrics/report``.
    """
    import random
    from app.core.config import settings
    from app.services.quality_alert_service import (
        collect_insufficient_samples,
        evaluate_quality_alerts,
        thresholds_from_settings,
    )
    from app.services.quality_metrics_report_service import (
        build_report,
        extract_metrics,
    )

    entries = list_resolved_events()
    if sample is not None and sample < len(entries):
        # Reproducible sample — seed fixed (same convention as the CLI
        # script and /quality-metrics/report).
        rng = random.Random(42)
        entries = rng.sample(entries, sample)
    if limit is not None:
        entries = entries[:limit]

    items: list[dict[str, Any]] = []
    report_errors: list[dict[str, str]] = []
    for entry in entries:
        record = entry.get("record")
        if not isinstance(record, dict):
            report_errors.append({
                "event_id": entry.get("event_id", "?"),
                "error": "record missing or not a dict",
            })
            continue
        try:
            items.append(extract_metrics(record))
        except Exception as exc:
            report_errors.append({
                "event_id": record.get("event_id", "?"),
                "error": str(exc),
            })

    report = build_report(items, report_errors)
    thresholds = thresholds_from_settings(settings)
    alerts = evaluate_quality_alerts(report, thresholds)

    result: dict[str, Any] = {"alerts": alerts, "alert_count": len(alerts)}
    if include_insufficient_samples:
        result["diagnostics"] = {
            "insufficient_samples": collect_insufficient_samples(report, thresholds),
        }
    return result


# ---------------------------------------------------------------------------
# /api/quality-metrics/domain-reliability
# ---------------------------------------------------------------------------

@router.get("/quality-metrics/domain-reliability")
async def domain_reliability(
    domain: str | None = Query(default=None),
    category: str | None = Query(default=None),
    min_samples: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Query domain reliability statistics. Read-only."""
    from app.core.config import settings
    from app.memory.domain_reliability_store import get_stats

    stats = get_stats(domain=domain, category=category, min_samples=min_samples)
    # Sample-weighted over the gradeable subset. brier_count is reported next to
    # sample_count rather than folded into it: an event that was never frozen
    # has no committed estimate to score, and averaging it in as 0.0 would read
    # as a perfect call nobody made.
    graded = sum(s["brier_count"] for s in stats)
    brier_total = sum(s["brier_sum"] for s in stats)
    return {
        "domains": stats,
        "total_domains": len({s["domain"] for s in stats}),
        "total_rows": len(stats),
        "total_samples": sum(s["sample_count"] for s in stats),
        "graded_samples": graded,
        "brier_mean": (brier_total / graded) if graded > 0 else None,
        "brier_skill_score": (1.0 - brier_total / graded) if graded > 0 else None,
        "prior_metric": settings.DOMAIN_RELIABILITY_PRIOR_METRIC,
    }


# Exposed at import time so test runners can wire it without going through
# the FastAPI app. Used by tests/test_quality_metrics.py.
__all__ = ["router"]
