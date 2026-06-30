# Quality Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the calibration drift detection + alerting layer (spec §1.7, item #10) and the unified quality operations dashboard (spec §1.6, item #9) so a maintainer can see in 30 seconds whether the quality engine is healthy, which phase is downgrading, and whether calibration is drifting — without reading logs or querying SQLite.

**Architecture:** The backend already exposes Prometheus metrics, `/api/quality-metrics/{summary,timeseries,anomalies}` routes, and Brier/bucket calibration. Plan 2 adds: (1) a pure calibration-drift service computing ECE + recent-vs-baseline drift; (2) an alert dispatcher wiring the drift score into the existing `CALIBRATION_DRIFT` gauge, a new `/quality-metrics/drift` route, and webhook + Sentry breadcrumb + log outlets gated by default-OFF config flags; (3) a frontend API client + types mirroring the existing routes; (4) a `/quality` dashboard page with an anomaly banner, drift chart, and the existing summary/timeseries panels. Drift computation is read-only and always available; alert *dispatch* (side effects) defaults OFF.

**Tech Stack:** Python 3.11 / FastAPI / prometheus_client / pytest (backend); Next.js 14 (static export) / TypeScript / Tailwind / vitest (frontend).

## Global Constraints

- Every quality-overlay feature flag (`DECISION_QUALITY_ENABLED`, `MARKET_QUALITY_ENABLED`, `SOURCE_RELIABILITY_ENABLED`, `LLM_TELEMETRY_ENABLED`, `GUARDRAILS_ENABLED`, `PREDICTION_CALIBRATION_ENABLED`) defaults to `false`; Plan 2 adds new flags following the same default-OFF convention for any side-effecting dispatch (`DRIFT_ALERTS_ENABLED=false`).
- The drift *computation* (ECE, drift_score, drift report) is pure and read-only — no feature flag needed; it enriches the `CALIBRATION_DRIFT` gauge and `/quality-metrics/drift` endpoint unconditionally.
- The alert *dispatcher* (webhook HTTP call, Sentry breadcrumb emission) is gated by `DRIFT_ALERTS_ENABLED` (default `false`); when off, drift is computed and exposed but no alerts fire — byte-identical alert silence to pre-Plan-2.
- `calibration_drift_service` functions MUST be pure: no I/O, no LLM, no `settings` reads, no `import` of store modules. Thresholds are passed as arguments by the caller.
- Alert dispatchers MUST use try/except best-effort fallback: a webhook failure or Sentry error never blocks the drift endpoint or the `/metrics` scrape.
- `CALIBRATION_DRIFT` gauge MUST be wired in `_refresh_calibration_gauges()` (metrics.py:291-308) to replace the current `CALIBRATION_DRIFT.set(0.0)` placeholder — never set it elsewhere.
- `downgrade_reason` / `final_downgrade_reason` vocabulary lock from prior phases still holds: drift alert `detail` MUST NOT contain the terms long/short/buy/sell/position/kelly/order.
- Frontend API base URL construction MUST use the existing `getApiBase()` helper; never duplicate `/api` prefixes (the routes already carry `/quality-metrics/...`).
- Frontend pages are in scope for Plan 2 (this is a dashboard/observability feature, not engine optimization); the "frontend pages must not be modified during engine optimization" constraint does not apply here.
- `sentry.py` already exports `capture_message(message, level, **context)`; the alert dispatcher reuses it for breadcrumb-level alerts rather than adding a new `add_breadcrumb` wrapper (YAGNI — `capture_message` with `level="warning"` is the established pattern).
- `event_store` entries are `{"event_id", "first_seen", "last_updated", "record": <dict>}` — always unwrap via `entry.get("record") or {}` when reading.
- New SQLite columns on `predictions` are NOT added by Plan 2 (drift is computed on-the-fly from existing `brier_score`, `ai_probability`, `actual_outcome`, `resolved_at`, `direction_correct`, `edge_bucket`, `confidence_bucket` columns); no schema migration needed.

---

## File Structure

**Backend — new files:**
- `backend/app/services/calibration_drift_service.py` — pure drift computation (ECE, drift_score, drift report, alert-rule evaluation). No I/O.
- `backend/app/services/drift_alert_dispatcher.py` — best-effort alert dispatch (webhook + Sentry + log) gated by config.
- `backend/tests/test_calibration_drift_service.py` — pure-function tests.
- `backend/tests/test_drift_alert_dispatcher.py` — dispatcher tests (mocked HTTP + Sentry).

**Backend — modified files:**
- `backend/app/memory/prediction_store.py` — add `list_scored_samples_for_drift(recent_n)` returning recent + baseline sample lists.
- `backend/app/api/routes/quality_metrics.py` — add `GET /quality-metrics/drift` route.
- `backend/app/utils/metrics.py` — wire `CALIBRATION_DRIFT` gauge in `_refresh_calibration_gauges()`.
- `backend/app/core/config.py` — add `DRIFT_*` config flags.
- `backend/tests/test_quality_metrics.py` — extend with `/quality-metrics/drift` + gauge-wiring tests.

**Frontend — new files:**
- `frontend/src/components/dashboard/quality-operations-dashboard.tsx` — top-level dashboard composing the panels.
- `frontend/src/components/dashboard/anomaly-banner.tsx` — renders the `/anomalies` list.
- `frontend/src/components/dashboard/drift-panel.tsx` — renders the `/drift` report (score, ECE, recent/baseline, triggered alerts).
- `frontend/src/components/dashboard/quality-summary-panel.tsx` — renders the `/summary` aggregates (direction, consensus, downgrade, market/source/llm).
- `frontend/src/components/dashboard/scheduler-timeseries.tsx` — renders the `/timeseries` scheduler runs.
- `frontend/src/app/quality/page.tsx` — the `/quality` route page.
- `frontend/src/components/dashboard/quality-operations-dashboard.test.tsx` — component test.

**Frontend — modified files:**
- `frontend/src/lib/api.ts` — add `QualityMetricsSummary` / `QualityMetricsAnomaly` / `QualityMetricsDrift` / `SchedulerTimeseriesPoint` types + `qualityMetricsApi` client.

---

## Task 1: Calibration drift computation service (pure functions)

**Files:**
- Create: `backend/app/services/calibration_drift_service.py`
- Test: `backend/tests/test_calibration_drift_service.py`

**Interfaces:**
- Consumes: nothing (pure functions take plain dicts/lists as arguments).
- Produces: `compute_ece(samples)`, `compute_drift_score(recent, baseline)`, `build_drift_report(recent_samples, baseline_samples)`, `evaluate_drift_alerts(report, thresholds)`. Task 2 imports these to wire the gauge + endpoint.

### Step 1: Write the failing test

- [ ] Create `backend/tests/test_calibration_drift_service.py`:

```python
"""Unit tests for calibration_drift_service (Plan 2 §1.7 drift algorithm).

Pure-function tests — no I/O, no settings, no store imports. The drift
service takes plain sample lists and returns computations; the caller
(Task 2 route + dispatcher) is responsible for fetching samples.
"""
from __future__ import annotations

import unittest

from app.services.calibration_drift_service import (
    compute_ece,
    compute_drift_score,
    build_drift_report,
    evaluate_drift_alerts,
)


class TestComputeECE(unittest.TestCase):
    def test_perfect_calibration_returns_zero(self):
        # predicted 0.8 → outcome YES(1) for all → bin avg matches freq
        samples = [{"predicted_prob": 0.8, "actual_outcome": 1}] * 10
        self.assertAlmostEqual(compute_ece(samples), 0.0, places=6)

    def test_empty_returns_none(self):
        self.assertIsNone(compute_ece([]))

    def test_miscalibrated_returns_positive(self):
        # predicted 0.9 but outcome always NO(0) → big gap in the 0.8-0.9 bin
        samples = [{"predicted_prob": 0.9, "actual_outcome": 0}] * 10
        ece = compute_ece(samples)
        self.assertIsNotNone(ece)
        self.assertGreater(ece, 0.5)  # 0.9 predicted, 0.0 observed

    def test_accepts_percentage_probs(self):
        # predicted_prob may arrive as 0-100 (ai_probability scale); the
        # function must normalize to 0-1 internally.
        samples = [{"predicted_prob": 80.0, "actual_outcome": 1}] * 5
        self.assertAlmostEqual(compute_ece(samples), 0.0, places=6)


class TestComputeDriftScore(unittest.TestCase):
    def test_recent_worse_than_baseline_positive_drift(self):
        recent = [0.30, 0.28, 0.32]  # mean 0.30
        baseline = [0.15, 0.20, 0.25]  # mean 0.20
        result = compute_drift_score(recent, baseline)
        self.assertAlmostEqual(result["drift_score"], 0.5, places=4)  # (0.30-0.20)/0.20
        self.assertAlmostEqual(result["recent_mean"], 0.3, places=4)
        self.assertAlmostEqual(result["baseline_mean"], 0.2, places=4)
        self.assertEqual(result["recent_n"], 3)
        self.assertEqual(result["baseline_n"], 3)

    def test_recent_better_than_baseline_negative_drift(self):
        recent = [0.10]
        baseline = [0.20]
        result = compute_drift_score(recent, baseline)
        self.assertAlmostEqual(result["drift_score"], -0.5, places=4)

    def test_empty_baseline_returns_none_drift(self):
        result = compute_drift_score([0.2], [])
        self.assertIsNone(result["drift_score"])
        self.assertEqual(result["baseline_n"], 0)

    def test_empty_recent_returns_none_drift(self):
        result = compute_drift_score([], [0.2])
        self.assertIsNone(result["drift_score"])
        self.assertEqual(result["recent_n"], 0)

    def test_zero_baseline_mean_returns_none(self):
        # baseline mean 0 would divide by zero; guard returns None
        result = compute_drift_score([0.1], [0.0, 0.0])
        self.assertIsNone(result["drift_score"])


class TestBuildDriftReport(unittest.TestCase):
    def test_report_includes_ece_drift_and_buckets(self):
        recent = [
            {"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.09,
             "edge_bucket": "5-10", "confidence_bucket": "high",
             "direction_correct": 1, "degraded": False},
            {"predicted_prob": 0.6, "actual_outcome": 0, "brier_score": 0.36,
             "edge_bucket": "5-10", "confidence_bucket": "medium",
             "direction_correct": 0, "degraded": False},
        ]
        baseline = [
            {"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.09,
             "edge_bucket": "5-10", "confidence_bucket": "high",
             "direction_correct": 1, "degraded": False},
        ]
        report = build_drift_report(recent, baseline)
        self.assertIn("drift", report)
        self.assertIn("ece", report)
        self.assertIn("degraded_mixing", report)
        self.assertEqual(report["degraded_mixing"]["recent_degraded_count"], 0)
        self.assertEqual(report["drift"]["recent_n"], 2)
        self.assertEqual(report["drift"]["baseline_n"], 1)

    def test_report_flags_degraded_mixing(self):
        recent = [
            {"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.09,
             "edge_bucket": "5-10", "confidence_bucket": "high",
             "direction_correct": 1, "degraded": True},
        ]
        report = build_drift_report(recent, [])
        self.assertEqual(report["degraded_mixing"]["recent_degraded_count"], 1)
        self.assertTrue(report["degraded_mixing"]["contaminated"])


class TestEvaluateDriftAlerts(unittest.TestCase):
    def _thresholds(self):
        return {
            "brier_relative_threshold": 0.30,
            "bucket_deviation_pp": 20.0,
            "bucket_min_samples": 2,
        }

    def test_no_alerts_when_drift_within_threshold(self):
        report = build_drift_report(
            [{"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.09,
              "edge_bucket": "5-10", "confidence_bucket": "high",
              "direction_correct": 1, "degraded": False}] * 3,
            [{"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.09,
              "edge_bucket": "5-10", "confidence_bucket": "high",
              "direction_correct": 1, "degraded": False}] * 3,
        )
        alerts = evaluate_drift_alerts(report, self._thresholds())
        self.assertEqual(alerts, [])

    def test_brier_relative_alert_when_recent_30pct_worse(self):
        report = build_drift_report(
            [{"predicted_prob": 0.7, "actual_outcome": 0, "brier_score": 0.30,
              "edge_bucket": "5-10", "confidence_bucket": "high",
              "direction_correct": 0, "degraded": False}] * 5,
            [{"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.15,
              "edge_bucket": "5-10", "confidence_bucket": "high",
              "direction_correct": 1, "degraded": False}] * 5,
        )
        alerts = evaluate_drift_alerts(report, self._thresholds())
        codes = [a["code"] for a in alerts]
        self.assertIn("brier_relative_drift", codes)

    def test_degraded_mixing_alert(self):
        report = build_drift_report(
            [{"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.09,
              "edge_bucket": "5-10", "confidence_bucket": "high",
              "direction_correct": 1, "degraded": True}] * 2,
            [],
        )
        alerts = evaluate_drift_alerts(report, self._thresholds())
        codes = [a["code"] for a in alerts]
        self.assertIn("degraded_mixing", codes)

    def test_alert_detail_excludes_banned_terms(self):
        report = build_drift_report(
            [{"predicted_prob": 0.7, "actual_outcome": 0, "brier_score": 0.30,
              "edge_bucket": "5-10", "confidence_bucket": "high",
              "direction_correct": 0, "degraded": True}] * 5,
            [{"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.15,
              "edge_bucket": "5-10", "confidence_bucket": "high",
              "direction_correct": 1, "degraded": False}] * 5,
        )
        alerts = evaluate_drift_alerts(report, self._thresholds())
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        for alert in alerts:
            blob = str(alert).lower()
            for term in banned:
                self.assertNotIn(term, blob, f"alert leaked banned term '{term}': {alert}")


if __name__ == "__main__":
    unittest.main()
```

### Step 2: Run test to verify it fails

- [ ] Run: `python -m pytest backend/tests/test_calibration_drift_service.py -v`
- Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.calibration_drift_service'`

### Step 3: Write the implementation

- [ ] Create `backend/app/services/calibration_drift_service.py`:

