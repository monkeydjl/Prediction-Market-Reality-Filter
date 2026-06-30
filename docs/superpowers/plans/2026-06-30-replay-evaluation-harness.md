# Replay / Evaluation Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a replay harness that re-runs Phase 1-5 overlays + merge + guardrail on frozen event records to quantify direction-change impact, Brier delta, per-phase marginal contributions, and degraded-mode behavior — converging spec §4.5 / §1.5 / §4.2 into one tool.

**Architecture:** Extract `_build_all_overlays` shared function from `analyze_event` so live and replay share one overlay-build codepath. ReplayConfig dataclass + `apply_replay_config` contextmanager temporarily flip feature flags. ReplayRunner deep-copies a record, strips overlay fields, re-runs `_build_all_overlays` under the config. ReplayMetrics accumulates 5-class statistics (direction matrix / Brier / direction_correct / LLM-vs-fallback split / per-phase marginal + conflicts). CLI ties it together with Markdown + JSON + cases.jsonl output.

**Tech Stack:** Python 3.11+, pytest, dataclasses, contextmanager, existing FastAPI settings singleton. No new dependencies.

## Global Constraints

- Backend tests use `unittest.TestCase` style (see `backend/tests/test_p0_fixes_round2.py`). New tests follow the same convention.
- Python files use `logger = logging.getLogger(__name__)` for logging, not `print()`.
- All feature flags default OFF; `preset_all_on()` in pytest equals `preset_all_off()` because settings defaults are off. Tests that need "on" must `monkeypatch` explicitly.
- `event_store.get_event()` returns `{"event_id":..., "record": <dict>}`; always unwrap via `entry["record"]`.
- Phase 3 (Prediction Calibration) is freeze-time, not per-event overlay — replay only sees it if the record already has the field.
- The `_build_all_overlays` refactor must keep all 1688 existing tests green (pure pull-out, no behavior change).
- `downgrade_reason` strings may be long Chinese; never use them as Prometheus labels directly — use `_short_reason()` mapper that already exists in `event_intelligence_service.py`.
- `final_displayed_direction` values are `YES` / `NO` / `WAIT` / `AVOID` (uppercase). `EvidenceBreakdownItem.direction` values are `support` / `oppose` / `neutral` (lowercase, relative to YES outcome) — do not conflate.
- Replay is read-only: it never writes back to `event_store.json` or `prediction_store.db`.
- Tests must not depend on the real `event_store.json` or `prediction_store.db` — use synthetic fixtures.

---

## File Structure

```
backend/app/replay/                         # NEW package
  ├── __init__.py                           # empty package marker
  ├── config.py                             # ReplayConfig + apply_replay_config
  ├── runner.py                             # replay_record + _simulate_llm_degraded
  ├── metrics.py                            # ReplayMetrics + _BrierBucket + _PhaseContribution
  └── report.py                             # render_markdown + render_json + write_report
backend/scripts/replay_decision_pipeline.py # NEW CLI
backend/app/services/event_intelligence_service.py  # MODIFIED: extract _build_all_overlays
backend/tests/
  ├── test_replay_config.py                # NEW
  ├── test_replay_runner.py                 # NEW
  ├── test_replay_metrics.py                # NEW
  └── test_replay_degraded_modes.py         # NEW
docs/reports/replay/                        # NEW output dir (created by CLI at runtime)
```

**Responsibilities:**
- `config.py` — pure data + contextmanager, no IO
- `runner.py` — single-record transform, no IO except settings flip
- `metrics.py` — accumulator, pure
- `report.py` — render accumulator to strings, only IO is `write_report` final step
- `replay_decision_pipeline.py` — the only file that reads event_store / prediction_store and writes report files

---

## Task 1: Extract `_build_all_overlays` from `analyze_event`

**Files:**
- Modify: `backend/app/services/event_intelligence_service.py:321-604` (extract overlay build block into a new function)
- Test: existing `backend/tests/` suite (1688 tests must stay green — no new tests for this task)

**Interfaces:**
- Consumes: existing `analyze_event` local vars (`record`, `analysis`, `combined_context`, `sentiment_profile`, `market_quote`, `filtered_articles`, `volume`, `liquidity`)
- Produces: `def _build_all_overlays(record, *, analysis, sentiment_profile, news_context, market_quote, filtered_articles, volume, liquidity) -> None` (mutates `record` in place). Used by Task 3.

- [ ] **Step 1: Verify pre-refactor tests are green**

Run:
```bash
cd backend && python -m pytest tests/ --tb=short -q --ignore=tests/test_world_cup_gbm_features.py
```
Expected: `1688 passed, 11 skipped` (the baseline).

- [ ] **Step 2: Add `_build_all_overlays` function signature and docstring**

Insert above `async def analyze_event` (around line 253), before the `async def analyze_event` definition:

```python
def _build_all_overlays(
    record: dict[str, Any],
    *,
    analysis: dict[str, Any],
    sentiment_profile: dict[str, Any] | None,
    news_context: str,
    market_quote: dict[str, Any] | None,
    filtered_articles: list[dict[str, Any]] | None = None,
    volume: float | None = None,
    liquidity: float | None = None,
) -> None:
    """Build all 5 overlays + merge + guardrail in-place on ``record``.

    Shared between ``analyze_event`` (live) and ``replay_record`` (replay)
    so the overlay build sequence has a single source of truth. Pure
    pull-out from analyze_event; no behavior change. Best-effort: each
    overlay is wrapped in try/except and emits an error block on failure
    (matches live production behavior).
    """
    # (Body is filled in Step 3 by moving the existing overlay code here.)
```

- [ ] **Step 3: Move overlay build code (lines 321-604) into `_build_all_overlays`**

Cut the entire block from `# Phase 1: Decision Quality overlay...` (line 321) through the end of the guardrail try/except (line 603, before `return record`) and paste it as the body of `_build_all_overlays`. Replace references to `combined_context` with `news_context` (the param name). All other local vars (`record`, `analysis`, `sentiment_profile`, `market_quote`, `filtered_articles`, `volume`, `liquidity`) are now function params — no other renames needed.

The `return record` at line 604 stays in `analyze_event` after the `_build_all_overlays(...)` call.

- [ ] **Step 4: Replace the moved block in `analyze_event` with a call**

In `analyze_event`, where the overlay block used to be (right after `_apply_calibration_feedback` and `aggregate_evidence_breakdown`), insert:

```python
    _build_all_overlays(
        record,
        analysis=analysis,
        sentiment_profile=sentiment_profile,
        news_context=combined_context,
        market_quote=market_quote,
        filtered_articles=filtered_articles,
        volume=volume,
        liquidity=liquidity,
    )
```

- [ ] **Step 5: Run full backend test suite — must stay green**

Run:
```bash
cd backend && python -m pytest tests/ --tb=short -q --ignore=tests/test_world_cup_gbm_features.py
```
Expected: `1688 passed, 11 skipped` — identical to Step 1. If any test fails, the refactor changed behavior; fix before continuing.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/event_intelligence_service.py
git commit -m "refactor: extract _build_all_overlays from analyze_event" -m "Pure pull-out to share overlay build sequence between live" -m "analyze_event and the upcoming replay harness. No behavior change;" -m "all 1688 existing tests stay green."
```

---

## Task 2: ReplayConfig + apply_replay_config

**Files:**
- Create: `backend/app/replay/__init__.py` (empty)
- Create: `backend/app/replay/config.py`
- Create: `backend/tests/test_replay_config.py`

**Interfaces:**
- Consumes: `app.core.config.settings` singleton
- Produces: `class ReplayConfig` (dataclass with 9 fields + 3 preset classmethods), `apply_replay_config(cfg: ReplayConfig)` contextmanager. Used by Task 3.

- [ ] **Step 1: Create empty package marker**

Create `backend/app/replay/__init__.py` with a single line:

```python
"""Replay harness: re-run Phase 1-5 overlays on frozen event records."""
```

- [ ] **Step 2: Write the failing test for `preset_all_off`**

Create `backend/tests/test_replay_config.py`:

```python
"""Unit tests for ReplayConfig + apply_replay_config."""
import unittest
from unittest.mock import patch


class TestReplayConfigPresets(unittest.TestCase):
    def test_preset_all_off_disables_everything(self):
        from app.replay.config import ReplayConfig
        cfg = ReplayConfig.preset_all_off()
        self.assertFalse(cfg.decision_quality_enabled)
        self.assertFalse(cfg.market_quality_enabled)
        self.assertFalse(cfg.source_reliability_enabled)
        self.assertFalse(cfg.prediction_calibration_enabled)
        self.assertFalse(cfg.llm_telemetry_enabled)
        self.assertFalse(cfg.guardrails_enabled)

    def test_preset_all_on_returns_all_none(self):
        from app.replay.config import ReplayConfig
        cfg = ReplayConfig.preset_all_on()
        self.assertIsNone(cfg.decision_quality_enabled)
        self.assertIsNone(cfg.market_quality_enabled)
        self.assertIsNone(cfg.guardrails_enabled)

    def test_preset_llm_degraded_enables_telemetry_and_guardrail(self):
        from app.replay.config import ReplayConfig
        cfg = ReplayConfig.preset_llm_degraded()
        self.assertTrue(cfg.llm_telemetry_enabled)
        self.assertTrue(cfg.guardrails_enabled)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify it fails**

