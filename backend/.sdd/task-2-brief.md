# Task 2: `_render_text` + `_render_json` + wire into `main`

**Files:**
- Modify: `backend/scripts/diagnose_event_quality.py` (add `_render_text`, `_render_json`, `_run_replay_comparison` stub, wire into `main`)
- Modify: `backend/tests/test_diagnose_event_quality.py` (add 6 render tests)

**Interfaces:**
- Consumes: `_extract_phase_data` output shape from Task 1.
- Produces: `_render_text(data: dict, replay_result: dict | None) -> str`, `_render_json(data: dict, replay_result: dict | None) -> str`. `main` now renders output instead of placeholder. `_run_replay_comparison` is a stub (real impl in Task 3).

## Step 1: Write failing tests for `_render_text` + `_render_json`

Add to `backend/tests/test_diagnose_event_quality.py` (before `if __name__ == "__main__":`):

```python
class TestRenderText(unittest.TestCase):
    def test_render_text_includes_all_phases(self):
        """Text output contains all 6 phase headers + guardrail + final direction."""
        from diagnose_event_quality import _render_text, _extract_phase_data
        record = _sample_record()
        data = _extract_phase_data(record)
        text = _render_text(data, replay_result=None)
        self.assertIn("Phase 1: Decision Quality", text)
        self.assertIn("Phase 2: Market Quality", text)
        self.assertIn("Phase 3: Prediction Calibration", text)
        self.assertIn("Phase 4: Source Reliability", text)
        self.assertIn("Phase 5: LLM Telemetry", text)
        self.assertIn("Phase 6: Execution Quality", text)
        self.assertIn("Guardrails", text)
        self.assertIn("Final Direction", text)

    def test_render_text_includes_event_header(self):
        """Text output starts with Event: <id> (<title>)."""
        from diagnose_event_quality import _render_text, _extract_phase_data
        record = _sample_record()
        data = _extract_phase_data(record)
        text = _render_text(data, replay_result=None)
        self.assertIn("Event: test-1", text)
        self.assertIn("Will X happen?", text)

    def test_render_text_missing_overlay_shows_skipped(self):
        """Missing overlay shows 'Skipped (overlay not built)'."""
        from diagnose_event_quality import _render_text, _extract_phase_data
        record = _sample_record()
        del record["llm_telemetry"]
        data = _extract_phase_data(record)
        text = _render_text(data, replay_result=None)
        self.assertIn("Skipped (overlay not built)", text)

    def test_render_text_uses_max_safe_size_label(self):
        """Text output uses 'max_safe_size' label, NOT 'max_safe_position_size'."""
        from diagnose_event_quality import _render_text, _extract_phase_data
        record = _sample_record()
        data = _extract_phase_data(record)
        text = _render_text(data, replay_result=None)
        self.assertIn("max_safe_size", text)
        # The banned term should not appear as a CLI-generated label
        self.assertNotIn("max_safe_position_size", text)


class TestRenderJson(unittest.TestCase):
    def test_render_json_valid_structure(self):
        """JSON output parses, has event_id/phases/guardrails/final_direction keys."""
        from diagnose_event_quality import _render_json, _extract_phase_data
        record = _sample_record()
        data = _extract_phase_data(record)
        json_str = _render_json(data, replay_result=None)
        parsed = json.loads(json_str)
        self.assertEqual(parsed["event_id"], "test-1")
        self.assertEqual(parsed["event_title"], "Will X happen?")
        self.assertIn("phases", parsed)
        self.assertIn("decision_quality", parsed["phases"])
        self.assertIn("execution_quality", parsed["phases"])
        self.assertEqual(
            parsed["phases"]["execution_quality"]["max_safe_size"], 1000.0
        )
        self.assertNotIn(
            "max_safe_position_size", parsed["phases"]["execution_quality"]
        )
        self.assertIn("guardrails", parsed)
        self.assertIn("fired_rules", parsed["guardrails"])
        self.assertEqual(parsed["final_direction"], "YES")
        # replay_comparison is null when no replay run
        self.assertIsNone(parsed["replay_comparison"])

    def test_render_json_replay_null_without_flag(self):
        """replay_comparison is null when replay_result is None."""
        from diagnose_event_quality import _render_json, _extract_phase_data
        record = _sample_record()
        data = _extract_phase_data(record)
        json_str = _render_json(data, replay_result=None)
        parsed = json.loads(json_str)
        self.assertIsNone(parsed["replay_comparison"])
```