```python
"""Calibration drift detection service (Plan 2 §1.7).

Pure-function layer that computes Expected Calibration Error (ECE) and a
recent-vs-baseline Brier drift score over resolved prediction samples.
The caller (``quality_metrics`` route + ``drift_alert_dispatcher``) is
responsible for fetching samples from ``prediction_store`` and passing
them as plain lists — this module does NO I/O, reads no settings, and
imports no store module.

Drift convention:
    drift_score = (recent_mean_brier - baseline_mean_brier) / baseline_mean_brier
    Positive = recent calibration is WORSE than baseline.
    Negative = recent is BETTER than baseline.
    None     = baseline empty or baseline mean is 0 (cannot divide).

ECE convention (10 equal-width bins over [0, 1]):
    For each bin, |avg(predicted_prob) - observed_frequency| weighted by
    bin sample count, summed. 0 = perfectly calibrated. ``predicted_prob``
    may arrive as 0-1 or 0-100 (ai_probability scale); values > 1.0 are
    divided by 100 internally.

Alert rules evaluated by ``evaluate_drift_alerts`` (rule 4, scheduler
zero-resolved, is evaluated by the dispatcher in Task 2 which has access
to ``loop_run_store``):
    1. brier_relative_drift — recent_mean > baseline_mean * (1 + threshold)
    2. bucket_deviation — any bucket direction_correct_rate deviates > pp
       from baseline, for buckets with >= min_samples
    3. degraded_mixing — recent window contains degraded-mode samples
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 10 equal-width bins over [0.0, 1.0]. A prediction at exactly 1.0 lands
# in the last bin (closed upper bound on the final bin).
_ECE_BIN_EDGES: tuple[float, ...] = (
    0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0,
)


def compute_ece(samples: list[dict[str, Any]]) -> float | None:
    """Expected Calibration Error over 10 equal-width probability bins.

    Args:
        samples: list of dicts with ``predicted_prob`` (0-1 or 0-100) and
            ``actual_outcome`` (0 or 1, where 1=YES).

    Returns:
        ECE as a float in [0, 1], or None when samples is empty.
    """
    if not samples:
        return None

    bins: list[list[tuple[float, int]]] = [[] for _ in range(10)]
    for s in samples:
        prob = _normalize_prob(s.get("predicted_prob"))
        outcome = s.get("actual_outcome")
        if prob is None or outcome is None:
            continue
        try:
            outcome_int = int(outcome)
        except (TypeError, ValueError):
            continue
        if outcome_int not in (0, 1):
            continue
        idx = _bin_index(prob)
        bins[idx].append((prob, outcome_int))

    total = sum(len(b) for b in bins)
    if total == 0:
        return None

    ece = 0.0
    for b in bins:
        if not b:
            continue
        avg_pred = sum(p for p, _ in b) / len(b)
        obs_freq = sum(o for _, o in b) / len(b)
        ece += (len(b) / total) * abs(avg_pred - obs_freq)
    return round(ece, 4)


def compute_drift_score(
    recent_briers: list[float],
    baseline_briers: list[float],
) -> dict[str, Any]:
    """Recent-vs-baseline Brier drift score.

    drift_score = (recent_mean - baseline_mean) / baseline_mean
    Positive = recent worse. None when baseline empty / zero.

    Returns a dict with ``drift_score``, ``recent_mean``, ``baseline_mean``,
    ``recent_n``, ``baseline_n``.
    """
    recent_clean = [float(b) for b in recent_briers if _is_finite_num(b)]
    baseline_clean = [float(b) for b in baseline_briers if _is_finite_num(b)]

    recent_mean = sum(recent_clean) / len(recent_clean) if recent_clean else None
    baseline_mean = (
        sum(baseline_clean) / len(baseline_clean) if baseline_clean else None
    )

    drift = None
    if recent_mean is not None and baseline_mean not in (None, 0.0):
        drift = round((recent_mean - baseline_mean) / baseline_mean, 4)

    return {
        "drift_score": drift,
        "recent_mean": round(recent_mean, 4) if recent_mean is not None else None,
        "baseline_mean": round(baseline_mean, 4) if baseline_mean is not None else None,
        "recent_n": len(recent_clean),
        "baseline_n": len(baseline_clean),
    }


def build_drift_report(
    recent_samples: list[dict[str, Any]],
    baseline_samples: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the full drift report consumed by the route + dispatcher.

    Each sample dict should carry: ``predicted_prob``, ``actual_outcome``,
    ``brier_score``, ``edge_bucket``, ``confidence_bucket``,
    ``direction_correct`` (1/0/None), ``degraded`` (bool).
    """
    recent_briers = [s.get("brier_score") for s in recent_samples if s.get("brier_score") is not None]
    baseline_briers = [s.get("brier_score") for s in baseline_samples if s.get("brier_score") is not None]

    drift = compute_drift_score(recent_briers, baseline_briers)
    recent_ece = compute_ece(recent_samples)
    baseline_ece = compute_ece(baseline_samples)

    recent_degraded = sum(1 for s in recent_samples if s.get("degraded"))

    return {
        "drift": drift,
        "ece": {
            "recent": recent_ece,
            "baseline": baseline_ece,
        },
        "degraded_mixing": {
            "recent_degraded_count": recent_degraded,
            "recent_n": len(recent_samples),
            "contaminated": recent_degraded > 0,
        },
        "buckets": _bucket_delta(recent_samples, baseline_samples),
    }


def evaluate_drift_alerts(
    report: dict[str, Any],
    thresholds: dict[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate drift alert rules 1-3 (rule 4 is dispatcher-side).

    thresholds keys:
        brier_relative_threshold: float (e.g. 0.30 = 30% worse)
        bucket_deviation_pp: float (e.g. 20.0 = 20 percentage points)
        bucket_min_samples: int (e.g. 2)
    """
    alerts: list[dict[str, Any]] = []
    drift = report.get("drift") or {}

    # Rule 1: Brier relative drift
    drift_score = drift.get("drift_score")
    recent_mean = drift.get("recent_mean")
    baseline_mean = drift.get("baseline_mean")
    rel_threshold = thresholds.get("brier_relative_threshold", 0.30)
    if drift_score is not None and baseline_mean and baseline_mean > 0:
        if drift_score >= rel_threshold:
            alerts.append({
                "code": "brier_relative_drift",
                "severity": "high",
                "detail": {
                    "drift_score": drift_score,
                    "recent_mean_brier": recent_mean,
                    "baseline_mean_brier": baseline_mean,
                    "threshold": rel_threshold,
                    "note": "Recent Brier is %.0f%% worse than baseline." % (
                        drift_score * 100
                    ),
                },
            })

    # Rule 2: bucket direction_correct_rate deviation
    bucket_dev_pp = thresholds.get("bucket_deviation_pp", 20.0)
    bucket_min = thresholds.get("bucket_min_samples", 2)
    for key, cell in (report.get("buckets") or {}).items():
        recent_cell = cell.get("recent") or {}
        baseline_cell = cell.get("baseline") or {}
        if recent_cell.get("n", 0) < bucket_min:
            continue
        if baseline_cell.get("n", 0) < bucket_min:
            continue
        recent_rate = recent_cell.get("direction_correct_rate")
        baseline_rate = baseline_cell.get("direction_correct_rate")
        if recent_rate is None or baseline_rate is None:
            continue
        delta_pp = abs(recent_rate - baseline_rate) * 100.0
        if delta_pp > bucket_dev_pp:
            alerts.append({
                "code": "bucket_deviation",
                "severity": "medium",
                "detail": {
                    "bucket": key,
                    "recent_rate": recent_rate,
                    "baseline_rate": baseline_rate,
                    "delta_pp": round(delta_pp, 2),
                    "threshold_pp": bucket_dev_pp,
                },
            })

    # Rule 3: degraded mixing
    mixing = report.get("degraded_mixing") or {}
    if mixing.get("contaminated"):
        alerts.append({
            "code": "degraded_mixing",
            "severity": "medium",
            "detail": {
                "recent_degraded_count": mixing.get("recent_degraded_count"),
                "recent_n": mixing.get("recent_n"),
                "note": "Recent calibration window contains LLM-degraded samples; "
                        "headline Brier may be contaminated.",
            },
        })

    return alerts


# ── Helpers ───────────────────────────────────────────────────────


def _normalize_prob(value: Any) -> float | None:
    if value is None:
        return None
    try:
        prob = float(value)
    except (TypeError, ValueError):
        return None
    if not _is_finite_num(prob):
        return None
    if prob > 1.0:
        prob = prob / 100.0
    return max(0.0, min(1.0, prob))


def _bin_index(prob: float) -> int:
    """Map a [0,1] probability to a 0-9 bin index (10 bins)."""
    for i in range(9):
        if prob < _ECE_BIN_EDGES[i + 1]:
            return i
    return 9  # prob == 1.0 lands in the last bin


def _bucket_delta(
    recent: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
) -> dict[str, Any]:
    """Group samples by edge_bucket|confidence_bucket and compute per-cell stats."""
    recent_cells = _group_by_bucket(recent)
    baseline_cells = _group_by_bucket(baseline)
    keys = set(recent_cells) | set(baseline_cells)
    out: dict[str, Any] = {}
    for key in keys:
        out[key] = {
            "recent": _cell_stats(recent_cells.get(key, [])),
            "baseline": _cell_stats(baseline_cells.get(key, [])),
        }
    return out


def _group_by_bucket(samples: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for s in samples:
        eb = s.get("edge_bucket") or "unknown"
        cb = s.get("confidence_bucket") or "unknown"
        key = f"{eb}|{cb}"
        groups.setdefault(key, []).append(s)
    return groups


def _cell_stats(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {"n": 0, "brier_score": None, "direction_correct_rate": None}
    briers = [s["brier_score"] for s in samples if s.get("brier_score") is not None]
    dc_vals = [s["direction_correct"] for s in samples
               if s.get("direction_correct") is not None]
    mean_brier = round(sum(briers) / len(briers), 4) if briers else None
    dc_rate = round(sum(dc_vals) / len(dc_vals), 4) if dc_vals else None
    return {
        "n": len(samples),
        "brier_score": mean_brier,
        "direction_correct_rate": dc_rate,
    }


def _is_finite_num(value: Any) -> bool:
    import math
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
```

### Step 4: Run test to verify it passes

- [ ] Run: `python -m pytest backend/tests/test_calibration_drift_service.py -v`
- Expected: PASS (all tests green)

### Step 5: Commit

- [ ] Run:
```bash
git add backend/app/services/calibration_drift_service.py backend/tests/test_calibration_drift_service.py
git commit -m "feat(calibration): add pure calibration_drift_service (ECE + drift score + alert rules)"
```

---

## Task 2: Alert dispatcher + config flags + drift gauge wiring + drift route

**Files:**
- Create: `backend/app/services/drift_alert_dispatcher.py`
- Modify: `backend/app/memory/prediction_store.py` (add `list_scored_samples_for_drift`)
- Modify: `backend/app/api/routes/quality_metrics.py` (add `/quality-metrics/drift` route)
- Modify: `backend/app/utils/metrics.py` (wire `CALIBRATION_DRIFT` gauge)
- Modify: `backend/app/core/config.py` (add `DRIFT_*` flags)
- Test: `backend/tests/test_drift_alert_dispatcher.py`, extend `backend/tests/test_quality_metrics.py`

**Interfaces:**
- Consumes: `calibration_drift_service.build_drift_report` + `evaluate_drift_alerts` (Task 1); `prediction_store.calibration_summary` (existing); `loop_run_store.last_run` / `recent_runs` (existing); `sentry.capture_message` (existing); `metrics.CALIBRATION_DRIFT` (existing gauge).
- Produces: `dispatch_drift_alerts(alerts, *, force=False)` best-effort dispatcher; `GET /quality-metrics/drift` route returning the drift report + triggered alerts; `CALIBRATION_DRIFT` gauge wired to the real drift score.

### Step 1: Write the failing tests

- [ ] Create `backend/tests/test_drift_alert_dispatcher.py`:

```python
"""Tests for drift_alert_dispatcher (Plan 2 §1.7 alert dispatch).

The dispatcher is best-effort: webhook/Sentry failures never raise. It is
gated by ``DRIFT_ALERTS_ENABLED`` (default false) — when off, dispatch is a
no-op. Tests patch settings + HTTP + Sentry to verify both branches.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from app.services import drift_alert_dispatcher


class TestDispatchDriftAlerts(unittest.TestCase):
    def _alert(self):
        return {
            "code": "brier_relative_drift",
            "severity": "high",
            "detail": {"drift_score": 0.5, "note": "recent worse"},
        }

    def test_disabled_flag_is_noop(self):
        with patch("app.services.drift_alert_dispatcher.settings") as s:
            s.DRIFT_ALERTS_ENABLED = False
            s.DRIFT_ALERT_WEBHOOK_URL = "http://example.com/hook"
            with patch("app.services.drift_alert_dispatcher._post_webhook") as wh, \
                 patch("app.services.drift_alert_dispatcher.capture_message") as cap:
                drift_alert_dispatcher.dispatch_drift_alerts([self._alert()])
                wh.assert_not_called()
                cap.assert_not_called()

    def test_enabled_dispatches_webhook_and_sentry(self):
        with patch("app.services.drift_alert_dispatcher.settings") as s:
            s.DRIFT_ALERTS_ENABLED = True
            s.DRIFT_ALERT_WEBHOOK_URL = "http://example.com/hook"
            s.DRIFT_ALERT_COOLDOWN_SECONDS = 0  # disable cooldown for test
            with patch("app.services.drift_alert_dispatcher._post_webhook") as wh, \
                 patch("app.services.drift_alert_dispatcher.capture_message") as cap:
                drift_alert_dispatcher.dispatch_drift_alerts([self._alert()])
                wh.assert_called_once()
                cap.assert_called_once()

    def test_no_webhook_url_skips_webhook_not_sentry(self):
        with patch("app.services.drift_alert_dispatcher.settings") as s:
            s.DRIFT_ALERTS_ENABLED = True
            s.DRIFT_ALERT_WEBHOOK_URL = ""
            s.DRIFT_ALERT_COOLDOWN_SECONDS = 0
            with patch("app.services.drift_alert_dispatcher._post_webhook") as wh, \
                 patch("app.services.drift_alert_dispatcher.capture_message") as cap:
                drift_alert_dispatcher.dispatch_drift_alerts([self._alert()])
                wh.assert_not_called()
                cap.assert_called_once()

    def test_webhook_failure_does_not_raise(self):
        with patch("app.services.drift_alert_dispatcher.settings") as s:
            s.DRIFT_ALERTS_ENABLED = True
            s.DRIFT_ALERT_WEBHOOK_URL = "http://example.com/hook"
            s.DRIFT_ALERT_COOLDOWN_SECONDS = 0
            with patch("app.services.drift_alert_dispatcher._post_webhook",
                       side_effect=Exception("network down")), \
                 patch("app.services.drift_alert_dispatcher.capture_message"):
                # Must not raise
                drift_alert_dispatcher.dispatch_drift_alerts([self._alert()])

    def test_cooldown_deduplicates_repeated_alerts(self):
        with patch("app.services.drift_alert_dispatcher.settings") as s:
            s.DRIFT_ALERTS_ENABLED = True
            s.DRIFT_ALERT_WEBHOOK_URL = ""
            s.DRIFT_ALERT_COOLDOWN_SECONDS = 3600
            with patch("app.services.drift_alert_dispatcher.capture_message") as cap:
                drift_alert_dispatcher.dispatch_drift_alerts([self._alert()])
                drift_alert_dispatcher.dispatch_drift_alerts([self._alert()])  # same code, deduped
                # First dispatch fires; second is within cooldown → skipped
                self.assertEqual(cap.call_count, 1)
            # Reset cooldown state for other tests
            drift_alert_dispatcher._reset_cooldown_state()

    def test_rule4_scheduler_zero_resolved_alert(self):
        """Rule 4: scheduler succeeded N times but 0 new resolved predictions."""
        runs = [
            {"job_name": "event_auto_resolve", "status": "success"},
            {"job_name": "event_auto_resolve", "status": "success"},
            {"job_name": "event_auto_resolve", "status": "success"},
        ]
        with patch("app.services.drift_alert_dispatcher.settings") as s:
            s.DRIFT_ALERTS_ENABLED = True
            s.DRIFT_ALERT_WEBHOOK_URL = ""
            s.DRIFT_ALERT_COOLDOWN_SECONDS = 0
            s.DRIFT_SCHEDULER_ZERO_RESOLVED_RUNS = 3
            with patch("app.services.drift_alert_dispatcher.loop_run_store") as lrs, \
                 patch("app.services.drift_alert_dispatcher.prediction_store") as ps, \
                 patch("app.services.drift_alert_dispatcher.capture_message") as cap:
                lrs.recent_runs.return_value = runs
                ps.count_recent_scored.return_value = 0
                alerts = drift_alert_dispatcher.evaluate_scheduler_alerts()
                codes = [a["code"] for a in alerts]
                self.assertIn("scheduler_zero_resolved", codes)


if __name__ == "__main__":
    unittest.main()
```

- [ ] Add the following test methods to the END of `backend/tests/test_quality_metrics.py` (inside a new test class, before the `if __name__` guard):

```python
class TestQualityMetricsDriftRoute(unittest.TestCase):
    """Tests for GET /api/quality-metrics/drift (Plan 2 Task 2)."""

    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(quality_metrics_routes.router)
        self.client = TestClient(self.app)

    def test_drift_returns_report_shape(self):
        with patch("app.api.routes.quality_metrics.prediction_store") as ps, \
             patch("app.api.routes.quality_metrics.event_store") as es:
            ps.list_scored_samples_for_drift.return_value = {
                "recent": [
                    {"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.09,
                     "edge_bucket": "5-10", "confidence_bucket": "high",
                     "direction_correct": 1, "degraded": False, "event_id": "e1",
                     "resolved_at": "2026-06-29T00:00:00+00:00"},
                ],
                "baseline": [
                    {"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.09,
                     "edge_bucket": "5-10", "confidence_bucket": "high",
                     "direction_correct": 1, "degraded": False, "event_id": "e0",
                     "resolved_at": "2026-06-01T00:00:00+00:00"},
                ],
            }
            es.list_all_events.return_value = []
            response = self.client.get("/api/quality-metrics/drift")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("drift", body)
        self.assertIn("ece", body)
        self.assertIn("alerts", body)
        self.assertIn("buckets", body)

    def test_drift_gauge_wired_uses_store_not_placeholder(self):
        """_refresh_calibration_gauges reads prediction_store for drift
        (real computation), not the old CALIBRATION_DRIFT.set(0.0) placeholder.

        The import inside _refresh_calibration_gauges is local
        (``from app.memory.prediction_store import ...``), so we patch the
        source module ``app.memory.prediction_store`` — NOT
        ``app.utils.metrics.prediction_store`` (that attribute does not
        exist on the metrics module).
        """
        from app.utils import metrics
        with patch("app.memory.prediction_store.calibration_summary",
                   return_value={"brier_score": 0.2}), \
             patch("app.memory.prediction_store.list_scored_samples_for_drift",
                   return_value={
                       "recent": [{"brier_score": 0.3}],
                       "baseline": [{"brier_score": 0.1}],
                   }) as mock_list:
            # Must not raise — best-effort refresh.
            metrics._refresh_calibration_gauges()
            # The store was consulted (real path), confirming the 0.0
            # placeholder is gone.
            mock_list.assert_called_once_with(recent_n=50)


### Step 2: Run tests to verify they fail

- [ ] Run: `python -m pytest backend/tests/test_drift_alert_dispatcher.py backend/tests/test_quality_metrics.py::TestQualityMetricsDriftRoute -v`
- Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.drift_alert_dispatcher'` and `AttributeError: ... has no attribute 'list_scored_samples_for_drift'`

### Step 3: Add config flags

- [ ] In `backend/app/core/config.py`, insert this block AFTER the `GUARDRAIL_HIGH_CONFLICT_THRESHOLD` block (line ~701) and BEFORE `settings = Settings()` (line 704):

