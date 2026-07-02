# Quality Diff Report Design (LATER #1)

**Date:** 2026-07-03
**Spec gap:** LATER phase #1 — "建立模型/规则变更前后对比流程" — ❌ 未实现
**Priority:** LATER (用户已批准启动)
**Status:** Design pending user review

---

## 1. Goal

Provide a CLI tool that compares the decision quality of the same batch of
resolved events under **two different configurations** (config A vs config B),
quantifying whether a rule/flag/threshold change makes things better or
worse. Output: direction transition matrix + sliced metric deltas +
regression summary.

This closes the "rule change comparison" half of LATER #1. The "model/LLM
change comparison" half (which needs frozen snapshots) is explicitly out of
scope for this spec — it will be a separate spec once snapshot storage
exists.

## 2. Non-Goals

- **Model/LLM/prompt change comparison.** `replay_record` does not re-run
  LLM; it only re-runs deterministic overlays on frozen `legacy_analysis`.
  Comparing two LLM versions requires either live re-analysis (expensive,
  non-deterministic) or pre-stored snapshots. Neither is built here.
- **API endpoint + frontend panel.** Service layer is designed to be
  reusable by a future `/quality-metrics/diff` route, but the route itself
  is not built in this spec.
- **Per-phase diff mode.** `--per-phase` + `--diff-*` is an illegal
  combination (exit 2). Per-phase marginal impact already has its own
  output format; mixing with diff would conflate two views.
- **Modifying any data.** Pure read-only: replay is in-memory deep-copy,
  no writes to stores.

## 3. Architecture

Mirrors the proven `quality_metrics_report_service.py` pattern: pure
service + thin CLI shell.

```
app/services/quality_diff_service.py       ← NEW: pure diff functions
app/replay/config.py                       ← EXTEND: ReplayConfig + settings_overrides
scripts/analyze_feature_flag_impact.py     ← EXTEND: --set/--set-a/--set-b + --diff-*
```

**Why extend the existing CLI instead of a new script?** The existing
`analyze_feature_flag_impact.py` already owns the "compare two configs"
surface and the `_compute_direction_matrix` / `_load_records` /
`_config_by_name` helpers. A second script would split the comparison
surface and force users to learn two tools. The diff report is a new
*output mode* on the same comparison action, not a new action.

**Why a service layer?** Same reason as `quality_metrics_report_service`:
the pure functions (build_diff, slice alignment, regression summary) must
be unit-testable without loading event_store or mutating global settings,
and a future API route will reuse them.

## 4. ReplayConfig Extension

### 4.1 New field

```python
@dataclass
class ReplayConfig:
    # ... existing 11 bool fields unchanged ...
    settings_overrides: dict[str, Any] | None = None
```

`settings_overrides` holds arbitrary `KEY → value` pairs (KEY already
UPPERCASE) that `apply_replay_config` will `setattr(settings, KEY, value)`
for the duration of the replay. `None` means no extra overrides.

### 4.2 apply_replay_config update

The existing `try/finally` already saves and restores every non-None
field. Extend it to also iterate `settings_overrides` in the same
save/restore loop:

```python
@contextmanager
def apply_replay_config(cfg: ReplayConfig) -> Iterator[None]:
    saved: dict[str, object] = {}
    try:
        # existing bool fields
        for field_name in cfg.__dataclass_fields__:
            if field_name == "settings_overrides":
                continue
            val = getattr(cfg, field_name)
            if val is not None:
                key = field_name.upper()
                saved[key] = getattr(settings, key)
                setattr(settings, key, val)
        # new: arbitrary settings overrides
        if cfg.settings_overrides:
            for key, val in cfg.settings_overrides.items():
                saved[key] = getattr(settings, key)
                setattr(settings, key, val)
        yield
    finally:
        for key, val in saved.items():
            setattr(settings, key, val)
```

Settings overrides are applied **after** bool fields, so if a key appears
in both (unlikely but possible), the override wins.

## 5. quality_diff_service.py Contract

### 5.1 Public API