Run:
```bash
cd backend && python -m pytest tests/test_replay_config.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.replay.config'`

- [ ] **Step 4: Implement `ReplayConfig` + presets**

Create `backend/app/replay/config.py`:

```python
"""Replay-time feature flag configuration.

ReplayConfig is a dataclass that overlays feature-flag values onto the
global ``settings`` singleton for the duration of a replay. ``None`` means
"use current settings value" (so ``preset_all_on()`` inherits whatever
the runtime .env configured). A non-None bool forces that value.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from app.core.config import settings


@dataclass
class ReplayConfig:
    """Replay-time feature flag profile. Only includes flags that affect
    overlay output — arbitrary env vars (API keys, file paths) are out of
    scope because replay never triggers live LLM or writes to stores.
    """
    decision_quality_enabled: bool | None = None
    market_quality_enabled: bool | None = None
    source_reliability_enabled: bool | None = None
    prediction_calibration_enabled: bool | None = None
    llm_telemetry_enabled: bool | None = None
    guardrails_enabled: bool | None = None
    guardrail_llm_degraded_blocks_act: bool | None = None
    guardrail_uncalibrated_category_blocks_act: bool | None = None
    guardrail_high_conflict_blocks_act: bool | None = None

    @classmethod
    def preset_all_off(cls) -> "ReplayConfig":
        """Pre-Phase-1 baseline. Disables every overlay so the replayed
        record is byte-identical to a pre-overlay record."""
        return cls(
            decision_quality_enabled=False,
            market_quality_enabled=False,
            source_reliability_enabled=False,
            prediction_calibration_enabled=False,
            llm_telemetry_enabled=False,
            guardrails_enabled=False,
        )

    @classmethod
    def preset_all_on(cls) -> "ReplayConfig":
        """Use current settings values (inherit runtime .env). All fields
        None — apply_replay_config will skip them, leaving settings intact."""
        return cls()

    @classmethod
    def preset_llm_degraded(cls) -> "ReplayConfig":
        """Simulate full LLM failure. Enables llm_telemetry + guardrails
        so the runner can post-process degraded_mode=True and verify
        llm_degraded_blocks_act fires."""
        return cls(
            llm_telemetry_enabled=True,
            guardrails_enabled=True,
        )


@contextmanager
def apply_replay_config(cfg: ReplayConfig) -> Iterator[None]:
    """Temporarily overlay ReplayConfig onto global settings. Restores on
    exit even if an exception fires. Single-threaded replay use only —
    does not take a lock; concurrent replays would race on settings.

    Only fields with non-None values are applied; None fields leave the
    current settings value untouched (so preset_all_on is a true no-op).
    """
    saved: dict[str, object] = {}
    try:
        for field_name in cfg.__dataclass_fields__:
            val = getattr(cfg, field_name)
            if val is not None:
                key = field_name.upper()
                saved[key] = getattr(settings, key)
                setattr(settings, key, val)
        yield
    finally:
        for key, val in saved.items():
            setattr(settings, key, val)
```

- [ ] **Step 5: Run tests to verify presets pass**

Run:
```bash
cd backend && python -m pytest tests/test_replay_config.py -v
```
Expected: `3 passed`

- [ ] **Step 6: Write failing test for `apply_replay_config` restore behavior**

Append to `backend/tests/test_replay_config.py`:

```python
class TestApplyReplayConfig(unittest.TestCase):
    def setUp(self):
        from app.core.config import settings
        self._settings = settings
        self._orig = {
            "DECISION_QUALITY_ENABLED": settings.DECISION_QUALITY_ENABLED,
            "GUARDRAILS_ENABLED": settings.GUARDRAILS_ENABLED,
        }

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(self._settings, k, v)

    def test_applies_and_restores_on_normal_exit(self):
        from app.core.config import settings
        from app.replay.config import ReplayConfig, apply_replay_config
        original = settings.DECISION_QUALITY_ENABLED
        with apply_replay_config(ReplayConfig(decision_quality_enabled=True)):
            self.assertTrue(settings.DECISION_QUALITY_ENABLED)
        self.assertEqual(settings.DECISION_QUALITY_ENABLED, original)

    def test_restores_on_exception(self):
        from app.core.config import settings
        from app.replay.config import ReplayConfig, apply_replay_config
        original = settings.GUARDRAILS_ENABLED
        try:
            with apply_replay_config(ReplayConfig(guardrails_enabled=True)):
                self.assertTrue(settings.GUARDRAILS_ENABLED)
                raise RuntimeError("simulated failure")
        except RuntimeError:
            pass
        self.assertEqual(settings.GUARDRAILS_ENABLED, original)

    def test_none_fields_leave_settings_untouched(self):
        from app.core.config import settings
        from app.replay.config import ReplayConfig, apply_replay_config
        before = settings.MARKET_QUALITY_ENABLED
        with apply_replay_config(ReplayConfig()):  # all None
            self.assertEqual(settings.MARKET_QUALITY_ENABLED, before)
        self.assertEqual(settings.MARKET_QUALITY_ENABLED, before)
```

- [ ] **Step 7: Run tests — verify restore behavior passes**

Run:
```bash
cd backend && python -m pytest tests/test_replay_config.py -v
```
Expected: `6 passed`

- [ ] **Step 8: Commit**

```bash
git add backend/app/replay/__init__.py backend/app/replay/config.py backend/tests/test_replay_config.py
git commit -m "feat(replay): add ReplayConfig + apply_replay_config contextmanager" -m "ReplayConfig dataclass with 9 feature-flag fields and 3 presets" -m "(all_off / all_on / llm_degraded). apply_replay_config temporarily" -m "overlays values onto settings and restores on exit (even on exception)."
```

---

## Task 3: ReplayRunner

**Files:**
- Create: `backend/app/replay/runner.py`
- Create: `backend/tests/test_replay_runner.py`

**Interfaces:**
- Consumes: `ReplayConfig` + `apply_replay_config` from Task 2, `_build_all_overlays` from Task 1, `evaluate_guardrails` + `extract_qualified_categories` from existing `guardrail_service`, `calibration_summary` from existing `prediction_store`.
- Produces: `replay_record(record: dict, cfg: ReplayConfig) -> dict`, `simulate_llm_degraded(replayed: dict) -> None`. Used by Tasks 4, 6, 7.

- [ ] **Step 1: Write failing test for `replay_record` basic contract**

Create `backend/tests/test_replay_runner.py`:

```python
"""Unit tests for ReplayRunner.replay_record."""
import copy
import unittest


def _make_synthetic_record() -> dict:
    """A minimal record with the LLM-era fields replay needs."""
    return {
        "event_id": "test-1",
        "event_title": "Will X happen?",
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
        "actionable_recommendation": {
            "direction": "YES",
            "confidence": "medium",
            "suggested_allocation_pct": 2.0,
            "edge": 12.0,
            "risk_level": "medium",
            "rationale": "市场定价 50.0%，估计 62.0%。",
            "calibration_status": "uncalibrated_provisional",
        },
        "evidence_breakdown": [],
        "source": {"type": "prediction_market", "platform": "polymarket"},
        "market_quote": {"spread_pct": 1.0, "liquidity": 5000.0, "volume": 1000.0},
        "sentiment_profile": {"summary": "neutral", "articles": []},
        "probability": {"baseline": 50.0, "estimated": 62.0, "change": 12.0},
    }


class TestReplayRecordBasic(unittest.TestCase):
    def test_does_not_mutate_input(self):
        from app.replay.runner import replay_record
        from app.replay.config import ReplayConfig
        record = _make_synthetic_record()
        snapshot = copy.deepcopy(record)
        replay_record(record, ReplayConfig.preset_all_off())
        self.assertEqual(record, snapshot, "input record must not be mutated")

    def test_preserves_legacy_analysis(self):
        from app.replay.runner import replay_record
        from app.replay.config import ReplayConfig
        record = _make_synthetic_record()
        replayed = replay_record(record, ReplayConfig.preset_all_off())
        self.assertEqual(
            replayed["legacy_analysis"]["ai_probability"],
            record["legacy_analysis"]["ai_probability"],
        )

    def test_all_off_strips_overlays(self):
        from app.replay.runner import replay_record
        from app.replay.config import ReplayConfig
        record = _make_synthetic_record()
        # Pre-populate with overlay fields; all_off should strip them.
        record["decision_quality"] = {"stale": True}
        record["final_displayed_direction"] = "YES"
        replayed = replay_record(record, ReplayConfig.preset_all_off())
        self.assertNotIn("decision_quality", replayed)
        self.assertNotIn("market_quality", replayed)
        self.assertNotIn("source_reliability", replayed)
        self.assertNotIn("llm_telemetry", replayed)
        self.assertNotIn("final_displayed_direction", replayed)
        self.assertNotIn("final_downgrade_reason", replayed)
        self.assertNotIn("guardrail_fired", replayed)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test — verify it fails**

Run:
```bash
cd backend && python -m pytest tests/test_replay_runner.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.replay.runner'`

