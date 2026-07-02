# Task 2 Report: `_render_text` + `_render_json` + wire into main

## What was implemented

Added three functions to `backend/scripts/diagnose_event_quality.py` (inserted BEFORE `def main`, per brief):

1. **`_render_text(data, replay_result)`** — Renders the 6-phase decomposition as human-readable text with emoji headers (`📊 Phase N: <name>`), `🛡️ Guardrails`, and `🎯 Final Direction`. Each phase shows either its fields or `⏭️ Skipped (overlay not built)` when the overlay is absent. Uses the `max_safe_size` label (vocabulary lock §8.1 — banned term `max_safe_position_size` never appears as a CLI-generated label). Replay comparison block only emitted when `replay_result is not None`.
2. **`_render_json(data, replay_result)`** — Returns `json.dumps(output, indent=2, default=str, ensure_ascii=False)` with keys `event_id`, `event_title`, `phases`, `guardrails`, `final_direction`, `replay_comparison` (null when no replay).
3. **`_run_replay_comparison(record)`** — STUB returning `{"all_on_direction": None, "all_off_direction": None, "delta": "no_change"}`. Real implementation is Task 3.

Replaced the placeholder section in `main` (the `_print(f"[diagnose_event_quality] ...")` block) with replay-then-render logic: `--replay` triggers `_run_replay_comparison`, then `--json` selects `_render_json` else `_render_text`. The pre-existing `copy`/`json` imports are now actually used (`json.dumps` in `_render_json`).

Added 6 tests to `backend/tests/test_diagnose_event_quality.py` (re-using the existing `_sample_record()` helper from Task 1):
- `TestRenderText`: `test_render_text_includes_all_phases`, `test_render_text_includes_event_header`, `test_render_text_missing_overlay_shows_skipped`, `test_render_text_uses_max_safe_size_label`.
- `TestRenderJson`: `test_render_json_valid_structure`, `test_render_json_replay_null_without_flag`.

## TDD Evidence

### RED (Step 2)

Command:
```
python -m pytest tests/test_diagnose_event_quality.py::TestRenderText tests/test_diagnose_event_quality.py::TestRenderJson -v
```

Result: 6 failed (all `ImportError: cannot import name '_render_text'/'_render_json' from 'diagnose_event_quality'`), as expected before implementation. Exit code 1.

```
FAILED tests/test_diagnose_event_quality.py::TestRenderText::test_render_text_includes_all_phases - ImportError
FAILED tests/test_diagnose_event_quality.py::TestRenderText::test_render_text_includes_event_header - ImportError
FAILED tests/test_diagnose_event_quality.py::TestRenderText::test_render_text_missing_overlay_shows_skipped - ImportError
FAILED tests/test_diagnose_event_quality.py::TestRenderText::test_render_text_uses_max_safe_size_label - ImportError
FAILED tests/test_diagnose_event_quality.py::TestRenderJson::test_render_json_replay_null_without_flag - ImportError
FAILED tests/test_diagnose_event_quality.py::TestRenderJson::test_render_json_valid_structure - ImportError
============================== 6 failed in 0.30s ==============================
```

### GREEN (Step 4)

Command:
```
python -m pytest tests/test_diagnose_event_quality.py -v
```

Result: 11 passed (5 from Task 1 + 4 text + 2 json). Exit code 0.

```
tests/test_diagnose_event_quality.py::TestLoadEvent::test_load_event_found PASSED [  9%]
tests/test_diagnose_event_quality.py::TestLoadEvent::test_load_event_not_found PASSED [ 18%]
tests/test_diagnose_event_quality.py::TestExtractPhaseData::test_extract_phase_data_full PASSED [ 27%]
tests/test_diagnose_event_quality.py::TestExtractPhaseData::test_extract_phase_data_missing_overlays PASSED [ 36%]
tests/test_diagnose_event_quality.py::TestExtractPhaseData::test_extract_phase_data_no_mutation PASSED [ 45%]
tests/test_diagnose_event_quality.py::TestRenderText::test_render_text_includes_all_phases PASSED [ 54%]
tests/test_diagnose_event_quality.py::TestRenderText::test_render_text_includes_event_header PASSED [ 63%]
tests/test_diagnose_event_quality.py::TestRenderText::test_render_text_missing_overlay_shows_skipped PASSED [ 72%]
tests/test_diagnose_event_quality.py::TestRenderText::test_render_text_uses_max_safe_size_label PASSED [ 81%]
tests/test_diagnose_event_quality.py::TestRenderJson::test_render_json_replay_null_without_flag PASSED [ 90%]
tests/test_diagnose_event_quality.py::TestRenderJson::test_render_json_valid_structure PASSED [100%]
============================= 11 passed in 0.41s ==============================
```

## Files changed

- `backend/scripts/diagnose_event_quality.py` — added `_render_text`, `_render_json`, `_run_replay_comparison` (stub); replaced placeholder block in `main` with rendering logic.
- `backend/tests/test_diagnose_event_quality.py` — added `TestRenderText` (4 tests) and `TestRenderJson` (2 tests).

## Commit

`f0818f9` — `feat(diagnose-cli): add _render_text + _render_json, wire into main`

Commit message written to `backend/.commit_msg.tmp`, committed via `git commit -F`, then temp file deleted.

## Self-review findings

- **Completeness**: All three functions implemented verbatim per brief; inserted before `def main` as specified; `main` placeholder replaced with replay+render logic; all 6 tests added. 11/11 tests pass.
- **Quality**: Code matches the brief exactly (no deviations). `_render_json` uses the already-imported `json` module. `_render_text` honors vocabulary lock §8.1 — emits `max_safe_size`, never `max_safe_position_size` as a CLI label. `default=str` + `ensure_ascii=False` handle non-serializable values and emoji/unicode cleanly.
- **Discipline**: TDD followed strictly — RED verified (6 ImportErrors) before any implementation, GREEN verified after. No scope creep: did not implement replay logic (correctly stubbed for Task 3), did not modify Task 1 functions or tests.
- **Testing**: Both text and JSON render paths covered; missing-overlay skip path covered; vocabulary-lock assertion (`assertNotIn("max_safe_position_size", ...)`) covered; null replay path covered.
- **No concerns.** Ready for Task 3.
