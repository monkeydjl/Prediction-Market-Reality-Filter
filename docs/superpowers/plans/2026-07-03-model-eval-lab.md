# Model Evaluation Lab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pure read-only CLI that slices resolved events by model / analysis_quality / degraded_mode and reports Brier / ECE / direction accuracy / cost / guardrail rate per group.

**Architecture:** New pure-function service `model_eval_lab_service.py` reuses `extract_metrics` + `slice_metrics` + `calibration_deviation` from `quality_metrics_report_service` (calls them, does not copy), then appends model/cost/guardrail fields and a new `compute_ece` function. New CLI `model_eval_lab.py` is a thin shell that loads resolved events, calls the service, renders ASCII tables or JSON.

**Tech Stack:** Python 3.11+, stdlib argparse/json/random, existing `app.memory.event_store.list_resolved_events`, existing `app.services.quality_metrics_report_service` primitives.

## Global Constraints

- **Model source locked**: `llm_telemetry.model` only; missing → `"unknown"`. Never infer model from current settings (would pollute historical attribution).
- **ECE scale**: 0-100 probability points (consistent with `calibration_deviation`). Formula: `sum(bucket_n / total_n * abs(predicted_mean - actual_mean))`. Last bucket upper bound 101.0 to cover 100.0 with `<` comparison.
- **Cost semantics**: `extract_model_metrics` sets missing/non-finite/bool cost to `None`. `slice_model_metrics` `cost_n` counts only non-None; `cost_avg` is `None` when `cost_n == 0`. CLI shows `[n/a]` when `cost_n == 0`; JSON emits `cost_avg: null`.
- **Bool defense**: `bool` is `int` subclass in Python. `compute_ece` must explicitly exclude bool from `estimated_probability` / `actual_outcome` eligibility checks.
- **`--min-samples` scope**: only flags groups with `insufficient_samples: True` (does NOT drop them, does NOT filter overview). Overview always computed from ALL items.
- **by_degraded_mode grouping key**: `"degraded_mode_label"` ("degraded"/"normal"), not the bool field (avoids "True"/"False" string keys).
- **CLI ASCII-only**: use `==` for separators only. No emoji, no box-drawing chars (─/═/│), unlike `report_quality_metrics.py` which uses them.
- **CLI `--json`**: stdout pure JSON (no `[INFO]` prefix), `indent=2`, `ensure_ascii=False`.
- **Exit codes**: 0 success (report_errors present still 0); 2 config/param errors.
- **report_errors scope**: only two cases — record not a dict, or `extract_model_metrics` raised. No field-level validation warnings.
- **`--sample` + `--event-ids` together**: filter by event_ids first, then sample within filtered set.
- **Param validation**: `--sample N` / `--min-samples N` with N < 0 → exit 2. `--sample 0` legal (empty report). `--event-ids ","` (parses to empty list) → exit 2.
- **Do NOT modify** `quality_metrics_report_service.py` / `group_by` / `slice_metrics` — write a local `_group_by` in the new service instead.
- **Sampling seed**: `random.Random(42)` for reproducibility (same convention as `report_quality_metrics.py`).

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/services/model_eval_lab_service.py` (Create) | Pure functions: `extract_model_metrics`, `compute_ece`, `slice_model_metrics`, `_group_by`, `group_model_slices`, `build_model_eval_report` |
| `backend/scripts/model_eval_lab.py` (Create) | CLI: `_collect_entries`, `_render_text`, `_render_json`, `main` |
| `backend/tests/test_model_eval_lab_service.py` (Create) | Service unit tests |
| `backend/tests/test_model_eval_lab_cli.py` (Create) | CLI main() tests |

No existing files are modified.

---

## Task 1: Service — `extract_model_metrics` + `compute_ece`

**Files:**
- Create: `backend/app/services/model_eval_lab_service.py`
- Create: `backend/tests/test_model_eval_lab_service.py`

**Interfaces:**
- Consumes: `app.services.quality_metrics_report_service.extract_metrics(record: dict) -> dict`, `app.services.quality_metrics_report_service.safe_float(value) -> float | None`
- Produces: `extract_model_metrics(record: dict[str, Any]) -> dict[str, Any]`, `compute_ece(items: list[dict[str, Any]]) -> float | None`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_model_eval_lab_service.py`:

```python
"""Unit tests for model_eval_lab_service."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.services.model_eval_lab_service import (
    compute_ece,
    extract_model_metrics,
)


def _record(**overrides):
    """Minimal record with calibration + llm_telemetry populated."""
    rec = {
        "event_id": "evt-001",
        "source": {"type": "prediction_market"},
        "llm_telemetry": {
            "model": "gpt-4o-mini",
            "analysis_quality": "llm",
            "degraded_mode": False,
            "estimated_token_cost": 0.0189,
        },
        "actionable_recommendation": {"direction": "YES", "edge": 12.0},
        "outcome": {"status": "resolved", "actual_outcome": 100.0},
        "calibration": {
            "brier_score": 0.16,
            "estimated_probability": 72.0,
        },
        "source_reliability": {"overall_score": 0.65},
        "guardrail_fired": ["wide_spread"],
    }
    rec.update(overrides)
    return rec


class TestExtractModelMetrics(unittest.TestCase):
    def test_appends_model_degraded_cost_guardrail(self):
        item = extract_model_metrics(_record())
        self.assertEqual(item["model"], "gpt-4o-mini")
        self.assertFalse(item["degraded_mode"])
        self.assertEqual(item["degraded_mode_label"], "normal")
        self.assertEqual(item["estimated_token_cost"], 0.0189)
        self.assertEqual(item["guardrail_fired"], ["wide_spread"])

    def test_preserves_extract_metrics_fields(self):
        item = extract_model_metrics(_record())
        # Fields from extract_metrics still present
        self.assertEqual(item["event_id"], "evt-001")
        self.assertEqual(item["source_type"], "prediction_market")
        self.assertEqual(item["analysis_quality"], "llm")
        self.assertEqual(item["brier_score"], 0.16)
        self.assertEqual(item["estimated_probability"], 72.0)
        self.assertEqual(item["actual_outcome"], 100.0)

    def test_model_unknown_when_llm_telemetry_missing(self):
        rec = _record()
        del rec["llm_telemetry"]
        item = extract_model_metrics(rec)
        self.assertEqual(item["model"], "unknown")
        self.assertIsNone(item["estimated_token_cost"])
        self.assertFalse(item["degraded_mode"])
        self.assertEqual(item["degraded_mode_label"], "normal")
        self.assertEqual(item["guardrail_fired"], [])

    def test_model_unknown_when_llm_telemetry_not_dict(self):
        item = extract_model_metrics(_record(llm_telemetry="broken"))
        self.assertEqual(item["model"], "unknown")

    def test_cost_none_for_bool(self):
        item = extract_model_metrics(
            _record(llm_telemetry={"model": "x", "estimated_token_cost": True})
        )
        self.assertIsNone(item["estimated_token_cost"])

    def test_cost_none_for_nan(self):
        item = extract_model_metrics(
            _record(llm_telemetry={"model": "x", "estimated_token_cost": float("nan")})
        )
        self.assertIsNone(item["estimated_token_cost"])

    def test_cost_none_for_string(self):
        item = extract_model_metrics(
            _record(llm_telemetry={"model": "x", "estimated_token_cost": "0.02"})
        )
        self.assertIsNone(item["estimated_token_cost"])

    def test_degraded_mode_label_degraded(self):
        item = extract_model_metrics(
            _record(llm_telemetry={"model": "x", "degraded_mode": True})
        )
        self.assertTrue(item["degraded_mode"])
        self.assertEqual(item["degraded_mode_label"], "degraded")

    def test_guardrail_fired_non_list_becomes_empty(self):
        item = extract_model_metrics(_record(guardrail_fired="not a list"))
        self.assertEqual(item["guardrail_fired"], [])


class TestComputeEce(unittest.TestCase):
    def test_returns_none_when_no_eligible(self):
        # No estimated_probability
        items = [{"estimated_probability": None, "actual_outcome": 100.0}]
        self.assertIsNone(compute_ece(items))

    def test_returns_none_when_empty(self):
        self.assertIsNone(compute_ece([]))

    def test_returns_zero_when_perfectly_calibrated(self):
        # All in [60,80) bucket, predicted=actual=70
        items = [
            {"estimated_probability": 70.0, "actual_outcome": 70.0},
            {"estimated_probability": 70.0, "actual_outcome": 70.0},
        ]
        self.assertAlmostEqual(compute_ece(items), 0.0)

    def test_computed_value_single_bucket(self):
        # [0,20) bucket: predicted_mean=10, actual_mean=0 → ECE = 10
        items = [
            {"estimated_probability": 10.0, "actual_outcome": 0.0},
        ]
        self.assertAlmostEqual(compute_ece(items), 10.0)

    def test_excludes_bool_probability(self):
        # bool True is int subclass; must not count as 1.0
        items = [
            {"estimated_probability": True, "actual_outcome": 100.0},
        ]
        self.assertIsNone(compute_ece(items))

    def test_excludes_bool_actual_outcome(self):
        items = [
            {"estimated_probability": 50.0, "actual_outcome": True},
        ]
        self.assertIsNone(compute_ece(items))

    def test_covers_100_boundary(self):
        # estimated_probability == 100 must fall in last bucket [80,101)
        items = [
            {"estimated_probability": 100.0, "actual_outcome": 100.0},
        ]
        # Perfectly calibrated → ECE 0, but confirms 100 is eligible
        self.assertAlmostEqual(compute_ece(items), 0.0)

    def test_multi_bucket_weighted(self):
        # Bucket [0,20): 2 items, pred_mean=10, act_mean=0 → |10|
        # Bucket [80,101): 2 items, pred_mean=90, act_mean=100 → |10|
        # total=4, ECE = (2/4)*10 + (2/4)*10 = 10
        items = [
            {"estimated_probability": 10.0, "actual_outcome": 0.0},
            {"estimated_probability": 10.0, "actual_outcome": 0.0},
            {"estimated_probability": 90.0, "actual_outcome": 100.0},
            {"estimated_probability": 90.0, "actual_outcome": 100.0},
        ]
        self.assertAlmostEqual(compute_ece(items), 10.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_model_eval_lab_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.model_eval_lab_service'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/model_eval_lab_service.py`:

