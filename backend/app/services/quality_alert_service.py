"""Quality alert service (LATER #3 — production quality alerting).

Pure functions that evaluate a quality metrics report and return a list of
alerts. No I/O, no settings import, no dispatch. The caller (API route or
CLI) builds the report via ``quality_metrics_report_service.build_report``,
constructs thresholds via ``thresholds_from_settings(settings)``, and
passes both to ``evaluate_quality_alerts``.

Alert object shape (stable for future dispatcher integration):

    {
        "code": "direction_accuracy_low",
        "severity": "high" | "medium",
        "scope": "overview" | "slice",
        "dimension": None | "by_source_type",
        "slice": None | "prediction_market",
        "metric": "direction_accuracy",
        "value": 0.47,
        "threshold": 0.50,
        "n": 42,
    }

Stable codes:
    - direction_accuracy_low
    - brier_score_high
    - missing_calibration_rate_high
    - report_errors_high

Rules:
    - Overview always participates. Slices only participate when
      ``slice.n >= min_samples``.
    - For each metric, emit only the most severe matching alert (high wins
      over medium; no duplicate medium when high fires).
    - Metrics with None values do not alert.
    - report_errors only fires at overview scope (not per-slice).
"""
from __future__ import annotations

from typing import Any

DEFAULT_THRESHOLDS: dict[str, Any] = {
    "min_samples": 10,
    "direction_accuracy_medium": 0.60,
    "direction_accuracy_high": 0.50,
    "brier_medium": 0.25,
    "brier_high": 0.35,
    "missing_calibration_rate_medium": 0.20,
    "missing_calibration_rate_high": 0.40,
    "report_errors_high": 1,
}

_DIMENSIONS = (
    "by_source_type",
    "by_analysis_quality",
    "by_edge_bucket",
    "by_source_reliability_bucket",
)


def thresholds_from_settings(settings: Any) -> dict[str, Any]:
    """Adapter: extract quality-alert thresholds from a Settings object.

    Lives in the service module so API/CLI/CLI tests share one mapping, but
    does NOT import the global settings singleton — callers pass it in.
    """
    return {
        "min_samples": settings.QUALITY_ALERT_MIN_SAMPLES,
        "direction_accuracy_medium": settings.QUALITY_ALERT_DIRECTION_ACCURACY_MEDIUM,
        "direction_accuracy_high": settings.QUALITY_ALERT_DIRECTION_ACCURACY_HIGH,
        "brier_medium": settings.QUALITY_ALERT_BRIER_MEDIUM,
        "brier_high": settings.QUALITY_ALERT_BRIER_HIGH,
        "missing_calibration_rate_medium": settings.QUALITY_ALERT_MISSING_CALIBRATION_RATE_MEDIUM,
        "missing_calibration_rate_high": settings.QUALITY_ALERT_MISSING_CALIBRATION_RATE_HIGH,
        "report_errors_high": settings.QUALITY_ALERT_REPORT_ERRORS_HIGH,
    }


def _check_direction_accuracy(
    value: float | None,
    th_medium: float,
    th_high: float,
    n: int,
    scope: str,
    dimension: str | None,
    slice_key: str | None,
) -> dict | None:
    """Return high alert if value < th_high, else medium if < th_medium, else None."""
    if value is None:
        return None
    if value < th_high:
        return _alert("direction_accuracy_low", "high", scope, dimension, slice_key,
                      "direction_accuracy", value, th_high, n)
    if value < th_medium:
        return _alert("direction_accuracy_low", "medium", scope, dimension, slice_key,
                      "direction_accuracy", value, th_medium, n)
    return None


def _check_brier_score(
    value: float | None,
    th_medium: float,
    th_high: float,
    n: int,
    scope: str,
    dimension: str | None,
    slice_key: str | None,
) -> dict | None:
    """Return high alert if value > th_high, else medium if > th_medium, else None."""
    if value is None:
        return None
    if value > th_high:
        return _alert("brier_score_high", "high", scope, dimension, slice_key,
                      "brier_score", value, th_high, n)
    if value > th_medium:
        return _alert("brier_score_high", "medium", scope, dimension, slice_key,
                      "brier_score", value, th_medium, n)
    return None


def _check_missing_calibration_rate(
    value: float | None,
    th_medium: float,
    th_high: float,
    n: int,
    scope: str,
    dimension: str | None,
    slice_key: str | None,
) -> dict | None:
    """Return high if value > th_high, else medium if > th_medium, else None."""
    if value is None:
        return None
    if value > th_high:
        return _alert("missing_calibration_rate_high", "high", scope, dimension, slice_key,
                      "missing_calibration_rate", value, th_high, n)
    if value > th_medium:
        return _alert("missing_calibration_rate_high", "medium", scope, dimension, slice_key,
                      "missing_calibration_rate", value, th_medium, n)
    return None