## Step 2: Run tests to verify they fail

Run: `cd backend && python -m pytest tests/test_diagnose_event_quality.py::TestRenderText tests/test_diagnose_event_quality.py::TestRenderJson -v`
Expected: ImportError / AttributeError — `_render_text` / `_render_json` not defined.

## Step 3: Add `_render_text` + `_render_json` + `_run_replay_comparison` stub to `diagnose_event_quality.py`

In `backend/scripts/diagnose_event_quality.py`, insert BEFORE `def main`:

```python
def _render_text(data: dict[str, Any], replay_result: dict[str, Any] | None) -> str:
    """Render human-readable text output.

    Vocabulary lock (§8.1): CLI-generated labels (the fixed strings here)
    avoid banned terms. Raw data values (event_title, downgrade_reason,
    fired_rules contents) flow through as-is and may contain trading terms.
    """
    lines: list[str] = []
    eid = data.get("event_id", "?")
    title = data.get("event_title", "?")
    lines.append(f"Event: {eid} ({title})")
    lines.append("─" * 50)
    lines.append("")

    phases = data.get("phases", {})

    # Phase 1: Decision Quality
    lines.append("📊 Phase 1: Decision Quality")
    dq = phases.get("decision_quality")
    if dq is None:
        lines.append("   ⏭️ Skipped (overlay not built)")
    else:
        lines.append("   ✅ Enabled")
        lines.append(f"   evidence_strength: {dq.get('evidence_strength')}")
        lines.append(f"   conflict_score: {dq.get('conflict_score')}")
        lines.append(f"   downgrade_reason: {dq.get('downgrade_reason')}")
    lines.append("")

    # Phase 2: Market Quality
    lines.append("📊 Phase 2: Market Quality")
    mq = phases.get("market_quality")
    if mq is None:
        lines.append("   ⏭️ Skipped (overlay not built)")
    else:
        degraded = mq.get("degraded")
        if degraded:
            lines.append(f"   ❌ Degraded ({mq.get('degrade_reason', 'unknown')})")
        else:
            lines.append("   ✅ Enabled")
        lines.append(f"   wide_spread_flag: {mq.get('wide_spread_flag')}")
        lines.append(f"   low_liquidity_flag: {mq.get('low_liquidity_flag')}")
    lines.append("")

    # Phase 3: Prediction Calibration
    lines.append("📊 Phase 3: Prediction Calibration")
    pc = phases.get("prediction_calibration")
    if pc is None:
        lines.append("   ⏭️ Skipped (no actionable_recommendation)")
    else:
        lines.append(f"   snapshot_recommendation: {pc.get('snapshot_recommendation')}")
        lines.append(f"   calibration_status: {pc.get('calibration_status')}")
        lines.append(f"   edge_bucket: {pc.get('edge_bucket')}")
        lines.append(f"   direction_correct: {pc.get('direction_correct')}")
    lines.append("")

    # Phase 4: Source Reliability
    lines.append("📊 Phase 4: Source Reliability")
    sr = phases.get("source_reliability")
    if sr is None:
        lines.append("   ⏭️ Skipped (overlay not built)")
    else:
        lines.append(f"   overall_score: {sr.get('overall_score')}")
        lines.append(f"   source_count: {sr.get('source_count')}")
        lines.append(f"   domain_diversity: {sr.get('domain_diversity')}")
    lines.append("")

    # Phase 5: LLM Telemetry
    lines.append("📊 Phase 5: LLM Telemetry")
    lt = phases.get("llm_telemetry")
    if lt is None:
        lines.append("   ⏭️ Skipped (overlay not built)")
    else:
        lines.append(f"   degraded_mode: {lt.get('degraded_mode')}")
        lines.append(f"   analysis_quality: {lt.get('analysis_quality')}")
        lines.append(f"   total_tokens: {lt.get('total_tokens')}")
        lines.append(f"   estimated_token_cost: ${lt.get('estimated_token_cost')}")
    lines.append("")

    # Phase 6: Execution Quality
    lines.append("📊 Phase 6: Execution Quality")
    eq = phases.get("execution_quality")
    if eq is None:
        lines.append("   ⏭️ Skipped (overlay not built)")
    else:
        lines.append(f"   executable: {eq.get('executable')}")
        lines.append(f"   estimated_slippage_pct: {eq.get('estimated_slippage_pct')}")
        lines.append(f"   stale_price_flag: {eq.get('stale_price_flag')}")
        lines.append(f"   max_safe_size: {eq.get('max_safe_size')}")
    lines.append("")

    # Guardrails
    lines.append("🛡️ Guardrails")
    fired = data.get("guardrails", {}).get("fired_rules", [])
    lines.append(f"   fired_rules: {fired}")
    lines.append("")

    # Final Direction
    final = data.get("final_direction")
    lines.append(f"🎯 Final Direction: {final} (displayed)")
    lines.append("")

    # Replay comparison (only if --replay)
    if replay_result is not None:
        lines.append("[Replay Comparison]")
        lines.append(f"   all_on  direction: {replay_result.get('all_on_direction')}")
        lines.append(f"   all_off direction: {replay_result.get('all_off_direction')}")
        lines.append(f"   Delta: {replay_result.get('delta')}")

    return "\n".join(lines)


def _render_json(data: dict[str, Any], replay_result: dict[str, Any] | None) -> str:
    """Render JSON output. replay_comparison is null when no replay run."""
    output = {
        "event_id": data.get("event_id"),
        "event_title": data.get("event_title"),
        "phases": data.get("phases", {}),
        "guardrails": data.get("guardrails", {}),
        "final_direction": data.get("final_direction"),
        "replay_comparison": replay_result,
    }
    return json.dumps(output, indent=2, default=str, ensure_ascii=False)


def _run_replay_comparison(record: dict[str, Any]) -> dict[str, Any]:
    """Run all_on vs all_off replay, return direction delta. (Stub —
    implemented in Task 3.)"""
    return {"all_on_direction": None, "all_off_direction": None, "delta": "no_change"}
```