```python
DIRECTION_LABELS: tuple[str, ...] = ("YES", "NO", "WAIT", "AVOID")

def build_diff(
    records_a: list[dict[str, Any]],
    records_b: list[dict[str, Any]],
    config_meta_a: dict[str, Any],
    config_meta_b: dict[str, Any],
) -> dict[str, Any]:
    ...
```

`config_meta_*` shape: `{"preset": str, "settings_overrides": dict, "name": str}`.

- `preset` — the preset name (e.g. `"all_on"`, `"all_off"`, `"current"`).
  `"current"` means no preset applied (inherit runtime `.env`).
- `settings_overrides` — the dict of `KEY → value` overrides applied on top
  of the preset (from `--set` / `--set-a` / `--set-b`). Empty dict if none.
- `name` — human-readable label for display, defaults to `preset` but may
  include a short `--set` summary when overrides are present (e.g.
  `"current +MARKET_MAX_SPREAD_PCT=15"`). Used in the text report header.

The service does **not** perform replay — it receives already-replayed
records from the CLI. This keeps the service pure (no I/O, no global
settings mutation, no event_store access).

### 5.2 Input contract (CLI must guarantee)

`records_a` / `records_b` are the output of `replay_record(original, cfg)`
with one post-processing step: **the CLI copies `outcome`, `event_id`,
`title`, and any other evaluation fields from the original record onto the
replayed record**. `replay_record` strips overlays but preserves these
fields; however, the contract is stated explicitly so future implementers
do not drop `outcome` (which `extract_metrics` needs for
`direction_correct`).

> **Contract:** Each record in `records_a` / `records_b` MUST contain
> `event_id`, `outcome` (with `actual_outcome` + `status`), `source`,
> `llm_telemetry`, `actionable_recommendation`, `calibration`,
> `source_reliability` — i.e. everything `extract_metrics` reads. The CLI
> is responsible for injecting `outcome` back onto the replayed record.

### 5.3 Alignment

Service aligns by `event_id` internally, not by list order:

```python
by_id_a = {r.get("event_id"): r for r in records_a if r.get("event_id")}
by_id_b = {r.get("event_id"): r for r in records_b if r.get("event_id")}
common_ids = sorted(by_id_a.keys() & by_id_b.keys())
```

Output includes `n_missing_a` (events only in B) and `n_missing_b` (events
only in A).

### 5.4 Output structure

```python
{
  "config_a": {preset, settings_overrides, name},
  "config_b": {preset, settings_overrides, name},
  "overview": {
    "n_total": int,                  # len(common_ids) + n_missing_a + n_missing_b
                                     # (total events seen across both sides)
    "n_direction_compared": int,     # both sides have a valid direction
    "n_scored_compared": int,        # both sides have direction + resolved outcome
    "n_missing_a": int,              # events only in B (no A counterpart)
    "n_missing_b": int,              # events only in A (no B counterpart)
    "direction_changed": int,        # direction differs between A and B
    "change_rate": float | None,     # changed / n_direction_compared
  },
  "direction_matrix": {
    # 5x5: DIRECTION_LABELS + "OTHER". Rows=A, cols=B.
    "YES": {"YES": n, "NO": n, "WAIT": n, "AVOID": n, "OTHER": n},
    # ... NO, WAIT, AVOID, OTHER ...
  },
  "top_transitions": [
    {"from": "YES", "to": "WAIT", "n": n, "pct": float},
    # ... non-diagonal transitions, sorted by n desc ...
  ],
  "slice_diff": {
    "by_source_type": {
      "<key>": {
        "a": QualityReportSlice,        # from quality_metrics_report_service
        "b": QualityReportSlice,
        "delta": {
          "n": {"a": int, "b": int, "delta": int},
          "direction_accuracy": float | None,   # b - a
          "brier_score": float | None,          # b - a
        },
      },
    },
    "by_analysis_quality": {...},
    "by_edge_bucket": {...},
    "by_source_reliability_bucket": {...},
  },
  "regression_summary": {
    "accuracy_regressions": int,      # Δacc < 0 slice count
    "accuracy_improvements": int,     # Δacc > 0
    "brier_regressions": int,         # Δbrier > 0 (Brier lower is better)
    "brier_improvements": int,
    "largest_accuracy_drop": {"slice": str, "delta": float} | None,
    "largest_brier_drop": {"slice": str, "delta": float} | None,
  },
  "diff_errors": [
    {"event_id": str, "side": "a"|"b", "stage": "extract_metrics"|"slice_metrics"|"direction", "error": str},
  ],
}
```

