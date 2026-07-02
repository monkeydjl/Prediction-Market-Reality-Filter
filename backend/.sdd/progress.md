# Quality Diagnosis CLI Progress Ledger

**Plan:** `docs/superpowers/plans/2026-07-02-quality-diagnosis-cli.md`
**Branch:** `main` (inline execution per established repo pattern)
**Base commit (MERGE_BASE):** `6a13b71`
**Spec sections covered:** §4.3 (质量诊断 CLI)
**Final HEAD:** `f66caf6`

## Tasks

| # | Task | Status | Commits | Notes |
|---|------|--------|---------|-------|
| 1 | _load_event + _extract_phase_data + skeleton main | DONE | `e75af1c` | Reviewer APPROVED (0 Critical/Important, 3 Minor defer). Controller-authorized plan-bug fix: seed record expanded with 8 missing EventRecord required fields. |
| 2 | _render_text + _render_json + wire into main | DONE | `f0818f9` | Reviewer APPROVED (0 Critical/Important, 3 Minor defer). 11/11 tests pass. |
| 3 | _run_replay_comparison + exit codes + vocab lock | DONE | `f14220e` | Reviewer APPROVED (0 Critical/Important, 2 Minor defer, 1 ⚠️ accepted). Controller-authorized plan-bug fix: `test_exit_code_success` seed record expanded with 8 missing EventRecord required fields (same pattern as Task 1). 17/17 tests pass; full backend suite green (1895 passed, 11 skipped, 0 failures). |

## Final Whole-Branch Review

- **Reviewer:** APPROVED (0 Critical, 0 Important, 9 Minor triaged, 2 ⚠️ accepted)
- **Post-review fixes (1 fix subagent + 1 controller cleanup):**
  - `05ad7b6` — M1: removed redundant asymmetric deep-copy in `_run_replay_comparison`; M5: added `displayed_direction` to `_render_text` Phase 1.
  - `f66caf6` — chore: removed now-unused `import copy`.
- **Final HEAD at review:** `f66caf6` (5 commits since MERGE_BASE `6a13b71`)
- **Minor findings accepted as-is:** M2 (`None` vs `<missing>` — needs sentinel design change, out of scope), M3 (`Delta: no_change` vs spec §5.1 space — spec internally inconsistent, code consistent), M4 (`calibration_status` extra field — additive/harmless), M6 (JSON omits `enabled` — code follows §6 authoritative table over §5.2 example), M7/M8/M9 (test gaps — low risk, nice-to-haves).

## Post-Merge Fixes (post-review, user-reported)

After branch push to origin/main, user reported 5 additional bugs across 3 separate reviews. All fixed and pushed.

### Review 1 — diagnose CLI semantics (3 bugs)

- **P1 `direction_correct` semantics** (`3517735`): CLI compared `actionable_recommendation.direction` vs `final_displayed_direction` instead of vs settled outcome. Raw YES + final YES + outcome=NO returned True (should be False). Fix: reuse `compute_direction_correct(rec_dir, actual_outcome)` from `prediction_calibration_service`, reading `record.outcome.actual_outcome`. Unsettled → None. +7 regression tests.
- **P2 `edge_bucket` hand-rolled buckets** (`3517735`): CLI used `edge // 10` producing wrong intervals (e.g. -12 → "-20--10", 25 → "20-30"). Fix: reuse `compute_edge_bucket(rec.get("edge"))` — abs value, half-open `[0,5)/[5,10)/[10,20)/[20,+inf)`, missing → "". +3 regression tests.
- **P2 exit code 2 contract** (`3517735`): `main()` only returned exit codes for not-found (1) and invalid record (2); other exceptions trace-backed. Fix: two narrow `try/except Exception` blocks (around `_load_event` and around extract/replay/render span), both print to stderr and return 2. +3 regression tests.

### Review 2 — diagnose CLI invalid-outcome gate (1 bug)

- **P1 `direction_correct` accepts invalid outcomes** (`7532c94`): CLI passed `actual_outcome` to `compute_direction_correct` unconditionally, without checking `outcome.status == "resolved"`. A `status="invalid"` outcome (written when verified link diverges) with `actual_outcome=100` returned True — but per `event_store.list_resolved_events` / `event_resolve_service`, non-resolved outcomes record the marker but are NOT scored. Fix: gate on `outcome.get("status", "resolved") == "resolved"` (missing status defaults to resolved per store convention). +2 regression tests (`test_direction_correct_invalid_outcome_is_none`, `test_direction_correct_missing_status_defaults_resolved`).

### Review 3 — test suite infrastructure (4 bugs, not diagnose-CLI)

These were discovered while running the full backend suite after the diagnose fixes. Not part of the diagnose CLI spec, but blocked the "all tests green" verification.

