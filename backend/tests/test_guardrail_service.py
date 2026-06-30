"""Unit tests for the strategy-layer Guardrails (P0-8).

The guardrail service is a pure function — no LLM / I/O / settings reads.
Tests cover:

- ``enabled=False`` no-op contract (byte-identical to pre-guardrail).
- ``final_direction=None`` no-op (no overlay produced a direction).
- WAIT / AVOID not downgraded (already conservative — guardrails escalate,
  never de-escalate).
- Each rule fires independently:
  * Rule 1 (llm_degraded_blocks_act) — YES/NO -> WAIT when
    ``llm_telemetry.degraded_mode=True``.
  * Rule 2 (uncalibrated_category_blocks_act) — YES/NO -> WAIT when the
    event's category is not in the qualified set.
  * Rule 3 (high_conflict_blocks_act) — YES/NO -> WAIT when
    ``decision_quality.conflict_score >= threshold``.
- Multiple rules fire simultaneously — reasons combined with ``" | "``.
- Existing ``final_downgrade_reason`` is preserved when a guardrail fires.
- ``extract_qualified_categories`` correctly filters by ``qualified=True``.

Scope:
- Pure-function tests only (no settings / no event_intelligence integration).
- The integration with ``event_intelligence_service.analyze_event`` is
  covered by the existing integration test suite; here we test the
  guardrail layer in isolation.
"""
from __future__ import annotations

import unittest

from app.services.guardrail_service import (
    evaluate_guardrails,
    extract_qualified_categories,
)


# ─── Test fixtures ─────────────────────────────────────────────────────────


def _record(
    *,
    llm_degraded: bool | None = None,
    conflict_score: float | None = None,
    category: str | None = "politics",
    source_type: str = "prediction_market",
) -> dict:
    """Synthetic event record matching the post-merge shape that
    ``event_intelligence_service.analyze_event`` produces.

    - ``llm_telemetry.degraded_mode`` is set when ``llm_degraded`` is not None.
    - ``decision_quality.conflict_score`` is set when ``conflict_score`` is
      not None.
    - ``legacy_analysis.base_rate_category`` carries the category.
    """
    record: dict = {
        "event_id": "test-event-1",
        "source": {"type": source_type, "platform": "polymarket"},
        "legacy_analysis": {},
    }
    if category is not None:
        record["legacy_analysis"]["base_rate_category"] = category
    if llm_degraded is not None:
        record["llm_telemetry"] = {
            "degraded_mode": llm_degraded,
            "analysis_quality": "deterministic_fallback" if llm_degraded else "llm",
        }
    if conflict_score is not None:
        record["decision_quality"] = {
            "conflict_score": conflict_score,
            "consensus_level": "high" if conflict_score < 0.2 else "medium" if conflict_score < 0.4 else "low",
        }
    return record


# ─── No-op contracts ──────────────────────────────────────────────────────


class TestEnabledFalseIsNoOp(unittest.TestCase):
    """When ``enabled=False`` the function MUST return inputs unchanged —
    byte-identical to pre-guardrail behavior. This is the feature-flag
    safety contract: GUARDRAILS_ENABLED defaults to false."""

    def test_returns_inputs_unchanged_when_disabled(self):
        record = _record(llm_degraded=True, conflict_score=0.9)
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason="existing reason",
            record=record,
            enabled=False,
            llm_degraded_blocks_act=True,
            uncalibrated_category_blocks_act=True,
            high_conflict_blocks_act=True,
            high_conflict_threshold=0.40,
            qualified_categories=set(),
        )
        self.assertEqual(direction, "YES")
        self.assertEqual(reason, "existing reason")
        self.assertEqual(fired, [])


class TestNoneDirectionIsNoOp(unittest.TestCase):
    """When ``final_direction=None`` (no overlay produced a direction),
    guardrails have nothing to gate — return inputs unchanged."""

    def test_none_direction_returns_none_with_no_fires(self):
        record = _record(llm_degraded=True, conflict_score=0.9)
        direction, reason, fired = evaluate_guardrails(
            final_direction=None,
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=True,
            uncalibrated_category_blocks_act=True,
            high_conflict_blocks_act=True,
            high_conflict_threshold=0.40,
            qualified_categories=set(),
        )
        self.assertIsNone(direction)
        self.assertIsNone(reason)
        self.assertEqual(fired, [])