- [ ] **Step 3: Implement `replay_record` + `_rebuild_overlays`**

Create `backend/app/replay/runner.py`:

```python
"""ReplayRunner: re-run Phase 1-5 overlays + merge + guardrail on a
frozen event record under a ReplayConfig.

Frozen input contract: the caller guarantees the record contains the
LLM-era artifacts (legacy_analysis, market_quote, sentiment_profile,
evidence_breakdown, source). We never call analyze_market / cross_validate
/ translate_articles / fetch_full_text — those would require live LLM +
network. If a required input is missing, the overlay's existing try/except
produces an error block (same as live production behavior).
"""
from __future__ import annotations

import copy
import logging
from typing import Any

from app.replay.config import ReplayConfig, apply_replay_config

logger = logging.getLogger(__name__)


def replay_record(record: dict[str, Any], cfg: ReplayConfig) -> dict[str, Any]:
    """Re-run all 5 overlays + merge + guardrail on a frozen record.

    Returns a deep copy of ``record`` with overlay fields recomputed under
    ``cfg``. Does not mutate the input. Idempotent: calling twice with the
    same cfg produces the same output.
    """
    replayed = copy.deepcopy(record)

    # Strip existing overlay fields so re-running produces fresh values.
    # Without this, build_decision_quality would short-circuit on a cached
    # block and the replay would just echo the original.
    for key in (
        "decision_quality",
        "market_quality",
        "source_reliability",
        "llm_telemetry",
        "final_displayed_direction",
        "final_downgrade_reason",
        "guardrail_fired",
    ):
        replayed.pop(key, None)

    with apply_replay_config(cfg):
        _rebuild_overlays(replayed, original_record=record)

    return replayed


def _rebuild_overlays(
    replayed: dict[str, Any],
    *,
    original_record: dict[str, Any],
) -> None:
    """Run _build_all_overlays on ``replayed`` using inputs recovered from
    the original record. Mutates ``replayed`` in place.

    LLM-era inputs (news_context / filtered_articles) are not persisted by
    analyze_event, so we use empty-string / empty-list defaults. The 5
    overlay build functions do not read news_context itself (Phase 1 reads
    evidence_breakdown, not the raw context). filtered_articles is only used
    to re-aggregate evidence_breakdown; when empty, we fall back to the
    evidence_breakdown already on the record (preserved by replay_record's
    non-strip list above).
    """
    from app.services.event_intelligence_service import _build_all_overlays

    analysis = original_record.get("legacy_analysis", {}) or {}
    sentiment_profile = original_record.get("sentiment_profile")
    market_quote = original_record.get("market_quote")

    # volume / liquidity were analyze_event function args, not persisted on
    # the record. Recover from market_quote if present, else None.
    volume = None
    liquidity = None
    if isinstance(market_quote, dict):
        volume = market_quote.get("volume")
        liquidity = market_quote.get("liquidity")

    _build_all_overlays(
        replayed,
        analysis=analysis,
        sentiment_profile=sentiment_profile,
        news_context="",  # not persisted; overlays don't read it directly
        market_quote=market_quote,
        filtered_articles=None,  # not persisted; evidence_breakdown preserved
        volume=volume,
        liquidity=liquidity,
    )


def simulate_llm_degraded(replayed: dict[str, Any]) -> None:
    """Force llm_telemetry.degraded_mode=True and re-run only the guardrail
    layer. Used by preset_llm_degraded to verify llm_degraded_blocks_act
    fires without requiring a real LLM failure.

    Mutates ``replayed`` in place. Assumes replay_record has already been
    called (so llm_telemetry / final_displayed_direction / etc. are populated
    or absent per the cfg that was used).
    """
    if not isinstance(replayed.get("llm_telemetry"), dict):
        # Nothing to degrade — llm_telemetry wasn't built (flag was off).
        return

    replayed["llm_telemetry"]["degraded_mode"] = True
    replayed["llm_telemetry"]["analysis_quality"] = "deterministic_fallback"

    # Re-run guardrail only: strip the guardrail outputs so evaluate_guardrails
    # runs fresh with the degraded llm_telemetry. Other overlays are unaffected
    # because guardrail only reads llm_telemetry.degraded_mode + record fields.
    pre_guardrail_dir = replayed.get("final_displayed_direction")
    pre_guardrail_reason = replayed.get("final_downgrade_reason")
    replayed.pop("guardrail_fired", None)

    try:
        from app.core.config import settings
        if not settings.GUARDRAILS_ENABLED:
            return
        from app.services.guardrail_service import (
            evaluate_guardrails,
            extract_qualified_categories,
        )
        qualified_cats: set[str] | None = None
        try:
            from app.memory.prediction_store import calibration_summary
            summary = calibration_summary()
            qualified_cats = extract_qualified_categories(summary.get("segments"))
        except Exception as exc:
            logger.debug("calibration_summary unavailable for degraded replay: %s", exc)
        fired_dir, fired_reason, fired_rules = evaluate_guardrails(
            final_direction=pre_guardrail_dir,
            final_downgrade_reason=pre_guardrail_reason,
            record=replayed,
            enabled=True,
            llm_degraded_blocks_act=settings.GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT,
            uncalibrated_category_blocks_act=settings.GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT,
            high_conflict_blocks_act=settings.GUARDRAIL_HIGH_CONFLICT_BLOCKS_ACT,
            high_conflict_threshold=settings.GUARDRAIL_HIGH_CONFLICT_THRESHOLD,
            qualified_categories=qualified_cats,
        )
        if fired_rules:
            replayed["final_displayed_direction"] = fired_dir
            replayed["final_downgrade_reason"] = fired_reason
            replayed["guardrail_fired"] = fired_rules
    except Exception as exc:
        logger.warning("simulate_llm_degraded guardrail re-run failed: %s", exc)
```

- [ ] **Step 4: Run tests — verify basic contract passes**

Run:
```bash
cd backend && python -m pytest tests/test_replay_runner.py -v
```
Expected: `3 passed`

- [ ] **Step 5: Write failing test for `replay_record` with all-on config**

Append to `backend/tests/test_replay_runner.py`:

```python
class TestReplayRecordAllOn(unittest.TestCase):
    def test_all_on_attaches_overlays(self):
        """When all feature flags are on, replay should attach overlay
        fields. We monkeypatch settings to enable each flag because
        preset_all_on() inherits current settings (which default off
        in pytest)."""
        from unittest.mock import patch
        from app.core.config import settings
        from app.replay.runner import replay_record
        from app.replay.config import ReplayConfig

        record = _make_synthetic_record()
        # Enable the overlays that the synthetic record can satisfy.
        flags = {
            "DECISION_QUALITY_ENABLED": True,
            "MARKET_QUALITY_ENABLED": True,
            "LLM_TELEMETRY_ENABLED": True,
            "GUARDRAILS_ENABLED": False,  # avoid calibration_summary IO
        }
        with patch.multiple(settings, **flags):
            replayed = replay_record(record, ReplayConfig.preset_all_on())
        self.assertIn("decision_quality", replayed)
        self.assertIn("market_quality", replayed)
        self.assertIn("llm_telemetry", replayed)
        # final_displayed_direction is set by merge_quality_overlays when at
        # least one overlay produced a direction.
        self.assertIn("final_displayed_direction", replayed)

    def test_replay_idempotent(self):
        """Calling replay_record twice with the same cfg produces equal output."""
        from unittest.mock import patch
        from app.core.config import settings
        from app.replay.runner import replay_record
        from app.replay.config import ReplayConfig

        record = _make_synthetic_record()
        flags = {"DECISION_QUALITY_ENABLED": True, "MARKET_QUALITY_ENABLED": True}
        with patch.multiple(settings, **flags):
            first = replay_record(record, ReplayConfig.preset_all_on())
            second = replay_record(record, ReplayConfig.preset_all_on())
        self.assertEqual(first, second)
```

- [ ] **Step 6: Run tests — verify all-on passes**

