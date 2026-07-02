# Task 3 Brief: `_run_replay_comparison` real impl + exit code tests + vocabulary lock test

> **This file is your requirements.** Read it first. It contains the exact values, code, test commands, and commit message to use verbatim. The plan file (`docs/superpowers/plans/2026-07-02-quality-diagnosis-cli.md`) is the source of truth — this brief extracts Task 3 verbatim from it.

## Context (where this task fits)

You are implementing the **final task** of a 3-task plan for the Quality Diagnosis CLI (`backend/scripts/diagnose_event_quality.py`, spec §4.3 / P2 #25). Tasks 1 and 2 are complete and committed (`e75af1c`, `f0818f9`). The CLI currently has `_load_event`, `_extract_phase_data`, `_render_text`, `_render_json`, a `_run_replay_comparison` **stub**, and a wired-up `main`. 11 tests pass.

Your job: replace the stub with a real replay-based comparison, add 6 tests (3 replay + 2 exit code + 1 vocabulary lock), run the full backend suite, and commit. After this, the CLI is feature-complete per spec.

## Files

- Modify: `backend/scripts/diagnose_event_quality.py` (replace `_run_replay_comparison` stub with real implementation)
- Modify: `backend/tests/test_diagnose_event_quality.py` (add 6 tests: 3 replay + 2 exit code + 1 vocab)

## Interfaces

- Consumes: `replay_record` from `app.replay.runner`, `ReplayConfig` from `app.replay.config` (import paths per Global Constraints — NOT `from app.replay`).
- Produces: Complete CLI with all features. `_run_replay_comparison(record) -> dict` returns `{"all_on_direction": str|None, "all_off_direction": str|None, "delta": "changed"|"no_change"}`.

## Global Constraints (bind this task — copy verbatim from plan)

- **Vocabulary lock scope:** CLI-generated labels/field names (fixed strings in render functions) must NOT contain `long`/`short`/`buy`/`sell`/`position`/`kelly`/`order` as whole words (case-insensitive). Raw external data values (`event_id`, `event_title`, `downgrade_reason`, `degrade_reason`, `fired_rules` contents) are NOT scanned — they are user/operator-authored and may contain trading terms. `max_safe_position_size` field is renamed to `max_safe_size` in ALL output (display-only rename; reads `max_safe_position_size` from record, emits as `max_safe_size`).
- **Pure read-only:** No writes to any store. No LLM calls. No network fetches. No mutations to the input record (deep-copy before replay).
- **CLI pattern:** argparse (not click). `main(argv: list[str] | None = None) -> int` entry point. `_print()` helper for UTF-8 stdout. `if __name__ == "__main__": sys.exit(main())`. Module path `scripts.diagnose_event_quality` (run via `python -m`).
- **Import paths:** `from app.memory.event_store import get_event` (NOT `get` — function is `get_event`). `from app.replay.config import ReplayConfig` and `from app.replay.runner import replay_record` (NOT `from app.replay` — `__init__.py` is empty). Same pattern as `backend/scripts/analyze_feature_flag_impact.py:38-39`.
- **event_store return shape:** `get_event(event_id)` returns store entry `{"event_id": ..., "record": ...}` or None. Caller must extract `entry.get("record")`.
- **Exit codes:** 0 success, 1 event_id not found, 2 other errors.
- **No new dependencies:** `requirements.txt` unchanged.

## Cross-task context (what the brief cannot know — from Tasks 1 & 2)

- The `_run_replay_comparison` stub currently lives at `backend/scripts/diagnose_event_quality.py` around lines 285-288. Its exact current text is:

```python
def _run_replay_comparison(record: dict[str, Any]) -> dict[str, Any]:
    """Run all_on vs all_off replay, return direction delta. (Stub —
    implemented in Task 3.)"""
    return {"all_on_direction": None, "all_off_direction": None, "delta": "no_change"}
```

Replace this **entire** function (signature, docstring, body) with the real implementation in Step 3 below. Keep the function in the same location (after `_render_json`, before `main`).

- The existing test file `backend/tests/test_diagnose_event_quality.py` already has: imports (`copy`, `json`, `os`, `sys`, `tempfile`, `unittest`, `Path`, `patch`), `_sample_record()` helper, and test classes `TestLoadEvent`, `TestExtractPhaseData`, `TestRenderText`, `TestRenderJson`. The new test classes go **before** the `if __name__ == "__main__":` block (which is `unittest.main()`).

- `_sample_record()` returns a synthetic record with all 6 overlays + `final_displayed_direction: "YES"` + `guardrail_fired: []`. It bypasses EventRecord validation (used only for extract/render/replay tests, not event_store tests).

## Steps (execute in order)

### Step 1: Write failing tests for replay + exit codes + vocabulary lock

Add to `backend/tests/test_diagnose_event_quality.py` (before `if __name__ == "__main__":`):

```python
class TestReplayComparison(unittest.TestCase):
    def test_replay_comparison_no_change(self):
        """When all_on and all_off produce same direction, delta=no_change.
        Uses a record with no overlays — both configs fall back to
        actionable_recommendation.direction = "YES"."""
        from app.core.config import settings
        from diagnose_event_quality import _run_replay_comparison
        record = _sample_record()
        # Strip all overlays so all_off and all_on both start fresh.
        # With DECISION_QUALITY_ENABLED=False and GUARDRAILS_ENABLED=False
        # (test env default), all_on inherits these → no overlay runs →
        # direction falls back to actionable_recommendation.direction.
        for key in ("decision_quality", "market_quality", "source_reliability",
                    "llm_telemetry", "execution_quality",
                    "final_displayed_direction", "final_downgrade_reason",
                    "guardrail_fired"):
            record.pop(key, None)
        flags = {
            "DECISION_QUALITY_ENABLED": False,
            "MARKET_QUALITY_ENABLED": False,
            "SOURCE_RELIABILITY_ENABLED": False,
            "LLM_TELEMETRY_ENABLED": False,
            "GUARDRAILS_ENABLED": False,
            "EXECUTION_QUALITY_ENABLED": False,
        }
        with patch.multiple(settings, **flags):
            result = _run_replay_comparison(record)
        self.assertEqual(result["all_off_direction"], "YES")
        self.assertEqual(result["all_on_direction"], "YES")
        self.assertEqual(result["delta"], "no_change")

    def test_replay_comparison_changed(self):
        """When all_on and all_off produce different directions, delta=changed.
        Enables DQ + guardrails (uncalibrated_category rule) so all_on
        downgrades YES → WAIT, while all_off leaves direction as YES."""
        from app.core.config import settings
        from diagnose_event_quality import _run_replay_comparison
        record = _sample_record()
        # Give the record support evidence so DQ keeps YES (not downgraded
        # to WAIT for empty evidence) — same pattern as test_replay_runner.py
        record["evidence_breakdown"] = [
            {
                "direction": "support",
                "source": "test",
                "title": "test evidence",
                "strength": 0.8,
                "credibility": 0.8,
                "rationale_zh": "",
            }
        ]
        # Also need legacy_analysis for DQ to compute consensus_level
        record["legacy_analysis"] = {
            "ai_probability": 62.0,
            "market_probability": 50.0,
            "signal": "WATCHLIST",
            "signal_direction": "LONG",
            "signal_strength": "MEDIUM",
            "evidence_strength": 0.7,
            "evidence_conflict_score": 0.2,
            "risk_flags": [],
            "analysis_quality": "llm",
        }
        record["market_quote"] = {
            "spread_pct": 1.0, "liquidity": 5000.0, "volume": 1000.0
        }
        record["sentiment_profile"] = {"summary": "neutral", "articles": []}
        record["source"] = {"type": "prediction_market", "platform": "manifold"}
        # Enable guardrails + DQ + uncalibrated_category rule so all_on
        # downgrades YES → WAIT (empty calibration store in test env)
        flags = {
            "DECISION_QUALITY_ENABLED": True,
            "GUARDRAILS_ENABLED": True,
            "GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT": True,
            "GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT": False,
            "GUARDRAIL_HIGH_CONFLICT_BLOCKS_ACT": False,
            "GUARDRAIL_MARKET_NOT_EXECUTABLE_BLOCKS_ACT": False,
            "EXECUTION_QUALITY_ENABLED": False,
        }
        with patch.multiple(settings, **flags):
            result = _run_replay_comparison(record)
        # all_off leaves direction as YES (from actionable_recommendation)
        self.assertEqual(result["all_off_direction"], "YES")
        # all_on downgrades YES → WAIT (uncalibrated category fires)
        self.assertEqual(result["all_on_direction"], "WAIT")
        self.assertEqual(result["delta"], "changed")

    def test_replay_no_mutation_of_input(self):
        """_run_replay_comparison must not mutate the input record."""
        from app.core.config import settings
        from diagnose_event_quality import _run_replay_comparison
        record = _sample_record()
        snapshot = copy.deepcopy(record)
        flags = {"DECISION_QUALITY_ENABLED": False, "GUARDRAILS_ENABLED": False}
        with patch.multiple(settings, **flags):
            _run_replay_comparison(record)
        self.assertEqual(record, snapshot)


class TestExitCodes(unittest.TestCase):
    def test_exit_code_not_found(self):
        """Missing event → exit 1, error to stderr."""
        from app.core.config import settings
        from diagnose_event_quality import main
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "event_store.json"
            import io as _io
            orig_stderr = sys.stderr
            try:
                with patch.object(settings, "EVENT_STORE_FILE", str(store_path)):
                    sys.stderr = _io.StringIO()
                    rc = main(["nonexistent"])
                self.assertEqual(rc, 1)
                self.assertIn("not found", sys.stderr.getvalue())
            finally:
                sys.stderr = orig_stderr

    def test_exit_code_success(self):
        """Found event → exit 0."""
        from app.core.config import settings
        from app.memory import event_store
        from diagnose_event_quality import main
        record = {
            "event_id": "test-1",
            "event_title": "Will X happen?",
            "source": {"type": "prediction_market", "platform": "manifold"},
            "market_quote": {"spread_pct": 1.0, "liquidity": 5000.0, "volume": 1000.0},
            "probability": {"baseline": 50.0, "estimated": 62.0, "change": 12.0},
            "actionable_recommendation": {
                "direction": "YES",
                "confidence": "medium",
                "suggested_allocation_pct": 2.0,
                "edge": 12.0,
                "risk_level": "medium",
                "rationale": "test",
                "calibration_status": "uncalibrated_provisional",
            },
            "legacy_analysis": {
                "ai_probability": 62.0,
                "market_probability": 50.0,
                "signal": "WATCHLIST",
                "signal_direction": "LONG",
                "signal_strength": "MEDIUM",
                "evidence_strength": 0.7,
                "evidence_conflict_score": 0.2,
                "risk_flags": [],
                "analysis_quality": "llm",
            },
            "evidence_breakdown": [],
            "sentiment_profile": {"summary": "neutral", "articles": []},
        }
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "event_store.json"
            import io as _io
            orig_stdout = sys.stdout
            orig_stderr = sys.stderr
            try:
                with patch.object(settings, "EVENT_STORE_FILE", str(store_path)):
                    event_store.save_event(record)
                    sys.stdout = _io.StringIO()
                    sys.stderr = _io.StringIO()
                    rc = main(["test-1", "--json"])
                self.assertEqual(rc, 0)
            finally:
                sys.stdout = orig_stdout
                sys.stderr = orig_stderr


class TestVocabularyLock(unittest.TestCase):
    def test_vocabulary_lock_cli_labels(self):
        """CLI-generated labels/field names (fixed strings in render
        functions) contain no banned terms. Raw external data values
        (event_title, downgrade_reason, fired_rules) are NOT scanned —
        they are user-authored and may contain trading terms (§8.1).
        _sample_record uses clean values (no trading terms), so a full
        output scan is equivalent to scanning only CLI labels here."""
        import re
        from diagnose_event_quality import _render_text, _render_json, _extract_phase_data
        record = _sample_record()
        data = _extract_phase_data(record)
        text = _render_text(data, replay_result=None)
        json_str = _render_json(data, replay_result=None)

        # Banned terms as whole words (case-insensitive)
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        for term in banned:
            pattern = r"\b" + re.escape(term) + r"\b"
            self.assertFalse(
                re.search(pattern, text, re.IGNORECASE),
                f"banned term '{term}' found in text output",
            )
            self.assertFalse(
                re.search(pattern, json_str, re.IGNORECASE),
                f"banned term '{term}' found in JSON output",
            )
```

### Step 2: Run tests to verify they fail

Run: `cd backend && python -m pytest tests/test_diagnose_event_quality.py::TestReplayComparison::test_replay_comparison_changed tests/test_diagnose_event_quality.py::TestExitCodes -v`
Expected: Replay test fails (stub returns no_change always — `test_replay_comparison_changed` fails because stub doesn't run replay). Exit code tests may pass (main already returns 1/0 correctly).

### Step 3: Replace `_run_replay_comparison` stub with real implementation

In `backend/scripts/diagnose_event_quality.py`, replace the stub:

```python
def _run_replay_comparison(record: dict[str, Any]) -> dict[str, Any]:
    """Run all_on vs all_off replay, return direction delta. (Stub —
    implemented in Task 3.)"""
    return {"all_on_direction": None, "all_off_direction": None, "delta": "no_change"}
```

With:

```python
def _run_replay_comparison(record: dict[str, Any]) -> dict[str, Any]:
    """Run all_on vs all_off replay, return direction delta.

    Uses replay_record from app.replay.runner (NOT app.replay — __init__.py
    is empty, same pattern as analyze_feature_flag_impact.py). Deep-copies
    the record before replay to avoid mutation (replay_record already
    deep-copies internally, but defensive — the contract says no mutation).

    Returns dict with:
      - all_on_direction: str | None (YES/NO/WAIT/AVOID or None)
      - all_off_direction: str | None
      - delta: "changed" | "no_change"
    """
    from app.replay.config import ReplayConfig
    from app.replay.runner import replay_record

    _DIRECTIONS = ("YES", "NO", "WAIT", "AVOID")

    def _effective_direction(replayed: dict[str, Any]) -> str | None:
        """Same fallback chain as analyze_feature_flag_impact._effective_direction:
        final_displayed_direction → actionable_recommendation.direction → None.
        probability.direction is NOT used (returns rising/falling/stable)."""
        dir_val = replayed.get("final_displayed_direction")
        if dir_val in _DIRECTIONS:
            return dir_val
        rec = replayed.get("actionable_recommendation")
        if isinstance(rec, dict):
            rec_dir = rec.get("direction")
            if rec_dir in _DIRECTIONS:
                return rec_dir
        return None

    # Deep-copy to avoid mutation (replay_record already deep-copies, but
    # defensive — the contract says no mutation of input)
    record_copy = copy.deepcopy(record)
    replayed_on = replay_record(record_copy, ReplayConfig.preset_all_on())
    replayed_off = replay_record(record, ReplayConfig.preset_all_off())

    dir_on = _effective_direction(replayed_on)
    dir_off = _effective_direction(replayed_off)
    delta = "changed" if dir_on != dir_off else "no_change"

    return {
        "all_on_direction": dir_on,
        "all_off_direction": dir_off,
        "delta": delta,
    }
```

### Step 4: Run all tests to verify they pass

Run: `cd backend && python -m pytest tests/test_diagnose_event_quality.py -v`
Expected: 17 tests PASS (11 from Tasks 1-2 + 3 replay + 2 exit code + 1 vocab = 17).

### Step 5: Run full backend suite for regression check

Run: `cd backend && python -m pytest --ignore=tests/test_gbm_engine.py -q`
Expected: all PASS, 0 failures (pre-existing `test_gbm_engine.py` excluded as env issue).

### Step 6: Commit

Write commit message to `backend/.commit_msg.tmp`:
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

Run (PowerShell):
```powershell
cd "e:\Github\Prediction Market Reality Filter"
git add backend/scripts/diagnose_event_quality.py backend/tests/test_diagnose_event_quality.py
git commit -F backend/.commit_msg.tmp
Remove-Item backend/.commit_msg.tmp
```

## Report Contract

Write your full report to `backend/.sdd/task-3-report.md` (create the file). The report MUST include:
- **What was implemented** (functions added/modified, tests added)
- **TDD evidence**: RED step (Step 2) command + output excerpt; GREEN step (Step 4) command + output excerpt (must show 17 passed); Step 5 full backend suite command + pass/fail summary
- **Files changed**
- **Commit hash** (from `git rev-parse HEAD` after commit)
- **Self-review findings** (completeness, quality, discipline, testing, concerns)
- **Status**: one of DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED

Return in your final message ONLY: status, commit hash, one-line test summary (e.g. "17/17 diagnose tests pass; full backend suite green"), and any concerns. Everything else goes in the report file.

## PowerShell / Windows notes

- The repo is on Windows. Use PowerShell-compatible commands. Do NOT use `cat <<'EOF'` heredoc (PowerShell doesn't support it) — write commit messages to a temp file and use `git commit -F`.
- Do NOT write anything under `.git/` (path is denylisted on this Windows env). All SDD artifacts go under `backend/.sdd/`.
- The working directory for `python -m pytest` is `backend/` (i.e. `cd backend && python -m pytest ...`).