class TestWaitAvoidNotDowngraded(unittest.TestCase):
    """WAIT/AVOID are already conservative — guardrails escalate, never
    de-escalate. A WAIT or AVOID must pass through unchanged even when
    every rule condition is met."""

    def test_wait_passes_through_unchanged(self):
        record = _record(llm_degraded=True, conflict_score=0.9)
        direction, reason, fired = evaluate_guardrails(
            final_direction="WAIT",
            final_downgrade_reason="existing",
            record=record,
            enabled=True,
            llm_degraded_blocks_act=True,
            uncalibrated_category_blocks_act=True,
            high_conflict_blocks_act=True,
            high_conflict_threshold=0.40,
            qualified_categories=set(),
        )
        self.assertEqual(direction, "WAIT")
        self.assertEqual(reason, "existing")
        self.assertEqual(fired, [])

    def test_avoid_passes_through_unchanged(self):
        record = _record(llm_degraded=True, conflict_score=0.9)
        direction, reason, fired = evaluate_guardrails(
            final_direction="AVOID",
            final_downgrade_reason="existing",
            record=record,
            enabled=True,
            llm_degraded_blocks_act=True,
            uncalibrated_category_blocks_act=True,
            high_conflict_blocks_act=True,
            high_conflict_threshold=0.40,
            qualified_categories=set(),
        )
        self.assertEqual(direction, "AVOID")
        self.assertEqual(reason, "existing")
        self.assertEqual(fired, [])


# ─── Rule 1: llm_degraded_blocks_act ──────────────────────────────────────


class TestRule1LlmDegraded(unittest.TestCase):
    """Rule 1: ``llm_telemetry.degraded_mode=True`` -> YES/NO forced to WAIT."""

    def test_fires_for_yes_when_llm_degraded(self):
        record = _record(llm_degraded=True)
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=True,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.40,
        )
        self.assertEqual(direction, "WAIT")
        self.assertIn("LLM 降级模式", reason)
        self.assertEqual(fired, ["llm_degraded_blocks_act"])

    def test_fires_for_no_when_llm_degraded(self):
        record = _record(llm_degraded=True)
        direction, reason, fired = evaluate_guardrails(
            final_direction="NO",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=True,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.40,
        )
        self.assertEqual(direction, "WAIT")
        self.assertIn("LLM 降级模式", reason)
        self.assertEqual(fired, ["llm_degraded_blocks_act"])

    def test_does_not_fire_when_llm_not_degraded(self):
        record = _record(llm_degraded=False)
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=True,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.40,
        )
        self.assertEqual(direction, "YES")
        self.assertIsNone(reason)
        self.assertEqual(fired, [])

    def test_does_not_fire_when_llm_telemetry_missing(self):
        record = _record()  # no llm_telemetry key
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=True,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.40,
        )
        self.assertEqual(direction, "YES")
        self.assertIsNone(reason)
        self.assertEqual(fired, [])

    def test_does_not_fire_when_rule_disabled(self):
        record = _record(llm_degraded=True)
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,  # rule off
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.40,
        )
        self.assertEqual(direction, "YES")
        self.assertIsNone(reason)
        self.assertEqual(fired, [])


# ─── Rule 2: uncalibrated_category_blocks_act ────────────────────────────