```python

    # Calibration drift alerts (Plan 2 §1.7). The drift *computation*
    # (ECE, drift_score) is always available and read-only; these flags
    # gate the alert *dispatch* (webhook + Sentry breadcrumb) which has
    # side effects. Default OFF so a fresh install computes drift silently
    # without firing webhooks — byte-identical alert silence to pre-Plan-2.
    DRIFT_ALERTS_ENABLED: bool = _env_bool("DRIFT_ALERTS_ENABLED", "false")
    # Rule 1: recent Brier mean must exceed baseline by this relative
    # threshold (0.30 = 30% worse) to fire brier_relative_drift.
    DRIFT_BRIER_RELATIVE_THRESHOLD: float = float(
        os.getenv("DRIFT_BRIER_RELATIVE_THRESHOLD", "0.30")
    )
    # Rule 2: bucket direction_correct_rate must deviate by more than this
    # many percentage points from baseline to fire bucket_deviation.
    DRIFT_BUCKET_DEVIATION_PP: float = float(
        os.getenv("DRIFT_BUCKET_DEVIATION_PP", "20.0")
    )
    DRIFT_BUCKET_MIN_SAMPLES: int = int(
        os.getenv("DRIFT_BUCKET_MIN_SAMPLES", "2")
    )
    # Number of most-recent scored predictions to treat as the "recent"
    # window for drift comparison.
    DRIFT_RECENT_WINDOW_N: int = int(os.getenv("DRIFT_RECENT_WINDOW_N", "50"))
    # Rule 4: fire scheduler_zero_resolved when this many consecutive
    # successful scheduler runs produce 0 new scored predictions.
    DRIFT_SCHEDULER_ZERO_RESOLVED_RUNS: int = int(
        os.getenv("DRIFT_SCHEDULER_ZERO_RESOLVED_RUNS", "3")
    )
    # Webhook destination for drift alerts. Empty = no webhook (Sentry +
    # log only). When set, POSTs a JSON alert payload on each dispatch.
    DRIFT_ALERT_WEBHOOK_URL: str = os.getenv("DRIFT_ALERT_WEBHOOK_URL", "")
    # Cooldown (seconds) per alert code — prevents webhook spam when the
    # drift condition persists across scrapes. 0 = no cooldown.
    DRIFT_ALERT_COOLDOWN_SECONDS: int = int(
        os.getenv("DRIFT_ALERT_COOLDOWN_SECONDS", "3600")
    )
```

### Step 4: Add `list_scored_samples_for_drift` to prediction_store

- [ ] In `backend/app/memory/prediction_store.py`, add this function after `calibration_bucket_summary()` (find its end, then append):

```python
def list_scored_samples_for_drift(recent_n: int = 50) -> dict[str, list[dict[str, Any]]]:
    """Return scored predictions split into recent + baseline for drift.

    Recent = the most recent ``recent_n`` scored predictions by
    ``resolved_at`` DESC. Baseline = all remaining scored predictions
    (older than the recent window). Each sample dict carries the fields
    the drift service needs: ``brier_score``, ``predicted_prob``
    (ai_probability), ``actual_outcome``, ``edge_bucket``,
    ``confidence_bucket``, ``direction_correct``, ``event_id``,
    ``resolved_at``.

    The ``degraded`` flag per sample is left False here — the route
    handler joins event_store llm_telemetry to set it, because the
    predictions table does not store LLM degradation state.
    """
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        rows = conn.execute(
            """
            SELECT event_id, ai_probability, actual_outcome, brier_score,
                   edge_bucket, confidence_bucket, direction_correct,
                   resolved_at
            FROM predictions
            WHERE status='scored' AND decision='act'
            ORDER BY resolved_at DESC
            """,
        ).fetchall()

    samples: list[dict[str, Any]] = []
    for r in rows:
        samples.append({
            "event_id": r["event_id"],
            "predicted_prob": r["ai_probability"],
            "actual_outcome": _to_outcome_int(r["actual_outcome"]),
            "brier_score": r["brier_score"],
            "edge_bucket": r["edge_bucket"] or "unknown",
            "confidence_bucket": r["confidence_bucket"] or "unknown",
            "direction_correct": r["direction_correct"],
            "resolved_at": r["resolved_at"],
            "degraded": False,  # set by route handler via event_store join
        })

    recent = samples[:recent_n]
    baseline = samples[recent_n:]
    return {"recent": recent, "baseline": baseline}
```

- [ ] Also add the `_to_outcome_int` helper near the other `_` helpers in `prediction_store.py` (place it after the existing `brier_score`/`skill_score` imports region, e.g. near the top helper section):

```python
def _to_outcome_int(value: Any) -> int | None:
    """Convert an actual_outcome (0-100) to a 0/1 indicator for ECE."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return 1 if v >= 50.0 else 0
```

(If a `_to_outcome_int` or equivalent already exists, reuse it — search for `>= 50` first. Do not duplicate.)

### Step 5: Create the alert dispatcher

- [ ] Create `backend/app/services/drift_alert_dispatcher.py`:

```python
"""Drift alert dispatcher (Plan 2 §1.7).

Best-effort alert dispatch for calibration drift. Three outlets:
1. Webhook HTTP POST (when ``DRIFT_ALERT_WEBHOOK_URL`` is set)
2. Sentry ``capture_message`` (breadcrumb-level, level=warning)
3. Structured log line

Gated by ``DRIFT_ALERTS_ENABLED`` (default false). When disabled, all
dispatch is a no-op — drift is still computed and exposed via the
``/quality-metrics/drift`` route and the ``CALIBRATION_DRIFT`` gauge, but
no side effects fire.

Cooldown: per-alert-code dedup within ``DRIFT_ALERT_COOLDOWN_SECONDS``
prevents webhook spam when a drift condition persists across scrapes.

Rule 4 (scheduler_zero_resolved) is evaluated here because it needs
``loop_run_store`` + ``prediction_store`` access (I/O), unlike the pure
drift rules in ``calibration_drift_service``.
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
    """Rule 4: scheduler succeeded N times but 0 new scored predictions.

    Returns a list of alert dicts (empty when the condition is not met or
    the dispatcher is disabled). Reads ``loop_run_store.recent_runs`` and
    ``prediction_store`` — called by the drift route on each request.
    """
    if not getattr(settings, "DRIFT_ALERTS_ENABLED", False):
        return []

    from app.memory import loop_run_store
    from app.memory import prediction_store

    threshold = getattr(settings, "DRIFT_SCHEDULER_ZERO_RESOLVED_RUNS", 3)
    runs = loop_run_store.recent_runs(limit=threshold + 5)
    # Take the most recent N runs of the auto-resolve job.
    resolve_runs = [
        r for r in runs
        if r.get("job_name") == "event_auto_resolve"
    ][:threshold]
    if len(resolve_runs) < threshold:
        return []
    if not all(r.get("status") == "success" for r in resolve_runs):
        return []

    # Count scored predictions created in the recent window. We approximate
    # "0 new resolved" by checking if the recent scored count is 0 in the
    # drift recent window — reuse the store helper.
    try:
        samples = prediction_store.list_scored_samples_for_drift(
            recent_n=getattr(settings, "DRIFT_RECENT_WINDOW_N", 50)
        )
        recent_count = len(samples.get("recent", []))
    except Exception:  # pragma: no cover - defensive
        return []

    if recent_count > 0:
        return []

    return [{
        "code": "scheduler_zero_resolved",
        "severity": "medium",
        "detail": {
            "consecutive_successes": threshold,
            "recent_scored_count": 0,
            "note": "Scheduler succeeded %d times but 0 new scored predictions "
                    "in the drift recent window — resolution pipeline may be stuck."
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
```

### Step 6: Wire the drift gauge in metrics.py

- [ ] In `backend/app/utils/metrics.py`, replace the placeholder line inside `_refresh_calibration_gauges()`:

```python
    # Drift score: Phase 1 placeholder — keep at 0 until drift algorithm lands.
    CALIBRATION_DRIFT.set(0.0)
```

with:

```python
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
```

### Step 7: Add the `/quality-metrics/drift` route

- [ ] In `backend/app/api/routes/quality_metrics.py`, add `list_scored_samples_for_drift` to the existing top-level import line from prediction_store. The existing line reads:

```python
from app.memory.prediction_store import calibration_bucket_summary, calibration_summary
```

Change it to:

```python
from app.memory.prediction_store import (
    calibration_bucket_summary,
    calibration_summary,
    list_scored_samples_for_drift,
)
```

Then add these top-level imports near the top (after the existing imports). These are module-level so the tests can patch them via `app.api.routes.quality_metrics.<name>`:

```python
from app.services.calibration_drift_service import build_drift_report, evaluate_drift_alerts
from app.services.drift_alert_dispatcher import dispatch_drift_alerts, evaluate_scheduler_alerts
```

(`list_all_events` is already imported at the top of the file — reuse it for the degraded-mode join; do NOT add `from app.memory import event_store`.)

- [ ] Then add this route after the `quality_metrics_anomalies` function (before the Helpers section):