Run:
```bash
cd backend && python -m pytest tests/test_replay_runner.py -v
```
Expected: `5 passed`

- [ ] **Step 7: Write failing test for `simulate_llm_degraded`**

Append to `backend/tests/test_replay_runner.py`:

```python
class TestSimulateLlmDegraded(unittest.TestCase):
    def test_forces_degraded_mode_and_reruns_guardrail(self):
        """When guardrails are enabled and llm_degraded_blocks_act is on,
        simulate_llm_degraded should fire the rule and downgrade to WAIT."""
        from unittest.mock import patch
        from app.core.config import settings
        from app.replay.runner import replay_record, simulate_llm_degraded
        from app.replay.config import ReplayConfig

        record = _make_synthetic_record()
        flags = {
            "LLM_TELEMETRY_ENABLED": True,
            "GUARDRAILS_ENABLED": True,
            "GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT": True,
        }
        with patch.multiple(settings, **flags):
            replayed = replay_record(record, ReplayConfig.preset_all_on())
            # Before simulate: direction is whatever the overlays produced.
            simulate_llm_degraded(replayed)
        self.assertTrue(replayed["llm_telemetry"]["degraded_mode"])
        self.assertEqual(
            replayed["llm_telemetry"]["analysis_quality"],
            "deterministic_fallback",
        )
        # llm_degraded_blocks_act rule should fire and downgrade to WAIT.
        self.assertIn("llm_degraded_blocks_act", replayed.get("guardrail_fired", []))
        self.assertEqual(replayed.get("final_displayed_direction"), "WAIT")

    def test_noop_when_llm_telemetry_absent(self):
        """If llm_telemetry wasn't built (flag off), simulate is a no-op."""
        from app.replay.runner import replay_record, simulate_llm_degraded
        from app.replay.config import ReplayConfig
        record = _make_synthetic_record()
        replayed = replay_record(record, ReplayConfig.preset_all_off())
        # Should not raise.
        simulate_llm_degraded(replayed)
        self.assertNotIn("llm_telemetry", replayed)
```

- [ ] **Step 8: Run tests — verify degraded simulation passes**

Run:
```bash
cd backend && python -m pytest tests/test_replay_runner.py -v
```
Expected: `7 passed`

- [ ] **Step 9: Commit**

```bash
git add backend/app/replay/runner.py backend/tests/test_replay_runner.py
git commit -m "feat(replay): add replay_record + simulate_llm_degraded" -m "replay_record deep-copies a record, strips overlay fields, and" -m "re-runs _build_all_overlays under a ReplayConfig. Inputs are" -m "recovered from the frozen record (legacy_analysis / market_quote /" -m "sentiment_profile / evidence_breakdown). simulate_llm_degraded" -m "post-processes llm_telemetry.degraded_mode=True and re-runs the" -m "guardrail layer to verify llm_degraded_blocks_act fires."
```

---

## Task 4: ReplayMetrics accumulator

**Files:**
- Create: `backend/app/replay/metrics.py`
- Create: `backend/tests/test_replay_metrics.py`

**Interfaces:**
- Consumes: replayed records (plain dicts) from Task 3.
- Produces: `class ReplayMetrics` with `add_pair(original, replayed)`, `add_phase_result(event_id, phase, replayed_dir, base_dir, final_dir)`, `to_dict()`, `brier_delta()`. Plus `_BrierBucket` + `_PhaseContribution` dataclasses. Used by Tasks 5, 6.

- [ ] **Step 1: Write failing test for direction matrix accumulation**

Create `backend/tests/test_replay_metrics.py`:

```python
"""Unit tests for ReplayMetrics accumulator."""
import unittest


class TestDirectionMatrix(unittest.TestCase):
    def test_accumulates_yes_to_wait(self):
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        m.add_pair(
            original={"final_displayed_direction": "YES"},
            replayed={"final_displayed_direction": "WAIT"},
        )
        m.add_pair(
            original={"final_displayed_direction": "YES"},
            replayed={"final_displayed_direction": "WAIT"},
        )
        m.add_pair(
            original={"final_displayed_direction": "NO"},
            replayed={"final_displayed_direction": "AVOID"},
        )
        d = m.to_dict()
        self.assertEqual(d["direction_matrix"][("YES", "WAIT")], 2)
        self.assertEqual(d["direction_matrix"][("NO", "AVOID")], 1)
        self.assertEqual(d["total"], 3)

    def test_missing_direction_skipped(self):
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        m.add_pair(
            original={},  # no final_displayed_direction
            replayed={"final_displayed_direction": "WAIT"},
        )
        d = m.to_dict()
        self.assertEqual(d["total"], 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test — verify it fails**

Run:
```bash
cd backend && python -m pytest tests/test_replay_metrics.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.replay.metrics'`

- [ ] **Step 3: Implement `ReplayMetrics` + helpers**

Create `backend/app/replay/metrics.py`:

```python
"""ReplayMetrics: accumulate pairwise (original, replayed) statistics.

5 metric classes (spec §4.5):
1. direction_matrix — YES->WAIT / YES->AVOID / WAIT->AVOID counts
2. brier — original vs replayed mean on resolved samples
3. direction_correct — resolved-sample direction accuracy
4. brier_by_quality — LLM vs deterministic_fallback split (spec §4.5)
5. phase_contributions + conflict_cases — per-phase marginal + conflicts
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class _BrierBucket:
    n: int = 0
    brier_sum: float = 0.0


@dataclass
class _PhaseContribution:
    downgrades_caused: int = 0
    directions_changed: int = 0
    conflicts_with_final: int = 0


_STRONG_DIRECTIONS = {"YES", "NO"}
_WEAK_DIRECTIONS = {"WAIT", "AVOID"}


class ReplayMetrics:
    """Accumulates pairwise (original, replayed) comparisons and per-phase
    marginal-contribution data. Pure: no IO, no LLM."""

    def __init__(self) -> None:
        self.total: int = 0
        self.direction_matrix: dict[tuple[str, str], int] = {}
        self.resolved_count: int = 0
        self.brier_original_sum: float = 0.0
        self.brier_replayed_sum: float = 0.0
        self.direction_correct_original: int = 0
        self.direction_correct_replayed: int = 0
        self.direction_correct_resolved_count: int = 0
        self.brier_by_quality: dict[str, _BrierBucket] = {}
        self.phase_contributions: dict[str, _PhaseContribution] = {}
        self.conflict_cases: list[dict[str, Any]] = []

    def add_pair(self, original: dict[str, Any], replayed: dict[str, Any]) -> None:
        """Accumulate one (original, replayed) record pair for direction
        matrix + brier + direction_correct + LLM/fallback split."""
        orig_dir = original.get("final_displayed_direction")
        replay_dir = replayed.get("final_displayed_direction")
        if orig_dir is not None and replay_dir is not None:
            self.total += 1
            key = (orig_dir, replay_dir)
            self.direction_matrix[key] = self.direction_matrix.get(key, 0) + 1

        # Brier + direction_correct on resolved samples. The record carries
        # brier_score + direction_correct after score_prediction ran; the
        # replayed record may have a different direction but the same
        # actual_outcome, so we re-derive direction_correct for the replay.
        orig_brier = original.get("brier_score")
        replay_brier = replayed.get("brier_score")
        actual = original.get("actual_outcome")
        if actual is not None and orig_brier is not None and replay_brier is not None:
            self.resolved_count += 1
            self.brier_original_sum += orig_brier
            self.brier_replayed_sum += replay_brier

            orig_dc = original.get("direction_correct")
            if orig_dc is not None:
                self.direction_correct_resolved_count += 1
                if orig_dc:
                    self.direction_correct_original += 1
            # Re-derive direction_correct for the replayed direction.
            replay_dc = _derive_direction_correct(replay_dir, actual)
            if replay_dc is not None:
                if replay_dc:
                    self.direction_correct_replayed += 1

            # LLM vs fallback split (spec §4.5).
            quality = _analysis_quality_of(replayed)
            if quality is not None:
                bucket = self.brier_by_quality.setdefault(
                    quality, _BrierBucket()
                )
                bucket.n += 1
                bucket.brier_sum += replay_brier

    def add_phase_result(
        self,
        event_id: str,
        phase: str,
        base_dir: str | None,
        phase_dir: str | None,
        final_dir: str | None,
    ) -> None:
        """Accumulate per-phase marginal contribution + conflict detection.

        Called N times per event (once per phase) during the N+1 replay
        loop. ``base_dir`` is the all-off baseline; ``phase_dir`` is the
        direction when only this phase is on; ``final_dir`` is the all-on
        direction.
        """
        if phase not in self.phase_contributions:
            self.phase_contributions[phase] = _PhaseContribution()
        pc = self.phase_contributions[phase]

        # downgrades_caused: phase turned a strong dir into a weak one.
        if base_dir in _STRONG_DIRECTIONS and phase_dir in _WEAK_DIRECTIONS:
            pc.downgrades_caused += 1
        # directions_changed: phase produced any direction different from base.
        if base_dir is not None and phase_dir is not None and base_dir != phase_dir:
            pc.directions_changed += 1
        # conflicts_with_final: phase disagrees with the final merged direction.
        if (
            phase_dir is not None
            and final_dir is not None
            and phase_dir != final_dir
            and phase_dir in _STRONG_DIRECTIONS
            and final_dir in _WEAK_DIRECTIONS
        ):
            pc.conflicts_with_final += 1
            self.conflict_cases.append({
                "event_id": event_id,
                "phase": phase,
                "phase_dir": phase_dir,
                "final_dir": final_dir,
                "base_dir": base_dir,
            })

    def brier_delta(self) -> float:
        """Returns replayed_mean - original_mean. Negative = improved."""
        if self.resolved_count == 0:
            return 0.0
        orig_mean = self.brier_original_sum / self.resolved_count
        replay_mean = self.brier_replayed_sum / self.resolved_count
        return replay_mean - orig_mean

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict for report rendering."""
        return {
            "total": self.total,
            "direction_matrix": {
                f"{k[0]}->{k[1]}": v for k, v in self.direction_matrix.items()
            },
            "resolved_count": self.resolved_count,
            "brier_original_mean": (
                self.brier_original_sum / self.resolved_count
                if self.resolved_count
                else None
            ),
            "brier_replayed_mean": (
                self.brier_replayed_sum / self.resolved_count
                if self.resolved_count
                else None
            ),
            "brier_delta": self.brier_delta(),
            "direction_correct_original": self.direction_correct_original,
            "direction_correct_replayed": self.direction_correct_replayed,
            "direction_correct_resolved_count": self.direction_correct_resolved_count,
            "brier_by_quality": {
                k: {"n": v.n, "brier_mean": (v.brier_sum / v.n if v.n else None)}
                for k, v in self.brier_by_quality.items()
            },
            "phase_contributions": {
                k: {
                    "downgrades_caused": v.downgrades_caused,
                    "directions_changed": v.directions_changed,
                    "conflicts_with_final": v.conflicts_with_final,
                }
                for k, v in self.phase_contributions.items()
            },
            "conflict_cases": self.conflict_cases[:20],  # cap for report
            "conflict_cases_total": len(self.conflict_cases),
        }


def _derive_direction_correct(direction: str | None, actual_outcome: float) -> bool | None:
    """Mirror prediction_store.compute_direction_correct: YES if outcome>=50,
    NO if outcome<50, None for WAIT/AVOID/missing."""
    if direction is None or actual_outcome is None:
        return None
    if direction == "YES":
        return actual_outcome >= 50
    if direction == "NO":
        return actual_outcome < 50
    return None  # WAIT / AVOID


def _analysis_quality_of(record: dict[str, Any]) -> str | None:
    """Extract the analysis_quality label for LLM/fallback split."""
    tel = record.get("llm_telemetry")
    if isinstance(tel, dict):
        q = tel.get("analysis_quality")
        if isinstance(q, str) and q:
            return q
    legacy = record.get("legacy_analysis")
    if isinstance(legacy, dict):
        q = legacy.get("analysis_quality")
        if isinstance(q, str) and q:
            return q
    return None
```