```python
"""Model evaluation lab — pure functions for slicing resolved events by
model / analysis_quality / degraded_mode (Plan §4.6).

Read-only: no LLM calls, no writes, no network. Reuses extract_metrics /
slice_metrics / calibration_deviation from quality_metrics_report_service
(calls them, does not copy logic) to preserve direction / Brier / edge
semantics. Appends model / cost / guardrail / ECE on top.
"""
from __future__ import annotations

import math
from typing import Any

from app.services.quality_metrics_report_service import (
    extract_metrics,
    safe_float,
)

# Probability buckets for ECE (0-100 scale). Last upper bound 101.0 so
# estimated_probability == 100.0 is included with `< hi`.
_PROB_BUCKETS = [(0.0, 20.0), (20.0, 40.0), (40.0, 60.0), (60.0, 80.0), (80.0, 101.0)]


def extract_model_metrics(record: dict[str, Any]) -> dict[str, Any]:
    """Extract model-eval metrics from a record.

    Calls existing extract_metrics (preserves direction/Brier/edge
    semantics), then appends model / degraded_mode / degraded_mode_label
    / estimated_token_cost / guardrail_fired.

    model source: llm_telemetry.model, missing -> "unknown".
    Never infer model from current settings (would pollute historical
    attribution).

    cost: safe_float filters None/NaN/inf/bool/non-numeric -> None.
    """
    item = extract_metrics(record)
    llm = record.get("llm_telemetry") or {}
    if not isinstance(llm, dict):
        llm = {}
    item["model"] = llm.get("model") or "unknown"
    item["degraded_mode"] = bool(llm.get("degraded_mode", False))
    item["degraded_mode_label"] = "degraded" if item["degraded_mode"] else "normal"
    item["estimated_token_cost"] = safe_float(llm.get("estimated_token_cost"))
    guardrails = record.get("guardrail_fired")
    item["guardrail_fired"] = guardrails if isinstance(guardrails, list) else []
    return item


def compute_ece(items: list[dict[str, Any]]) -> float | None:
    """Expected Calibration Error (0-100 scale).

    Formula: sum(bucket_n / total_n * abs(predicted_mean - actual_mean))
    Only counts records with both estimated_probability and actual_outcome
    as real numbers (bool excluded — bool is int subclass in Python).
    Returns None when no eligible records.

    Scale: 0-100 probability points (consistent with calibration_deviation).
    """
    eligible = [
        it for it in items
        if _is_real_number(it.get("estimated_probability"))
        and _is_real_number(it.get("actual_outcome"))
    ]
    total = len(eligible)
    if total == 0:
        return None
    ece = 0.0
    for lo, hi in _PROB_BUCKETS:
        bucket = [
            it for it in eligible
            if lo <= it["estimated_probability"] < hi
        ]
        if not bucket:
            continue
        bucket_n = len(bucket)
        predicted_mean = sum(it["estimated_probability"] for it in bucket) / bucket_n
        actual_mean = sum(it["actual_outcome"] for it in bucket) / bucket_n
        ece += (bucket_n / total) * abs(predicted_mean - actual_mean)
    return ece


def _is_real_number(value: Any) -> bool:
    """True only for int/float that is not bool and is finite."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_model_eval_lab_service.py -v`