### 5.5 Reuse

- **`extract_metrics` + `slice_metrics`** from
  `quality_metrics_report_service` — called once per side per common
  event. Single-event extraction failures go to `diff_errors` with
  `stage="extract_metrics"` (resilient, no abort).
- **Direction matrix** — re-implemented in the service (small, ~20 lines)
  rather than imported from the CLI script, so the service is
  self-contained and testable without the CLI. Same `_effective_direction`
  fallback chain (`final_displayed_direction` →
  `actionable_recommendation.direction` → None; unknown values → "OTHER").
- **`compute_direction_correct`** — reused from
  `prediction_calibration_service` for the slice accuracy math.

## 6. CLI Extension

### 6.1 New flags

```
--set KEY=VALUE [KEY=VALUE ...]     # shared overrides, applied to both A and B
--set-a KEY=VALUE [KEY=VALUE ...]   # config A only
--set-b KEY=VALUE [KEY=VALUE ...]   # config B only
--diff-report                       # print text diff report to stdout
--diff-report-path PATH             # write text diff report to file
--diff-json PATH                    # write JSON diff report to file
```

`--set*` flags are `action="append"` (can repeat). Each value parsed as
`KEY=VALUE`.

### 6.2 Default behavior

- No `--compare` given: `A = current`, `B = current`. Then `--set` applies
  to both, `--set-a` to A only, `--set-b` to B only. This lets
  `--set-b MARKET_MAX_SPREAD_PCT=15 --diff-report` compare "current vs
  current+threshold" without flag noise drowning the threshold effect.
- `--compare A B` given: presets loaded as before, then `--set*` overlays
  on top.

### 6.3 `--set` parsing

```python
def parse_kv(s: str) -> tuple[str, Any]:
    if "=" not in s: error exit 2
    key, raw = s.split("=", 1)
    key = key.strip().upper()
    # 1. existence check
    if not hasattr(settings, key): exit 2 "unknown setting: {key}"
    # 2. sensitive denylist
    if key.endswith(("_API_KEY", "_SECRET", "_TOKEN", "_PASSWORD")) or key == "OPENAI_API_KEY":
        exit 2 "{key} blocked by sensitive-name policy"
    # 3. type coercion: bool literal → int → float → str
    if raw.lower() in ("true", "false", "on", "off", "yes", "no"):
        val = raw.lower() in ("true", "on", "yes")
    else:
        try: val = int(raw)
        except ValueError:
            try: val = float(raw)
            except ValueError: val = raw
    return (key, val)
```

Note: `*_KEY` is NOT in the denylist (would over-block non-sensitive keys
like `CACHE_KEY`). Only the four suffixes above + `OPENAI_API_KEY`.

### 6.4 Mode determination

- `--diff-report` or `--diff-report-path` or `--diff-json` present →
  **diff mode** (call `build_diff`).
- Otherwise → **legacy matrix mode** (existing behavior unchanged).

### 6.5 Illegal combinations (exit 2)

- `--per-phase` + any `--diff-*`
- `--json` + `--diff-json` (two JSON outputs at once)
- `--set-a` or `--set-b` without any `--diff-*` (legacy mode doesn't need
  A/B overrides — use `--compare` for preset switching)
- `--diff-json` + `--diff-report-path` (pick one file output format)

**Legal combos involving text output:**
- `--diff-report` alone → text to stdout
- `--diff-report-path PATH` alone → text to file
- `--diff-report` + `--diff-report-path PATH` → text to both stdout and file
- `--diff-report` + `--diff-json PATH` → text to stdout + JSON to file (different
  consumers, no conflict)