class TestRule2UncalibratedCategory(unittest.TestCase):
    """Rule 2: when category not in qualified set -> YES/NO forced to WAIT."""

    def test_fires_when_category_not_qualified(self):
        record = _record(category="politics")
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=True,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.40,
            qualified_categories={"sports_event", "crypto"},  # politics not in set
        )
        self.assertEqual(direction, "WAIT")
        self.assertIn("未校准类别", reason)
        self.assertEqual(fired, ["uncalibrated_category_blocks_act"])

    def test_does_not_fire_when_category_qualified(self):
        record = _record(category="politics")
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=True,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.40,
            qualified_categories={"politics", "crypto"},
        )
        self.assertEqual(direction, "YES")
        self.assertIsNone(reason)
        self.assertEqual(fired, [])

    def test_does_not_fire_when_qualified_categories_none(self):
        """None = skip the qualification check (cold-start path)."""
        record = _record(category="politics")
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=True,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.40,
            qualified_categories=None,
        )
        self.assertEqual(direction, "YES")
        self.assertIsNone(reason)
        self.assertEqual(fired, [])

    def test_fires_when_category_falls_back_to_source_type(self):
        """When ``legacy_analysis.base_rate_category`` is missing, the
        category falls back to ``source.type`` (per ``_extract_category``).
        This fallback category is NOT in the qualified set, so rule 2 fires
        — fail-closed behavior for novel categories. The cold-start bypass
        (qualified_categories=None) is the only path that skips rule 2."""
        record = _record(category=None)  # no base_rate_category in legacy
        # _extract_category falls back to source.type="prediction_market"
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=True,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.40,
            qualified_categories=set(),  # prediction_market not qualified
        )
        self.assertEqual(direction, "WAIT")
        self.assertIn("未校准类别", reason)
        self.assertEqual(fired, ["uncalibrated_category_blocks_act"])

    def test_uses_sports_event_for_sports_source(self):
        """Sports events are categorized by source.type, not legacy_analysis."""
        record = _record(category=None, source_type="sports_event")
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=True,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.40,
            qualified_categories=set(),  # sports_event not qualified
        )
        self.assertEqual(direction, "WAIT")
        self.assertIn("未校准类别", reason)
        self.assertEqual(fired, ["uncalibrated_category_blocks_act"])

    def test_does_not_fire_when_rule_disabled(self):
        record = _record(category="politics")
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=False,  # rule off
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.40,
            qualified_categories=set(),
        )
        self.assertEqual(direction, "YES")
        self.assertIsNone(reason)
        self.assertEqual(fired, [])


# ─── Rule 3: high_conflict_blocks_act ─────────────────────────────────────


class TestRule3HighConflict(unittest.TestCase):
    """Rule 3: ``conflict_score >= threshold`` -> YES/NO forced to WAIT."""

    def test_fires_when_conflict_at_threshold(self):
        """Boundary: >= means equal fires (half-open upper semantics)."""
        record = _record(conflict_score=0.40)
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=True,
            high_conflict_threshold=0.40,
        )
        self.assertEqual(direction, "WAIT")
        self.assertIn("证据冲突过高", reason)
        self.assertEqual(fired, ["high_conflict_blocks_act"])

    def test_fires_when_conflict_above_threshold(self):
        record = _record(conflict_score=0.55)
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=True,
            high_conflict_threshold=0.40,
        )
        self.assertEqual(direction, "WAIT")
        self.assertEqual(fired, ["high_conflict_blocks_act"])

    def test_does_not_fire_when_conflict_below_threshold(self):
        record = _record(conflict_score=0.30)
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=True,
            high_conflict_threshold=0.40,
        )
        self.assertEqual(direction, "YES")
        self.assertIsNone(reason)
        self.assertEqual(fired, [])

    def test_does_not_fire_when_decision_quality_missing(self):
        record = _record()  # no decision_quality key
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=True,
            high_conflict_threshold=0.40,
        )
        self.assertEqual(direction, "YES")
        self.assertIsNone(reason)
        self.assertEqual(fired, [])

    def test_does_not_fire_when_rule_disabled(self):
        record = _record(conflict_score=0.9)
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=False,  # rule off
            high_conflict_threshold=0.40,
        )
        self.assertEqual(direction, "YES")
        self.assertIsNone(reason)
        self.assertEqual(fired, [])

    def test_handles_non_numeric_conflict_score(self):
        record = _record()
        record["decision_quality"] = {"conflict_score": "not a number"}
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=True,
            high_conflict_threshold=0.40,
        )
        self.assertEqual(direction, "YES")
        self.assertIsNone(reason)
        self.assertEqual(fired, [])


# ─── Multi-rule combination ───────────────────────────────────────────────


