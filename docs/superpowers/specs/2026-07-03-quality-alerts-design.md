# Quality Alerts Design (LATER #3)

**Date:** 2026-07-03
**Spec gap:** LATER #3 - production quality alerting for quality report slices
**Status:** Design written for user review

---

## 1. Goal

Add production quality alerts on top of the existing quality metrics report.
The first version evaluates quality degradation in the report and exposes the
result through a pure service, an API endpoint, and a CLI.

This feature answers: "Is the decision quality system currently healthy, and
if not, which overall metric or slice is responsible?"

## 2. Non-Goals

- No webhook, Sentry, or log dispatch in version 1.
- No dynamic baseline or historical threshold learning.
- No frontend dashboard changes in this spec.
- No event store writes.
- No new rule registry or dataclass rule engine unless the rule set grows
  substantially later.

## 3. Architecture

Use the same shape as the existing quality report module: a deep pure module
behind a small interface, with API and CLI kept as thin adapters.

```text
backend/app/services/quality_alert_service.py       NEW pure alert evaluator
backend/app/services/quality_metrics_report_service.py  EXTEND report metrics
backend/app/core/config.py                          NEW quality alert settings
backend/app/api/routes/quality_metrics.py           NEW /quality-metrics/alerts
backend/scripts/check_quality_alerts.py             NEW CLI
```

The public service interface should stay small:

```python
def evaluate_quality_alerts(
    report: dict,
    thresholds: dict[str, Any] | None = None,
) -> list[dict]:
    ...
```

Internal implementation can use private helpers:

```python
def _check_overview(report: dict, thresholds: dict[str, Any]) -> list[dict]:
    ...

def _check_slice(
    dimension: str,
    slice_key: str,
    metrics: dict,
    thresholds: dict[str, Any],
) -> list[dict]:
    ...
```

The API and CLI should not duplicate alert rules. They both build a report,
construct thresholds from settings, and call the service.

## 4. Report Shape Changes

The existing `build_report(items, report_errors)` overview only contains
counts. Quality alerts need overall metrics without double-counting events
across multiple slice dimensions.

Extend `build_report` so it computes overall metrics directly from `items`,
using the same logic as `slice_metrics(items)`.

Recommended overview shape:

```python
"overview": {
    "total_resolved": int,
    "with_calibration": int,
    "missing_calibration": int,
    "missing_calibration_rate": float | None,
    "direction_accuracy": float | None,
    "brier_score": float | None,
    "brier_n": int,
}
```

Also extend each slice returned by `slice_metrics(items)` with real missing
calibration fields:

```python
{
    "n": int,
    "missing_calibration": int,
    "missing_calibration_rate": float | None,
    "direction_correct_true": int,
    "direction_correct_false": int,
    "direction_correct_none": int,
    "direction_accuracy": float | None,
    "brier": {
        "brier_score": float | None,
        "skill_score": float | None,
        "grade": str,
        "n": int,
    },
}
```

Do not derive slice missing calibration from `direction_correct_none / n`.
That value can mean missing outcome, missing direction, unscored direction, or
other non-calibration states. Missing calibration should be counted directly.

## 5. Threshold Configuration

Add settings in `backend/app/core/config.py`:

```python
# Quality alerts (LATER #3): slice-threshold alerting on quality reports.
QUALITY_ALERT_MIN_SAMPLES: int = 10
QUALITY_ALERT_DIRECTION_ACCURACY_MEDIUM: float = 0.60
QUALITY_ALERT_DIRECTION_ACCURACY_HIGH: float = 0.50
QUALITY_ALERT_BRIER_MEDIUM: float = 0.25
QUALITY_ALERT_BRIER_HIGH: float = 0.35
QUALITY_ALERT_MISSING_CALIBRATION_RATE_MEDIUM: float = 0.20
QUALITY_ALERT_MISSING_CALIBRATION_RATE_HIGH: float = 0.40
QUALITY_ALERT_REPORT_ERRORS_HIGH: int = 1
```

In `quality_alert_service.py`, keep a local fallback for tests and non-app
callers:

```python
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
```

Production adapters should pass thresholds explicitly:

```python
def thresholds_from_settings(settings: Any) -> dict[str, Any]:
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
```

`evaluate_quality_alerts(..., thresholds=None)` should use
`DEFAULT_THRESHOLDS`. It should not import global `settings`.

## 6. Alert Rules

Alerts use one object shape for overview and slice-level findings:

```python
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
```

Stable codes:

- `direction_accuracy_low`
- `brier_score_high`
- `missing_calibration_rate_high`
- `report_errors_high`

Overview rules:

| Code | Severity | Condition |
| --- | --- | --- |
| `direction_accuracy_low` | high | `overview.direction_accuracy < direction_accuracy_high` |
| `direction_accuracy_low` | medium | `overview.direction_accuracy < direction_accuracy_medium` |
| `brier_score_high` | high | `overview.brier_score > brier_high` |
| `brier_score_high` | medium | `overview.brier_score > brier_medium` |
| `missing_calibration_rate_high` | high | `overview.missing_calibration_rate > missing_calibration_rate_high` |
| `missing_calibration_rate_high` | medium | `overview.missing_calibration_rate > missing_calibration_rate_medium` |
| `report_errors_high` | high | `len(report.report_errors) >= report_errors_high` |

Slice rules apply only when `slice.n >= min_samples`:

| Code | Severity | Condition |
| --- | --- | --- |
| `direction_accuracy_low` | high | `slice.direction_accuracy < direction_accuracy_high` |
| `direction_accuracy_low` | medium | `slice.direction_accuracy < direction_accuracy_medium` |
| `brier_score_high` | high | `slice.brier.brier_score > brier_high` |
| `brier_score_high` | medium | `slice.brier.brier_score > brier_medium` |
| `missing_calibration_rate_high` | high | `slice.missing_calibration_rate > missing_calibration_rate_high` |
| `missing_calibration_rate_high` | medium | `slice.missing_calibration_rate > missing_calibration_rate_medium` |

For each metric, emit only the most severe matching alert. If a value breaches
both high and medium thresholds, emit the high alert only.

Metrics with `None` values do not alert.

## 7. Insufficient Samples

Low-sample slices are diagnostics, not default alerts.

Add a pure helper in `quality_alert_service.py`:

```python
def collect_insufficient_samples(
    report: dict,
    thresholds: dict[str, Any] | None = None,
) -> list[dict]:
    ...
```

It returns slices where `n < min_samples`:

```python
{
    "dimension": "by_edge_bucket",
    "slice": "20+",
    "n": 2,
    "min_samples": 10,
}
```

API and CLI include these diagnostics only when explicitly requested.

## 8. API Endpoint

Add a read-only endpoint in `backend/app/api/routes/quality_metrics.py`:

```python
@router.get("/quality-metrics/alerts")
async def quality_metrics_alerts(
    limit: int | None = Query(default=None, ge=1),
    sample: int | None = Query(default=None, ge=1),
    include_insufficient_samples: bool = Query(default=False),
) -> dict[str, Any]:
    ...
```

Behavior:

- Load resolved events with `list_resolved_events()`.
- Reuse the same `limit` and `sample` semantics as `/quality-metrics/report`.
- Use deterministic sampling with `random.Random(42)`.
- Build the report with `extract_metrics` and `build_report`.
- Convert settings with `thresholds_from_settings(settings)`.
- Call `evaluate_quality_alerts(report, thresholds)`.
- Return `alerts` and `alert_count`.
- When `include_insufficient_samples=true`, include
  `diagnostics.insufficient_samples`.
- Do not require auth. This endpoint is read-only and does not dispatch.
- Be resilient to malformed records by adding `report_errors` and continuing.

Response shape:

```python
{
    "alerts": [...],
    "alert_count": 3,
    "diagnostics": {
        "insufficient_samples": [...]
    },
}
```

`diagnostics` is omitted unless requested.

## 9. CLI

Add:

```text
backend/scripts/check_quality_alerts.py
```

Arguments:

```text
--limit N
--sample N
--json
--include-insufficient-samples
```

The CLI mirrors `report_quality_metrics.py`:

- collect resolved entries from the event store
- apply sample and limit
- extract metrics defensively
- build the report
- construct thresholds from settings
- evaluate alerts
- print text by default or JSON with `--json`
- return exit code `0` even when alerts are present

Text output must be ASCII-only for Windows consoles, logs, and CI.

Example:

```text
Quality Alerts Report - 3 alerts found

Config: min_samples=10, dir_acc=0.60/0.50, brier=0.25/0.35,
        miss_cal=0.20/0.40, report_errors>=1

[HIGH] 2
  [overview] brier_score_high
    value=0.38, threshold=0.35, n=42
  [slice:by_source_type/sports_event] direction_accuracy_low
    value=0.47, threshold=0.50, n=18

[MEDIUM] 1
  [overview] missing_calibration_rate_high
    value=0.24, threshold=0.20, n=42

[INSUFFICIENT] 3 slices skipped
  by_edge_bucket[20+]: n=2 (< 10)
  by_source_reliability_bucket[very_high]: n=1 (< 10)

Summary: 3 alerts (2 high, 1 medium). 0 alerts = healthy.
```

`[INSUFFICIENT]` appears only with `--include-insufficient-samples`.

## 10. Sharing And Duplication

Do not move event-store loading into the pure service. Loading entries is I/O
and belongs in API/CLI adapters.

Short duplicated `_collect_entries` adapter logic is acceptable between
`report_quality_metrics.py` and `check_quality_alerts.py` if no existing shared
script helper exists. Keep pure diagnostics shared in
`quality_alert_service.collect_insufficient_samples`.

## 11. Test Strategy

Add tests:

```text
backend/tests/test_quality_alert_service.py
backend/tests/test_quality_alerts_endpoint.py
backend/tests/test_check_quality_alerts_cli.py
```

### 11.1 Service Tests

`TestEvaluateQualityAlerts` should cover:

| Test | Input | Assertion |
| --- | --- | --- |
| `test_empty_report_no_alerts` | empty report | `alerts == []` |
| `test_overview_direction_accuracy_high` | overview `direction_accuracy=0.45` | high `direction_accuracy_low` |
| `test_overview_direction_accuracy_medium` | overview `direction_accuracy=0.55` | medium only |
| `test_overview_direction_accuracy_ok` | overview `direction_accuracy=0.75` | no alert |
| `test_overview_brier_high` | overview `brier_score=0.38` | high `brier_score_high` |
| `test_overview_brier_medium` | overview `brier_score=0.28` | medium |
| `test_overview_missing_calibration_high` | `missing_calibration_rate=0.45` | high |
| `test_overview_missing_calibration_medium` | `missing_calibration_rate=0.25` | medium |
| `test_overview_report_errors` | two report errors | high `report_errors_high` |
| `test_high_and_medium_dedup` | `direction_accuracy=0.45` | one high alert, no duplicate medium |
| `test_slice_below_min_samples_skipped` | slice `n=2`, `min_samples=10` | no alert |
| `test_slice_direction_accuracy_alert` | slice `direction_accuracy=0.50`, `n=15` | high slice alert |
| `test_slice_brier_alert` | slice `brier_score=0.30`, `n=20` | medium slice alert |
| `test_collect_insufficient_samples` | two low-sample slices | two diagnostics |
| `test_thresholds_from_settings` | mock settings | expected threshold dict |
| `test_default_thresholds_when_none` | `thresholds=None` | uses `DEFAULT_THRESHOLDS` |

Private helper tests for `_check_overview` and `_check_slice` are optional if
the public `evaluate_quality_alerts` tests already cover the behavior clearly.

### 11.2 API Endpoint Tests

`TestQualityAlertsEndpoint` should cover:

| Test | Scenario | Assertion |
| --- | --- | --- |
| `test_alerts_empty_store_returns_200` | empty store | `200`, no alerts |
| `test_alerts_returns_alerts_for_degraded_quality` | low-quality records | non-empty alerts, stable alert shape |
| `test_alerts_limit_truncates` | `?limit=2` | same limit semantics as report endpoint |
| `test_alerts_sample_reproducible` | `?sample=4` twice | identical results with seed `42` |
| `test_alerts_include_insufficient_samples` | diagnostics flag | diagnostics included |
| `test_alerts_invalid_limit_rejected` | `?limit=0` | `422` |
| `test_alerts_resilient_to_malformed_record` | malformed record | `200`, report error counted, no `500` |

### 11.3 CLI Tests

`TestCheckQualityAlertsCli` should cover:

| Test | Scenario | Assertion |
| --- | --- | --- |
| `test_cli_empty_store_exit_0` | empty store | exit `0`, output includes `0 alerts` |
| `test_cli_text_output_contains_alerts` | low-quality records | exit `0`, output includes `[HIGH]` and alert code |
| `test_cli_json_output_shape` | `--json` | JSON includes `alerts` and `alert_count` |
| `test_cli_include_insufficient_samples` | diagnostics flag | output includes `[INSUFFICIENT]` |
| `test_cli_no_emoji_in_output` | any text output | output contains no emoji characters |

### 11.4 Regression Tests

Run existing report/quality tests to ensure the `slice_metrics` and overview
shape additions do not break current consumers:

```powershell
python -m unittest tests.test_report_quality_metrics tests.test_quality_metrics_report tests.test_quality_metrics -v
```

## 12. Verification Checklist

Backend:

```powershell
python -m unittest tests.test_quality_alert_service tests.test_quality_alerts_endpoint tests.test_check_quality_alerts_cli -v
python -m unittest tests.test_report_quality_metrics tests.test_quality_metrics_report tests.test_quality_metrics -v
python -m compileall app\services\quality_alert_service.py app\services\quality_metrics_report_service.py app\api\routes\quality_metrics.py scripts\check_quality_alerts.py tests\test_quality_alert_service.py tests\test_quality_alerts_endpoint.py tests\test_check_quality_alerts_cli.py
python -m scripts.check_quality_alerts
git diff --check
```

Frontend side-effect check:

```powershell
npm.cmd run typecheck
```

If the project explicitly prefers direct TypeScript invocation in the local
environment, `npx tsc --noEmit` is an acceptable equivalent.

## 13. Implementation Order

1. Extend `quality_metrics_report_service.build_report` and `slice_metrics`.
2. Add `quality_alert_service.py` and service tests.
3. Add config settings.
4. Add `/quality-metrics/alerts` endpoint and endpoint tests.
5. Add `scripts/check_quality_alerts.py` and CLI tests.
6. Run regression and verification commands.