### 6.6 Diff mode flow

```python
# 1. Parse --set* into three dicts: shared, a_only, b_only
# 2. Build cfg_a = _config_by_name(compare_a) + settings_overrides = {**shared, **a_only}
# 3. Build cfg_b = _config_by_name(compare_b) + settings_overrides = {**shared, **b_only}
# 4. Load records (shared by both sides — same event batch)
# 5. For each record:
#    replayed_a = replay_record(record, cfg_a)
#    replayed_b = replay_record(record, cfg_b)
#    # inject outcome back (replay preserves outcome, but make contract explicit)
#    records_a.append({**replayed_a, "outcome": record.get("outcome")})
#    records_b.append({**replayed_b, "outcome": record.get("outcome")})
# 6. config_meta_a = {"preset": compare_a, "settings_overrides": cfg_a.settings_overrides or {}, "name": compare_a}
# 7. diff = build_diff(records_a, records_b, config_meta_a, config_meta_b)
# 8. Render: --diff-report (stdout text) / --diff-report-path (file text) / --diff-json (file JSON)
```

### 6.7 Text report layout

```
Config A: preset=all_off, settings_overrides={}
Config B: preset=all_on, settings_overrides={"MARKET_MAX_SPREAD_PCT": 15}

Overview
  n_total: 120
  n_direction_compared: 118   (n_missing_a: 0, n_missing_b: 2)
  n_scored_compared: 95
  direction_changed: 23 (19.5%)
  change_rate: 0.195

Regression summary
  accuracy_regressions: 3 slices
  accuracy_improvements: 5 slices
  brier_regressions: 1 slices
  brier_improvements: 7 slices
  largest_accuracy_drop: by_edge_bucket[20+]: -0.08
  largest_brier_drop: by_source_type[sports_event]: +0.03

Direction matrix (rows=A, cols=B)
          YES     NO   WAIT  AVOID  OTHER
  YES      45      3      8      0      0
  NO        2     30      1      0      0
  WAIT      5      1     20      0      0
  AVOID     0      0      0      3      0
  OTHER     0      0      0      0      0

Top transitions
  YES -> WAIT: 8 (6.8%)
  WAIT -> YES: 5 (4.2%)
  ...

Slice diff: by_source_type
  slice              n_a  n_b  acc_a   acc_b   Δacc   brier_a  brier_b  Δbrier
  prediction_market   60   60   0.72    0.78   +0.06   0.183   0.171   -0.012
  sports_event        35   35   0.60    0.63   +0.03   0.220   0.215   -0.005
  ...

(repeat for by_analysis_quality / by_edge_bucket / by_source_reliability_bucket)

Diff errors: 2
  evt_abc (side=a, stage=extract_metrics): missing source field
  evt_xyz (side=b, stage=direction): unknown direction value "SKIP"
```

### 6.8 JSON output

`--diff-json PATH` writes `build_diff(...)` return value verbatim, plus
an `effective_config` block per side:

```python
{
  ...build_diff output...,
  "effective_config_a": {"preset": str, "settings_overrides": dict, "applied_bool_fields": {FIELD: bool, ...}},
  "effective_config_b": {...},
}
```

`applied_bool_fields` lists the non-None bool fields from the ReplayConfig
(after preset + `--set` resolution), so users can audit what actually ran.

## 7. Testing

### 7.1 `tests/test_quality_diff_service.py` (new)

Pure-function tests, no fixtures:

- `test_build_diff_empty_inputs` — empty lists → overview all 0, empty slices
- `test_build_diff_by_event_id_alignment` — A has 3, B has 2 → `n_missing_b=1`, only common compared
- `test_build_diff_direction_matrix` — construct A/B direction scenarios, verify 5×5 matrix + OTHER bucket + top_transitions sort
- `test_build_diff_slice_metrics` — source_type grouping, verify a/b/delta structure
- `test_build_diff_n_direction_vs_n_scored` — some records lack outcome → `n_direction_compared` > `n_scored_compared`
- `test_build_diff_regression_summary` — mixed Δacc/Δbrier signs → regression/improvement counts + largest_*_drop
- `test_build_diff_diff_errors_stage` — extract_metrics raises → diff_errors with stage, no abort
- `test_build_diff_outcome_injection_contract` — record with outcome field works (validates service depends on CLI injecting outcome)
- `test_build_diff_unknown_direction_goes_to_other` — direction "SKIP" → OTHER bucket