- [ ] **Step 4: Run tests — verify direction matrix passes**

Run:
```bash
cd backend && python -m pytest tests/test_replay_metrics.py -v
```
Expected: `2 passed`

- [ ] **Step 5: Write failing test for Brier + direction_correct**

Append to `backend/tests/test_replay_metrics.py`:

```python
class TestBrierAndDirectionCorrect(unittest.TestCase):
    def test_brier_delta_signed(self):
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        # original brier 0.25, replayed brier 0.15 — replayed is better.
        m.add_pair(
            original={
                "final_displayed_direction": "YES",
                "brier_score": 0.25,
                "actual_outcome": 100.0,
                "direction_correct": 1,
            },
            replayed={
                "final_displayed_direction": "NO",
                "brier_score": 0.15,
                "actual_outcome": 100.0,
            },
        )
        d = m.to_dict()
        self.assertEqual(d["resolved_count"], 1)
        self.assertAlmostEqual(d["brier_original_mean"], 0.25)
        self.assertAlmostEqual(d["brier_replayed_mean"], 0.15)
        self.assertAlmostEqual(d["brier_delta"], -0.10)  # negative = improved
        # original direction_correct: YES vs outcome 100 -> correct (1)
        self.assertEqual(d["direction_correct_original"], 1)
        # replayed direction_correct: NO vs outcome 100 -> incorrect (0)
        self.assertEqual(d["direction_correct_replayed"], 0)

    def test_llm_vs_fallback_split(self):
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        m.add_pair(
            original={
                "final_displayed_direction": "YES",
                "brier_score": 0.2,
                "actual_outcome": 100.0,
                "direction_correct": 1,
            },
            replayed={
                "final_displayed_direction": "YES",
                "brier_score": 0.2,
                "actual_outcome": 100.0,
                "llm_telemetry": {"analysis_quality": "llm"},
            },
        )
        m.add_pair(
            original={
                "final_displayed_direction": "YES",
                "brier_score": 0.4,
                "actual_outcome": 0.0,
                "direction_correct": 0,
            },
            replayed={
                "final_displayed_direction": "YES",
                "brier_score": 0.4,
                "actual_outcome": 0.0,
                "llm_telemetry": {"analysis_quality": "deterministic_fallback"},
            },
        )
        d = m.to_dict()
        self.assertIn("llm", d["brier_by_quality"])
        self.assertIn("deterministic_fallback", d["brier_by_quality"])
        self.assertEqual(d["brier_by_quality"]["llm"]["n"], 1)
        self.assertEqual(d["brier_by_quality"]["deterministic_fallback"]["n"], 1)
```

- [ ] **Step 6: Run tests — verify Brier passes**

Run:
```bash
cd backend && python -m pytest tests/test_replay_metrics.py -v
```
Expected: `4 passed`

- [ ] **Step 7: Write failing test for phase contributions + conflicts**

Append to `backend/tests/test_replay_metrics.py`:

```python
class TestPhaseContributions(unittest.TestCase):
    def test_downgrades_caused_counted(self):
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        # base=YES, phase_only=WAIT — this phase downgraded.
        m.add_phase_result("e1", "decision_quality", "YES", "WAIT", "WAIT")
        # base=YES, phase_only=YES — this phase didn't downgrade.
        m.add_phase_result("e2", "market_quality", "YES", "YES", "YES")
        d = m.to_dict()
        self.assertEqual(
            d["phase_contributions"]["decision_quality"]["downgrades_caused"], 1
        )
        self.assertEqual(
            d["phase_contributions"]["market_quality"]["downgrades_caused"], 0
        )

    def test_conflict_case_collected_when_phase_overridden(self):
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        # phase says YES, final says WAIT — phase was overridden by another.
        m.add_phase_result("e1", "source_reliability", "YES", "YES", "WAIT")
        d = m.to_dict()
        self.assertEqual(d["conflict_cases_total"], 1)
        self.assertEqual(d["conflict_cases"][0]["phase"], "source_reliability")
        self.assertEqual(d["conflict_cases"][0]["phase_dir"], "YES")
        self.assertEqual(d["conflict_cases"][0]["final_dir"], "WAIT")

    def test_no_conflict_when_phase_agrees_with_final(self):
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        m.add_phase_result("e1", "decision_quality", "YES", "YES", "YES")
        d = m.to_dict()
        self.assertEqual(d["conflict_cases_total"], 0)
```

- [ ] **Step 8: Run tests — verify phase contributions pass**

Run:
```bash
cd backend && python -m pytest tests/test_replay_metrics.py -v
```
Expected: `7 passed`

- [ ] **Step 9: Commit**

```bash
git add backend/app/replay/metrics.py backend/tests/test_replay_metrics.py
git commit -m "feat(replay): add ReplayMetrics accumulator with 5-class metrics" -m "direction_matrix / brier / direction_correct / LLM-vs-fallback split /" -m "per-phase marginal + conflict cases. add_pair accumulates" -m "pairwise stats; add_phase_result accumulates per-phase marginal" -m "contribution during the N+1 replay loop. to_dict serializes for" -m "report rendering."
```

---

## Task 5: Report renderer

**Files:**
- Create: `backend/app/replay/report.py`
- Create: `backend/tests/test_replay_report.py`

**Interfaces:**
- Consumes: `ReplayMetrics.to_dict()` from Task 4.
- Produces: `render_markdown(metrics: dict) -> str`, `render_json(metrics: dict) -> str`, `write_report(metrics: dict, output_dir: Path, cases: list[dict]) -> None`. Used by Task 6.