Then modify `main` to call the renderers. Replace the placeholder section:

```python
    data = _extract_phase_data(record)

    # Rendering + replay added in later tasks
    _print(f"[diagnose_event_quality] event_id={data['event_id']} "
           f"(rendering not yet implemented)")
    return 0
```

With:

```python
    data = _extract_phase_data(record)

    # Replay comparison (only if --replay flag)
    replay_result: dict[str, Any] | None = None
    if args.replay:
        replay_result = _run_replay_comparison(record)

    if args.json:
        _print(_render_json(data, replay_result))
    else:
        _print(_render_text(data, replay_result))
    return 0
```

## Step 4: Run tests to verify they pass

Run: `cd backend && python -m pytest tests/test_diagnose_event_quality.py -v`
Expected: 11 tests PASS (5 from Task 1 + 4 text + 2 json = 11).

## Step 5: Commit

Write commit message to `backend/.commit_msg.tmp`:
```
feat(diagnose-cli): add _render_text + _render_json, wire into main

_render_text produces 6-phase decomposition with emoji headers + indented
fields. Missing overlays show "Skipped (overlay not built)". Uses
max_safe_size label (not max_safe_position_size) per vocabulary lock.
_render_json produces structured JSON with replay_comparison=null when
no replay. main now renders output instead of placeholder.
_run_replay_comparison stub added (implemented in Task 3). 11 tests pass.
```

Run:
```bash
cd "e:\Github\Prediction Market Reality Filter"
git add backend/scripts/diagnose_event_quality.py backend/tests/test_diagnose_event_quality.py
git commit -F backend/.commit_msg.tmp
Remove-Item backend/.commit_msg.tmp
```