```python
@router.get("/quality-metrics/drift")
async def quality_metrics_drift() -> dict[str, Any]:
    """Calibration drift report + triggered alerts (Plan 2 §1.7).

    Returns:
        - ``drift`` — recent-vs-baseline Brier delta (positive = recent worse)
        - ``ece`` — Expected Calibration Error for recent + baseline windows
        - ``degraded_mixing`` — whether recent window contains LLM-degraded samples
        - ``buckets`` — per-cell (edge|confidence) recent vs baseline stats
        - ``alerts`` — list of triggered alert dicts (rules 1-4)

    The drift *computation* always runs (read-only). Alert *dispatch*
    (webhook/Sentry) is gated by ``DRIFT_ALERTS_ENABLED`` and fires as a
    side effect of this route being called — so the dashboard's periodic
    poll acts as the alert heartbeat.
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

    # Best-effort dispatch (no-op when DRIFT_ALERTS_ENABLED=false)
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
```

### Step 8: Run tests to verify they pass

- [ ] Run: `python -m pytest backend/tests/test_drift_alert_dispatcher.py backend/tests/test_quality_metrics.py -v`
- Expected: PASS

- [ ] Run the full backend suite to confirm no regressions:
```bash
python -m pytest backend/tests/ -q
```
- Expected: all green (1724+ tests pass, plus the new ones)

### Step 9: Commit

- [ ] Run:
```bash
git add backend/app/services/drift_alert_dispatcher.py backend/app/memory/prediction_store.py backend/app/api/routes/quality_metrics.py backend/app/utils/metrics.py backend/app/core/config.py backend/tests/test_drift_alert_dispatcher.py backend/tests/test_quality_metrics.py
git commit -m "feat(observability): add drift alert dispatcher + /quality-metrics/drift route + gauge wiring"
```

---

## Task 3: Frontend API client + types for quality-metrics

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Test: `frontend/src/lib/api.test.ts` (extend) or create `frontend/src/lib/quality-metrics-api.test.ts`

**Interfaces:**
- Consumes: the existing `api<T>(path)` helper + `getApiBase()` in `api.ts`.
- Produces: `QualityMetricsSummary`, `QualityMetricsAnomaly`, `QualityMetricsDrift`, `SchedulerTimeseriesPoint` types + `qualityMetricsApi` client object. Task 4 imports these.

### Step 1: Write the failing test

- [ ] Append to `frontend/src/lib/api.test.ts` (or create the file if it does not exist — first check with a glob; if `api.test.ts` exists, append; otherwise create `quality-metrics-api.test.ts`):

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { qualityMetricsApi } from "./api";

describe("qualityMetricsApi", () => {
  beforeEach(() => {
    vi.resetModules();
    (globalThis as { fetch?: typeof fetch }).fetch = vi.fn();
  });

  it("summary calls /quality-metrics/summary with timeframe", async () => {
    (globalThis as { fetch?: typeof fetch }).fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ timeframe: "24h", counts: { events: 0 } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ) as typeof fetch;
    const data = await qualityMetricsApi.summary("7d");
    expect(data.timeframe).toBe("24h");
    const calls = (globalThis as { fetch?: typeof fetch }).fetch as unknown as ReturnType<typeof vi.fn>;
    expect(calls).toHaveBeenCalledWith(
      expect.stringContaining("/quality-metrics/summary?timeframe=7d"),
      expect.anything(),
    );
  });

  it("drift calls /quality-metrics/drift", async () => {
    (globalThis as { fetch?: typeof fetch }).fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ drift: { drift_score: 0.1 }, alerts: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ) as typeof fetch;
    const data = await qualityMetricsApi.drift();
    expect(data.drift?.drift_score).toBe(0.1);
  });

  it("anomalies calls /quality-metrics/anomalies", async () => {
    (globalThis as { fetch?: typeof fetch }).fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ count: 0, anomalies: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ) as typeof fetch;
    const data = await qualityMetricsApi.anomalies();
    expect(data.count).toBe(0);
  });

  it("timeseries calls /quality-metrics/timeseries with window", async () => {
    (globalThis as { fetch?: typeof fetch }).fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ window: "7d", points: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ) as typeof fetch;
    const data = await qualityMetricsApi.timeseries("30d");
    expect(data.window).toBe("7d");
  });
});
```

### Step 2: Run test to verify it fails

- [ ] Run: `cd frontend && npx vitest run src/lib/api.test.ts` (or `npm test -- src/lib/api.test.ts`)
- Expected: FAIL with `qualityMetricsApi is not exported` / `not defined`

### Step 3: Add types + client to api.ts

- [ ] In `frontend/src/lib/api.ts`, add these type definitions (place them after the existing `PredictionCalibration` interface, around line 590):

```typescript
// ── Quality operations metrics (Plan 2 §1.6) ───────────────────────────────
// Mirrors backend /api/quality-metrics/{summary,timeseries,anomalies,drift}.

export interface QualityMetricsSummary {
  timeframe: string;
  counts: {
    events: number;
    resolved_events: number;
    with_decision_quality: number;
    with_market_quality: number;
    with_source_reliability: number;
    with_llm_telemetry: number;
  };
  final_direction: Record<string, number>;
  consensus: Record<string, number>;
  downgrade: {
    final_downgrade_reason_present: number;
    build_errors: {
      decision_quality: number;
      market_quality: number;
      source_reliability: number;
    };
  };
  market_quality: {
    count: number;
    wide_spread_flag_count: number;
    thin_market_flag_count: number;
    score_avg: number | null;
    score_min: number | null;
    score_max: number | null;
  };
  source_reliability: {
    count: number;
    overall_score_avg: number | null;
    source_count_avg: number | null;
    domain_diversity_avg: number | null;
  };
  llm_telemetry: {
    count: number;
    degraded_mode_count: number;
    estimated_token_cost_total: number;
  };
  calibration: Record<string, unknown>;
  calibration_buckets: Record<string, unknown>;
  scheduler: {
    last_runs: Record<string, {
      status: string | null;
      started_at: string | null;
      finished_at: string | null;
      duration_ms: number | null;
    } | null>;
    recent_failed_count: number;
    recent_runs_count: number;
  };
}

export interface QualityMetricsAnomaly {
  code: string;
  severity: "high" | "medium" | "low" | string;
  detail: unknown;
}

export interface SchedulerTimeseriesPoint {
  job_name: string | null;
  status: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
}

export interface QualityMetricsDrift {
  recent_window_n: number;
  drift: {
    drift_score: number | null;
    recent_mean: number | null;
    baseline_mean: number | null;
    recent_n: number;
    baseline_n: number;
  } | null;
  ece: {
    recent: number | null;
    baseline: number | null;
  };
  degraded_mixing: {
    recent_degraded_count: number;
    recent_n: number;
    contaminated: boolean;
  };
  buckets: Record<string, {
    recent: { n: number; brier_score: number | null; direction_correct_rate: number | null };
    baseline: { n: number; brier_score: number | null; direction_correct_rate: number | null };
  }>;
  alerts: QualityMetricsAnomaly[];
  alerts_enabled: boolean;
}
```

- [ ] Then add the `qualityMetricsApi` client object. Place it after the existing `eventsApi` object (which ends around line 835 — find the `};` that closes `eventsApi`):

```typescript
export const qualityMetricsApi = {
  summary: (timeframe: "24h" | "7d" | "all" = "24h") =>
    api<QualityMetricsSummary>(`/quality-metrics/summary?timeframe=${timeframe}`),

  timeseries: (window: "24h" | "7d" | "30d" = "7d") =>
    api<{ window: string; points: SchedulerTimeseriesPoint[] }>(
      `/quality-metrics/timeseries?window=${window}`,
    ),

  anomalies: () =>
    api<{ count: number; anomalies: QualityMetricsAnomaly[] }>(
      "/quality-metrics/anomalies",
    ),

  drift: () =>
    api<QualityMetricsDrift>("/quality-metrics/drift"),
};
```

### Step 4: Run test to verify it passes

- [ ] Run: `cd frontend && npx vitest run src/lib/api.test.ts`
- Expected: PASS

### Step 5: Commit

- [ ] Run:
```bash
git add frontend/src/lib/api.ts frontend/src/lib/api.test.ts
git commit -m "feat(frontend): add qualityMetricsApi client + types for quality-metrics routes"
```

---

## Task 4: Quality operations dashboard page + components

**Files:**
- Create: `frontend/src/components/dashboard/anomaly-banner.tsx`
- Create: `frontend/src/components/dashboard/drift-panel.tsx`
- Create: `frontend/src/components/dashboard/quality-summary-panel.tsx`
- Create: `frontend/src/components/dashboard/scheduler-timeseries.tsx`
- Create: `frontend/src/components/dashboard/quality-operations-dashboard.tsx`
- Create: `frontend/src/app/quality/page.tsx`
- Test: `frontend/src/components/dashboard/quality-operations-dashboard.test.tsx`

**Interfaces:**
- Consumes: `qualityMetricsApi` + types from Task 3 (`QualityMetricsSummary`, `QualityMetricsAnomaly`, `QualityMetricsDrift`, `SchedulerTimeseriesPoint`). Existing `AppNav`, `SectionErrorBoundary`, `lucide-react` icons, Tailwind classes.
- Produces: the `/quality` route rendering the unified dashboard.

### Step 1: Write the failing test

- [ ] Create `frontend/src/components/dashboard/quality-operations-dashboard.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QualityOperationsDashboard } from "./quality-operations-dashboard";

