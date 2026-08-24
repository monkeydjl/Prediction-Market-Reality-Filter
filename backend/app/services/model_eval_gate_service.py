"""Model-eval release gate — 发布门槛 (Q1).

Pure functions that turn a ``build_model_eval_report`` into a PASS/FAIL
verdict. No I/O, no dispatch, no settings import: the caller builds the report,
constructs thresholds via ``gate_thresholds_from_settings(settings)``, and
passes both in. Mirrors ``quality_alert_service``'s shape on purpose.

**The one rule that makes a gate different from an alert:** a missing
measurement must fail. ``quality_alert_service`` documents "metrics with None
values do not alert", which is right for alerting — you do not page someone
because a slice has no data. It is wrong for a gate: a report where Brier, ECE
and direction accuracy are all ``None`` describes an eval that measured
nothing, and letting it through would certify a model on the strength of having
no evidence against it. The same ``None`` therefore means "no alert" in one
consumer and "no pass" in the other, and each check records ``reason`` so the
distinction is visible in the output rather than implied by a threshold.

Every check is reported even when an earlier one already failed — including
when ``min_samples`` fails. Short-circuiting would hide the metrics an operator
needs in order to decide whether to widen the eval set or fix the model.

**Each metric is held to ``min_samples`` on its own denominator**, not on the
slice size. ``overview.n`` counts events; ``overview.ece_n``,
``overview.brier.n`` and ``direction_correct_true + false`` count the events
that carry each metric, and on the live store those diverge by a factor of
seven. Checking only ``overview.n`` would let the gate rule on six events while
reporting that it had forty-five.

Off by default: nothing calls this unless ``scripts/model_eval_lab --gate`` is
passed, and ``require_eval_set`` defaults to False so an unpinned report is
gradeable (just not certifiable as a fixed set).
"""
from __future__ import annotations

from typing import Any

DEFAULT_GATE_THRESHOLDS: dict[str, Any] = {
    "min_samples": 20,
    # 0.25 is the Brier of a 50/50 call: a model that cannot beat a coin flip
    # does not ship.
    "brier_max": 0.25,
    # ECE is on the 0-100 probability-point scale (see compute_ece).
    "ece_max": 15.0,
    "direction_accuracy_min": 0.55,
    "degraded_rate_max": 0.20,
    "report_errors_max": 0,
    "require_eval_set": False,
}


def gate_thresholds_from_settings(settings: Any) -> dict[str, Any]:
    """Adapter: extract release-gate thresholds from a Settings object."""
    return {
        "min_samples": settings.MODEL_EVAL_GATE_MIN_SAMPLES,
        "brier_max": settings.MODEL_EVAL_GATE_BRIER_MAX,
        "ece_max": settings.MODEL_EVAL_GATE_ECE_MAX,
        "direction_accuracy_min": settings.MODEL_EVAL_GATE_DIRECTION_ACCURACY_MIN,
        "degraded_rate_max": settings.MODEL_EVAL_GATE_DEGRADED_RATE_MAX,
        "report_errors_max": settings.MODEL_EVAL_GATE_REPORT_ERRORS_MAX,
        "require_eval_set": settings.MODEL_EVAL_GATE_REQUIRE_EVAL_SET,
    }


def _check(
    name: str,
    metric: str,
    value: Any,
    threshold: Any,
    comparison: str,
    *,
    reason: str | None = None,
    sample_count: int | None = None,
    min_samples: int | None = None,
) -> dict[str, Any]:
    """One gate check. ``value is None`` fails with a stated reason.

    Guarding on None *before* comparing also keeps the comparison from raising
    a TypeError on ``None <= 0.25``.

    ``sample_count`` is the count behind *this* metric, which is not the slice
    size: on the live store the ``llm`` slice held 45 events and its ECE was
    computed from 6. A gate that checked ``overview.n >= min_samples`` and then
    compared that ECE to a threshold would be ruling on six events while
    believing it had forty-five, so each metric is held to ``min_samples`` on
    its own denominator.
    """
    if value is None:
        return {
            "name": name, "metric": metric, "value": None,
            "threshold": threshold, "comparison": comparison,
            "sample_count": sample_count, "passed": False,
            "reason": reason or "no measurement (a gate cannot pass on absent evidence)",
        }
    if (
        sample_count is not None
        and min_samples is not None
        and sample_count < min_samples
    ):
        return {
            "name": name, "metric": metric, "value": value,
            "threshold": threshold, "comparison": comparison,
            "sample_count": sample_count, "passed": False,
            "reason": (
                f"only {sample_count} event(s) carry this metric "
                f"(min_samples={min_samples})"
            ),
        }
    if comparison == ">=":
        passed = value >= threshold
    elif comparison == "<=":
        passed = value <= threshold
    else:  # pragma: no cover - only the two comparisons above are constructed
        raise ValueError(f"unsupported comparison: {comparison!r}")
    return {
        "name": name, "metric": metric, "value": value,
        "threshold": threshold, "comparison": comparison,
        "sample_count": sample_count, "passed": passed, "reason": None,
    }


