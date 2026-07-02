# Task 3 Report: `_run_replay_comparison` real impl + exit code tests + vocabulary lock test

## Status: DONE_WITH_CONCERNS

A brief deviation from the brief's verbatim test code was required (see Self-review → Concerns). All Steps 1-6 executed in order. 17/17 diagnose tests pass; full backend suite green.

## What was implemented

### Functions modified
- **`backend/scripts/diagnose_event_quality.py`** — replaced the `_run_replay_comparison` stub (4 lines) with a real implementation (~47 lines) per the brief's Step 3 verbatim:
  - Imports `ReplayConfig` from `app.replay.config` and `replay_record` from `app.replay.runner` (NOT `from app.replay` — `__init__.py` is empty).
  - Defines `_effective_direction(replayed)` helper with the fallback chain `final_displayed_direction → actionable_recommendation.direction → None`, validating against `_DIRECTIONS = ("YES", "NO", "WAIT", "AVOID")` (mirrors `analyze_feature_flag_impact._effective_direction`).
  - Deep-copies the record before `replay_record(record_copy, ReplayConfig.preset_all_on())` (defensive — `replay_record` deep-copies internally, but the contract says no mutation).
  - Runs `replay_record(record, ReplayConfig.preset_all_off())` for the baseline.
  - Returns `{"all_on_direction": str|None, "all_off_direction": str|None, "delta": "changed"|"no_change"}`.
- Function location preserved: after `_render_json`, before `main`.

### Tests added (6 new tests, 3 new classes)
Added to `backend/tests/test_diagnose_event_quality.py` before the `if __name__ == "__main__":` block:

- **`TestReplayComparison`** (3 tests):
  - `test_replay_comparison_no_change` — all overlays stripped, all flags off → both all_on/all_off fall back to `actionable_recommendation.direction = "YES"` → `delta="no_change"`.
  - `test_replay_comparison_changed` — enables DQ + guardrails + `GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT` → all_on downgrades YES→WAIT (empty calibration store in test env), all_off stays YES → `delta="changed"`.
  - `test_replay_no_mutation_of_input` — deep-copies record before calling `_run_replay_comparison`, asserts `record == snapshot` after.

- **`TestExitCodes`** (2 tests):
  - `test_exit_code_not_found` — missing event_id → `main` returns 1, "not found" in stderr.
  - `test_exit_code_success` — saved event → `main(["test-1", "--json"])` returns 0.

- **`TestVocabularyLock`** (1 test):
  - `test_vocabulary_lock_cli_labels` — scans rendered text + JSON output for banned terms (`long`, `short`, `buy`, `sell`, `position`, `kelly`, `order`) as whole words (case-insensitive). `_sample_record` uses clean values so full-output scan == CLI-label-only scan per the brief's note.

### Total tests in the file: 17 (11 from Tasks 1-2 + 6 new)

## TDD evidence

### Step 2 (RED) — `cd backend && python -m pytest tests/test_diagnose_event_quality.py::TestReplayComparison::test_replay_comparison_changed tests/test_diagnose_event_quality.py::TestExitCodes -v`

Result: **1 passed, 2 failed** (exit code 1).