// Mock the API client so the dashboard renders with deterministic data.
vi.mock("@/lib/api", () => ({
  qualityMetricsApi: {
    summary: vi.fn().mockResolvedValue({
      timeframe: "24h",
      counts: { events: 5, resolved_events: 2, with_decision_quality: 5,
                with_market_quality: 3, with_source_reliability: 4, with_llm_telemetry: 5 },
      final_direction: { YES: 2, NO: 1, WAIT: 1, AVOID: 1 },
      consensus: { none: 1, low: 1, medium: 2, high: 1 },
      downgrade: { final_downgrade_reason_present: 1,
        build_errors: { decision_quality: 0, market_quality: 0, source_reliability: 0 } },
      market_quality: { count: 3, wide_spread_flag_count: 0, thin_market_flag_count: 0,
        score_avg: 0.7, score_min: 0.6, score_max: 0.8 },
      source_reliability: { count: 4, overall_score_avg: 0.75,
        source_count_avg: 3, domain_diversity_avg: 2 },
      llm_telemetry: { count: 5, degraded_mode_count: 0, estimated_token_cost_total: 0.01 },
      calibration: { n: 2, brier_score: 0.15, grade: "GOOD" },
      calibration_buckets: {},
      scheduler: { last_runs: {}, recent_failed_count: 0, recent_runs_count: 10 },
    }),
    timeseries: vi.fn().mockResolvedValue({ window: "7d", points: [] }),
    anomalies: vi.fn().mockResolvedValue({ count: 0, anomalies: [] }),
    drift: vi.fn().mockResolvedValue({
      recent_window_n: 50,
      drift: { drift_score: 0.1, recent_mean: 0.2, baseline_mean: 0.18,
               recent_n: 5, baseline_n: 10 },
      ece: { recent: 0.05, baseline: 0.04 },
      degraded_mixing: { recent_degraded_count: 0, recent_n: 5, contaminated: false },
      buckets: {},
      alerts: [],
      alerts_enabled: false,
    }),
  },
}));

describe("QualityOperationsDashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the summary panel with event count", async () => {
    render(<QualityOperationsDashboard />);
    await waitFor(() => {
      expect(screen.getByText(/5/)).toBeInTheDocument();
    });
    expect(screen.getByText(/质量运营仪表盘/)).toBeInTheDocument();
  });

  it("renders the anomaly banner with zero anomalies", async () => {
    render(<QualityOperationsDashboard />);
    await waitFor(() => {
      expect(screen.getByText(/无异常/)).toBeInTheDocument();
    });
  });

  it("renders the drift panel with drift score", async () => {
    render(<QualityOperationsDashboard />);
    await waitFor(() => {
      expect(screen.getByText(/0.1/)).toBeInTheDocument();
    });
  });
});
```

### Step 2: Run test to verify it fails

- [ ] Run: `cd frontend && npx vitest run src/components/dashboard/quality-operations-dashboard.test.tsx`
- Expected: FAIL with `Cannot find module './quality-operations-dashboard'`

### Step 3: Implement the components

- [ ] Create `frontend/src/components/dashboard/anomaly-banner.tsx`:

```tsx
import { AlertTriangle, CheckCircle2 } from "lucide-react";
import type { QualityMetricsAnomaly } from "@/lib/api";

const SEVERITY_TONE: Record<string, string> = {
  high: "border-neg/40 bg-neg/10 text-neg",
  medium: "border-primary/40 bg-primary/10 text-primary",
  low: "border-border bg-muted text-muted-foreground",
};

const SEVERITY_LABEL: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

const CODE_LABEL: Record<string, string> = {
  scheduler_not_running: "调度器未运行",
  scheduler_job_failed: "调度任务失败",
  scheduler_zero_resolved: "调度成功但无新结算",
  calibration_brier_high: "Brier 分数过高",
  wide_spread_not_downgraded: "宽价差未降级",
  llm_degraded_mode_events: "LLM 降级模式事件",
  brier_relative_drift: "Brier 相对漂移",
  bucket_deviation: "桶偏差",
  degraded_mixing: "降级样本混入",
};