- **lightgbm C-abort** (`5fc353b`): `test_gbm_engine.py` crashed the process (exit `0xC0000409` STATUS_STACK_BUFFER_OVERRUN) because `.gitattributes` `* text=auto` converted LF→CRLF on Windows checkout, corrupting `backend/data/gbm_*.txt` model files. LightGBM's C parser (`LGBM_BoosterCreateFromModelfile`) is `\r\n`-sensitive. Fix: mark `backend/data/gbm_*.txt` as `binary` in `.gitattributes`. Root cause was CRLF corruption, NOT Python 3.14 / lightgbm binary incompatibility.
- **staleness time-bombs (conftest attempt)** (`1f55794` → `f4f6bd1`): 13 tests across 3 files used hardcoded `observed_at` dates (2026-06-25) that exceeded the `WORLD_CUP_DATA_MAX_AGE_HOURS=168` threshold as the wall clock advanced past 2026-07-02. First attempt (`f4f6bd1`) added a `conftest.py` autouse fixture patching `WORLD_CUP_DATA_MAX_AGE_HOURS=0` — but this only works under pytest; the README documents `python -m unittest discover -s tests` which doesn't load conftest.py.
- **staleness time-bombs (final fix)** (`e78883b`): Replaced conftest.py with 13 explicit per-test `patch.object(settings, "WORLD_CUP_DATA_MAX_AGE_HOURS", 0)` patches inside each affected test's existing `with` block. Runner-agnostic (works under both pytest and unittest). 2 staleness-enforcement tests left untouched (they use tighter scope: `=1` patch or `max_age_hours=1` kwarg). Verified: `python -m unittest discover -s tests` → 1906 OK (skipped=1).

### Final HEAD

- `e78883b` (14 commits since MERGE_BASE `6a13b71`)
- Both documented runners green: `python -m unittest discover -s tests` (1906 OK, skipped=1) and `python -m pytest` (1923 passed, 11 skipped).

## Lesson Log (post-merge additions)

- **Validator reuse over reimplementation:** The CLI hand-rolled `direction_correct` and `edge_bucket` logic that already existed as pure functions in `prediction_calibration_service`. When a canonical function exists, reuse it — hand-rolled approximations diverge from system semantics. The P1/P2 bugs in Review 1 would not have existed if the canonical functions had been called from the start.
- **Outcome status gating:** Any code path that reads `record.outcome.actual_outcome` for scoring must gate on `outcome.get("status", "resolved") == "resolved"`. This mirrors `event_store.list_resolved_events` (line 359) and `event_resolve_service` (line 64). Non-resolved statuses (e.g. "invalid") record the marker but exclude from calibration.
- **Git attributes and C parsers:** Text-model files consumed by C libraries (lightgbm, potentially others) are sensitive to `\r\n`. Always mark them `binary` in `.gitattributes` — `* text=auto` will corrupt them on Windows checkout. The crash is a C-level abort that bypasses Python's `except Exception`, killing the whole process.
- **Test runners and fixtures:** `conftest.py` autouse fixtures only work under pytest. If the README documents `python -m unittest discover`, test fixes must be runner-agnostic (per-test patches or `setUp`/`tearDown`). A conftest-only fix is insufficient for repos with multiple documented runners.
- **Time-bomb test fixtures:** Hardcoded dates in test fixtures (`observed_at: "2026-06-25T00:00:00Z"`) become stale as the wall clock advances. For freshness-sensitive code paths, either (a) disable the gate in the test (`patch WORLD_CUP_DATA_MAX_AGE_HOURS=0`), (b) derive dates from `datetime.now(timezone.utc)` in the fixture, or (c) inject `now=` / `max_age_hours=` parameters. Audit the whole test suite when one time-bomb is found — they cluster.

## Accumulated Minor Findings (to triage at final whole-branch review)
- Task 1: 3 Minor (deferred — not yet documented in detail; from Task 1 review)
- Task 2: 3 Minor (deferred — not yet documented in detail; from Task 2 review)
- Task 3: 2 Minor:
  1. Asymmetric deep-copy in `_run_replay_comparison` (verbatim from brief; `replay_record` deep-copies internally; no-mutation test passes). No action needed.
  2. Test fixture contains `signal_direction: "LONG"` (raw external data, out of vocab-lock scope; matches existing Task 1 pattern). No action needed.

## Plan-Bug Fixes (controller-authorized)
- **Task 1:** seed record in `test_load_event_found` was missing 8 EventRecord required fields. Implementer expanded seed record using `tests/test_event_store.py::_make_record` validated shape. Only test scaffolding affected.
- **Task 3:** seed record in `test_exit_code_success` was missing the same 8 EventRecord required fields. Implementer added them by mirroring the `test_load_event_found` record shape (verbatim from Task 1). Only test scaffolding affected; assertion unchanged (`rc == 0`).
- **Root cause:** The plan author wrote seed records that pass business-logic shape but not `EventRecord.model_validate`. Both instances follow the same pattern and were fixed consistently.

## Lesson Log
- Plan authors writing test records that go through `event_store.save_event` must include ALL `EventRecord` required fields. Reference `tests/test_event_store.py::_make_record` for the canonical valid shape. This plan had 2 instances of this bug (Tasks 1 and 3) — a systematic authoring error, not a one-off.