Failure 1 (expected per brief — stub doesn't run replay):
```
tests/test_diagnose_event_quality.py::TestReplayComparison::test_replay_comparison_changed FAILED
>       self.assertEqual(result["all_off_direction"], "YES")
E       AssertionError: None != 'YES'
```

Failure 2 (NOT expected per brief — see Concerns below):
```
tests/test_diagnose_event_quality.py::TestExitCodes::test_exit_code_success FAILED
app\memory\event_store.py:155: ValidationError
E                   pydantic_core._pydantic_core.ValidationError: 8 validation errors for EventRecord
E                   event_summary
E                     Field required [type=missing, ...]
E                   probability.direction
E                     Field required [type=missing, ...]
E                   credibility, impact, risk, evidence, value_score, intelligence_report — all "Field required"
```

`test_exit_code_not_found` PASSED (main already returns 1 correctly for missing events, as the brief predicted).

### Step 4 (GREEN) — `cd backend && python -m pytest tests/test_diagnose_event_quality.py -v`

Result: **17 passed in 1.60s** (exit code 0).

```
collected 17 items

tests/test_diagnose_event_quality.py::TestLoadEvent::test_load_event_found PASSED [  5%]
tests/test_diagnose_event_quality.py::TestLoadEvent::test_load_event_not_found PASSED [ 11%]
tests/test_diagnose_event_quality.py::TestExtractPhaseData::test_extract_phase_data_full PASSED [ 17%]
tests/test_diagnose_event_quality.py::TestExtractPhaseData::test_extract_phase_data_missing_overlays PASSED [ 23%]
tests/test_diagnose_event_quality.py::TestExtractPhaseData::test_extract_phase_data_no_mutation PASSED [ 29%]
tests/test_diagnose_event_quality.py::TestRenderText::test_render_text_includes_all_phases PASSED [ 35%]
tests/test_diagnose_event_quality.py::TestRenderText::test_render_text_includes_event_header PASSED [ 41%]
tests/test_diagnose_event_quality.py::TestRenderText::test_render_text_missing_overlay_shows_skipped PASSED [ 47%]
tests/test_diagnose_event_quality.py::TestRenderText::test_render_text_uses_max_safe_size_label PASSED [ 52%]
tests/test_diagnose_event_quality.py::TestRenderJson::test_render_json_replay_null_without_flag PASSED [ 58%]
tests/test_diagnose_event_quality.py::TestRenderJson::test_render_json_valid_structure PASSED [ 64%]
tests/test_diagnose_event_quality.py::TestReplayComparison::test_replay_comparison_changed PASSED [ 70%]
tests/test_diagnose_event_quality.py::TestReplayComparison::test_replay_comparison_no_change PASSED [ 76%]
tests/test_diagnose_event_quality.py::TestReplayComparison::test_replay_no_mutation_of_input PASSED [ 82%]
tests/test_diagnose_event_quality.py::TestExitCodes::test_exit_code_not_found PASSED [ 88%]
tests/test_diagnose_event_quality.py::TestExitCodes::test_exit_code_success PASSED [ 94%]
tests/test_diagnose_event_quality.py::TestVocabularyLock::test_vocabulary_lock_cli_labels PASSED [100%]

============================= 17 passed in 1.60s ==============================
```

### Step 5 (full backend suite) — `cd backend && python -m pytest --ignore=tests/test_gbm_engine.py -q`

Result: **1895 passed, 11 skipped, 20 subtests passed in 308.20s (0:05:08)** (exit code 0). No failures. The pre-existing `test_gbm_engine.py` was excluded per the brief (env issue).

## Files changed

- `backend/scripts/diagnose_event_quality.py` — `_run_replay_comparison` stub replaced with real implementation (+49 lines, -3 lines net for the function).
- `backend/tests/test_diagnose_event_quality.py` — added 3 test classes (6 tests, +226 lines).

## Commit hash

`f14220e1215a80735b0cd26ee6a8e8b7082d9914`

Commit message (verbatim from brief, applied via `git commit -F backend/.commit_msg.tmp` per the PowerShell constraint):
```
feat(diagnose-cli): implement _run_replay_comparison + exit codes + vocab lock

Replaces stub with real replay_record-based comparison (all_on vs all_off).
Imports from app.replay.config/app.replay.runner directly (not app.replay
— __init__.py is empty). Deep-copies record before replay to avoid
mutation. _effective_direction mirrors analyze_feature_flag_impact pattern
(final_displayed_direction → actionable_recommendation.direction → None).
Adds 6 tests: 2 replay (no_change/changed), 1 replay no-mutation, 2 exit
codes (not_found/success), 1 vocabulary lock. 17 tests pass; full backend
suite green. Spec §4.3 (P2 #25) now DONE.
```

Committed files: only `backend/scripts/diagnose_event_quality.py` and `backend/tests/test_diagnose_event_quality.py` (2 files changed, 272 insertions(+), 3 deletions(-)). The temp file `backend/.commit_msg.tmp` was created, used, and deleted (`Remove-Item`) — it was never staged.

## Self-review findings

### Completeness
- All 6 Steps executed in order: write failing tests → RED → implement → GREEN → full suite → commit.
- The `_run_replay_comparison` implementation matches the brief's Step 3 verbatim (deep-copy, `preset_all_on()`/`preset_all_off()`, `_effective_direction` fallback chain, return shape).
- All 6 required tests added (3 replay + 2 exit code + 1 vocab). 17/17 diagnose tests pass.
- Spec §4.3 (P2 #25) CLI is now feature-complete.

### Quality
- Implementation follows the cross-task import-path constraint (`from app.replay.config import ReplayConfig`, `from app.replay.runner import replay_record` — NOT `from app.replay`).
- The `_effective_direction` helper is the same fallback chain as `analyze_feature_flag_impact._effective_direction`, with `_DIRECTIONS = ("YES", "NO", "WAIT", "AVOID")` validation guarding against `probability.direction` (which returns `rising/falling/stable` and would otherwise leak through).
- Vocabulary lock respected: the new `_run_replay_comparison` adds no CLI labels (it returns a dict; `_render_text`/`_render_json` already use `all_on_direction`/`all_off_direction`/`delta` keys, none of which contain banned terms). The vocab lock test confirms this end-to-end.
- Pure read-only: no writes, no LLM calls, no network. The only mutation concern (input record) is guarded by `copy.deepcopy(record)` + `replay_record`'s own internal deep-copy.

### Discipline
- TDD: RED step run and documented BEFORE implementation. The expected replay failure (`None != 'YES'`) confirmed the test exercises the right behavior. GREEN step shows 17 passed.
- No gold-plating: only the stub function and the test file were touched. No surrounding refactors, no extra comments on unchanged code.
- PowerShell constraints respected: commit message written to `backend/.commit_msg.tmp` via the `Write` tool, `git commit -F`, then `Remove-Item`. No heredoc, no `.git/` writes.
- Staged only the two intended files explicitly (no `git add -A` / `git add .`).

### Testing
- 17/17 diagnose tests pass.
- 1895 passed / 11 skipped / 20 subtests passed in the full backend suite (excluding `test_gbm_engine.py`). 0 failures.

### Concerns

**Single deviation from the brief's verbatim test code (necessary to meet Step 4 requirement):**

The brief's `TestExitCodes.test_exit_code_success` record (verbatim from the brief) is missing 8 fields required by `EventRecord` validation (`event_summary`, `probability.direction`, `credibility`, `impact`, `risk`, `evidence`, `value_score`, `intelligence_report`). `event_store.save_event` runs `EventRecord.model_validate(candidate)`, so the verbatim record raises `pydantic_core.ValidationError` at the RED step — contradicting the brief's own prediction that "Exit code tests may pass (main already returns 1/0 correctly)."

To meet the brief's Step 4 hard requirement ("MUST show 17 passed"), I added the missing required fields to the `test_exit_code_success` record by mirroring the canonical valid-record structure already used in the same file by `TestLoadEvent.test_load_event_found` (which is verbatim from Task 1 and passes validation). The added fields use the same placeholder values (`"summary"`, `"rising"`, score/level integers, etc.) — no semantic change to what the test asserts (it only checks `rc == 0`).

This is a test-setup fix, not a behavior change. The `_run_replay_comparison` implementation, the other 5 tests, and the commit message are all verbatim from the brief. The fix is the minimum necessary to satisfy the brief's Step 4 requirement.

Flagging as DONE_WITH_CONCERNS solely so the parent agent is aware of this deviation. If the brief's author intended a different resolution (e.g., bypassing `event_store.save_event` with a direct store write), that would be a follow-up — but the current fix is consistent with the existing test patterns in the same file.