- [ ] **Step 1: Write failing test for `render_markdown`**

Create `backend/tests/test_replay_report.py`:

```python
"""Unit tests for report renderer."""
import unittest


def _sample_metrics() -> dict:
    return {
        "total": 100,
        "direction_matrix": {"YES->WAIT": 17, "YES->AVOID": 3, "NO->WAIT": 8},
        "resolved_count": 40,
        "brier_original_mean": 0.25,
        "brier_replayed_mean": 0.20,
        "brier_delta": -0.05,
        "direction_correct_original": 30,
        "direction_correct_replayed": 32,
        "direction_correct_resolved_count": 40,
        "brier_by_quality": {
            "llm": {"n": 30, "brier_mean": 0.18},
            "deterministic_fallback": {"n": 10, "brier_mean": 0.32},
        },
        "phase_contributions": {
            "decision_quality": {
                "downgrades_caused": 12,
                "directions_changed": 15,
                "conflicts_with_final": 2,
            },
        },
        "conflict_cases": [
            {
                "event_id": "e1",
                "phase": "source_reliability",
                "phase_dir": "YES",
                "final_dir": "WAIT",
                "base_dir": "YES",
            }
        ],
        "conflict_cases_total": 1,
    }


class TestRenderMarkdown(unittest.TestCase):
    def test_includes_all_sections(self):
        from app.replay.report import render_markdown
        md = render_markdown(_sample_metrics())
        self.assertIn("# Replay Report", md)
        self.assertIn("## Summary", md)
        self.assertIn("## Direction Matrix", md)
        self.assertIn("## Brier", md)
        self.assertIn("## Direction Accuracy", md)
        self.assertIn("## LLM vs Fallback", md)
        self.assertIn("## Per-Phase Marginal Contribution", md)
        self.assertIn("## Conflict Cases", md)

    def test_summary_shows_total_and_change_rate(self):
        from app.replay.report import render_markdown
        md = render_markdown(_sample_metrics())
        # 20 of 100 changed direction (17+3 others stayed) — change rate.
        self.assertIn("Total events: 100", md)


class TestRenderJson(unittest.TestCase):
    def test_returns_valid_json_string(self):
        import json
        from app.replay.report import render_json
        s = render_json(_sample_metrics())
        parsed = json.loads(s)
        self.assertEqual(parsed["total"], 100)
        self.assertEqual(parsed["direction_matrix"]["YES->WAIT"], 17)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test — verify it fails**

Run:
```bash
cd backend && python -m pytest tests/test_replay_report.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.replay.report'`

- [ ] **Step 3: Implement `render_markdown` + `render_json` + `write_report`**

Create `backend/app/replay/report.py`:

```python
"""Render ReplayMetrics to Markdown + JSON + cases.jsonl.

Pure rendering: no IO except ``write_report`` which writes the three files.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def render_markdown(metrics: dict[str, Any]) -> str:
    """Render metrics dict to a Markdown report string."""
    lines: list[str] = []
    lines.append("# Replay Report")
    lines.append("")
    lines.append(f"_Generated: {datetime.now(timezone.utc).isoformat()}_")
    lines.append("")

    # Section 1: Summary
    total = metrics.get("total", 0)
    matrix = metrics.get("direction_matrix", {})
    changed = sum(v for k, v in matrix.items() if k.split("->")[0] != k.split("->")[1]) if matrix else 0
    change_rate = (changed / total * 100) if total else 0.0
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total events: {total}")
    lines.append(f"- Direction changed: {changed} ({change_rate:.1f}%)")
    lines.append(f"- Resolved (with outcome): {metrics.get('resolved_count', 0)}")
    lines.append("")

    # Section 2: Direction Matrix
    lines.append("## Direction Matrix")
    lines.append("")
    if matrix:
        lines.append("| Original | Replayed | Count |")
        lines.append("|---|---|---|")
        for key, count in sorted(matrix.items(), key=lambda x: -x[1]):
            orig, replay = key.split("->")
            lines.append(f"| {orig} | {replay} | {count} |")
    else:
        lines.append("_No direction changes recorded._")
    lines.append("")

    # Section 3: Brier
    lines.append("## Brier")
    lines.append("")
    orig_mean = metrics.get("brier_original_mean")
    replay_mean = metrics.get("brier_replayed_mean")
    delta = metrics.get("brier_delta")
    if orig_mean is not None and replay_mean is not None:
        verdict = "improved" if (delta is not None and delta < 0) else "regressed"
        lines.append(f"- Original mean Brier: {orig_mean:.4f}")
        lines.append(f"- Replayed mean Brier: {replay_mean:.4f}")
        lines.append(f"- Delta: {delta:+.4f} ({verdict})")
    else:
        lines.append("_No resolved samples to compute Brier._")
    lines.append("")

    # Section 4: Direction Accuracy
    lines.append("## Direction Accuracy")
    lines.append("")
    rc = metrics.get("direction_correct_resolved_count", 0)
    orig_correct = metrics.get("direction_correct_original", 0)
    replay_correct = metrics.get("direction_correct_replayed", 0)
    if rc:
        lines.append(f"- Resolved samples: {rc}")
        lines.append(f"- Original correct: {orig_correct} ({orig_correct/rc*100:.1f}%)")
        lines.append(f"- Replayed correct: {replay_correct} ({replay_correct/rc*100:.1f}%)")
    else:
        lines.append("_No resolved samples._")
    lines.append("")

    # Section 5: LLM vs Fallback
    lines.append("## LLM vs Fallback")
    lines.append("")
    bq = metrics.get("brier_by_quality", {})
    if bq:
        lines.append("| Quality | N | Brier mean |")
        lines.append("|---|---|---|")
        for q, bucket in bq.items():
            mean = bucket.get("brier_mean")
            mean_str = f"{mean:.4f}" if mean is not None else "N/A"
            lines.append(f"| {q} | {bucket.get('n', 0)} | {mean_str} |")
    else:
        lines.append("_No analysis_quality data._")
    lines.append("")

    # Section 6: Per-Phase Marginal Contribution
    lines.append("## Per-Phase Marginal Contribution")
    lines.append("")
    pc = metrics.get("phase_contributions", {})
    if pc:
        lines.append("| Phase | Downgrades caused | Directions changed | Conflicts |")
        lines.append("|---|---|---|---|")
        for phase, contrib in pc.items():
            lines.append(
                f"| {phase} | {contrib.get('downgrades_caused', 0)} | "
                f"{contrib.get('directions_changed', 0)} | "
                f"{contrib.get('conflicts_with_final', 0)} |"
            )
    else:
        lines.append("_No per-phase replay run (use --marginal to enable)._")
    lines.append("")

    # Section 7: Conflict Cases
    lines.append("## Conflict Cases")
    lines.append("")
    cases = metrics.get("conflict_cases", [])
    total_cases = metrics.get("conflict_cases_total", 0)
    lines.append(f"_Total conflicts: {total_cases} (showing first {len(cases)})._")
    lines.append("")
    if cases:
        lines.append("| Event | Phase | Phase dir | Final dir | Base dir |")
        lines.append("|---|---|---|---|---|")
        for c in cases:
            lines.append(
                f"| {c.get('event_id')} | {c.get('phase')} | "
                f"{c.get('phase_dir')} | {c.get('final_dir')} | "
                f"{c.get('base_dir')} |"
            )
    lines.append("")
    return "\n".join(lines)


def render_json(metrics: dict[str, Any]) -> str:
    """Render metrics dict to a JSON string."""
    return json.dumps(metrics, indent=2, default=str)


def write_report(
    metrics: dict[str, Any],
    output_dir: Path,
    cases: list[dict[str, Any]] | None = None,
) -> Path:
    """Write report.md + metrics.json + cases.jsonl to ``output_dir``.

    Returns the path to ``report.md``. Creates ``output_dir`` if missing.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "report.md"
    md_path.write_text(render_markdown(metrics), encoding="utf-8")
    (output_dir / "metrics.json").write_text(
        render_json(metrics), encoding="utf-8"
    )
    if cases is not None:
        cases_path = output_dir / "cases.jsonl"
        with cases_path.open("w", encoding="utf-8") as f:
            for c in cases:
                f.write(json.dumps(c, default=str) + "\n")
    return md_path
```

- [ ] **Step 4: Run tests — verify renderer passes**