def _eval_set_checks(
    report: dict[str, Any], th: dict[str, Any],
) -> list[dict[str, Any]]:
    """Checks about *which* events were graded, not how well.

    Two separate concerns: whether a pinned set is required at all
    (``require_eval_set``, default off), and whether the pinned set was graded
    whole (``eval_set_complete``). A report with no ``eval_set`` block skips the
    completeness check rather than passing it — there is nothing to be complete
    about, and a vacuous pass is the failure mode this whole module exists to
    avoid.
    """
    eval_set = report.get("eval_set")
    if not isinstance(eval_set, dict):
        if th["require_eval_set"]:
            return [{
                "name": "eval_set_required", "metric": "eval_set",
                "value": None, "threshold": "pinned manifest",
                "comparison": "==", "sample_count": None, "passed": False,
                "reason": "report was not built against a pinned eval set",
            }]
        return []

    event_count = eval_set.get("event_count") or 0
    matched = eval_set.get("matched") or 0
    missing = eval_set.get("missing_event_ids") or []
    drifted = eval_set.get("drifted_event_ids") or []
    reason = None
    if missing:
        reason = f"{len(missing)} pinned event(s) missing from the store"
    if drifted:
        drift_note = f"{len(drifted)} pinned event(s) re-graded since minting"
        reason = f"{reason}; {drift_note}" if reason else drift_note
    return [{
        "name": "eval_set_complete", "metric": "eval_set.matched",
        "value": matched, "threshold": event_count, "comparison": "==",
        "sample_count": matched,
        "passed": matched == event_count and not drifted and event_count > 0,
        "reason": reason or (None if event_count > 0 else "pinned set is empty"),
    }]


def evaluate_release_gate(
    report: dict[str, Any],
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate a model-eval report against release thresholds.

    Returns ``{"passed", "checks", "failed", "thresholds"}``. ``passed`` is the
    conjunction of every check, so an empty check list cannot happen: the
    sample-count, Brier, ECE, accuracy, degraded-rate and report-error checks
    are always constructed.
    """
    th = dict(thresholds) if thresholds is not None else dict(DEFAULT_GATE_THRESHOLDS)
    overview = report.get("overview")
    if not isinstance(overview, dict):
        overview = {}
    brier_block = overview.get("brier")
    if not isinstance(brier_block, dict):
        brier_block = {}
    errors = report.get("report_errors")
    error_count = len(errors) if isinstance(errors, list) else 0
    min_samples = th["min_samples"]
    directional = (
        (overview.get("direction_correct_true") or 0)
        + (overview.get("direction_correct_false") or 0)
    )

    checks = [
        _check(
            "min_samples", "overview.n", overview.get("n"),
            min_samples, ">=",
            reason="report has no overview",
        ),
        _check(
            "brier_max", "overview.brier.brier_score",
            brier_block.get("brier_score"), th["brier_max"], "<=",
            reason="no gradeable calibration in the eval set",
            sample_count=brier_block.get("n") or 0, min_samples=min_samples,
        ),
        _check(
            "ece_max", "overview.ece", overview.get("ece"), th["ece_max"], "<=",
            sample_count=overview.get("ece_n") or 0, min_samples=min_samples,
        ),
        _check(
            "direction_accuracy_min", "overview.direction_accuracy",
            overview.get("direction_accuracy"), th["direction_accuracy_min"], ">=",
            reason="no directional recommendation in the eval set",
            sample_count=directional, min_samples=min_samples,
        ),
        _check(
            "degraded_rate_max", "overview.degraded_rate",
            overview.get("degraded_rate"), th["degraded_rate_max"], "<=",
            sample_count=overview.get("n") or 0, min_samples=min_samples,
        ),
        _check(
            "report_errors_max", "report_errors", error_count,
            th["report_errors_max"], "<=",
        ),
        *_eval_set_checks(report, th),
    ]

    return {
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
        "failed": [c["name"] for c in checks if not c["passed"]],
        "thresholds": th,
    }
