"""Quality diff service (LATER #1 — rule change comparison).

Pure functions that build a diff report comparing the same batch of
resolved events under two configurations (config A vs config B). The
service receives already-replayed records from the CLI — it does NOT
perform replay, does NOT read event_store, does NOT mutate global
settings. This keeps it unit-testable without I/O.

Mirrors the pattern of ``quality_metrics_report_service``: same reuse
of canonical pure functions (``extract_metrics`` / ``slice_metrics`` /
``compute_direction_correct``), same slice dimensions, same Brier
aggregation.

Input contract: each record in records_a / records_b MUST contain
``event_id``, ``outcome``, ``source``, ``llm_telemetry``,
``actionable_recommendation``, ``calibration``, ``source_reliability``
— i.e. everything ``extract_metrics`` reads. The CLI is responsible
for injecting ``outcome`` back onto the replayed record (replay_record
strips overlays but preserves these fields; stating it explicitly so
future implementers don't drop outcome, which direction_correct needs).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.services.quality_metrics_report_service import extract_metrics, slice_metrics

# Direction labels for the transition matrix. Kept as a constant so the
# matrix can grow (e.g. add "UNKNOWN") without touching every callsite.
# Records with a direction not in this tuple fall into the "OTHER" bucket.
DIRECTION_LABELS: tuple[str, ...] = ("YES", "NO", "WAIT", "AVOID")
_MATRIX_KEYS = DIRECTION_LABELS + ("OTHER",)


def _effective_direction(record: dict[str, Any]) -> str | None:
    """Return the effective direction of a record.

    Fallback chain (mirrors analyze_feature_flag_impact._effective_direction):
      1. ``final_displayed_direction`` — set by merge_quality_overlays when
         at least one overlay ran. all_off replay strips this.
      2. ``actionable_recommendation.direction`` — pre-overlay direction
         from LLM analysis.

    Returns None when neither field yields a direction in DIRECTION_LABELS.
    Records with None direction are excluded from the direction matrix
    AND from n_direction_compared, so the change-rate denominator stays
    correct. Records with an unrecognized direction (e.g. "SKIP") also
    return None here — they don't go into the OTHER bucket of the matrix
    (OTHER is only for when one side has a known direction and the other
    has an unknown one, which can't happen if both go through this fn).
    """
    dir_val = record.get("final_displayed_direction")
    if dir_val in DIRECTION_LABELS:
        return dir_val
    rec = record.get("actionable_recommendation")
    if isinstance(rec, dict):
        rec_dir = rec.get("direction")
        if rec_dir in DIRECTION_LABELS:
            return rec_dir
    return None


def _empty_slice() -> dict[str, Any]:
    """Slice shape matching quality_metrics_report_service.slice_metrics
    output for an empty group — used when one side has 0 events in a slice.
    """
    return {
        "n": 0,
        "direction_correct_true": 0,
        "direction_correct_false": 0,
        "direction_correct_none": 0,
        "direction_accuracy": None,
        "brier": {"brier_score": None, "skill_score": None, "grade": "no_data", "n": 0},
    }


def _slice_delta(slice_a: dict[str, Any], slice_b: dict[str, Any]) -> dict[str, Any]:
    """Compute delta between two slices (b - a)."""
    acc_a = slice_a.get("direction_accuracy")
    acc_b = slice_b.get("direction_accuracy")
    brier_a = slice_a.get("brier", {}).get("brier_score")
    brier_b = slice_b.get("brier", {}).get("brier_score")
    return {
        "n": {
            "a": slice_a.get("n", 0),
            "b": slice_b.get("n", 0),
            "delta": slice_b.get("n", 0) - slice_a.get("n", 0),
        },
        "direction_accuracy": round(acc_b - acc_a, 4)
            if acc_a is not None and acc_b is not None else None,
        "brier_score": round(brier_b - brier_a, 4)
            if brier_a is not None and brier_b is not None else None,
    }


def _build_slice_diff(
    items_a: list[dict[str, Any]],
    items_b: list[dict[str, Any]],
    dimension: str,
) -> dict[str, Any]:
    """Build the slice diff for one dimension (source_type / edge_bucket / etc.).

    Groups items_a and items_b by item[dimension], computes slice_metrics
    per group, then aligns by slice key and computes delta.
    """
    groups_a: dict[str, list[dict[str, Any]]] = defaultdict(list)
    groups_b: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items_a:
        groups_a[str(item.get(dimension, "<missing>"))].append(item)
    for item in items_b:
        groups_b[str(item.get(dimension, "<missing>"))].append(item)

    all_keys = set(groups_a.keys()) | set(groups_b.keys())
    out: dict[str, Any] = {}
    for key in all_keys:
        slice_a = slice_metrics(groups_a[key]) if groups_a[key] else _empty_slice()
        slice_b = slice_metrics(groups_b[key]) if groups_b[key] else _empty_slice()
        out[key] = {"a": slice_a, "b": slice_b, "delta": _slice_delta(slice_a, slice_b)}
    return out


def _regression_summary(slice_diff: dict[str, Any]) -> dict[str, Any]:
    """Tally accuracy/brier regressions and improvements across all slices.

    Brier lower is better, so Δbrier > 0 = regression (got worse).
    """
    acc_reg = 0
    acc_imp = 0
    brier_reg = 0
    brier_imp = 0
    largest_acc_drop: dict[str, Any] | None = None
    largest_brier_drop: dict[str, Any] | None = None

    for dim_name, slices in slice_diff.items():
        for slice_key, sl in slices.items():
            delta = sl["delta"]
            acc = delta.get("direction_accuracy")
            if acc is not None:
                if acc < 0:
                    acc_reg += 1
                    if largest_acc_drop is None or acc < largest_acc_drop["delta"]:
                        largest_acc_drop = {"slice": f"{dim_name}[{slice_key}]", "delta": acc}
                elif acc > 0:
                    acc_imp += 1
            brier = delta.get("brier_score")
            if brier is not None:
                if brier > 0:
                    brier_reg += 1
                    if largest_brier_drop is None or brier > largest_brier_drop["delta"]:
                        largest_brier_drop = {"slice": f"{dim_name}[{slice_key}]", "delta": brier}
                elif brier < 0:
                    brier_imp += 1

    return {
        "accuracy_regressions": acc_reg,
        "accuracy_improvements": acc_imp,
        "brier_regressions": brier_reg,
        "brier_improvements": brier_imp,
        "largest_accuracy_drop": largest_acc_drop,
        "largest_brier_drop": largest_brier_drop,
    }


def build_diff(
    records_a: list[dict[str, Any]],
    records_b: list[dict[str, Any]],
    config_meta_a: dict[str, Any],
    config_meta_b: dict[str, Any],
) -> dict[str, Any]:
    """Build a diff report comparing records_a (config A) vs records_b (config B).

    Pure function: no I/O, no settings mutation. Aligns by event_id
    internally (not list order). Single-event extraction failures go to
    diff_errors with a stage tag (resilient, no abort).

    See module docstring for the input contract (each record must contain
    event_id + outcome + the overlay fields extract_metrics reads).
    """
    # Align by event_id
    by_id_a = {r.get("event_id"): r for r in records_a if r.get("event_id")}
    by_id_b = {r.get("event_id"): r for r in records_b if r.get("event_id")}
    common_ids = sorted(set(by_id_a.keys()) & set(by_id_b.keys()))
    n_missing_a = len(by_id_b) - len(common_ids)  # in B but not A
    n_missing_b = len(by_id_a) - len(common_ids)  # in A but not B

    # Extract metrics per side, collect errors
    items_a: list[dict[str, Any]] = []
    items_b: list[dict[str, Any]] = []
    diff_errors: list[dict[str, str]] = []

    for eid in common_ids:
        for side, source, items in (("a", by_id_a, items_a), ("b", by_id_b, items_b)):
            try:
                items.append(extract_metrics(source[eid]))
            except Exception as exc:
                diff_errors.append({
                    "event_id": eid,
                    "side": side,
                    "stage": "extract_metrics",
                    "error": str(exc),
                })

    # Direction matrix
    matrix: dict[str, dict[str, int]] = {
        a: {b: 0 for b in _MATRIX_KEYS} for a in _MATRIX_KEYS
    }
    n_direction_compared = 0
    n_scored_compared = 0
    direction_changed = 0

    for eid in common_ids:
        dir_a = _effective_direction(by_id_a[eid])
        dir_b = _effective_direction(by_id_b[eid])
        if dir_a is not None and dir_b is not None:
            matrix[dir_a][dir_b] += 1
            n_direction_compared += 1
            if dir_a != dir_b:
                direction_changed += 1
            # scored = both sides have resolved outcome
            outcome_a = (by_id_a[eid].get("outcome") or {}).get("status", "resolved") == "resolved"
            outcome_b = (by_id_b[eid].get("outcome") or {}).get("status", "resolved") == "resolved"
            if outcome_a and outcome_b:
                n_scored_compared += 1

    # Top transitions (non-diagonal)
    transitions = []
    for a in _MATRIX_KEYS:
        for b in _MATRIX_KEYS:
            if a != b and matrix[a][b] > 0:
                transitions.append({"from": a, "to": b, "n": matrix[a][b]})
    transitions.sort(key=lambda t: t["n"], reverse=True)
    for t in transitions:
        t["pct"] = round(t["n"] / n_direction_compared * 100.0, 1) if n_direction_compared else 0.0

    # Slice diff
    slice_diff = {
        "by_source_type": _build_slice_diff(items_a, items_b, "source_type"),
        "by_analysis_quality": _build_slice_diff(items_a, items_b, "analysis_quality"),
        "by_edge_bucket": _build_slice_diff(items_a, items_b, "edge_bucket"),
        "by_source_reliability_bucket": _build_slice_diff(items_a, items_b, "source_reliability_bucket"),
    }

    change_rate = round(direction_changed / n_direction_compared, 4) if n_direction_compared else None
    n_total = len(common_ids) + n_missing_a + n_missing_b

    return {
        "config_a": config_meta_a,
        "config_b": config_meta_b,
        "overview": {
            "n_total": n_total,
            "n_direction_compared": n_direction_compared,
            "n_scored_compared": n_scored_compared,
            "n_missing_a": n_missing_a,
            "n_missing_b": n_missing_b,
            "direction_changed": direction_changed,
            "change_rate": change_rate,
        },
        "direction_matrix": matrix,
        "top_transitions": transitions,
        "slice_diff": slice_diff,
        "regression_summary": _regression_summary(slice_diff),
        "diff_errors": diff_errors,
    }