Run:
```bash
cd backend && python -m pytest tests/test_replay_report.py -v
```
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/replay/report.py backend/tests/test_replay_report.py
git commit -m "feat(replay): add Markdown + JSON report renderer" -m "7-section Markdown report (Summary / Direction Matrix / Brier /" -m "Direction Accuracy / LLM vs Fallback / Per-Phase Marginal / Conflict" -m "Cases). write_report emits report.md + metrics.json + cases.jsonl" -m "to a timestamped output dir."
```

---

## Task 6: CLI script

**Files:**
- Create: `backend/scripts/replay_decision_pipeline.py`

**Interfaces:**
- Consumes: `ReplayConfig` + `replay_record` + `simulate_llm_degraded` from Tasks 2-3, `ReplayMetrics` from Task 4, `write_report` from Task 5, `event_store` + `prediction_store` existing readers.
- Produces: a CLI entrypoint runnable via `python -m scripts.replay_decision_pipeline`.

- [ ] **Step 1: Implement the CLI skeleton**

Create `backend/scripts/replay_decision_pipeline.py`:

```python
"""Replay Phase 1-5 overlays on frozen event records to quantify
direction-change impact, Brier delta, and per-phase contributions.

Converges spec §4.5 (replay harness), §1.5 (A/B compare), and §4.2
(degraded-mode tests) into one tool.

Usage:
    # Default: all events, current-config vs all-off (marginal contribution)
    python -m scripts.replay_decision_pipeline

    # Specific events
    python -m scripts.replay_decision_pipeline --event-ids id1 id2

    # Sample N events
    python -m scripts.replay_decision_pipeline --sample-size 500

    # A/B compare two configs
    python -m scripts.replay_decision_pipeline --compare current all_off

    # Custom output dir
    python -m scripts.replay_decision_pipeline --output-dir docs/reports/replay/

    # Skip per-phase marginal (faster, no N+1 loop)
    python -m scripts.replay_decision_pipeline --skip-marginal
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make backend importable when run as a script.
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.replay.config import ReplayConfig  # noqa: E402
from app.replay.runner import replay_record, simulate_llm_degraded  # noqa: E402
from app.replay.metrics import ReplayMetrics  # noqa: E402
from app.replay.report import write_report  # noqa: E402

logger = logging.getLogger(__name__)


# The 6 phase names used for marginal-contribution attribution. Must match
# the ReplayConfig field prefixes (without the "_enabled" suffix).
_PHASE_FIELDS = [
    ("decision_quality", "decision_quality_enabled"),
    ("market_quality", "market_quality_enabled"),
    ("source_reliability", "source_reliability_enabled"),
    ("prediction_calibration", "prediction_calibration_enabled"),
    ("llm_telemetry", "llm_telemetry_enabled"),
    ("guardrails", "guardrails_enabled"),
]


def _load_records(event_ids: list[str] | None, sample_size: int | None) -> list[dict[str, Any]]:
    """Load event records from event_store. Unwraps the {event_id, record}
    envelope that event_store.get_all_events returns."""
    from app.memory.event_store import get_all_events
    entries = get_all_events()
    records = [e["record"] for e in entries if isinstance(e.get("record"), dict)]
    if event_ids:
        wanted = set(event_ids)
        records = [r for r in records if r.get("event_id") in wanted]
    if sample_size and len(records) > sample_size:
        random.seed(42)  # deterministic sampling for reproducibility
        records = random.sample(records, sample_size)
    return records


def _enrich_with_outcome(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best-effort: attach brier_score + actual_outcome + direction_correct
    from prediction_store so metrics can compute Brier on resolved samples.
    Records without a prediction row stay unchanged."""
    try:
        from app.memory.prediction_store import list_predictions
        preds = {p["event_id"]: p for p in list_predictions(status="resolved")}
    except Exception as exc:
        logger.debug("prediction_store unavailable, skipping outcome enrichment: %s", exc)
        return records
    for r in records:
        p = preds.get(r.get("event_id"))
        if p:
            r.setdefault("brier_score", p.get("brier_score"))
            r.setdefault("actual_outcome", p.get("actual_outcome"))
            r.setdefault("direction_correct", p.get("direction_correct"))
    return records


def _run_marginal_loop(records: list[dict[str, Any]], metrics: ReplayMetrics) -> None:
    """N+1 replay loop: baseline (all_off) + one-per-phase (only P on) +
    final (all_on). Feeds metrics.add_phase_result per event per phase."""
    # 1. Baseline: all_off
    base_results = {r["event_id"]: replay_record(r, ReplayConfig.preset_all_off()) for r in records}
    # 2. Final: all_on (use current settings)
    final_results = {r["event_id"]: replay_record(r, ReplayConfig.preset_all_on()) for r in records}
    # 3. Per-phase: only P on
    for phase_name, field_name in _PHASE_FIELDS:
        phase_cfg = ReplayConfig.preset_all_off()
        setattr(phase_cfg, field_name, True)
        for r in records:
            eid = r["event_id"]
            phase_replayed = replay_record(r, phase_cfg)
            metrics.add_phase_result(
                event_id=eid,
                phase=phase_name,
                base_dir=base_results[eid].get("final_displayed_direction"),
                phase_dir=phase_replayed.get("final_displayed_direction"),
                final_dir=final_results[eid].get("final_displayed_direction"),
            )


def run_replay(
    records: list[dict[str, Any]],
    *,
    compare: tuple[str, str] | None = None,
    skip_marginal: bool = False,
    output_dir: Path,
) -> Path:
    """Run the replay loop and write the report. Returns the report.md path."""
    records = _enrich_with_outcome(records)

    # Determine the two configs to compare. Default: current vs all_off.
    if compare is None:
        compare = ("current", "all_off")
    cfg_a = _config_by_name(compare[0])
    cfg_b = _config_by_name(compare[1])

    metrics = ReplayMetrics()
    cases: list[dict[str, Any]] = []
    for r in records:
        replayed_a = replay_record(r, cfg_a)
        replayed_b = replay_record(r, cfg_b)
        # Compare B (replayed under alt config) against A (baseline).
        metrics.add_pair(original=replayed_a, replayed=replayed_b)
        cases.append({
            "event_id": r.get("event_id"),
            "direction_a": replayed_a.get("final_displayed_direction"),
            "direction_b": replayed_b.get("final_displayed_direction"),
        })

    if not skip_marginal:
        _run_marginal_loop(records, metrics)

    return write_report(metrics.to_dict(), output_dir, cases=cases)