export function AnomalyBanner({ anomalies }: { anomalies: QualityMetricsAnomaly[] }) {
  if (anomalies.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted-foreground">
        <CheckCircle2 className="size-4 text-pos" aria-hidden="true" />
        <span>无异常 — 质量引擎运行正常</span>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {anomalies.map((a, i) => {
        const tone = SEVERITY_TONE[a.severity] ?? SEVERITY_TONE.low;
        const label = CODE_LABEL[a.code] ?? a.code;
        const detail = typeof a.detail === "string" ? a.detail : JSON.stringify(a.detail);
        return (
          <div
            key={`${a.code}-${i}`}
            className={`flex items-start gap-2 rounded-lg border px-4 py-3 text-sm ${tone}`}
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <div className="flex flex-col gap-0.5">
              <span className="font-medium">
                {label}
                <span className="ml-2 rounded px-1.5 py-0.5 text-xs bg-card/50">
                  {SEVERITY_LABEL[a.severity] ?? a.severity}
                </span>
              </span>
              <span className="text-xs opacity-80">{detail}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] Create `frontend/src/components/dashboard/drift-panel.tsx`:

```tsx
import { Activity, TrendingUp, TrendingDown } from "lucide-react";
import type { QualityMetricsDrift } from "@/lib/api";

function Stat({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="flex flex-col gap-1 px-4 py-3 first:pl-4">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-mono text-xl font-semibold tabular-nums">{value}</div>
      <div className="text-xs text-muted-foreground">{hint}</div>
    </div>
  );
}

export function DriftPanel({ drift }: { drift: QualityMetricsDrift | null }) {
  if (!drift) {
    return (
      <section className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
        加载漂移数据…
      </section>
    );
  }
  const d = drift.drift;
  const driftScore = d?.drift_score;
  const tone =
    driftScore == null
      ? "text-muted-foreground"
      : driftScore > 0.3
        ? "text-neg"
        : driftScore < -0.1
          ? "text-pos"
          : "text-foreground";
  const driftIcon =
    driftScore != null && driftScore > 0 ? (
      <TrendingUp className="size-3.5" aria-hidden="true" />
    ) : driftScore != null && driftScore < 0 ? (
      <TrendingDown className="size-3.5" aria-hidden="true" />
    ) : null;

  const eceRecent = drift.ece.recent;
  const mixing = drift.degraded_mixing;

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <Activity className="size-4 text-primary" aria-hidden="true" />
        校准漂移
        {drift.alerts_enabled && (
          <span className="rounded bg-primary/15 px-1.5 py-0.5 text-xs text-primary">
            告警已启用
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 divide-border rounded-lg border border-border md:grid-cols-4 md:divide-x">
        <Stat
          label="漂移分数"
          value={driftScore == null ? "—" : `${(driftScore * 100).toFixed(1)}%`}
          hint="正=近期变差 / 负=改善"
        />
        <Stat
          label="近期 Brier"
          value={d?.recent_mean == null ? "—" : d.recent_mean.toFixed(4)}
          hint={`近 ${d?.recent_n ?? 0} 条`}
        />
        <Stat
          label="基线 Brier"
          value={d?.baseline_mean == null ? "—" : d.baseline_mean.toFixed(4)}
          hint={`基线 ${d?.baseline_n ?? 0} 条`}
        />
        <Stat
          label="近期 ECE"
          value={eceRecent == null ? "—" : eceRecent.toFixed(4)}
          hint="期望校准误差"
        />
      </div>
      {mixing?.contaminated && (
        <div className={`flex items-center gap-2 text-xs ${tone}`}>
          {driftIcon}
          <span>
            近期窗口含 {mixing.recent_degraded_count} 条 LLM 降级样本，headline Brier 可能被污染
          </span>
        </div>
      )}
      {drift.alerts.length > 0 && (
        <div className="flex flex-col gap-1 text-xs">
          <span className="font-medium text-muted-foreground">触发的告警：</span>
          {drift.alerts.map((a, i) => (
            <span key={i} className="font-mono">
              {a.code} ({a.severity})
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
```

- [ ] Create `frontend/src/components/dashboard/quality-summary-panel.tsx`:

```tsx
import type { QualityMetricsSummary } from "@/lib/api";

function Row({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-center justify-between border-b border-border py-1.5 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono tabular-nums">{value}</span>
    </div>
  );
}

export function QualitySummaryPanel({ summary }: { summary: QualityMetricsSummary | null }) {
  if (!summary) {
    return (
      <section className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
        加载汇总…
      </section>
    );
  }
  const dir = summary.final_direction;
  const mq = summary.market_quality;
  const sr = summary.source_reliability;
  const lt = summary.llm_telemetry;
  const cal = summary.calibration as { brier_score?: number | null; grade?: string; n?: number };

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <h2 className="text-sm font-semibold">质量汇总</h2>
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">事件计数</h3>
          <Row label="在库事件" value={summary.counts.events} />
          <Row label="已结算" value={summary.counts.resolved_events} />
          <Row label="含 decision_quality" value={summary.counts.with_decision_quality} />
          <Row label="含 market_quality" value={summary.counts.with_market_quality} />
          <Row label="含 llm_telemetry" value={summary.counts.with_llm_telemetry} />
        </div>
        <div>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">最终方向分布</h3>
          <Row label="YES" value={dir.YES ?? 0} />
          <Row label="NO" value={dir.NO ?? 0} />
          <Row label="WAIT" value={dir.WAIT ?? 0} />
          <Row label="AVOID" value={dir.AVOID ?? 0} />
        </div>
        <div>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">市场质量</h3>
          <Row label="样本数" value={mq.count} />
          <Row label="宽价差" value={mq.wide_spread_flag_count} />
          <Row label="薄流动性" value={mq.thin_market_flag_count} />
          <Row label="平均分" value={mq.score_avg ?? "—"} />
        </div>
        <div>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">LLM 遥测</h3>
          <Row label="样本数" value={lt.count} />
          <Row label="降级模式" value={lt.degraded_mode_count} />
          <Row label="总成本 ($)" value={lt.estimated_token_cost_total.toFixed(4)} />
        </div>
        <div>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">来源可信度</h3>
          <Row label="样本数" value={sr.count} />
          <Row label="平均分" value={sr.overall_score_avg ?? "—"} />
          <Row label="平均来源数" value={sr.source_count_avg ?? "—"} />
        </div>
        <div>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">校准</h3>
          <Row label="Brier" value={cal.brier_score ?? "—"} />
          <Row label="等级" value={cal.grade ?? "—"} />
          <Row label="样本数" value={cal.n ?? 0} />
        </div>
      </div>
    </section>
  );
}
```

- [ ] Create `frontend/src/components/dashboard/scheduler-timeseries.tsx`:

```tsx
import type { SchedulerTimeseriesPoint } from "@/lib/api";

const STATUS_TONE: Record<string, string> = {
  success: "text-pos",
  failed: "text-neg",
  running: "text-primary",
};

export function SchedulerTimeseries({ points }: { points: SchedulerTimeseriesPoint[] }) {
  if (points.length === 0) {
    return (
      <section className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
        调度时间线 — 无近期运行
      </section>
    );
  }
  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <h2 className="text-sm font-semibold">调度时间线</h2>
      <div className="max-h-64 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1 text-left font-medium">任务</th>
              <th className="py-1 text-left font-medium">状态</th>
              <th className="py-1 text-left font-medium">开始</th>
              <th className="py-1 text-right font-medium">耗时</th>
            </tr>
          </thead>
          <tbody>
            {points.slice(0, 100).map((p, i) => (
              <tr key={i} className="border-t border-border">
                <td className="py-1 font-mono">{p.job_name ?? "—"}</td>
                <td className={`py-1 font-mono ${STATUS_TONE[p.status ?? ""] ?? ""}`}>
                  {p.status ?? "—"}
                </td>
                <td className="py-1 font-mono text-muted-foreground">
                  {p.started_at ?? "—"}
                </td>
                <td className="py-1 text-right font-mono tabular-nums">
                  {p.duration_ms != null ? `${p.duration_ms}ms` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
```

- [ ] Create `frontend/src/components/dashboard/quality-operations-dashboard.tsx`:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import {
  qualityMetricsApi,
  type QualityMetricsSummary,
  type QualityMetricsAnomaly,
  type QualityMetricsDrift,
  type SchedulerTimeseriesPoint,
} from "@/lib/api";
import { AnomalyBanner } from "./anomaly-banner";
import { DriftPanel } from "./drift-panel";
import { QualitySummaryPanel } from "./quality-summary-panel";
import { SchedulerTimeseries } from "./scheduler-timeseries";

const REFRESH_MS = 30_000;

export function QualityOperationsDashboard() {
  const [summary, setSummary] = useState<QualityMetricsSummary | null>(null);
  const [timeseries, setTimeseries] = useState<SchedulerTimeseriesPoint[]>([]);
  const [anomalies, setAnomalies] = useState<QualityMetricsAnomaly[]>([]);
  const [drift, setDrift] = useState<QualityMetricsDrift | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [s, t, a, d] = await Promise.all([
        qualityMetricsApi.summary("24h"),
        qualityMetricsApi.timeseries("7d"),
        qualityMetricsApi.anomalies(),
        qualityMetricsApi.drift(),
      ]);
      setSummary(s);
      setTimeseries(t.points);
      setAnomalies(a.anomalies);
      setDrift(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  if (loading && !summary) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="size-5 animate-spin" aria-hidden="true" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">质量运营仪表盘</h1>
        <button
          type="button"
          onClick={load}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-card px-2.5 text-xs text-muted-foreground transition-colors hover:bg-muted"
        >
          <RefreshCw className="size-3.5" aria-hidden="true" />
          刷新
        </button>
      </div>
      {error && (
        <div className="rounded-lg border border-neg/40 bg-neg/10 px-4 py-2 text-sm text-neg">
          {error}
        </div>
      )}
      <AnomalyBanner anomalies={anomalies} />
      <DriftPanel drift={drift} />
      <QualitySummaryPanel summary={summary} />
      <SchedulerTimeseries points={timeseries} />
    </div>
  );
}
```

- [ ] Create `frontend/src/app/quality/page.tsx`:

```tsx
"use client";

import { AppNav } from "@/components/app-nav";
import { SectionErrorBoundary } from "@/components/section-error-boundary";
import { QualityOperationsDashboard } from "@/components/dashboard/quality-operations-dashboard";

export default function QualityPage() {
  return (
    <div className="min-h-screen bg-background">
      <AppNav />
      <main className="mx-auto max-w-6xl px-4 py-6">
        <SectionErrorBoundary>
          <QualityOperationsDashboard />
        </SectionErrorBoundary>
      </main>
    </div>
  );
}
```

### Step 4: Run test to verify it passes

- [ ] Run: `cd frontend && npx vitest run src/components/dashboard/quality-operations-dashboard.test.tsx`
- Expected: PASS

- [ ] Run the full frontend test suite + type check:
```bash
cd frontend && npx vitest run && npx tsc --noEmit
```
- Expected: all green

### Step 5: Commit

- [ ] Run:
```bash
git add frontend/src/components/dashboard/anomaly-banner.tsx frontend/src/components/dashboard/drift-panel.tsx frontend/src/components/dashboard/quality-summary-panel.tsx frontend/src/components/dashboard/scheduler-timeseries.tsx frontend/src/components/dashboard/quality-operations-dashboard.tsx frontend/src/components/dashboard/quality-operations-dashboard.test.tsx frontend/src/app/quality/page.tsx
git commit -m "feat(frontend): add /quality operations dashboard with anomaly banner + drift panel"
```

---

## Self-Review Checklist (completed by plan author)

1. **Spec coverage:**
   - §1.6 (unified quality operations dashboard) → Tasks 3 + 4 (frontend client + page/components). ✓
   - §1.7 (calibration drift + alert strategy) → Tasks 1 + 2 (drift algorithm + dispatcher + gauge + endpoint). ✓
   - Alert rules 1-4 from §1.7: rules 1-3 in Task 1 `evaluate_drift_alerts`; rule 4 in Task 2 `evaluate_scheduler_alerts`. ✓
   - Alert outlets (log + metrics + Sentry/webhook) → Task 2 dispatcher. ✓
   - Dashboard panels (downgrade, direction, LLM cost, calibration, source, market, scheduler) → Task 4 `QualitySummaryPanel` + `SchedulerTimeseries` + `DriftPanel`. ✓

2. **Placeholder scan:** No TBD/TODO/"add error handling" placeholders. All code blocks are complete. ✓

3. **Type consistency:** `QualityMetricsDrift` (Task 3) matches `build_drift_report` return (Task 1) + route return (Task 2). `QualityMetricsAnomaly` matches dispatcher alert dict shape. `SchedulerTimeseriesPoint` matches `/timeseries` route point shape. ✓