Expected: PASS (all 18 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/model_eval_lab_service.py backend/tests/test_model_eval_lab_service.py
git commit -m "feat(model-eval-lab): extract_model_metrics + compute_ece"
```

---

## Task 2: Service — `slice_model_metrics` + `_group_by` + `group_model_slices` + `build_model_eval_report`

**Files:**
- Modify: `backend/app/services/model_eval_lab_service.py` (append functions)
- Modify: `backend/tests/test_model_eval_lab_service.py` (append test classes)

**Interfaces:**
- Consumes: `app.services.quality_metrics_report_service.slice_metrics(items: list) -> dict`, `app.services.quality_metrics_report_service.calibration_deviation(items: list) -> list[dict]`
- Produces: `slice_model_metrics(items) -> dict`, `group_model_slices(items, key, *, min_samples=0) -> dict[str, dict]`, `build_model_eval_report(items, report_errors, *, min_samples=0) -> dict`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_model_eval_lab_service.py` (before the `if __name__` block):

```python
from app.services.model_eval_lab_service import (
    build_model_eval_report,
    group_model_slices,
    slice_model_metrics,
)


def _item(**overrides):
    """Minimal extracted item for slice tests."""
    base = {
        "event_id": "evt-x",
        "source_type": "prediction_market",
        "analysis_quality": "llm",
        "edge_bucket": "10-20",
        "source_reliability_bucket": "high(0.6-0.8)",
        "direction_correct": True,
        "brier_score": 0.16,
        "estimated_probability": 72.0,
        "actual_outcome": 100.0,
        "model": "gpt-4o-mini",
        "degraded_mode": False,
        "degraded_mode_label": "normal",
        "estimated_token_cost": 0.02,
        "guardrail_fired": ["wide_spread"],
    }
    base.update(overrides)
    return base


class TestSliceModelMetrics(unittest.TestCase):
    def test_inherits_slice_metrics_fields(self):
        items = [_item(), _item(direction_correct=False)]
        s = slice_model_metrics(items)
        self.assertEqual(s["n"], 2)
        self.assertEqual(s["direction_correct_true"], 1)
        self.assertEqual(s["direction_correct_false"], 1)
        self.assertEqual(s["direction_accuracy"], 0.5)
        self.assertIn("brier", s)
        self.assertIn("missing_calibration_rate", s)

    def test_ece_computed(self):
        items = [_item(), _item()]
        s = slice_model_metrics(items)
        self.assertAlmostEqual(s["ece"], 0.0)  # perfectly calibrated

    def test_cost_aggregation(self):
        items = [_item(estimated_token_cost=0.02), _item(estimated_token_cost=0.04)]
        s = slice_model_metrics(items)
        self.assertEqual(s["cost_n"], 2)
        self.assertAlmostEqual(s["cost_total"], 0.06)
        self.assertAlmostEqual(s["cost_avg"], 0.03)

    def test_cost_avg_none_when_all_missing(self):
        items = [_item(estimated_token_cost=None), _item(estimated_token_cost=None)]
        s = slice_model_metrics(items)
        self.assertEqual(s["cost_n"], 0)
        self.assertIsNone(s["cost_avg"])
        self.assertEqual(s["cost_total"], 0.0)

    def test_cost_partial(self):
        items = [
            _item(estimated_token_cost=0.02),
            _item(estimated_token_cost=None),
        ]
        s = slice_model_metrics(items)
        self.assertEqual(s["cost_n"], 1)
        self.assertAlmostEqual(s["cost_avg"], 0.02)

    def test_guardrail_rate(self):
        items = [
            _item(guardrail_fired=["x"]),
            _item(guardrail_fired=[]),
        ]
        s = slice_model_metrics(items)
        self.assertEqual(s["guardrail_count"], 1)
        self.assertAlmostEqual(s["guardrail_rate"], 0.5)

    def test_degraded_rate(self):
        items = [
            _item(degraded_mode=True, degraded_mode_label="degraded"),
            _item(degraded_mode=False, degraded_mode_label="normal"),
        ]
        s = slice_model_metrics(items)
        self.assertEqual(s["degraded_count"], 1)
        self.assertAlmostEqual(s["degraded_rate"], 0.5)

    def test_empty_items(self):
        s = slice_model_metrics([])
        self.assertEqual(s["n"], 0)
        self.assertIsNone(s["cost_avg"])
        self.assertEqual(s["guardrail_rate"], 0.0)


class TestGroupModelSlices(unittest.TestCase):
    def test_groups_by_model(self):
        items = [
            _item(model="gpt-4o-mini"),
            _item(model="gpt-4o-mini"),
            _item(model="unknown"),
        ]
        result = group_model_slices(items, "model")
        self.assertEqual(set(result.keys()), {"gpt-4o-mini", "unknown"})
        self.assertEqual(result["gpt-4o-mini"]["n"], 2)
        self.assertEqual(result["unknown"]["n"], 1)

    def test_min_samples_flags_insufficient(self):
        items = [_item(model="rare")]
        result = group_model_slices(items, "model", min_samples=5)
        self.assertTrue(result["rare"]["insufficient_samples"])

    def test_min_samples_does_not_drop(self):
        items = [_item(model="rare"), _item(model="common"), _item(model="common")]
        result = group_model_slices(items, "model", min_samples=2)
        self.assertIn("rare", result)  # not dropped
        self.assertTrue(result["rare"]["insufficient_samples"])
        self.assertFalse(result["common"]["insufficient_samples"])

    def test_min_samples_zero_never_flags(self):
        items = [_item(model="rare")]
        result = group_model_slices(items, "model", min_samples=0)
        self.assertFalse(result["rare"]["insufficient_samples"])

    def test_groups_by_degraded_mode_label(self):
        items = [
            _item(degraded_mode_label="normal"),
            _item(degraded_mode_label="degraded"),
        ]
        result = group_model_slices(items, "degraded_mode_label")
        self.assertEqual(set(result.keys()), {"normal", "degraded"})


class TestBuildModelEvalReport(unittest.TestCase):
    def test_overview_from_all_items(self):
        items = [
            _item(model="a"),
            _item(model="b"),
        ]
        report = build_model_eval_report(items, [], min_samples=5)
        self.assertEqual(report["overview"]["n"], 2)

    def test_min_samples_does_not_filter_overview(self):
        items = [_item(model="a")]
        report = build_model_eval_report(items, [], min_samples=10)
        # Overview still shows all items
        self.assertEqual(report["overview"]["n"], 1)
        # But by_model group is flagged insufficient
        self.assertTrue(report["by_model"]["a"]["insufficient_samples"])

    def test_report_has_all_sections(self):
        report = build_model_eval_report([_item()], [])
        for key in ("overview", "by_model", "by_analysis_quality",
                    "by_degraded_mode", "calibration_deviation",
                    "report_errors", "min_samples"):
            self.assertIn(key, report)

    def test_by_degraded_mode_uses_label_keys(self):
        items = [
            _item(degraded_mode_label="normal"),
            _item(degraded_mode_label="degraded"),
        ]
        report = build_model_eval_report(items, [])
        self.assertEqual(set(report["by_degraded_mode"].keys()), {"normal", "degraded"})

    def test_report_errors_passed_through(self):
        errors = [{"event_id": "x", "error": "boom"}]
        report = build_model_eval_report([], errors)
        self.assertEqual(report["report_errors"], errors)

    def test_empty_items_overview_n_zero(self):
        report = build_model_eval_report([], [])
        self.assertEqual(report["overview"]["n"], 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_model_eval_lab_service.py -v -k "TestSliceModelMetrics or TestGroupModelSlices or TestBuildModelEvalReport"`
Expected: FAIL with `ImportError: cannot import name 'slice_model_metrics'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/app/services/model_eval_lab_service.py` (at end of file):

```python
from app.services.quality_metrics_report_service import (
    calibration_deviation,
    slice_metrics,
)


def slice_model_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Extended slice_metrics with ECE, cost, and guardrail aggregations.

    Inherits all fields from slice_metrics (n, direction_correct_*,
    brier, missing_calibration_rate, direction_accuracy), then adds:
        ece                  — float | None
        cost_total           — float (0.0 when no cost data)
        cost_avg             — float | None (None when cost_n == 0)
        cost_n               — int (count of non-None costs)
        guardrail_count      — int
        guardrail_rate       — float (0.0-1.0)
        degraded_count       — int
        degraded_rate        — float (0.0-1.0)
    """
    base = slice_metrics(items)
    cost_values = [
        it["estimated_token_cost"]
        for it in items
        if it.get("estimated_token_cost") is not None
    ]
    cost_total = sum(cost_values) if cost_values else 0.0
    cost_n = len(cost_values)
    cost_avg = cost_total / cost_n if cost_n else None
    guardrail_count = sum(1 for it in items if it.get("guardrail_fired"))
    guardrail_rate = guardrail_count / len(items) if items else 0.0
    degraded_count = sum(1 for it in items if it.get("degraded_mode"))
    degraded_rate = degraded_count / len(items) if items else 0.0
    return {
        **base,
        "ece": compute_ece(items),
        "cost_total": cost_total,
        "cost_avg": cost_avg,
        "cost_n": cost_n,
        "guardrail_count": guardrail_count,
        "guardrail_rate": guardrail_rate,
        "degraded_count": degraded_count,
        "degraded_rate": degraded_rate,
    }


def _group_by(
    items: list[dict[str, Any]],
    key: str,
) -> dict[str, list[dict[str, Any]]]:
    """Group items by a flat key on the item dict. Local helper — does
    not touch quality_metrics_report_service.group_by (which hardcodes
    slice_metrics and would drop cost/guardrail/ECE)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        k = str(it.get(key, "unknown"))
        groups.setdefault(k, []).append(it)
    return groups


def group_model_slices(
    items: list[dict[str, Any]],
    key: str,
    *,
    min_samples: int = 0,
) -> dict[str, dict[str, Any]]:
    """Group items by key, slice each group with slice_model_metrics.

    Groups with fewer than min_samples are still computed but flagged
    ``insufficient_samples: True`` (not dropped — caller decides).
    """
    groups = _group_by(items, key)
    result: dict[str, dict[str, Any]] = {}
    for k, group_items in groups.items():
        slice_data = slice_model_metrics(group_items)
        slice_data["insufficient_samples"] = len(group_items) < min_samples
        result[k] = slice_data
    return result


def build_model_eval_report(
    items: list[dict[str, Any]],
    report_errors: list[dict[str, Any]],
    *,
    min_samples: int = 0,
) -> dict[str, Any]:
    """Build the full model evaluation report.

    overview always computed from ALL items (min_samples does NOT filter
    overview). by_model / by_analysis_quality / by_degraded_mode use
    group_model_slices with min_samples flagging (not filtering).
    """
    overview = slice_model_metrics(items)
    return {
        "overview": overview,
        "by_model": group_model_slices(items, "model", min_samples=min_samples),
        "by_analysis_quality": group_model_slices(
            items, "analysis_quality", min_samples=min_samples,
        ),
        "by_degraded_mode": group_model_slices(
            items, "degraded_mode_label", min_samples=min_samples,
        ),
        "calibration_deviation": calibration_deviation(items),
        "report_errors": report_errors,
        "min_samples": min_samples,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_model_eval_lab_service.py -v`
Expected: PASS (all tests from Task 1 + Task 2)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/model_eval_lab_service.py backend/tests/test_model_eval_lab_service.py
git commit -m "feat(model-eval-lab): slice_model_metrics + group + build_report"
```

---

## Task 3: CLI — `model_eval_lab.py`

**Files:**
- Create: `backend/scripts/model_eval_lab.py`
- Create: `backend/tests/test_model_eval_lab_cli.py`

**Interfaces:**
- Consumes: `app.services.model_eval_lab_service.extract_model_metrics`, `app.services.model_eval_lab_service.build_model_eval_report`, `app.memory.event_store.list_resolved_events`
- Produces: `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_model_eval_lab_cli.py`:

```python
"""CLI tests for model_eval_lab."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from scripts.model_eval_lab import main


def _record(event_id="evt-001", model="gpt-4o-mini", **overrides):
    rec = {
        "event_id": event_id,
        "source": {"type": "prediction_market"},
        "llm_telemetry": {
            "model": model,
            "analysis_quality": "llm",
            "degraded_mode": False,
            "estimated_token_cost": 0.02,
        },
        "actionable_recommendation": {"direction": "YES", "edge": 12.0},
        "outcome": {"status": "resolved", "actual_outcome": 100.0},
        "calibration": {"brier_score": 0.16, "estimated_probability": 72.0},
        "source_reliability": {"overall_score": 0.65},
        "guardrail_fired": [],
    }
    rec.update(overrides)
    return rec


def _entry(event_id="evt-001", **overrides):
    return {"event_id": event_id, "record": _record(event_id, **overrides)}


class TestCliExitCodes(unittest.TestCase):
    def test_no_args_returns_0(self):
        with patch("scripts.model_eval_lab._collect_entries", return_value=([], [])):
            rc = main([])
        self.assertEqual(rc, 0)

    def test_sample_negative_returns_2(self):
        rc = main(["--sample", "-1"])
        self.assertEqual(rc, 2)

    def test_min_samples_negative_returns_2(self):
        with patch("scripts.model_eval_lab._collect_entries", return_value=([], [])):
            rc = main(["--min-samples", "-1"])
        self.assertEqual(rc, 2)

    def test_event_ids_empty_after_parse_returns_2(self):
        rc = main(["--event-ids", ","])
        self.assertEqual(rc, 2)

    def test_sample_zero_legal_empty_report(self):
        with patch("scripts.model_eval_lab._collect_entries", return_value=([], [])):
            rc = main(["--sample", "0"])
        self.assertEqual(rc, 0)


class TestCliOutput(unittest.TestCase):
    def test_json_mode_outputs_pure_json(self):
        entries = [_entry()]
        with patch("scripts.model_eval_lab._collect_entries", return_value=(entries, [])):
            from io import StringIO
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = main(["--json"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        # Must be pure JSON, no [INFO] prefix
        self.assertFalse(out.startswith("[INFO]"))
        data = json.loads(out)
        self.assertIn("overview", data)
        self.assertIn("by_model", data)

    def test_text_mode_has_overview_header(self):
        entries = [_entry()]
        with patch("scripts.model_eval_lab._collect_entries", return_value=(entries, [])):
            from io import StringIO
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = main([])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("== Overview", out)
        self.assertIn("[INFO]", out)

    def test_ascii_only_no_emoji_or_box_chars(self):
        entries = [_entry()]
        with patch("scripts.model_eval_lab._collect_entries", return_value=(entries, [])):
            from io import StringIO
            buf = StringIO()
            with patch("sys.stdout", buf):
                main([])
        out = buf.getvalue()
        # No box drawing chars or emoji
        for bad in ("─", "═", "│", "📊", "⚠️"):
            self.assertNotIn(bad, out, f"found forbidden char {bad!r}")

    def test_insufficient_flag_in_output(self):
        entries = [_entry(model="rare")]
        with patch("scripts.model_eval_lab._collect_entries", return_value=(entries, [])):
            from io import StringIO
            buf = StringIO()
            with patch("sys.stdout", buf):
                main(["--min-samples", "5"])
        out = buf.getvalue()
        self.assertIn("[INSUFFICIENT]", out)

    def test_cost_na_when_no_cost_data(self):
        # Record with no cost
        entries = [_entry(llm_telemetry={
            "model": "x", "analysis_quality": "llm",
            "degraded_mode": False, "estimated_token_cost": None,
        })]
        with patch("scripts.model_eval_lab._collect_entries", return_value=(entries, [])):
            from io import StringIO
            buf = StringIO()
            with patch("sys.stdout", buf):
                main(["--json"])
        out = buf.getvalue()
        data = json.loads(out)
        self.assertIsNone(data["overview"]["cost_avg"])


class TestCliCollectEntries(unittest.TestCase):
    def test_event_ids_filter_first_then_sample(self):
        from scripts.model_eval_lab import _collect_entries
        entries = [
            _entry("a"), _entry("b"), _entry("c"),
        ]
        with patch("app.memory.event_store.list_resolved_events", return_value=entries):
            items, errors = _collect_entries(sample=1, event_ids=["a", "b"])
        # Filtered to a+b, then sampled 1
        self.assertEqual(len(items), 1)
        self.assertIn(items[0]["event_id"], {"a", "b"})

    def test_event_ids_none_returns_all(self):
        from scripts.model_eval_lab import _collect_entries
        entries = [_entry("a"), _entry("b")]
        with patch("app.memory.event_store.list_resolved_events", return_value=entries):
            items, errors = _collect_entries(sample=None, event_ids=None)
        self.assertEqual(len(items), 2)

    def test_report_errors_for_non_dict_record(self):
        from scripts.model_eval_lab import _collect_entries
        # An entry whose record is not a dict
        entries = [{"event_id": "bad", "record": "not a dict"}]
        with patch("app.memory.event_store.list_resolved_events", return_value=entries):
            items, errors = _collect_entries(sample=None, event_ids=None)
        self.assertEqual(items, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("not a dict", errors[0]["error"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_model_eval_lab_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.model_eval_lab'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/scripts/model_eval_lab.py`:

```python
"""Model evaluation lab CLI (Plan §4.6).

Pure read-only slicing of resolved events by model / analysis_quality /
degraded_mode. Reports Brier / ECE / direction accuracy / cost / guardrail
rate per group. Distinct from report_quality_metrics.py (which slices by
source_type / analysis_quality / edge_bucket / source_reliability).

Usage:
    python -m scripts.model_eval_lab
    python -m scripts.model_eval_lab --sample 50
    python -m scripts.model_eval_lab --event-ids evt-001,evt-002
    python -m scripts.model_eval_lab --min-samples 10 --json
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
from typing import Any

# UTF-8 stdout for Windows GBK console safety.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, io.UnsupportedOperation):  # pragma: no cover
    pass

from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.services.model_eval_lab_service import (  # noqa: E402
    build_model_eval_report,
    extract_model_metrics,
)


def _print(text: str) -> None:
    """Print with UTF-8 stdout (Windows GBK safety)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


# ─── Collection ────────────────────────────────────────────────────────────

def _collect_entries(
    sample: int | None,
    event_ids: list[str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load resolved events from event_store, extract model metrics.

    Returns (items, report_errors).
    report_errors only records:
      - record is not a dict
      - extract_model_metrics raised
    Does NOT validate field types (degraded_mode not bool etc.).

    When event_ids is given, filters first; then applies sample within
    the filtered set.
    """
    from app.memory import event_store
    entries = event_store.list_resolved_events()
    if event_ids:
        id_set = set(event_ids)
        entries = [e for e in entries if e.get("event_id") in id_set]
    if sample is not None and sample < len(entries):
        rng = random.Random(42)
        entries = rng.sample(entries, sample)

    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for entry in entries:
        record = entry.get("record")
        if not isinstance(record, dict):
            errors.append({
                "event_id": entry.get("event_id", "?"),
                "error": "record missing or not a dict",
            })
            continue
        try:
            items.append(extract_model_metrics(record))
        except Exception as exc:
            errors.append({
                "event_id": record.get("event_id", "?"),
                "error": str(exc),
            })
    return items, errors


# ─── Rendering ─────────────────────────────────────────────────────────────

def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "  -  "
    return f"{v * 100:.2f}%"


def _fmt_cost(v: float | None, n: int) -> str:
    if n == 0:
        return "[n/a]"
    return f"${v:.4f}" if v is not None else "[n/a]"


def _fmt_brier(block: dict[str, Any]) -> str:
    b = block.get("brier_score")
    return "  -  " if b is None else f"{b:.4f}"


def _render_slice_table(
    title: str,
    slices: dict[str, dict[str, Any]],
    key_label: str,
) -> list[str]:
    """Render one slice dimension as an ASCII table."""
    lines: list[str] = [title, "=="]
    lines.append(
        f"  {key_label:<24} {'n':>4} {'brier':>7} {'ece':>6} "
        f"{'dir_acc':>10} {'cost_avg':>10} {'guard%':>8} {'degr%':>7}"
    )
    for k in sorted(slices.keys(), key=lambda x: -slices[x]["n"]):
        s = slices[k]
        brier_str = _fmt_brier(s["brier"])
        ece = s["ece"]
        ece_str = "  -  " if ece is None else f"{ece:.2f}"
        acc = s["direction_accuracy"]
        dc_true = s["direction_correct_true"]
        dc_false = s["direction_correct_false"]
        acc_str = "  -  " if acc is None else f"{acc:.4f} ({dc_true}/{dc_true + dc_false})"
        cost_str = _fmt_cost(s["cost_avg"], s["cost_n"])
        guard_str = _fmt_pct(s["guardrail_rate"])
        degr_str = _fmt_pct(s["degraded_rate"])
        suffix = "  [INSUFFICIENT]" if s.get("insufficient_samples") else ""
        lines.append(
            f"  {k:<24} {s['n']:>4} {brier_str:>7} {ece_str:>6} "
            f"{acc_str:>10} {cost_str:>10} {guard_str:>8} {degr_str:>7}{suffix}"
        )
    lines.append("")
    return lines


def _render_text(report: dict[str, Any]) -> str:
    """Render human-readable ASCII report."""
    lines: list[str] = []
    ov = report["overview"]
    lines.append(
        f"[INFO] Loaded {ov['n']} resolved events "
        f"({len(report['report_errors'])} report errors)"
    )
    lines.append(f"[INFO] Min samples for table display: {report['min_samples']}")
    lines.append("")

    # Overview
    lines.append(f"== Overview (all {ov['n']} events) ==")
    brier_str = _fmt_brier(ov["brier"])
    ece = ov["ece"]
    ece_str = "  -  " if ece is None else f"{ece:.2f}"
    acc = ov["direction_accuracy"]
    dc_true = ov["direction_correct_true"]
    dc_false = ov["direction_correct_false"]
    acc_str = "  -  " if acc is None else f"{acc:.4f} ({dc_true}/{dc_true + dc_false})"
    cost_str = _fmt_cost(ov["cost_avg"], ov["cost_n"])
    lines.append(
        f"  n={ov['n']}  brier={brier_str}  ece={ece_str}  "
        f"direction_acc={acc_str}"
    )
    lines.append(
        f"  cost_total=${ov['cost_total']:.4f}  cost_avg={cost_str} (n={ov['cost_n']})  "
        f"guardrail_rate={_fmt_pct(ov['guardrail_rate'])}  "
        f"degraded_rate={_fmt_pct(ov['degraded_rate'])}"
    )
    lines.append("")

    # Slices
    lines.extend(_render_slice_table("== By Model ==", report["by_model"], "model"))
    lines.extend(_render_slice_table(
        "== By Analysis Quality ==", report["by_analysis_quality"], "analysis_quality",
    ))
    lines.extend(_render_slice_table(
        "== By Degraded Mode ==", report["by_degraded_mode"], "mode",
    ))

    # Calibration deviation
    lines.append("== Calibration Deviation ==")
    lines.append(f"  {'bucket':<10} {'n':>4} {'pred_mean':>10} {'act_mean':>10} {'dev':>7}")
    for row in report["calibration_deviation"]:
        pred = "  -  " if row["predicted_mean"] is None else f"{row['predicted_mean']:.2f}"
        act = "  -  " if row["actual_mean"] is None else f"{row['actual_mean']:.2f}"
        dev = "  -  " if row["deviation"] is None else f"{row['deviation']:+.2f}"
        lines.append(f"  {row['bucket']:<10} {row['n']:>4} {pred:>10} {act:>10} {dev:>7}")
    lines.append("")

    # Report errors
    if report["report_errors"]:
        lines.append(f"== Report Errors ({len(report['report_errors'])}) ==")
        for err in report["report_errors"]:
            lines.append(f"  [WARN] {err.get('event_id', '?')}: {err['error']}")
        lines.append("")

    return "\n".join(lines)


# ─── CLI ───────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code.

    Exit codes: 0 success (report_errors present still 0); 2 param errors.
    """
    parser = argparse.ArgumentParser(
        prog="model_eval_lab",
        description=(
            "Model evaluation lab. Slices resolved events by model / "
            "analysis_quality / degraded_mode. Reports Brier / ECE / "
            "direction accuracy / cost / guardrail rate per group."
        ),
    )
    parser.add_argument(
        "--sample", type=int, default=None,
        help="Randomly sample N resolved events (reproducible seed=42)",
    )
    parser.add_argument(
        "--event-ids", type=str, default=None,
        help="Comma-separated event IDs to restrict analysis",
    )
    parser.add_argument(
        "--min-samples", type=int, default=5,
        help="Min samples for table display (insufficient groups flagged, "
             "not dropped). Default 5. Does NOT affect overview.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output JSON instead of human-readable text",
    )
    args = parser.parse_args(argv)

    # Param validation
    if args.sample is not None and args.sample < 0:
        print("Error: --sample must be >= 0", file=sys.stderr)
        return 2
    if args.min_samples < 0:
        print("Error: --min-samples must be >= 0", file=sys.stderr)
        return 2

    event_ids: list[str] | None = None
    if args.event_ids is not None:
        event_ids = [s.strip() for s in args.event_ids.split(",") if s.strip()]
        if not event_ids:
            print("Error: --event-ids parsed to empty list", file=sys.stderr)
            return 2

    try:
        items, report_errors = _collect_entries(args.sample, event_ids)
    except Exception as exc:
        print(f"Error: failed to load events: {exc}", file=sys.stderr)
        return 2

    try:
        report = build_model_eval_report(items, report_errors, min_samples=args.min_samples)
    except Exception as exc:
        print(f"Error: report build failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        _print(json.dumps(report, indent=2, default=str, ensure_ascii=False))
    else:
        _print(_render_text(report))

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_model_eval_lab_cli.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/model_eval_lab.py backend/tests/test_model_eval_lab_cli.py
git commit -m "feat(model-eval-lab): CLI with --sample/--event-ids/--min-samples/--json"
```

---

## Task 4: CLI end-to-end smoke test + full regression run

**Files:**
- No new files. This task validates the CLI works end-to-end and no regression.

**Interfaces:**
- Consumes: All previously created files

- [ ] **Step 1: Run service tests**

Run: `python -m pytest backend/tests/test_model_eval_lab_service.py -v`
Expected: PASS (all tests)

- [ ] **Step 2: Run CLI tests**

Run: `python -m pytest backend/tests/test_model_eval_lab_cli.py -v`
Expected: PASS (all tests)

- [ ] **Step 3: Run CLI with no args (smoke test against real event_store)**

Run: `python -m scripts.model_eval_lab`
Expected: Either "[INFO] Loaded N resolved events" with overview + slices, or "[INFO] Loaded 0 resolved events" with empty overview. Exit code 0.

- [ ] **Step 4: Run CLI with --json**

Run: `python -m scripts.model_eval_lab --json`
Expected: Valid JSON with overview / by_model / by_analysis_quality / by_degraded_mode / calibration_deviation / report_errors / min_samples keys. Exit code 0.

- [ ] **Step 5: Run full backend regression**

Run: `python -m pytest backend/tests/ -q`
Expected: PASS (no regressions; new tests added, existing tests untouched)

- [ ] **Step 6: Commit (if any cleanup needed)**

Only commit if smoke test surfaced fixes. Otherwise no commit needed.

```bash
git status  # confirm clean
```

---

## Self-Review

### Spec coverage

| Spec section | Task | Status |
|---|---|---|
| §3.2 `extract_model_metrics` | Task 1 | ✅ |
| §3.3 `compute_ece` | Task 1 | ✅ |
| §3.4 `slice_model_metrics` | Task 2 | ✅ |
| §3.5 `_group_by` | Task 2 | ✅ |
| §3.6 `group_model_slices` | Task 2 | ✅ |
| §3.7 `build_model_eval_report` | Task 2 | ✅ |
| §4.1 CLI file | Task 3 | ✅ |
| §4.2 params (`--sample`/`--event-ids`/`--min-samples`/`--json`) | Task 3 | ✅ |
| §4.3 `_collect_entries` | Task 3 | ✅ |
| §4.4 ASCII output | Task 3 | ✅ |
| §4.5 JSON mode | Task 3 | ✅ |
| §4.6 Exit codes | Task 3 | ✅ |
| §4.7 report_errors scope | Task 3 | ✅ |
| §6.1 service tests | Task 1 + 2 | ✅ |
| §6.2 CLI tests | Task 3 | ✅ |
| Param validation (N<0 / sample 0 / event_ids empty) | Task 3 | ✅ |
| ASCII-only (no emoji/box chars) | Task 3 | ✅ |
| Cost `[n/a]` when cost_n==0 | Task 3 | ✅ |
| `[INSUFFICIENT]` flag | Task 3 | ✅ |

No gaps found.

### Placeholder scan

No TBD/TODO/placeholder. All code blocks complete. All test functions have real assertions.

### Type consistency

- `extract_model_metrics(record: dict[str, Any]) -> dict[str, Any]` — Task 1 defines, Task 3 consumes ✅
- `compute_ece(items: list[dict[str, Any]]) -> float | None` — Task 1 defines, Task 2's `slice_model_metrics` consumes ✅
- `slice_model_metrics(items: list[dict[str, Any]]) -> dict[str, Any]` — Task 2 defines, Task 2's `group_model_slices` + `build_model_eval_report` consume ✅
- `group_model_slices(items, key, *, min_samples=0) -> dict[str, dict[str, Any]]` — Task 2 defines, Task 2's `build_model_eval_report` + Task 3 consume ✅
- `build_model_eval_report(items, report_errors, *, min_samples=0) -> dict[str, Any]` — Task 2 defines, Task 3 consumes ✅
- `_collect_entries(sample, event_ids) -> tuple[list, list]` — Task 3 defines and consumes ✅
- `main(argv: list[str] | None = None) -> int` — Task 3 defines, tests consume ✅

All consistent.