class TestMultipleRulesFire(unittest.TestCase):
    """When multiple rules fire simultaneously, all reasons are combined with
    ``" | "`` separator and the direction is forced to WAIT once."""

    def test_all_three_rules_fire_at_once(self):
        record = _record(
            llm_degraded=True,
            conflict_score=0.6,
            category="politics",
        )
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=True,
            uncalibrated_category_blocks_act=True,
            high_conflict_blocks_act=True,
            high_conflict_threshold=0.40,
            qualified_categories=set(),  # politics not qualified
        )
        self.assertEqual(direction, "WAIT")
        # All three reasons present, separated by " | "
        self.assertIsNotNone(reason)
        self.assertIn("LLM 降级模式", reason)
        self.assertIn("未校准类别", reason)
        self.assertIn("证据冲突过高", reason)
        self.assertEqual(
            fired,
            [
                "llm_degraded_blocks_act",
                "uncalibrated_category_blocks_act",
                "high_conflict_blocks_act",
            ],
        )

    def test_existing_reason_is_preserved(self):
        """Existing ``final_downgrade_reason`` (from overlay merge) is
        preserved — guardrail reason is appended, never overwritten."""
        record = _record(llm_degraded=True)
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason="市场过薄，spread=18 超阈值",
            record=record,
            enabled=True,
            llm_degraded_blocks_act=True,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.40,
        )
        self.assertEqual(direction, "WAIT")
        self.assertIsNotNone(reason)
        # Existing reason preserved + guardrail reason appended
        self.assertTrue(reason.startswith("市场过薄，spread=18 超阈值 | "))
        self.assertIn("LLM 降级模式", reason)
        self.assertEqual(fired, ["llm_degraded_blocks_act"])

    def test_two_rules_fire_preserves_existing_reason(self):
        record = _record(
            llm_degraded=True,
            conflict_score=0.6,
        )
        direction, reason, fired = evaluate_guardrails(
            final_direction="NO",
            final_downgrade_reason="source_reliability: 来源不足",
            record=record,
            enabled=True,
            llm_degraded_blocks_act=True,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=True,
            high_conflict_threshold=0.40,
        )
        self.assertEqual(direction, "WAIT")
        self.assertEqual(
            fired,
            ["llm_degraded_blocks_act", "high_conflict_blocks_act"],
        )
        # Order: existing | rule1 | rule3
        parts = reason.split(" | ")
        self.assertEqual(parts[0], "source_reliability: 来源不足")
        self.assertTrue(any("LLM 降级模式" in p for p in parts))
        self.assertTrue(any("证据冲突过高" in p for p in parts))


# ─── extract_qualified_categories helper ──────────────────────────────────


class TestExtractQualifiedCategories(unittest.TestCase):
    """Helper: extract qualified category names from
    ``prediction_store.calibration_summary().segments``."""

    def test_returns_empty_set_for_none(self):
        self.assertEqual(extract_qualified_categories(None), set())

    def test_returns_empty_set_for_empty_dict(self):
        self.assertEqual(extract_qualified_categories({}), set())

    def test_returns_only_qualified_categories(self):
        segments = {
            "politics": {"n": 10, "qualified": True, "brier_score": 0.18},
            "crypto": {"n": 2, "qualified": False, "brier_score": 0.35},
            "sports_event": {"n": 15, "qualified": True, "brier_score": 0.12},
        }
        result = extract_qualified_categories(segments)
        self.assertEqual(result, {"politics", "sports_event"})

    def test_returns_empty_set_when_none_qualified(self):
        segments = {
            "politics": {"n": 1, "qualified": False},
            "crypto": {"n": 0, "qualified": False},
        }
        self.assertEqual(extract_qualified_categories(segments), set())

    def test_handles_malformed_segments(self):
        """Malformed segment entries are skipped (not crash)."""
        segments = {
            "politics": {"qualified": True},
            "crypto": "not a dict",  # malformed
            "sports_event": {"n": 5},  # qualified missing -> falsy -> skipped
            None: {"qualified": True},  # malformed key (None)
            "geopolitics": {"qualified": 1},  # truthy int -> qualified
        }
        result = extract_qualified_categories(segments)
        # politics (qualified=True) and geopolitics (qualified=1, truthy)
        # sports_event skipped (qualified key absent -> falsy)
        # crypto and None skipped
        self.assertIn("politics", result)
        self.assertIn("geopolitics", result)
        self.assertNotIn("sports_event", result)
        self.assertNotIn("crypto", result)


