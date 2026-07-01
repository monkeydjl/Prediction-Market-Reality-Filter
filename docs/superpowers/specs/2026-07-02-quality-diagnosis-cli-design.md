# Quality Diagnosis CLI Design (Spec §4.3)

**Date:** 2026-07-02
**Spec gap:** §4.3 质量诊断 CLI — ❌ 未实现
**Priority:** P2 #25
**Status:** Design approved, ready for implementation plan

---

## 1. Goal

Provide a single-event debugging CLI that decomposes an event into its quality layers (Decision Quality, Market Quality, Calibration, Source Reliability, LLM Telemetry, Execution Quality) plus guardrail state and final direction, so operators can quickly locate quality issues without writing ad-hoc scripts.

Optional `--replay` flag re-runs the replay harness (all_on vs all_off) on the event to show whether the current overlay stack changed the direction.

## 2. Non-Goals

- Batch diagnosis (covered by §4.4 `audit_quality_consistency.py`)
- A/B comparison across presets (covered by §1.5 `analyze_feature_flag_impact.py`)
- Modifying any data (pure read-only diagnosis)
- Triggering live LLM calls or network fetches

## 3. CLI Interface

```
python -m scripts.diagnose_event_quality EVENT_ID [--json] [--replay]
```

**Positional argument:**
- `EVENT_ID` — the event ID to diagnose (required)

**Flags:**
- `--json` — output machine-readable JSON instead of human-readable text
- `--replay` — additionally run replay harness (all_on vs all_off) and show direction delta

**Exit codes:**
- `0` — success
- `1` — event_id not found in event_store
- `2` — other errors (e.g., event_store IO error)

## 4. Architecture

### 4.1 File layout

- **Create** `backend/scripts/diagnose_event_quality.py` — CLI entry point
- **Create** `backend/tests/test_diagnose_event_quality.py` — unit tests

No new dependencies. Uses argparse (consistent with all 14 existing CLI scripts in `backend/scripts/`). No changes to `requirements.txt`.

### 4.2 Module structure

```python
# backend/scripts/diagnose_event_quality.py

def _load_event(event_id: str) -> dict | None:
    """Load event from event_store. Returns None if not found."""

def _extract_phase_data(record: dict) -> dict:
    """Extract 6 phases + guardrail + final direction from record."""

def _render_text(data: dict, replay_result: dict | None) -> str:
    """Render human-readable text output."""

def _render_json(data: dict, replay_result: dict | None) -> str:
    """Render JSON output."""

def _run_replay_comparison(record: dict) -> dict:
    """Run all_on vs all_off replay, return direction delta."""

def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code."""

if __name__ == "__main__":
    sys.exit(main())
```

### 4.3 Data flow

1. Parse args (argparse)
2. `_load_event(event_id)` → `event_store.get(event_id)` → None? exit 1
3. `_extract_phase_data(record)` → dict with 6 phase keys + guardrail + final_direction
4. If `--replay`: `_run_replay_comparison(record)` → dict with all_on/all_off directions
5. If `--json`: `_render_json(data, replay_result)` → stdout
   Else: `_render_text(data, replay_result)` → stdout
6. exit 0

## 5. Output Specification

### 5.1 Text mode (default)

```
Event: <event_id> (<event_title>)
───────────────────────────────────────────

📊 Phase 1: Decision Quality
   ✅ Enabled
   evidence_strength: 0.82
   conflict_score: 0.15
   downgrade_reason: None

📊 Phase 2: Market Quality
   ❌ Degraded (missing spread data)
   wide_spread_flag: False

📊 Phase 3: Prediction Calibration
   snapshot_recommendation: YES
   edge_bucket: 10-20
   direction_correct: True

📊 Phase 4: Source Reliability
   overall_score: 0.78
   source_count: 4
   domain_diversity: 3

📊 Phase 5: LLM Telemetry
   degraded_mode: False
   total_tokens: 1247
   estimated_token_cost: $0.0018

📊 Phase 6: Execution Quality
   executable: True
   estimated_slippage_pct: 0.3
   stale_price_flag: False

🛡️ Guardrails
   fired_rules: []

🎯 Final Direction: YES (displayed)

[Replay Comparison]
   all_on  direction: YES
   all_off direction: YES
   Delta: no change
```

