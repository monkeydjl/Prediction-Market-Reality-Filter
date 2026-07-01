# Quality Diagnosis CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `backend/scripts/diagnose_event_quality.py` — a single-event debugging CLI that decomposes an event into 6 quality layers + guardrail state + final direction, with optional `--json` and `--replay` (all_on vs all_off) flags. Closes spec §4.3 (P2 #25).

**Architecture:** Single new CLI script following the project's existing argparse pattern (same as `source_trust_registry_cli.py` / `review_queue_cli.py`). Pure read-only: loads event from `event_store.get_event()`, extracts 6 overlay phases + guardrail + final direction into a dict, renders as text or JSON. Optional `--replay` reuses `replay_record` from `app.replay.runner` to compare all_on vs all_off directions. No new dependencies.

**Tech Stack:** Python 3.14 stdlib only (argparse, json, sys, io, copy). Reuses `app.memory.event_store`, `app.replay.config`, `app.replay.runner`. No new deps in `requirements.txt`.

## Global Constraints

Copied verbatim from spec `2026-07-02-quality-diagnosis-cli-design.md`:

- **Vocabulary lock scope:** CLI-generated labels/field names (fixed strings in render functions) must NOT contain `long`/`short`/`buy`/`sell`/`position`/`kelly`/`order` as whole words (case-insensitive). Raw external data values (`event_id`, `event_title`, `downgrade_reason`, `degrade_reason`, `fired_rules` contents) are NOT scanned — they are user/operator-authored and may contain trading terms. `max_safe_position_size` field is renamed to `max_safe_size` in ALL output (display-only rename; reads `max_safe_position_size` from record, emits as `max_safe_size`).
- **Pure read-only:** No writes to any store. No LLM calls. No network fetches. No mutations to the input record (deep-copy before replay).
- **CLI pattern:** argparse (not click). `main(argv: list[str] | None = None) -> int` entry point. `_print()` helper for UTF-8 stdout. `if __name__ == "__main__": sys.exit(main())`. Module path `scripts.diagnose_event_quality` (run via `python -m`).
- **Import paths:** `from app.memory.event_store import get_event` (NOT `get` — function is `get_event`). `from app.replay.config import ReplayConfig` and `from app.replay.runner import replay_record` (NOT `from app.replay` — `__init__.py` is empty). Same pattern as `backend/scripts/analyze_feature_flag_impact.py:38-39`.
- **event_store return shape:** `get_event(event_id)` returns store entry `{"event_id": ..., "record": ...}` or None. Caller must extract `entry.get("record")`.
- **event_store test fixture pattern:** Tests do NOT use `event_store._save()` or `event_store._STORE_PATH` (these don't exist). Use `patch.object(settings, "EVENT_STORE_FILE", str(path))` + `save_event(record)` to seed the store, OR (simpler) patch `diagnose_event_quality._load_event` to return a synthetic entry directly. The extract/render/replay tests bypass event_store entirely — they call `_extract_phase_data(record)` / `_render_text(data)` / `_run_replay_comparison(record)` directly with a synthetic record dict.
- **Exit codes:** 0 success, 1 event_id not found, 2 other errors.
- **No new files except:** `backend/scripts/diagnose_event_quality.py` (CLI) + `backend/tests/test_diagnose_event_quality.py` (tests).
- **No new dependencies:** `requirements.txt` unchanged.

---

## File Structure

- **Create** `backend/scripts/diagnose_event_quality.py` — CLI entry point. Contains `_load_event`, `_extract_phase_data`, `_render_text`, `_render_json`, `_run_replay_comparison`, `main`.
- **Create** `backend/tests/test_diagnose_event_quality.py` — 17 unit tests (5 load/extract + 6 render + 6 replay/exit/vocab).

No existing files modified. No new dependencies.

---

## Task 1: `_load_event` + `_extract_phase_data` + skeleton `main`

**Files:**
- Create: `backend/scripts/diagnose_event_quality.py`
- Create: `backend/tests/test_diagnose_event_quality.py`

**Interfaces:**
- Produces: `_load_event(event_id: str) -> dict | None` (returns store entry or None), `_extract_phase_data(record: dict) -> dict` (returns dict with 6 phase keys + guardrail + final_direction), `main(argv) -> int` (skeleton — loads + extracts, prints nothing useful yet).

- [ ] **Step 1: Write failing tests for `_load_event` + `_extract_phase_data`**

Create `backend/tests/test_diagnose_event_quality.py`:

```python
"""Unit tests for diagnose_event_quality CLI."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure backend/ is on sys.path (same pattern as other test files)
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# scripts/ is not a package — add it to sys.path so we can import
# diagnose_event_quality as a top-level module (same pattern as
# test_analyze_feature_flag_impact.py importing analyze_feature_flag_impact).
_SCRIPTS_DIR = _BACKEND_DIR / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _sample_record() -> dict:
    """A minimal record with all 6 overlays populated for testing.
    Bypasses EventRecord validation — used for extract/render/replay tests
    that don't touch event_store."""
    return {
        "event_id": "test-1",
        "event_title": "Will X happen?",
        "actionable_recommendation": {
            "direction": "YES",
            "confidence": "medium",
            "calibration_status": "uncalibrated_provisional",
            "edge": 12.0,
        },
        "probability": {
            "baseline": 50.0,
            "estimated": 62.0,
            "change": 12.0,
        },
        "decision_quality": {
            "evidence_strength": 0.82,
            "conflict_score": 0.15,
            "downgrade_reason": None,
            "displayed_direction": "YES",
        },
        "market_quality": {
            "degraded": False,
            "degrade_reason": None,
            "wide_spread_flag": False,
            "low_liquidity_flag": False,
        },
        "source_reliability": {
            "overall_score": 0.78,
            "source_count": 4,
            "domain_diversity": 3,
        },
        "llm_telemetry": {
            "degraded_mode": False,
            "total_tokens": 1247,
            "estimated_token_cost": 0.0018,
            "analysis_quality": "llm",
        },
        "execution_quality": {
            "executable": True,
            "estimated_slippage_pct": 0.3,
            "stale_price_flag": False,
            "max_safe_position_size": 1000.0,
        },
        "guardrail_fired": [],
        "final_displayed_direction": "YES",
    }


class TestLoadEvent(unittest.TestCase):
    def test_load_event_found(self):
        """When event exists in store, _load_event returns the store entry."""
        from app.memory import event_store
        from app.core.config import settings
        from diagnose_event_quality import _load_event
        # Use a minimal valid record that passes EventRecord validation.
        # event_store.save_event runs normalize_event_record + model_validate,
        # so the record must have required fields. Use the same shape as
        # test_operational_readiness.py: patch EVENT_STORE_FILE + save_event.
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
            with patch.object(settings, "EVENT_STORE_FILE", str(store_path)):
                event_store.save_event(record)
                entry = _load_event("test-1")
            self.assertIsNotNone(entry)
            self.assertEqual(entry["event_id"], "test-1")
            self.assertEqual(entry["record"]["event_title"], "Will X happen?")

    def test_load_event_not_found(self):
        """When event does not exist, _load_event returns None."""
        from app.core.config import settings
        from diagnose_event_quality import _load_event
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "event_store.json"
            with patch.object(settings, "EVENT_STORE_FILE", str(store_path)):
                # Empty store (file doesn't exist yet — get_event returns None)
                entry = _load_event("nonexistent")
            self.assertIsNone(entry)


class TestExtractPhaseData(unittest.TestCase):
    def test_extract_phase_data_full(self):
        """All 6 overlays present → all phases populated."""
        from diagnose_event_quality import _extract_phase_data
        record = _sample_record()
        data = _extract_phase_data(record)
        self.assertEqual(data["event_id"], "test-1")
        self.assertEqual(data["event_title"], "Will X happen?")
        self.assertIn("decision_quality", data["phases"])
        self.assertEqual(data["phases"]["decision_quality"]["evidence_strength"], 0.82)
        self.assertIn("market_quality", data["phases"])
        self.assertIn("source_reliability", data["phases"])
        self.assertIn("llm_telemetry", data["phases"])
        self.assertIn("execution_quality", data["phases"])
        # max_safe_position_size renamed to max_safe_size per §8.1
        self.assertEqual(data["phases"]["execution_quality"]["max_safe_size"], 1000.0)
        self.assertNotIn("max_safe_position_size", data["phases"]["execution_quality"])
        # guardrail + final direction
        self.assertEqual(data["guardrails"]["fired_rules"], [])
        self.assertEqual(data["final_direction"], "YES")

    def test_extract_phase_data_missing_overlays(self):
        """When some overlays are absent, they show as None."""
        from diagnose_event_quality import _extract_phase_data
        record = _sample_record()
        del record["llm_telemetry"]
        del record["execution_quality"]
        data = _extract_phase_data(record)
        self.assertIsNone(data["phases"]["llm_telemetry"])
        self.assertIsNone(data["phases"]["execution_quality"])
        # Other phases still populated
        self.assertIsNotNone(data["phases"]["decision_quality"])

    def test_extract_phase_data_no_mutation(self):
        """_extract_phase_data must not mutate the input record."""
        from diagnose_event_quality import _extract_phase_data
        record = _sample_record()
        snapshot = copy.deepcopy(record)
        _extract_phase_data(record)
        self.assertEqual(record, snapshot)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_diagnose_event_quality.py -v`
Expected: ImportError — `diagnose_event_quality` module not found.

- [ ] **Step 3: Create `diagnose_event_quality.py` with `_load_event` + `_extract_phase_data` + skeleton `main`**

Create `backend/scripts/diagnose_event_quality.py`:

```python
"""Quality Diagnosis CLI (Spec §4.3, P2 #25).

Single-event debugging CLI: decomposes an event into its 6 quality layers
(Decision Quality, Market Quality, Prediction Calibration, Source
Reliability, LLM Telemetry, Execution Quality) plus guardrail state and
final direction. Optional --json and --replay flags.

Pure read-only: no writes, no LLM calls, no network fetches.

Usage:
    python -m scripts.diagnose_event_quality EVENT_ID [--json] [--replay]
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import sys
from typing import Any

# UTF-8 stdout for Windows GBK console safety (same convention as
# source_trust_registry_cli.py / review_queue_cli.py).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, io.UnsupportedOperation):  # pragma: no cover
    pass


def _print(text: str) -> None:
    """Print with UTF-8 stdout (Windows GBK safety)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def _load_event(event_id: str) -> dict[str, Any] | None:
    """Load event from event_store. Returns the store entry (which contains
    a ``record`` key) or None if not found. Caller must extract
    ``entry.get("record")`` to get the event record dict."""
    from app.memory.event_store import get_event
    return get_event(event_id)


def _extract_phase_data(record: dict[str, Any]) -> dict[str, Any]:
    """Extract 6 phases + guardrail + final direction from a record.

    Returns a dict with keys:
      - event_id, event_title
      - phases: {decision_quality, market_quality, prediction_calibration,
                 source_reliability, llm_telemetry, execution_quality}
        (each value is a dict of fields, or None if overlay absent)
      - guardrails: {fired_rules: list}
      - final_direction: str | None

    Field rename per §8.1: ``max_safe_position_size`` → ``max_safe_size``
    (display-only; reads the original key, emits the renamed key).
    """
    phases: dict[str, dict[str, Any] | None] = {}

    # Phase 1: Decision Quality
    dq = record.get("decision_quality")
    if isinstance(dq, dict):
        phases["decision_quality"] = {
            "evidence_strength": dq.get("evidence_strength"),
            "conflict_score": dq.get("conflict_score"),
            "downgrade_reason": dq.get("downgrade_reason"),
            "displayed_direction": dq.get("displayed_direction"),
        }
    else:
        phases["decision_quality"] = None

    # Phase 2: Market Quality
    mq = record.get("market_quality")
    if isinstance(mq, dict):
        phases["market_quality"] = {
            "degraded": mq.get("degraded"),
            "degrade_reason": mq.get("degrade_reason"),
            "wide_spread_flag": mq.get("wide_spread_flag"),
            "low_liquidity_flag": mq.get("low_liquidity_flag"),
        }
    else:
        phases["market_quality"] = None

    # Phase 3: Prediction Calibration (derived from actionable_recommendation
    # + probability, not a top-level overlay key)
    rec = record.get("actionable_recommendation")
    if isinstance(rec, dict):
        cal_status = rec.get("calibration_status")
        edge = rec.get("edge")
        # edge_bucket derived from edge value (e.g. 12.0 → "10-20")
        edge_bucket = None
        if isinstance(edge, (int, float)):
            edge_bucket = f"{int(edge // 10 * 10)}-{int(edge // 10 * 10 + 10)}"
        # direction_correct: True if recommendation direction matches
        # final_displayed_direction, False otherwise. None if no final.
        final_dir = record.get("final_displayed_direction")
        rec_dir = rec.get("direction")
        direction_correct = (rec_dir == final_dir) if final_dir else None
        phases["prediction_calibration"] = {
            "snapshot_recommendation": rec_dir,
            "calibration_status": cal_status,
            "edge_bucket": edge_bucket,
            "direction_correct": direction_correct,
        }
    else:
        phases["prediction_calibration"] = None

    # Phase 4: Source Reliability
    sr = record.get("source_reliability")
    if isinstance(sr, dict):
        phases["source_reliability"] = {
            "overall_score": sr.get("overall_score"),
            "source_count": sr.get("source_count"),
            "domain_diversity": sr.get("domain_diversity"),
        }
    else:
        phases["source_reliability"] = None

    # Phase 5: LLM Telemetry
    lt = record.get("llm_telemetry")
    if isinstance(lt, dict):
        phases["llm_telemetry"] = {
            "degraded_mode": lt.get("degraded_mode"),
            "total_tokens": lt.get("total_tokens"),
            "estimated_token_cost": lt.get("estimated_token_cost"),
            "analysis_quality": lt.get("analysis_quality"),
        }
    else:
        phases["llm_telemetry"] = None

    # Phase 6: Execution Quality
    eq = record.get("execution_quality")
    if isinstance(eq, dict):
        # max_safe_position_size → max_safe_size (§8.1 vocabulary lock)
        phases["execution_quality"] = {
            "executable": eq.get("executable"),
            "estimated_slippage_pct": eq.get("estimated_slippage_pct"),
            "stale_price_flag": eq.get("stale_price_flag"),
            "max_safe_size": eq.get("max_safe_position_size"),
        }
    else:
        phases["execution_quality"] = None

    # Guardrails
    fired = record.get("guardrail_fired")
    guardrails = {"fired_rules": list(fired) if isinstance(fired, list) else []}

    return {
        "event_id": record.get("event_id"),
        "event_title": record.get("event_title"),
        "phases": phases,
        "guardrails": guardrails,
        "final_direction": record.get("final_displayed_direction"),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code."""
    parser = argparse.ArgumentParser(
        description="Diagnose event quality (Spec §4.3). Single-event "
                    "6-layer decomposition + guardrail + final direction."
    )
    parser.add_argument("event_id", help="Event ID to diagnose")
    parser.add_argument(
        "--json", action="store_true",
        help="Output JSON instead of human-readable text",
    )
    parser.add_argument(
        "--replay", action="store_true",
        help="Additionally run replay (all_on vs all_off) direction comparison",
    )
    args = parser.parse_args(argv)

    entry = _load_event(args.event_id)
    if entry is None:
        print(f"Error: event '{args.event_id}' not found in event_store",
              file=sys.stderr)
        return 1

    record = entry.get("record")
    if not isinstance(record, dict):
        print(f"Error: event '{args.event_id}' has no valid record",
              file=sys.stderr)
        return 2

    data = _extract_phase_data(record)

    # Rendering + replay added in later tasks
    _print(f"[diagnose_event_quality] event_id={data['event_id']} "
           f"(rendering not yet implemented)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_diagnose_event_quality.py -v`
Expected: 5 tests PASS (2 load + 3 extract including no-mutation).

- [ ] **Step 5: Commit**

Write commit message to `backend/.commit_msg.tmp`:
```
feat(diagnose-cli): add _load_event + _extract_phase_data + skeleton main

First task of spec §4.3 quality diagnosis CLI. _load_event wraps
event_store.get_event (returns store entry with record key).
_extract_phase_data extracts 6 phases + guardrail + final direction,
renames max_safe_position_size → max_safe_size per vocabulary lock.
Skeleton main parses args, loads event, extracts data, prints placeholder.
5 tests pass (2 load via save_event + 3 extract including no-mutation).
```

Run:
```bash
cd "e:\Github\Prediction Market Reality Filter"
git add backend/scripts/diagnose_event_quality.py backend/tests/test_diagnose_event_quality.py
git commit -F backend/.commit_msg.tmp
Remove-Item backend/.commit_msg.tmp
```

---

## Task 2: `_render_text` + `_render_json` + wire into `main`

**Files:**
- Modify: `backend/scripts/diagnose_event_quality.py` (add `_render_text`, `_render_json`, `_run_replay_comparison` stub, wire into `main`)
- Modify: `backend/tests/test_diagnose_event_quality.py` (add 6 render tests)

**Interfaces:**
- Consumes: `_extract_phase_data` output shape from Task 1.
- Produces: `_render_text(data: dict, replay_result: dict | None) -> str`, `_render_json(data: dict, replay_result: dict | None) -> str`. `main` now renders output instead of placeholder. `_run_replay_comparison` is a stub (real impl in Task 3).

- [ ] **Step 1: Write failing tests for `_render_text` + `_render_json`**

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

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_diagnose_event_quality.py::TestRenderText tests/test_diagnose_event_quality.py::TestRenderJson -v`
Expected: ImportError / AttributeError — `_render_text` / `_render_json` not defined.

- [ ] **Step 3: Add `_render_text` + `_render_json` + `_run_replay_comparison` stub to `diagnose_event_quality.py`**

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_diagnose_event_quality.py -v`
Expected: 11 tests PASS (5 from Task 1 + 4 text + 2 json = 11).

- [ ] **Step 5: Commit**

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

---

## Task 3: `_run_replay_comparison` real impl + exit code tests + vocabulary lock test + no-mutation test

**Files:**
- Modify: `backend/scripts/diagnose_event_quality.py` (replace `_run_replay_comparison` stub with real implementation)
- Modify: `backend/tests/test_diagnose_event_quality.py` (add 6 tests: 3 replay + 2 exit code + 1 vocab)

**Interfaces:**
- Consumes: `replay_record` from `app.replay.runner`, `ReplayConfig` from `app.replay.config` (import paths per Global Constraints — NOT `from app.replay`).
- Produces: Complete CLI with all features. `_run_replay_comparison(record) -> dict` returns `{"all_on_direction": str|None, "all_off_direction": str|None, "delta": "changed"|"no_change"}`.

- [ ] **Step 1: Write failing tests for replay + exit codes + vocabulary lock**

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

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_diagnose_event_quality.py::TestReplayComparison::test_replay_comparison_changed tests/test_diagnose_event_quality.py::TestExitCodes -v`
Expected: Replay test fails (stub returns no_change always — `test_replay_comparison_changed` fails because stub doesn't run replay). Exit code tests may pass (main already returns 1/0 correctly).

- [ ] **Step 3: Replace `_run_replay_comparison` stub with real implementation**

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

- [ ] **Step 4: Run all tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_diagnose_event_quality.py -v`
Expected: 17 tests PASS (11 from Tasks 1-2 + 3 replay + 2 exit code + 1 vocab = 17).

- [ ] **Step 5: Run full backend suite for regression check**

Run: `cd backend && python -m pytest --ignore=tests/test_gbm_engine.py -q`
Expected: all PASS, 0 failures (pre-existing `test_gbm_engine.py` excluded as env issue).

- [ ] **Step 6: Commit**

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

Run:
```bash
cd "e:\Github\Prediction Market Reality Filter"
git add backend/scripts/diagnose_event_quality.py backend/tests/test_diagnose_event_quality.py
git commit -F backend/.commit_msg.tmp
Remove-Item backend/.commit_msg.tmp
```

---

## Self-Review

### 1. Spec coverage

| Spec requirement | Task |
|---|---|
| `python -m scripts.diagnose_event_quality EVENT_ID [--json] [--replay]` | Task 1 (argparse + main), Task 2 (--json), Task 3 (--replay) |
| 6 phase decomposition (DQ/MQ/Calibration/SR/LLM Telemetry/Execution Quality) | Task 1 (`_extract_phase_data`) |
| Guardrail state + Final Direction | Task 1 (`_extract_phase_data`) |
| Text mode with emoji headers + indented fields | Task 2 (`_render_text`) |
| JSON mode with structured output | Task 2 (`_render_json`) |
| Missing overlay → `⏭️ Skipped` (text) / `null` (JSON) | Task 1 (extract returns None), Task 2 (render checks None) |
| `--replay` all_on vs all_off direction comparison | Task 3 (`_run_replay_comparison`) |
| Exit code 0 (success) / 1 (not found) / 2 (other) | Task 1 (main returns 1/2), Task 3 (tests) |
| Vocabulary lock (CLI labels only, max_safe_size rename) | Task 1 (rename in extract), Task 2 (label in render), Task 3 (test) |
| Pure read-only (no writes, no LLM, no mutation) | Task 1 (no-mutation test), Task 3 (replay no-mutation test) |
| Import paths (app.replay.config/runner, event_store.get_event) | Task 1 (get_event), Task 3 (replay imports) |
| event_store entry → record extraction | Task 1 (`_load_event` + main extracts `entry.get("record")`) |
| 14+ unit tests | Task 1 (5) + Task 2 (6) + Task 3 (6) = 17 (exceeds 14) |
| No new dependencies | All tasks (stdlib only) |

No gaps.

### 2. Placeholder scan

No TBD/TODO/"implement later"/"add appropriate error handling" found. All code blocks complete. The Task 2 `_run_replay_comparison` stub is explicitly labeled as a stub and replaced in Task 3 — this is intentional staged implementation, not a placeholder.

### 3. Type consistency

- `_load_event(event_id: str) -> dict[str, Any] | None` — consistent across Tasks 1-3.
- `_extract_phase_data(record: dict[str, Any]) -> dict[str, Any]` — consistent.
- `_render_text(data: dict[str, Any], replay_result: dict[str, Any] | None) -> str` — consistent.
- `_render_json(data: dict[str, Any], replay_result: dict[str, Any] | None) -> str` — consistent.
- `_run_replay_comparison(record: dict[str, Any]) -> dict[str, Any]` — stub in Task 2, real in Task 3, same signature.
- `main(argv: list[str] | None = None) -> int` — consistent.
- `_print(text: str) -> None` — consistent.
- Phase dict keys: `decision_quality`/`market_quality`/`prediction_calibration`/`source_reliability`/`llm_telemetry`/`execution_quality` — consistent across extract + render + tests.
- `max_safe_size` (renamed from `max_safe_position_size`) — consistent in extract (Task 1), render (Task 2), tests (Tasks 1-3), vocab test (Task 3).

No type inconsistencies.

### 4. Import path verification

- `from app.memory.event_store import get_event` — verified against `backend/app/memory/event_store.py:190` (function is `get_event`, not `get`).
- `from app.replay.config import ReplayConfig` — verified against `backend/scripts/analyze_feature_flag_impact.py:38` (same pattern).
- `from app.replay.runner import replay_record` — verified against `backend/scripts/analyze_feature_flag_impact.py:39` (same pattern).
- `app.replay.__init__.py` is empty (verified) — must import from submodules.
- Test fixture: `patch.object(settings, "EVENT_STORE_FILE", str(path))` + `save_event(record)` — verified against `backend/tests/test_operational_readiness.py:662` (same pattern).

All import paths and test patterns correct.