### 7.2 `tests/test_replay_config_overrides.py` (new)

- `test_settings_overrides_applied_and_restored` — values applied inside context, restored after
- `test_settings_overrides_exception_still_restores` — exception inside context, finally still restores
- `test_settings_overrides_none_is_noop` — `None` doesn't touch settings
- `test_settings_overrides_takes_precedence_over_bool_fields` — same key in both → override wins

### 7.3 `tests/test_analyze_feature_flag_impact_cli.py` (extend existing)

- `test_set_flag_parsing_bool_literal` — `--set FLAG=true` → bool
- `test_set_flag_parsing_int_float_str` — int / float / str coercion
- `test_set_flag_rejects_unknown_setting` — `--set NONEXISTENT=1` → exit 2
- `test_set_flag_rejects_sensitive_field` — `--set OPENAI_API_KEY=x` → exit 2, message contains "sensitive-name policy"
- `test_set_a_set_b_split` — `--set-a K=1 --set-b K=2` → A and B get separate overrides
- `test_default_compare_is_current_current` — no `--compare` → A/B both `current` preset
- `test_diff_report_text_output` — `--diff-report` stdout contains Overview / Regression summary / Direction matrix / Slice diff sections
- `test_diff_json_output_shape` — `--diff-json PATH` JSON contains overview / direction_matrix / slice_diff / regression_summary / effective_config_*
- `test_invalid_combo_per_phase_diff_report` — `--per-phase --diff-report` → exit 2
- `test_invalid_combo_json_diff_json` — `--json x --diff-json y` → exit 2
- `test_set_a_without_diff_report_exits_2` — `--set-a K=1` (no `--diff-*`) → exit 2
- `test_backward_compat_compare_no_diff` — `--compare all_off all_on` (no `--diff-*`) → legacy matrix output unchanged

### 7.4 Regression

Run existing tests to confirm no regressions:
- `test_report_quality_metrics.py`
- `test_quality_metrics_report.py`
- `test_quality_metrics.py`
- `test_sweep_event_quality.py`
- `test_diagnose_event_quality.py`

### 7.5 End-to-end smoke

```bash
python -m scripts.analyze_feature_flag_impact \
  --set-b MARKET_MAX_SPREAD_PCT=15 \
  --diff-report \
  --sample-size 5
```

Confirms: import chain, replay with override, diff build, text render all
work on a small sample. Output must contain "Regression summary" section.

## 8. Verification

1. `python -m pytest tests/test_quality_diff_service.py tests/test_replay_config_overrides.py tests/test_analyze_feature_flag_impact_cli.py -v` — new tests green
2. `python -m pytest tests/test_report_quality_metrics.py tests/test_quality_metrics_report.py tests/test_quality_metrics.py tests/test_sweep_event_quality.py tests/test_diagnose_event_quality.py -q` — no regressions
3. End-to-end smoke (§7.5)
4. `npx tsc --noEmit` (frontend untouched, sanity check)

## 9. Future Work (out of scope)

- **API endpoint `/quality-metrics/diff`** — reuse `quality_diff_service.build_diff`. The route would accept `preset_a` / `preset_b` / `settings_overrides_a` / `settings_overrides_b` as query/body params and run replay server-side. Deferred until the CLI proves the contract.
- **Frontend diff panel** — consume the future API endpoint. Deferred.
- **Model/LLM change comparison** — requires a snapshot store for frozen `legacy_analysis` / `sentiment_profile` from two LLM runs. Separate spec.
- **Per-phase diff mode** — if needed, design a `--per-phase-diff` flag that runs N+1 diff comparisons (baseline vs each overlay's marginal impact) with diff output. Currently illegal combination.