# ─── Integration smoke test ───────────────────────────────────────────────


class TestIntegrationSmoke(unittest.TestCase):
    """Smoke test: full pass with realistic inputs mirroring what
    ``event_intelligence_service.analyze_event`` would pass after the
    overlay merge. Confirms the contract holds end-to-end."""

    def test_no_fires_when_all_conditions_clean(self):
        """All overlays clean -> direction preserved, no guardrail fired."""
        record = _record(
            llm_degraded=False,
            conflict_score=0.10,
            category="politics",
        )
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason="market_quality: 流动性不足",
            record=record,
            enabled=True,
            llm_degraded_blocks_act=True,
            uncalibrated_category_blocks_act=True,
            high_conflict_blocks_act=True,
            high_conflict_threshold=0.40,
            qualified_categories={"politics"},
        )
        self.assertEqual(direction, "YES")
        # Reason preserved (no fire)
        self.assertEqual(reason, "market_quality: 流动性不足")
        self.assertEqual(fired, [])


# ─── Rule 4: market_not_executable_blocks_act ─────────────────────────────


class TestRule4MarketNotExecutable(unittest.TestCase):
    """Rule 4: execution_quality.executable=False blocks YES/NO → WAIT."""

    def _base_record(self, executable: bool) -> dict:
        return {
            "execution_quality": {
                "executable": executable,
                "platform_constraint_reasons": [] if executable else ["价差过宽无法成交"],
            },
            "llm_telemetry": {"degraded_mode": False},
            "decision_quality": {"conflict_score": 0.1},
            "category": "politics",
        }

    def test_executable_false_forces_yes_to_wait(self):
        from app.services.guardrail_service import evaluate_guardrails
        record = self._base_record(executable=False)
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.7,
            market_not_executable_blocks_act=True,
            qualified_categories=None,
        )
        self.assertEqual(direction, "WAIT")
        self.assertIn("market_not_executable_blocks_act", fired)
        self.assertIn("不可执行", (reason or ""))

    def test_executable_true_does_not_fire(self):
        from app.services.guardrail_service import evaluate_guardrails
        record = self._base_record(executable=True)
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.7,
            market_not_executable_blocks_act=True,
            qualified_categories=None,
        )
        self.assertEqual(direction, "YES")
        self.assertEqual(fired, [])

    def test_rule4_skipped_when_execution_quality_absent(self):
        """When execution_quality key is absent (feature off), rule 4 is a no-op."""
        from app.services.guardrail_service import evaluate_guardrails
        record = {
            "llm_telemetry": {"degraded_mode": False},
            "decision_quality": {"conflict_score": 0.1},
        }
        direction, reason, fired = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.7,
            market_not_executable_blocks_act=True,
            qualified_categories=None,
        )
        self.assertEqual(direction, "YES")
        self.assertEqual(fired, [])

    def test_rule4_does_not_fire_on_wait(self):
        """WAIT is already conservative — rule 4 does not escalate."""
        from app.services.guardrail_service import evaluate_guardrails
        record = self._base_record(executable=False)
        direction, reason, fired = evaluate_guardrails(
            final_direction="WAIT",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.7,
            market_not_executable_blocks_act=True,
            qualified_categories=None,
        )
        self.assertEqual(direction, "WAIT")
        self.assertEqual(fired, [])

    def test_rule4_reason_excludes_banned_terms(self):
        from app.services.guardrail_service import evaluate_guardrails
        record = self._base_record(executable=False)
        _, reason, _ = evaluate_guardrails(
            final_direction="YES",
            final_downgrade_reason=None,
            record=record,
            enabled=True,
            llm_degraded_blocks_act=False,
            uncalibrated_category_blocks_act=False,
            high_conflict_blocks_act=False,
            high_conflict_threshold=0.7,
            market_not_executable_blocks_act=True,
            qualified_categories=None,
        )
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        for term in banned:
            self.assertNotIn(term, (reason or "").lower())


if __name__ == "__main__":
    unittest.main()