def _alert(
    code: str,
    severity: str,
    scope: str,
    dimension: str | None,
    slice: str | None,
    metric: str,
    value: float | int,
    threshold: float | int,
    n: int,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "scope": scope,
        "dimension": dimension,
        "slice": slice,
        "metric": metric,
        "value": value,
        "threshold": threshold,
        "n": n,
    }


def _check_overview(report: dict[str, Any], th: dict[str, Any]) -> list[dict[str, Any]]:
    """Evaluate overview-level alerts."""
    ov = report.get("overview") or {}
    n = ov.get("total_resolved", 0)
    alerts: list[dict[str, Any]] = []

    acc = _check_direction_accuracy(
        ov.get("direction_accuracy"),
        th["direction_accuracy_medium"], th["direction_accuracy_high"],
        n, "overview", None, None,
    )
    if acc:
        alerts.append(acc)

    brier = _check_brier_score(
        ov.get("brier_score"),
        th["brier_medium"], th["brier_high"],
        ov.get("brier_n", 0), "overview", None, None,
    )
    if brier:
        alerts.append(brier)

    miss = _check_missing_calibration_rate(
        ov.get("missing_calibration_rate"),
        th["missing_calibration_rate_medium"], th["missing_calibration_rate_high"],
        n, "overview", None, None,
    )
    if miss:
        alerts.append(miss)

    # report_errors only at overview scope
    errors = report.get("report_errors") or []
    if len(errors) >= th["report_errors_high"]:
        alerts.append(_alert(
            "report_errors_high", "high", "overview", None, None,
            "report_errors_count", len(errors), th["report_errors_high"], n,
        ))

    return alerts


def _check_slice(
    dimension: str,
    slice_key: str,
    metrics: dict[str, Any],
    th: dict[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate slice-level alerts. Returns [] if n < min_samples."""
    n = metrics.get("n", 0)
    if n < th["min_samples"]:
        return []

    alerts: list[dict[str, Any]] = []

    acc = _check_direction_accuracy(
        metrics.get("direction_accuracy"),
        th["direction_accuracy_medium"], th["direction_accuracy_high"],
        n, "slice", dimension, slice_key,
    )
    if acc:
        alerts.append(acc)

    brier_block = metrics.get("brier") or {}
    brier = _check_brier_score(
        brier_block.get("brier_score"),
        th["brier_medium"], th["brier_high"],
        brier_block.get("n", 0), "slice", dimension, slice_key,
    )
    if brier:
        alerts.append(brier)

    miss = _check_missing_calibration_rate(
        metrics.get("missing_calibration_rate"),
        th["missing_calibration_rate_medium"], th["missing_calibration_rate_high"],
        n, "slice", dimension, slice_key,
    )
    if miss:
        alerts.append(miss)

    return alerts


def _check_dimension(
    dimension: str,
    slices: dict[str, dict[str, Any]],
    th: dict[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate one slice dimension."""
    alerts: list[dict[str, Any]] = []
    for slice_key, metrics in slices.items():
        alerts.extend(_check_slice(dimension, slice_key, metrics, th))
    return alerts


def evaluate_quality_alerts(
    report: dict[str, Any],
    thresholds: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate a quality metrics report and return a list of alerts.

    Pure: no I/O, no settings import. ``thresholds=None`` uses
    ``DEFAULT_THRESHOLDS``.
    """
    th = thresholds if thresholds is not None else DEFAULT_THRESHOLDS
    alerts: list[dict[str, Any]] = []
    alerts.extend(_check_overview(report, th))
    for dimension in _DIMENSIONS:
        alerts.extend(_check_dimension(dimension, report.get(dimension, {}), th))
    return alerts


def collect_insufficient_samples(
    report: dict[str, Any],
    thresholds: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return slices where n < min_samples. For diagnostics output only.

    Pure: no I/O. Does not produce alerts — low-sample slices are skipped
    by ``evaluate_quality_alerts`` and surfaced here only when the caller
    explicitly requests diagnostics.
    """
    th = thresholds if thresholds is not None else DEFAULT_THRESHOLDS
    min_n = th["min_samples"]
    result: list[dict[str, Any]] = []
    for dimension in _DIMENSIONS:
        for slice_key, metrics in (report.get(dimension) or {}).items():
            n = metrics.get("n", 0)
            if n < min_n:
                result.append({
                    "dimension": dimension,
                    "slice": slice_key,
                    "n": n,
                    "min_samples": min_n,
                })
    return result