### 5.2 JSON mode (`--json`)

```json
{
  "event_id": "manifold-12345",
  "event_title": "世界杯决赛",
  "phases": {
    "decision_quality": {
      "enabled": true,
      "evidence_strength": 0.82,
      "conflict_score": 0.15,
      "downgrade_reason": null
    },
    "market_quality": {
      "enabled": true,
      "degraded": true,
      "degrade_reason": "missing spread data",
      "wide_spread_flag": false
    },
    "prediction_calibration": {
      "snapshot_recommendation": "YES",
      "edge_bucket": "10-20",
      "direction_correct": true
    },
    "source_reliability": {
      "overall_score": 0.78,
      "source_count": 4,
      "domain_diversity": 3
    },
    "llm_telemetry": {
      "degraded_mode": false,
      "total_tokens": 1247,
      "estimated_token_cost": 0.0018
    },
    "execution_quality": {
      "executable": true,
      "estimated_slippage_pct": 0.3,
      "stale_price_flag": false
    }
  },
  "guardrails": {
    "fired_rules": []
  },
  "final_direction": "YES",
  "replay_comparison": {
    "all_on_direction": "YES",
    "all_off_direction": "YES",
    "delta": "no_change"
  }
}
```

`replay_comparison` is `null` when `--replay` not passed.

### 5.3 Missing overlay handling

When an overlay field is absent from the record (e.g., `llm_telemetry` not built because flag was off):

**Text mode:**
```
📊 Phase 5: LLM Telemetry
   ⏭️ Skipped (overlay not built)
```

**JSON mode:**
```json
"llm_telemetry": null
```

### 5.4 Missing event handling

When `event_id` not found in `event_store`:

**stderr:** `Error: event 'manifold-12345' not found in event_store`
**stdout:** (nothing)
**Exit code:** 1

## 6. Phase field extraction

Each phase extracts a fixed set of fields from the record's overlay dict. Missing fields within an existing overlay render as `null` (JSON) or `<missing>` (text).

| Phase | Record key | Extracted fields |
|---|---|---|
| Decision Quality | `decision_quality` | `evidence_strength`, `conflict_score`, `downgrade_reason`, `displayed_direction` |
| Market Quality | `market_quality` | `degraded`, `degrade_reason`, `wide_spread_flag`, `low_liquidity_flag` |
| Prediction Calibration | `prediction_calibration` (from `actionable_recommendation.calibration_status` + `probability`) | `snapshot_recommendation`, `edge_bucket`, `direction_correct` |
| Source Reliability | `source_reliability` | `overall_score`, `source_count`, `domain_diversity` |
| LLM Telemetry | `llm_telemetry` | `degraded_mode`, `total_tokens`, `estimated_token_cost`, `analysis_quality` |
| Execution Quality | `execution_quality` | `executable`, `estimated_slippage_pct`, `stale_price_flag`, `max_safe_position_size` |
| Guardrails | `guardrail_fired` | `fired_rules` (list) |
| Final Direction | `final_displayed_direction` | (single value) |

Note: `prediction_calibration` is not a top-level key in the record. It's derived from `actionable_recommendation.calibration_status` and `probability` fields. If `actionable_recommendation` is absent, the phase shows as Skipped.

## 7. Replay comparison (`--replay` flag)

When `--replay` is passed:

1. Import `replay_record` and `ReplayConfig` from `app.replay`
2. `replayed_on = replay_record(record, ReplayConfig.preset_all_on())`
3. `replayed_off = replay_record(record, ReplayConfig.preset_all_off())`
4. Extract `final_displayed_direction` from each (via `_effective_direction` fallback to `actionable_recommendation.direction`)
5. Compute delta: `changed` if directions differ, `no_change` if same
6. Include in output under `replay_comparison`

