# Task 1: `_load_event` + `_extract_phase_data` + skeleton `main`

**Files:**
- Create: `backend/scripts/diagnose_event_quality.py`
- Create: `backend/tests/test_diagnose_event_quality.py`

**Interfaces:**
- Produces: `_load_event(event_id: str) -> dict | None` (returns store entry or None), `_extract_phase_data(record: dict) -> dict` (returns dict with 6 phase keys + guardrail + final_direction), `main(argv) -> int` (skeleton — loads + extracts, prints nothing useful yet).

## Step 1: Write failing tests for `_load_event` + `_extract_phase_data`

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

## Step 2: Run tests to verify they fail

Run: `cd backend && python -m pytest tests/test_diagnose_event_quality.py -v`
Expected: ImportError — `diagnose_event_quality` module not found.

## Step 3: Create `diagnose_event_quality.py` with `_load_event` + `_extract_phase_data` + skeleton `main`

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

## Step 4: Run tests to verify they pass

Run: `cd backend && python -m pytest tests/test_diagnose_event_quality.py -v`
Expected: 5 tests PASS (2 load + 3 extract including no-mutation).

## Step 5: Commit

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
