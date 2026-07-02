# Task 1 Report — `_load_event` + `_extract_phase_data` + skeleton `main`

## Status: DONE_WITH_CONCERNS

Task 1 of the quality diagnosis CLI plan (spec §4.3, P2 #25) is implemented.
All 5 tests pass (2 load + 3 extract). One concern flagged below: the brief's
verbatim seed record for `test_load_event_found` was missing 8 required
`EventRecord` fields, so the seed record was augmented (test scaffolding only
— script implementation is 100% verbatim from the brief, all test assertions
unchanged).

## Commits

- **Started from:** `6a13b71` (docs(plan): add quality diagnosis CLI implementation plan)
- **Task 1 commit:** `e75af1c` (feat(diagnose-cli): add _load_event + _extract_phase_data + skeleton main)
- **Diff range for reviewer:** `git diff 6a13b71..e75af1c -- backend/scripts/diagnose_event_quality.py backend/tests/test_diagnose_event_quality.py`

## Files created

- `e:\Github\Prediction Market Reality Filter\backend\scripts\diagnose_event_quality.py`
  - Module docstring (Spec §4.3, P2 #25; read-only CLI; usage line)
  - UTF-8 stdout reconfigure guard (Windows GBK safety, same convention as `source_trust_registry_cli.py`)
  - `_print(text)` helper
  - `_load_event(event_id) -> dict | None` — wraps `app.memory.event_store.get_event`
  - `_extract_phase_data(record) -> dict` — extracts 6 phases (Decision Quality, Market Quality, Prediction Calibration, Source Reliability, LLM Telemetry, Execution Quality) + guardrails + final_direction; renames `max_safe_position_size` → `max_safe_size` per §8.1 vocabulary lock
  - `main(argv) -> int` — argparse skeleton: parses `event_id` / `--json` / `--replay`, loads event, extracts data, prints placeholder; rendering + replay deferred to Tasks 2/3

- `e:\Github\Prediction Market Reality Filter\backend\tests\test_diagnose_event_quality.py`
  - `_sample_record()` helper — bypasses EventRecord validation (used only for extract tests)
  - `TestLoadEvent` — 2 tests: `test_load_event_found`, `test_load_event_not_found`
  - `TestExtractPhaseData` — 3 tests: `test_extract_phase_data_full`, `test_extract_phase_data_missing_overlays`, `test_extract_phase_data_no_mutation`

No existing files modified. No new dependencies added.

## Test results

### TDD RED phase (verified before implementation)

Command:
```
cd backend && python -m pytest tests/test_diagnose_event_quality.py -v
```
Output:
```
collected 5 items
tests/test_diagnose_event_quality.py::TestLoadEvent::test_load_event_found FAILED [ 20%]
tests/test_diagnose_event_quality.py::TestLoadEvent::test_load_event_not_found FAILED [ 40%]
tests/test_diagnose_event_quality.py::TestExtractPhaseData::test_extract_phase_data_full FAILED [ 60%]
tests/test_diagnose_event_quality.py::TestExtractPhaseData::test_extract_phase_data_missing_overlays FAILED [ 80%]
tests/test_diagnose_event_quality.py::TestExtractPhaseData::test_extract_phase_data_no_mutation FAILED [100%]
================================== FAILURES ===================================
... (all 5 failures)
E       ModuleNotFoundError: No module named 'diagnose_event_quality'
============================== 5 failed in 0.58s ==============================
```
All 5 failed with `ModuleNotFoundError: No module named 'diagnose_event_quality'` — exactly the expected RED reason (script module didn't exist yet).

### TDD GREEN phase (after implementation)

Command:
```
cd backend && python -m pytest tests/test_diagnose_event_quality.py -v
```
Output:
```
collected 5 items
tests/test_diagnose_event_quality.py::TestLoadEvent::test_load_event_found PASSED [ 20%]
tests/test_diagnose_event_quality.py::TestLoadEvent::test_load_event_not_found PASSED [ 40%]
tests/test_diagnose_event_quality.py::TestExtractPhaseData::test_extract_phase_data_full PASSED [ 60%]
tests/test_diagnose_event_quality.py::TestExtract_phase_data_missing_overlays PASSED [ 80%]
tests/test_diagnose_event_quality.py::TestExtractPhaseData::test_extract_phase_data_no_mutation PASSED [100%]
============================== 5 passed in 0.43s ==============================
```

## Self-review findings

### 1. Five new tests pass — YES
All 5 tests pass (see GREEN output above).

### 2. Script implementation is verbatim from brief — YES
`backend/scripts/diagnose_event_quality.py` is byte-for-byte the code block
from `backend/.sdd/task-1-brief.md` Step 3. No deviations, no extra features,
no added error handling, no refactoring. Confirmed by visual diff.

### 3. Brief defect: seed record in `test_load_event_found` did not pass EventRecord validation — FIXED (concern)

The brief's verbatim test record for `test_load_event_found` was missing 8
required `EventRecord` fields and could not pass `event_store.save_event`
(which runs `normalize_event_record` + `EventRecord.model_validate`).
The failure (before fix):
```
pydantic_core._pydantic_core.ValidationError: 8 validation errors for EventRecord
  event_summary           Field required
  probability.direction   Field required
  credibility             Field required
  impact                  Field required
  risk                    Field required
  evidence                Field required
  value_score             Field required
  intelligence_report     Field required
```

**Root cause:** The brief's note claims "verified pattern from
`backend/tests/test_operational_readiness.py:662`", but that line verifies
`backup_stores.create_backup` (which only writes `"{}"` to the store file),
NOT `save_event`. The actual verified `save_event` pattern lives in
`backend/tests/test_event_store.py::_make_record` (lines 19–56), which
includes all 8 required fields.

**Fix applied:** Augmented ONLY the seed record in `test_load_event_found`
with the 8 missing required fields (`event_summary`, `probability.direction`,
`credibility`, `impact`, `risk`, `evidence`, `value_score`,
`intelligence_report`), using the same field shapes as `_make_record`. All
brief-original extra fields (`market_quote`, `actionable_recommendation`,
`legacy_analysis`, `evidence_breakdown`, `sentiment_profile`) preserved
unchanged — they survive via `EventRecord.model_config = ConfigDict(extra="allow")`.

**Not affected:**
- The script implementation (still 100% verbatim from brief).
- All test assertions (unchanged).
- The test's stated intent: "When event exists in store, `_load_event`
  returns the store entry."
- The brief's `_sample_record()` helper (used only by extract tests, which
  bypass EventRecord validation — left verbatim).
- The other 4 tests (verbatim from brief).

**Why fixed rather than blocked:** The brief's own comment explicitly states
the record "must have required fields" — the verbatim record simply failed
its own stated requirement. The fix is minimal, documented inline in the
test, faithful to the brief's intent, and required to achieve the brief's
mandatory GREEN step. The seed record is test scaffolding, not the contract
under test.

### 4. No-mutation test passes — YES
`test_extract_phase_data_no_mutation` deep-copies the record, calls
`_extract_phase_data`, and asserts equality. The implementation reads via
`.get()` and constructs new dicts/lists; it never mutates the input. The
`guardrails` field uses `list(fired)` (a copy), not the original list ref.

### 5. Vocabulary lock honored — YES
`_extract_phase_data` renames `max_safe_position_size` → `max_safe_size`
(§8.1). The renamed key is asserted in `test_extract_phase_data_full`:
`assertEqual(data["phases"]["execution_quality"]["max_safe_size"], 1000.0)`
and `assertNotIn("max_safe_position_size", ...)`. No `position`/`long`/
`short`/`buy`/`sell`/`kelly`/`order` trading-vocabulary terms introduced in
the script or test.

### 6. Surgical / no scope creep — YES
- Only 2 files created (the 2 specified in the brief).
- No existing files modified.
- No comments/docstrings added to code outside what the brief specified.
- No error handling for impossible scenarios (the brief's main() does check
  for None entry and non-dict record, but that's verbatim from the brief).
- Commit contains exactly the 2 intended files (`git show --stat e75af1c`).

### 7. Unused imports in script — noted but verbatim from brief
The script imports `copy` and `json` but does not use them in Task 1's scope.
Both are present verbatim in the brief and will be used by Tasks 2/3
(rendering + replay). Left as-is per "verbatim from brief" requirement.

### 8. Commit message matches brief — YES
Commit message written to `backend/.commit_msg.tmp` then committed via
`git commit -F`. Temp file deleted after commit. Message body is verbatim
from the brief Step 5.

## Summary

Task 1 is complete and committed as `e75af1c`. The diagnosis CLI skeleton
loads an event from `event_store.get_event`, extracts the 6 quality phases
+ guardrails + final direction (with the §8.1 `max_safe_position_size` →
`max_safe_size` rename), and prints a placeholder via argparse-driven
`main`. All 5 tests pass. The single concern (brief's malformed seed
record) is documented above with the minimal fix applied — it does not
affect Tasks 2 or 3, which build on `_load_event` / `_extract_phase_data`
unchanged.