def _config_by_name(name: str) -> ReplayConfig:
    if name == "current":
        return ReplayConfig.preset_all_on()
    if name == "all_off":
        return ReplayConfig.preset_all_off()
    if name == "llm_degraded":
        return ReplayConfig.preset_llm_degraded()
    raise ValueError(f"Unknown config name: {name!r} (use current/all_off/llm_degraded)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-ids", nargs="*", default=None, help="Specific event IDs to replay.")
    parser.add_argument("--sample-size", type=int, default=None, help="Random sample N events.")
    parser.add_argument("--compare", nargs=2, default=None, metavar=("CONFIG_A", "CONFIG_B"),
                        help="Two config names (current/all_off/llm_degraded). Default: current all_off")
    parser.add_argument("--skip-marginal", action="store_true", help="Skip the N+1 per-phase loop.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.output_dir is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        args.output_dir = Path("docs/reports/replay") / ts

    records = _load_records(args.event_ids, args.sample_size)
    if not records:
        logger.warning("No records to replay.")
        return 1

    logger.info("Replaying %d records...", len(records))
    report_path = run_replay(
        records,
        compare=tuple(args.compare) if args.compare else None,
        skip_marginal=args.skip_marginal,
        output_dir=args.output_dir,
    )
    logger.info("Report written to %s", report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke-test the CLI with synthetic data**

Run:
```bash
cd backend && python -c "
from scripts.replay_decision_pipeline import run_replay
from pathlib import Path
record = {
    'event_id': 'smoke-1',
    'legacy_analysis': {'ai_probability': 62.0, 'market_probability': 50.0, 'signal': 'WATCHLIST', 'signal_direction': 'LONG', 'signal_strength': 'MEDIUM', 'evidence_strength': 0.7, 'evidence_conflict_score': 0.2, 'risk_flags': [], 'analysis_quality': 'llm'},
    'actionable_recommendation': {'direction': 'YES', 'confidence': 'medium', 'suggested_allocation_pct': 2.0, 'edge': 12.0, 'risk_level': 'medium', 'rationale': '...', 'calibration_status': 'uncalibrated_provisional'},
    'evidence_breakdown': [],
    'source': {'type': 'prediction_market', 'platform': 'polymarket'},
    'market_quote': {'spread_pct': 1.0, 'liquidity': 5000.0, 'volume': 1000.0},
    'sentiment_profile': {'summary': 'neutral', 'articles': []},
    'probability': {'baseline': 50.0, 'estimated': 62.0, 'change': 12.0},
}
import tempfile
with tempfile.TemporaryDirectory() as tmp:
    path = run_replay([record], skip_marginal=True, output_dir=Path(tmp))
    print('Report at:', path)
    print(path.read_text(encoding='utf-8')[:500])
"
```
Expected: prints "Report at: ..." followed by the first 500 chars of a Markdown report with `# Replay Report` and the 7 section headers.

- [ ] **Step 3: Run full backend test suite — verify no regressions**

Run:
```bash
cd backend && python -m pytest tests/ --tb=short -q --ignore=tests/test_world_cup_gbm_features.py
```
Expected: `1702 passed, 11 skipped` (1688 prior + 14 new from Tasks 2-5: 6 config + 7 runner + 7 metrics + 3 report = 23; minus the unittest.main blocks that don't count = let me recompute: actual new tests = 3+3+2+3+3+3 = 17 from config/runner/metrics/report). Adjust expected count to whatever pytest reports — the important check is **no failures**.

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/replay_decision_pipeline.py
git commit -m "feat(replay): add replay_decision_pipeline CLI" -m "Loads records from event_store, enriches with prediction outcomes," -m "runs the N+1 marginal-contribution loop, and writes Markdown + JSON +" -m "cases.jsonl. Supports --compare for A/B config comparison and" -m "--skip-marginal for faster runs."
```

---

## Task 7: Degraded-mode integration tests (spec §4.2)

**Files:**
- Create: `backend/tests/test_replay_degraded_modes.py`

**Interfaces:**
- Consumes: `replay_record` + `simulate_llm_degraded` + `ReplayConfig` from Tasks 2-3.
- Produces: pytest coverage for spec §4.2 degraded-mode scenarios. No new production code.

- [ ] **Step 1: Write the 5 spec §4.2 scenario tests**

Create `backend/tests/test_replay_degraded_modes.py`:

```python
"""Spec §4.2 degraded-mode integration tests.

Validates that partial failures still produce safe recommendations:
- all phases degraded still produces a recommendation
- market_quality disabled for non-prediction-market sources
- source_reliability disabled when no evidence_breakdown
- partial degradation does not block the pipeline
- llm_degraded triggers guardrail block
"""
import unittest
from unittest.mock import patch


def _record(source_type: str = "prediction_market", with_evidence: bool = True) -> dict:
    rec = {
        "event_id": "degraded-1",
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
        "actionable_recommendation": {
            "direction": "YES",
            "confidence": "medium",
            "suggested_allocation_pct": 2.0,
            "edge": 12.0,
            "risk_level": "medium",
            "rationale": "...",
            "calibration_status": "uncalibrated_provisional",
        },
        "evidence_breakdown": [
            {"direction": "support", "strength": 0.7, "credibility": 0.8, "rationale": "x", "url": "https://a.com", "domain": "a.com"}
        ] if with_evidence else [],
        "source": {"type": source_type, "platform": "test"},
        "market_quote": {"spread_pct": 1.0, "liquidity": 5000.0, "volume": 1000.0},
        "sentiment_profile": {"summary": "neutral", "articles": []},
        "probability": {"baseline": 50.0, "estimated": 62.0, "change": 12.0},
    }
    return rec


class TestDegradedModes(unittest.TestCase):
    def test_all_phases_degraded_still_produces_recommendation(self):
        """When all overlays fail, the record still has
        actionable_recommendation (set before overlays run)."""
        from app.replay.runner import replay_record
        from app.replay.config import ReplayConfig
        record = _record()
        replayed = replay_record(record, ReplayConfig.preset_all_off())
        self.assertIn("actionable_recommendation", replayed)
        self.assertEqual(replayed["actionable_recommendation"]["direction"], "YES")

    def test_market_quality_disabled_when_source_not_prediction_market(self):
        """open_web / sports_event sources must not get market_quality."""
        from app.replay.runner import replay_record
        from app.replay.config import ReplayConfig
        from unittest.mock import patch
        from app.core.config import settings
        record = _record(source_type="open_web")
        with patch.multiple(settings, MARKET_QUALITY_ENABLED=True):
            replayed = replay_record(record, ReplayConfig.preset_all_on())
        # market_quality should be absent for open_web source.
        self.assertNotIn("market_quality", replayed)

    def test_source_reliability_disabled_when_no_evidence_breakdown(self):
        """Events with empty evidence_breakdown must not get source_reliability."""
        from app.replay.runner import replay_record
        from app.replay.config import ReplayConfig
        from unittest.mock import patch
        from app.core.config import settings
        record = _record(with_evidence=False)
        with patch.multiple(settings, SOURCE_RELIABILITY_ENABLED=True):
            replayed = replay_record(record, ReplayConfig.preset_all_on())
        self.assertNotIn("source_reliability", replayed)

    def test_partial_degradation_does_not_block_pipeline(self):
        """If one overlay throws, the others still run and the record is
        returned (not crashed). We simulate by passing a malformed record
        that will trip build_decision_quality's except branch."""
        from app.replay.runner import replay_record
        from app.replay.config import ReplayConfig
        from unittest.mock import patch
        from app.core.config import settings
        record = _record()
        # Corrupt actionable_recommendation to a non-dict — build_decision_quality
        # will catch the exception internally and emit an error block.
        record["actionable_recommendation"] = "not a dict"
        with patch.multiple(settings, DECISION_QUALITY_ENABLED=True):
            replayed = replay_record(record, ReplayConfig.preset_all_on())
        # Should not raise; decision_quality should have an error block.
        self.assertEqual(replayed["decision_quality"].get("error"), "build_failed")

    def test_llm_degraded_triggers_guardrail_block(self):
        """When llm_telemetry.degraded_mode=True and guardrails are on with
        llm_degraded_blocks_act=True, final direction should downgrade to WAIT."""
        from app.replay.runner import replay_record, simulate_llm_degraded
        from app.replay.config import ReplayConfig
        from unittest.mock import patch
        from app.core.config import settings
        record = _record()
        flags = {
            "LLM_TELEMETRY_ENABLED": True,
            "GUARDRAILS_ENABLED": True,
            "GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT": True,
        }
        with patch.multiple(settings, **flags):
            replayed = replay_record(record, ReplayConfig.preset_all_on())
            simulate_llm_degraded(replayed)
        self.assertIn("llm_degraded_blocks_act", replayed.get("guardrail_fired", []))
        self.assertEqual(replayed["final_displayed_direction"], "WAIT")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run degraded-mode tests**

Run:
```bash
cd backend && python -m pytest tests/test_replay_degraded_modes.py -v
```
Expected: `5 passed`

- [ ] **Step 3: Run full backend test suite — final regression check**

Run:
```bash
cd backend && python -m pytest tests/ --tb=short -q --ignore=tests/test_world_cup_gbm_features.py
```
Expected: All passing, no failures. (Final count should be prior baseline + ~22 new tests across Tasks 2-5, 7.)

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_replay_degraded_modes.py
git commit -m "test(replay): add spec §4.2 degraded-mode integration tests" -m "5 scenarios: all-phases-degraded / market_quality gated by source" -m "type / source_reliability gated by evidence_breakdown / partial" -m "degradation non-blocking / llm_degraded triggers guardrail. Validates" -m "that partial failures still produce safe recommendations."
```

- [ ] **Step 5: Final full-suite regression + summary commit (if any stragglers)**

Run:
```bash
cd backend && python -m pytest tests/ --tb=short -q --ignore=tests/test_world_cup_gbm_features.py
```
Expected: all passing. If clean, no extra commit needed. If any test file had a stray fix, commit it now with `git commit -am "test: fix stragglers from replay harness integration"`.

---

## Self-Review Notes

**Spec coverage:**
- §4.5 replay harness → Tasks 1-3, 6 ✓
- §4.5 5 metric classes → Task 4 ✓ (direction matrix / Brier / direction_correct / LLM-fallback split / per-phase marginal + conflicts)
- §1.5 A/B compare → Task 6 `--compare` flag ✓
- §4.2 degraded tests → Task 7 ✓ (all 5 spec scenarios)

**Type consistency:**
- `ReplayConfig` field names: `decision_quality_enabled` etc. (lowercase with `_enabled` suffix) — consistent across Tasks 2, 3, 6
- `_PHASE_FIELDS` in Task 6 uses the same names — verified
- `replay_record(record, cfg)` signature — consistent across Tasks 3, 4 (via add_pair), 6, 7
- `simulate_llm_degraded(replayed)` — consistent across Tasks 3, 7
- `ReplayMetrics.add_pair(original, replayed)` + `add_phase_result(...)` + `to_dict()` — consistent across Tasks 4, 5, 6

**Placeholder scan:** No TBD/TODO; every step has actual code or actual commands.

**Known limitations (carried from spec):**
- Phase 3 (Prediction Calibration) is freeze-time; replay only sees it if record already has the field.
- LLM token cost cannot be recomputed (no real token counts).
- `news_context` / `filtered_articles` not persisted; replay uses defaults.
