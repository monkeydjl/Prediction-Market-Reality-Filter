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
    if recent_mean is not None and baseline_mean is not None and baseline_mean != 0.0:
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
    recent_briers = [b for s in recent_samples if (b := s.get("brier_score")) is not None]
    baseline_briers = [b for s in baseline_samples if (b := s.get("brier_score")) is not None]

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