Note: `preset_all_on()` inherits current env settings. In production this reflects the live config; in test env it reflects test flags (tests patch settings as needed).

## 8. Constraints

### 8.1 Vocabulary lock

Output must NOT contain trading terms `long`/`short`/`buy`/`sell`/`position`/`kelly`/`order` as whole words (case-insensitive). Direction vocabulary is `YES`/`NO`/`WAIT`/`AVOID` only.

Note: `max_safe_position_size` field name in Execution Quality contains `position`. This is a field name in the existing `execution_quality_service.py` output, not trading advice. The CLI will display it as-is for consistency with the data model, but this is a known exception. If strict vocabulary lock is required, rename the display label to `max_safe_size` (but keep the JSON key as `max_safe_position_size` for data fidelity).

**Decision:** Display label uses `max_safe_size` (no `position`); JSON key uses `max_safe_position_size` (data fidelity). This satisfies the vocabulary lock in the human-readable text while preserving the data model.

### 8.2 Pure read-only

- No writes to any store
- No LLM calls
- No network fetches
- No mutations to the input record (deep-copy before replay)

### 8.3 Consistency with existing CLI patterns

- argparse (not click)
- `main(argv: list[str] | None = None) -> int` entry point
- `_print()` helper for UTF-8 stdout (Windows GBK safety)
- `if __name__ == "__main__": sys.exit(main())`
- Module path: `scripts.diagnose_event_quality` (run via `python -m`)

### 8.4 No new dependencies

`requirements.txt` unchanged. Only stdlib + existing project modules (`event_store`, `replay`).

## 9. Testing

Test file: `backend/tests/test_diagnose_event_quality.py`

| Test | Description |
|---|---|
| `test_load_event_found` | Event exists in store → returns dict |
| `test_load_event_not_found` | Event missing → returns None → exit 1 |
| `test_extract_phase_data_full` | Record with all 6 overlays → all phases populated |
| `test_extract_phase_data_missing_overlays` | Record with some overlays missing → missing phases show as None |
| `test_render_text_includes_all_phases` | Text output contains all 6 phase headers + guardrail + final direction |
| `test_render_text_missing_overlay_shows_skipped` | Missing overlay shows `⏭️ Skipped` |
| `test_render_json_valid_structure` | JSON output parses, has `event_id`/`phases`/`guardrails`/`final_direction` keys |
| `test_render_json_replay_null_without_flag` | `replay_comparison` is null when no replay run |
| `test_replay_comparison_no_change` | all_on and all_off produce same direction → delta=no_change |
| `test_replay_comparison_changed` | all_on and all_off produce different directions → delta=changed |
| `test_exit_code_not_found` | Missing event → exit 1, error to stderr |
| `test_exit_code_success` | Found event → exit 0 |
| `test_vocabulary_lock` | Output contains no banned terms (long/short/buy/sell/position/kelly/order as whole words) |
| `test_no_mutation_of_input` | `_extract_phase_data` and `_run_replay_comparison` do not mutate the input record |

## 10. Acceptance criteria

- [ ] `python -m scripts.diagnose_event_quality <existing_event_id>` prints 6 phases + guardrail + final direction
- [ ] `python -m scripts.diagnose_event_quality <missing_id>` exits 1 with stderr message
- [ ] `--json` outputs valid JSON with all keys
- [ ] `--replay` shows all_on/all_off direction comparison
- [ ] Missing overlays show `⏭️ Skipped` (text) / `null` (JSON)
- [ ] No banned trading terms in output
- [ ] No new dependencies in requirements.txt
- [ ] All 14 unit tests pass
- [ ] Full backend suite regression-free (excluding pre-existing `test_gbm_engine.py`)
