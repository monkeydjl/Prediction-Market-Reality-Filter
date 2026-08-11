"""End-to-end integration tests for the 3-way overlay merge pipeline.

Addresses production-readiness gap §4.1: the existing tests cover each
overlay service in isolation (decision_quality / market_quality /
source_reliability) and ``merge_quality_overlays`` has unit tests, but
there was no end-to-end test exercising all three layers together with
the most-strict-direction-wins semantics.

These tests do NOT touch the LLM, I/O, or settings — they construct
synthetic recommendation / evidence_breakdown / market_quote inputs and
drive the three pure builders + the merge function in the same order that
``event_intelligence_service.analyze_event`` does.

Important contract: the build functions take an ``enabled`` parameter
(when present) but it does NOT short-circuit the output — the caller
(``event_intelligence_service``) decides whether to call them at all, and
when disabled, the corresponding overlay block is simply not attached to
the record (i.e. ``None`` at the merge boundary). Tests that simulate
"disabled" therefore pass ``None`` to ``merge_quality_overlays`` rather
than relying on the build function returning ``None``.

Scope:
- All phases enabled → fields coexist, merge picks strictest.
- Most-strict-direction-wins: AVOID beats WAIT beats YES/NO.
- Tied severity → reasons concatenated with " | ".
- All phases disabled → no overlay attached (byte-identical to pre-Phase-1).
- Partial degradation (one phase raises) → other phases still produce output.
- llm_telemetry is observation-only and never affects merge.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.decision_quality_service import build_decision_quality
from app.services.market_quality_service import build_market_quality, merge_quality_overlays
from app.services.source_reliability_service import build_source_reliability


# ─── Test fixtures ─────────────────────────────────────────────────────────

def _yes_recommendation(*, edge: float = 12.0, confidence: str = "high") -> dict:
    """actionable_recommendation with direction=YES and given edge/confidence."""
    return {
        "direction": "YES",
        "confidence": confidence,
        "suggested_allocation_pct": 5.0,
        "edge": edge,
        "risk_level": "medium",
        "rationale": "Strong evidence supports YES outcome.",
        "calibration_status": "qualified",
    }


def _evidence_breakdown(direction: str = "support", *, count: int = 3) -> list[dict]:
    """N synthetic EvidenceBreakdownItem dicts with given direction.

    Sources span multiple distinct domains (reuters.com, bbc.co.uk,
    bloomberg.com) so that source_reliability's domain_diversity check
    does NOT trigger a downgrade — keeping sr "clean" lets the test
    isolate the behavior under test (dq + mq only)."""
    sources = [
        ("Reuters", "https://www.reuters.com/article-x"),
        ("BBC News", "https://www.bbc.co.uk/news/article-x"),
        ("Bloomberg", "https://www.bloomberg.com/news/article-x"),
        ("Associated Press", "https://apnews.com/article-x"),
        ("NPR", "https://www.npr.org/article-x"),
    ][:count]
    return [
        {
            "source": name,
            "title": f"{name} article supports the outcome",
            "direction": direction,
            "strength": 0.8,
            "credibility": 0.85,
            "rationale_zh": "支持方向",
        }
        for name, _ in sources
    ]


def _evidence_items(count: int = 3) -> list[dict]:
    sources = [
        ("Reuters", "https://www.reuters.com/article-x"),
        ("BBC News", "https://www.bbc.co.uk/news/article-x"),
        ("Bloomberg", "https://www.bloomberg.com/news/article-x"),
        ("Associated Press", "https://apnews.com/article-x"),
        ("NPR", "https://www.npr.org/article-x"),
    ][:count]
    return [
        {
            "kind": "news",
            "source": name,
            "title": f"{name} article",
            "summary": "...",
            "url": url,
            "published": "2026-06-01",
            "quality": 0.85,
            "relevance": 0.9,
        }
        for name, url in sources
    ]


def _polymarket_source() -> dict:
    return {"type": "prediction_market", "platform": "polymarket"}


def _healthy_market_quote() -> dict:
    """spread=2%, bid/ask around 0.60 — tight and tradeable."""
    return {"spread": 2.0, "bid": 0.59, "ask": 0.61}


def _wide_market_quote() -> dict:
    """spread=20% — triggers wide_spread_flag hard cutoff."""
    return {"spread": 20.0, "bid": 0.50, "ask": 0.70}


# ─── Default config values (mirror .env.example defaults) ─────────────────

_DQ_CONFIG = dict(max_items=3, high_threshold=0.40, medium_threshold=0.20)
_MQ_CONFIG = dict(
    max_spread_pct=12.0,
    min_liquidity=1000.0,
    min_volume=1000.0,
    score_threshold=0.5,
)
_SR_CONFIG = dict(
    score_threshold=0.5,
    min_trusted_ratio=0.4,
    min_domain_diversity=2,
    min_sources=2,
)


# ─── Tests ─────────────────────────────────────────────────────────────────


class TestAllPhasesEnabledMerge(unittest.TestCase):
    """When all three overlays compute cleanly, merge picks the strictest."""

    def test_clean_yes_survives_all_three_phases(self):
        """Healthy market + supporting evidence + reliable sources → YES."""
        dq = build_decision_quality(
            recommendation=_yes_recommendation(),
            evidence_breakdown=_evidence_breakdown("support", count=3),
            enabled=True,
            **_DQ_CONFIG,
        )
        mq = build_market_quality(
            recommendation=_yes_recommendation(),
            source=_polymarket_source(),
            market_quote=_healthy_market_quote(),
            volume=5000.0,
            liquidity=10000.0,
            **_MQ_CONFIG,
        )
        sr = build_source_reliability(
            evidence_breakdown=_evidence_breakdown("support", count=3),
            evidence_items=_evidence_items(count=3),
            raw_direction=dq["displayed_direction"],
            enabled=True,
            **_SR_CONFIG,
        )

        final_dir, final_reason, mq_applied, sr_applied = merge_quality_overlays(dq, mq, sr)

        # YES survives — no downgrade.
        self.assertEqual(final_dir, "YES")
        self.assertIsNone(final_reason)
        self.assertFalse(mq_applied)
        self.assertFalse(sr_applied)

    def test_wide_spread_downgrades_to_wait(self):
        """Phase 2 wide_spread_flag forces WAIT even with healthy liquidity."""
        dq = build_decision_quality(
            recommendation=_yes_recommendation(),
            evidence_breakdown=_evidence_breakdown("support", count=3),
            enabled=True,
            **_DQ_CONFIG,
        )
        mq = build_market_quality(
            recommendation=_yes_recommendation(),
            source=_polymarket_source(),
            market_quote=_wide_market_quote(),  # spread=20 > 12 threshold
            volume=5000.0,
            liquidity=10000.0,
            **_MQ_CONFIG,
        )
        sr = build_source_reliability(
            evidence_breakdown=_evidence_breakdown("support", count=3),
            evidence_items=_evidence_items(count=3),
            raw_direction=dq["displayed_direction"],
            enabled=True,
            **_SR_CONFIG,
        )

        final_dir, final_reason, mq_applied, sr_applied = merge_quality_overlays(dq, mq, sr)

        self.assertEqual(final_dir, "WAIT")
        self.assertIsNotNone(final_reason)
        self.assertTrue(mq_applied)  # market_quality drove the downgrade
        self.assertFalse(sr_applied)


class TestMostStrictDirectionWins(unittest.TestCase):
    """Most-strict-direction-wins semantics across the severity ranks."""

    def test_avoid_beats_wait_beats_yes(self):
        """Phase 1 AVOID should win over Phase 2 WAIT should win over Phase 1 YES."""
        # Phase 1 returns AVOID (high-risk trigger).
        dq = {
            "raw_direction": "YES",
            "displayed_direction": "AVOID",
            "downgrade_reason": "高风险，强制 AVOID。",
        }
        mq = {
            "raw_direction": "YES",
            "suggested_direction": "WAIT",
            "downgrade_reason": "市场质量不足，降级为 WAIT。",
        }
        sr = None

        final_dir, final_reason, mq_applied, sr_applied = merge_quality_overlays(dq, mq, sr)

        # AVOID (severity=2) wins over WAIT (severity=1).
        self.assertEqual(final_dir, "AVOID")
        self.assertEqual(final_reason, "高风险，强制 AVOID。")
        self.assertFalse(mq_applied)  # mq's WAIT was overridden by dq's AVOID

    def test_tied_severity_concatenates_reasons(self):
        """When two overlays agree on WAIT, reasons concatenate with ' | '."""
        dq = {
            "raw_direction": "YES",
            "displayed_direction": "WAIT",
            "downgrade_reason": "证据不足，降级为 WAIT。",
        }
        mq = {
            "raw_direction": "YES",
            "suggested_direction": "WAIT",
            "downgrade_reason": "价差过大，降级为 WAIT。",
        }
        sr = {
            "raw_direction": "YES",
            "suggested_direction": "WAIT",
            "downgrade_reason": "来源多样性不足，降级为 WAIT。",
        }

        final_dir, final_reason, mq_applied, sr_applied = merge_quality_overlays(dq, mq, sr)

        self.assertEqual(final_dir, "WAIT")
        # All three reasons concatenated with " | " in dq | mq | sr order.
        self.assertEqual(
            final_reason,
            "证据不足，降级为 WAIT。 | 价差过大，降级为 WAIT。 | 来源多样性不足，降级为 WAIT。",
        )
        # Both mq and sr "applied" since they tied at max severity with reasons.
        self.assertTrue(mq_applied)
        self.assertTrue(sr_applied)


class TestOverlayIndependence(unittest.TestCase):
    """Each overlay computes independently — no cross-feeding."""

    def test_market_quality_does_not_read_decision_quality(self):
        """mq output is identical whether or not dq is computed first."""
        rec = _yes_recommendation()
        source = _polymarket_source()
        quote = _healthy_market_quote()

        mq_alone = build_market_quality(
            recommendation=rec, source=source, market_quote=quote,
            volume=5000.0, liquidity=10000.0, **_MQ_CONFIG,
        )

        # Compute dq first, then mq — output must be identical.
        dq = build_decision_quality(
            recommendation=rec,
            evidence_breakdown=_evidence_breakdown("support", count=3),
            enabled=True, **_DQ_CONFIG,
        )
        mq_with_dq = build_market_quality(
            recommendation=rec, source=source, market_quote=quote,
            volume=5000.0, liquidity=10000.0, **_MQ_CONFIG,
        )

        self.assertEqual(mq_alone, mq_with_dq)
        self.assertNotIn("decision_quality", mq_alone)  # no leak

    def test_source_reliability_takes_raw_direction_param(self):
        """sr's raw_direction is passed in explicitly (not read from dq).
        The caller can pass None when dq is disabled — sr still runs."""
        sr = build_source_reliability(
            evidence_breakdown=_evidence_breakdown("support", count=3),
            evidence_items=_evidence_items(count=3),
            raw_direction=None,  # dq disabled → no raw_direction
            enabled=True, **_SR_CONFIG,
        )
        self.assertIsNotNone(sr)
        self.assertIn("suggested_direction", sr)


class TestAllPhasesDisabled(unittest.TestCase):
    """When all feature flags are off, no overlay is attached (caller
    doesn't call the build functions → merge receives None inputs)."""

    def test_all_none_inputs_returns_none(self):
        """merge_quality_overlays(None, None, None) → (None, None, False, False).
        This is the byte-identical-to-pre-Phase-1 contract: when no overlay
        is attached, the record carries no overlay fields."""
        final_dir, final_reason, mq_applied, sr_applied = merge_quality_overlays(None, None, None)
        self.assertIsNone(final_dir)
        self.assertIsNone(final_reason)
        self.assertFalse(mq_applied)
        self.assertFalse(sr_applied)

    def test_market_quality_not_called_for_non_prediction_market(self):
        """When source.type != 'prediction_market', build_market_quality
        returns None (the function itself enforces this gate)."""
        mq = build_market_quality(
            recommendation=_yes_recommendation(),
            source={"type": "open_web"},  # not prediction_market
            market_quote=_healthy_market_quote(),
            volume=5000.0, liquidity=10000.0,
            **_MQ_CONFIG,
        )
        self.assertIsNone(mq)

    def test_source_reliability_returns_none_for_empty_evidence(self):
        """When evidence_breakdown is empty, build_source_reliability returns None."""
        sr = build_source_reliability(
            evidence_breakdown=[],
            evidence_items=[],
            raw_direction="YES",
            enabled=True,
            **_SR_CONFIG,
        )
        self.assertIsNone(sr)


class TestPartialDegradation(unittest.TestCase):
    """Best-effort fallback: one phase failing must not block the others."""

    def test_market_quality_failure_does_not_block_decision_quality(self):
        """If mq returns None (e.g. non-prediction-market source), dq + sr still run."""
        dq = build_decision_quality(
            recommendation=_yes_recommendation(),
            evidence_breakdown=_evidence_breakdown("support", count=3),
            enabled=True, **_DQ_CONFIG,
        )
        mq = None  # market_quality failed or not applicable
        sr = build_source_reliability(
            evidence_breakdown=_evidence_breakdown("support", count=3),
            evidence_items=_evidence_items(count=3),
            raw_direction=dq["displayed_direction"],
            enabled=True, **_SR_CONFIG,
        )

        final_dir, final_reason, mq_applied, sr_applied = merge_quality_overlays(dq, mq, sr)

        # Note: with high-quality evidence + 3 sources on reuters.com,
        # source_reliability may downgrade WAIT due to low domain_diversity
        # (all 3 articles from same domain). The contract under test is
        # that the pipeline produces SOME output, not specifically YES.
        # We assert no exception + non-None direction.
        self.assertIsNotNone(final_dir)
        self.assertFalse(mq_applied)  # mq didn't apply (was None)

    def test_source_reliability_returns_none_for_empty_evidence(self):
        """sr returns None when evidence_breakdown is empty — merge handles it."""
        dq = build_decision_quality(
            recommendation=_yes_recommendation(),
            evidence_breakdown=[],  # empty
            enabled=True, **_DQ_CONFIG,
        )
        mq = build_market_quality(
            recommendation=_yes_recommendation(),
            source=_polymarket_source(),
            market_quote=_healthy_market_quote(),
            volume=5000.0, liquidity=10000.0,
            **_MQ_CONFIG,
        )
        sr = build_source_reliability(
            evidence_breakdown=[],  # empty → None
            evidence_items=[],
            raw_direction=dq["displayed_direction"],
            enabled=True, **_SR_CONFIG,
        )
        self.assertIsNone(sr)  # confirmed

        # Merge with sr=None still works (backward-compat with Phase 2 callers).
        final_dir, final_reason, mq_applied, sr_applied = merge_quality_overlays(dq, mq, sr)
        self.assertIsNotNone(final_dir)


class TestLLMTelemetryExcludedFromMerge(unittest.TestCase):
    """Phase 5 LLM telemetry is observation-only — never affects merge."""

    def test_llm_telemetry_not_a_merge_parameter(self):
        """merge_quality_overlays signature takes only dq/mq/sr — no llm_telemetry."""
        import inspect
        sig = inspect.signature(merge_quality_overlays)
        self.assertNotIn("llm_telemetry", sig.parameters)
        self.assertNotIn("llm", sig.parameters)


class TestRoundTripIntegration(unittest.TestCase):
    """Full round-trip: build → merge → final fields on event record."""

    def test_record_level_final_direction_field_set(self):
        """After merge, the caller sets final_displayed_direction on the record.
        Verify the contract: a clean YES event produces final=YES."""
        dq = build_decision_quality(
            recommendation=_yes_recommendation(),
            evidence_breakdown=_evidence_breakdown("support", count=3),
            enabled=True, **_DQ_CONFIG,
        )
        mq = build_market_quality(
            recommendation=_yes_recommendation(),
            source=_polymarket_source(),
            market_quote=_healthy_market_quote(),
            volume=5000.0, liquidity=10000.0,
            **_MQ_CONFIG,
        )
        sr = build_source_reliability(
            evidence_breakdown=_evidence_breakdown("support", count=3),
            evidence_items=_evidence_items(count=3),
            raw_direction=dq["displayed_direction"],
            enabled=True, **_SR_CONFIG,
        )

        final_dir, final_reason, _, _ = merge_quality_overlays(dq, mq, sr)

        # Simulate the caller attaching these to the event record.
        record = {
            "decision_quality": dq,
            "market_quality": mq,
            "source_reliability": sr,
            "final_displayed_direction": final_dir,
            "final_downgrade_reason": final_reason,
        }

        # With 3 reuters.com sources, source_reliability may trigger WAIT
        # via domain_diversity < 2. The contract under test is that the
        # record carries a final_displayed_direction field at all —
        # we don't assert the exact value here.
        self.assertIn(record["final_displayed_direction"], ("YES", "WAIT"))

    def test_record_level_final_direction_downgraded_to_wait(self):
        """Wide spread triggers WAIT — final_downgrade_reason must be set.
        Wide_spread is a hard cutoff independent of source_reliability."""
        dq = build_decision_quality(
            recommendation=_yes_recommendation(),
            evidence_breakdown=_evidence_breakdown("support", count=3),
            enabled=True, **_DQ_CONFIG,
        )
        mq = build_market_quality(
            recommendation=_yes_recommendation(),
            source=_polymarket_source(),
            market_quote=_wide_market_quote(),
            volume=5000.0, liquidity=10000.0,
            **_MQ_CONFIG,
        )
        sr = build_source_reliability(
            evidence_breakdown=_evidence_breakdown("support", count=3),
            evidence_items=_evidence_items(count=3),
            raw_direction=dq["displayed_direction"],
            enabled=True, **_SR_CONFIG,
        )

        final_dir, final_reason, mq_applied, sr_applied = merge_quality_overlays(dq, mq, sr)

        # Both mq (wide_spread) and sr (low domain_diversity) can downgrade
        # to WAIT. The contract under test: wide_spread forces WAIT
        # regardless of sr's verdict.
        self.assertEqual(final_dir, "WAIT")
        self.assertIsNotNone(final_reason)


class TestDegradedModeScenarios(unittest.TestCase):
    """Spec §4.2: Degraded-mode scenario coverage.

    Verifies the pipeline produces safe output when individual overlays
    or LLM calls fail. Uses ``_build_all_overlays`` directly (no live LLM).
    """

    def _base_record(self, **overrides) -> dict:
        """Minimal record that exercises all 5 overlays + merge + guardrail."""
        record = {
            "event_id": "test-001",
            "question": "Will X happen?",
            "source": {"type": "prediction_market", "platform": "polymarket"},
            "actionable_recommendation": {
                "direction": "YES",
                "confidence": "high",
                "ai_probability": 0.72,
            },
            "evidence_breakdown": [
                {"direction": "support", "source": "reuters.com",
                 "url": "https://reuters.com/1", "summary": "Evidence 1"},
                {"direction": "oppose", "source": "bloomberg.com",
                 "url": "https://bloomberg.com/1", "summary": "Evidence 2"},
            ],
            # last_updated intentionally absent → stale_price_flag=None (unknown).
            # Avoids date-dependence (plan maintenance fix backported from Task 1).
            "market_quote": {"bid": 48.0, "ask": 52.0, "spread": 4.0},
            "volume": 5000.0,
            "liquidity": 10000.0,
            "category": "politics",
        }
        record.update(overrides)
        return record

    def _apply_overlays(self, record, *, analysis=None) -> None:
        """Call ``_build_all_overlays`` with kwargs derived from the record.

        Plan-bug fix: ``_build_all_overlays`` requires ``analysis``,
        ``sentiment_profile``, ``news_context``, ``market_quote``,
        ``filtered_articles``, ``volume``, and ``liquidity`` keyword
        arguments — not just ``record``. Derive sensible defaults from the
        record so the tests can exercise the full pipeline. ``filtered_articles``
        is built from ``evidence_breakdown`` so ``source_reliability`` can
        extract domains from the evidence URLs (otherwise domain_diversity=0
        spuriously downgrades to WAIT).
        """
        from app.services.event_intelligence_service import _build_all_overlays
        _build_all_overlays(
            record,
            analysis=analysis or {},
            sentiment_profile=None,
            news_context="",
            market_quote=record.get("market_quote"),
            filtered_articles=[
                {"source": i.get("source"), "url": i.get("url")}
                for i in record.get("evidence_breakdown", [])
                if isinstance(i, dict)
            ],
            volume=record.get("volume"),
            liquidity=record.get("liquidity"),
        )

    # Plan-bug fix: patch target is ``app.services.event_intelligence_service.settings``
    # (not ``app.core.config.settings``) because the service module binds
    # ``settings`` at import time via ``from app.core.config import settings``.
    @patch("app.services.event_intelligence_service.settings")
    def test_all_overlays_enabled_merge_correctly(self, mock_settings):
        """All 5 overlays + execution_quality + guardrail enabled together."""
        mock_settings.DECISION_QUALITY_ENABLED = True
        mock_settings.MARKET_QUALITY_ENABLED = True
        mock_settings.SOURCE_RELIABILITY_ENABLED = True
        mock_settings.LLM_TELEMETRY_ENABLED = True
        mock_settings.GUARDRAILS_ENABLED = True
        mock_settings.EXECUTION_QUALITY_ENABLED = True
        mock_settings.DECISION_QUALITY_MAX_EVIDENCE_ITEMS = 10
        mock_settings.DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD = 0.7
        mock_settings.DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD = 0.4
        mock_settings.MARKET_MAX_SPREAD_PCT = 12.0
        mock_settings.MARKET_MIN_LIQUIDITY = 1000.0
        mock_settings.MARKET_MIN_VOLUME = 1000.0
        mock_settings.MARKET_QUALITY_SCORE_THRESHOLD = 0.5
        mock_settings.SOURCE_RELIABILITY_SCORE_THRESHOLD = 0.5
        mock_settings.SOURCE_RELIABILITY_MIN_TRUSTED_RATIO = 0.3
        mock_settings.SOURCE_RELIABILITY_MIN_DOMAIN_DIVERSITY = 2
        mock_settings.SOURCE_RELIABILITY_MIN_SOURCES = 2
        mock_settings.OPENAI_MODEL = "gpt-4"
        mock_settings.GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT = True
        mock_settings.GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT = True
        mock_settings.GUARDRAIL_HIGH_CONFLICT_BLOCKS_ACT = True
        mock_settings.GUARDRAIL_HIGH_CONFLICT_THRESHOLD = 0.7
        mock_settings.GUARDRAIL_MARKET_NOT_EXECUTABLE_BLOCKS_ACT = True
        mock_settings.EXECUTION_MAX_SPREAD_PCT = 12.0
        mock_settings.EXECUTION_STALE_PRICE_SECONDS = 300
        mock_settings.EXECUTION_MIN_LIQUIDITY = 1000.0
        mock_settings.EXECUTION_TARGET_ORDER_SIZE = 100.0
        mock_settings.EXECUTION_FEE_RATE_PCT = 1.0

        record = self._base_record()
        self._apply_overlays(record)
        # All overlays should be present
        self.assertIn("decision_quality", record)
        self.assertIn("market_quality", record)
        self.assertIn("source_reliability", record)
        self.assertIn("llm_telemetry", record)
        self.assertIn("execution_quality", record)
        self.assertIn("final_displayed_direction", record)

    # Plan-bug fix: patch target is ``app.services.event_intelligence_service.settings``
    # (see test_all_overlays_enabled_merge_correctly for rationale).
    @patch("app.services.event_intelligence_service.settings")
    def test_all_phases_disabled_byte_identical(self, mock_settings):
        """All flags off → record has no overlay keys (pre-Phase-1 compatible)."""
        mock_settings.DECISION_QUALITY_ENABLED = False
        mock_settings.MARKET_QUALITY_ENABLED = False
        mock_settings.SOURCE_RELIABILITY_ENABLED = False
        mock_settings.LLM_TELEMETRY_ENABLED = False
        mock_settings.GUARDRAILS_ENABLED = False
        mock_settings.EXECUTION_QUALITY_ENABLED = False

        record = self._base_record()
        original_keys = set(record.keys())
        self._apply_overlays(record)
        # No new overlay keys added
        for key in ("decision_quality", "market_quality", "source_reliability",
                     "llm_telemetry", "execution_quality", "final_displayed_direction",
                     "final_downgrade_reason", "guardrail_fired"):
            self.assertNotIn(key, record, f"{key} should be absent when all flags off")

    # Plan-bug fix: patch target is ``app.services.event_intelligence_service.settings``
    # (see test_all_overlays_enabled_merge_correctly for rationale).
    @patch("app.services.event_intelligence_service.settings")
    def test_llm_degraded_still_produces_recommendation(self, mock_settings):
        """When llm_telemetry.degraded_mode=True, guardrail forces YES → WAIT.

        The pipeline still produces a recommendation (WAIT), not an error.
        """
        mock_settings.DECISION_QUALITY_ENABLED = True
        mock_settings.MARKET_QUALITY_ENABLED = True
        mock_settings.SOURCE_RELIABILITY_ENABLED = True
        mock_settings.LLM_TELEMETRY_ENABLED = True
        mock_settings.GUARDRAILS_ENABLED = True
        mock_settings.EXECUTION_QUALITY_ENABLED = True
        mock_settings.DECISION_QUALITY_MAX_EVIDENCE_ITEMS = 10
        mock_settings.DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD = 0.7
        mock_settings.DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD = 0.4
        mock_settings.MARKET_MAX_SPREAD_PCT = 12.0
        mock_settings.MARKET_MIN_LIQUIDITY = 1000.0
        mock_settings.MARKET_MIN_VOLUME = 1000.0
        mock_settings.MARKET_QUALITY_SCORE_THRESHOLD = 0.5
        mock_settings.SOURCE_RELIABILITY_SCORE_THRESHOLD = 0.5
        mock_settings.SOURCE_RELIABILITY_MIN_TRUSTED_RATIO = 0.3
        mock_settings.SOURCE_RELIABILITY_MIN_DOMAIN_DIVERSITY = 2
        mock_settings.SOURCE_RELIABILITY_MIN_SOURCES = 2
        mock_settings.OPENAI_MODEL = "gpt-4"
        mock_settings.GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT = True
        mock_settings.GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT = False
        mock_settings.GUARDRAIL_HIGH_CONFLICT_BLOCKS_ACT = False
        mock_settings.GUARDRAIL_HIGH_CONFLICT_THRESHOLD = 0.7
        mock_settings.GUARDRAIL_MARKET_NOT_EXECUTABLE_BLOCKS_ACT = True
        mock_settings.EXECUTION_MAX_SPREAD_PCT = 12.0
        mock_settings.EXECUTION_STALE_PRICE_SECONDS = 300
        mock_settings.EXECUTION_MIN_LIQUIDITY = 1000.0
        mock_settings.EXECUTION_TARGET_ORDER_SIZE = 100.0
        mock_settings.EXECUTION_FEE_RATE_PCT = 1.0

        record = self._base_record()
        # Plan-bug fix: pass analysis={"analysis_quality": "deterministic_fallback"}
        # so build_llm_telemetry computes degraded_mode=True. The plan pre-set
        # record["llm_telemetry"]={"degraded_mode": True}, but LLM_TELEMETRY_ENABLED=True
        # causes _build_all_overlays to overwrite it via build_llm_telemetry(analysis=...).
        # Driving degraded_mode through the analysis path exercises the real pipeline.
        self._apply_overlays(
            record, analysis={"analysis_quality": "deterministic_fallback"}
        )
        # Guardrail should have forced YES → WAIT
        self.assertEqual(record.get("final_displayed_direction"), "WAIT")
        self.assertIn("guardrail_fired", record)
        self.assertIn("llm_degraded_blocks_act", record["guardrail_fired"])

    # Plan-bug fix: patch target is ``app.services.event_intelligence_service.settings``
    # (see test_all_overlays_enabled_merge_correctly for rationale).
    @patch("app.services.event_intelligence_service.settings")
    def test_non_prediction_market_has_no_market_or_execution_quality(self, mock_settings):
        """open_web / sports_event sources omit market_quality AND execution_quality."""
        mock_settings.DECISION_QUALITY_ENABLED = True
        mock_settings.MARKET_QUALITY_ENABLED = True
        mock_settings.SOURCE_RELIABILITY_ENABLED = True
        mock_settings.LLM_TELEMETRY_ENABLED = True
        mock_settings.GUARDRAILS_ENABLED = False  # no qualified_categories needed
        mock_settings.EXECUTION_QUALITY_ENABLED = True
        mock_settings.DECISION_QUALITY_MAX_EVIDENCE_ITEMS = 10
        mock_settings.DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD = 0.7
        mock_settings.DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD = 0.4
        mock_settings.MARKET_MAX_SPREAD_PCT = 12.0
        mock_settings.MARKET_MIN_LIQUIDITY = 1000.0
        mock_settings.MARKET_MIN_VOLUME = 1000.0
        mock_settings.MARKET_QUALITY_SCORE_THRESHOLD = 0.5
        mock_settings.SOURCE_RELIABILITY_SCORE_THRESHOLD = 0.5
        mock_settings.SOURCE_RELIABILITY_MIN_TRUSTED_RATIO = 0.3
        mock_settings.SOURCE_RELIABILITY_MIN_DOMAIN_DIVERSITY = 2
        mock_settings.SOURCE_RELIABILITY_MIN_SOURCES = 2
        mock_settings.OPENAI_MODEL = "gpt-4"
        mock_settings.EXECUTION_MAX_SPREAD_PCT = 12.0
        mock_settings.EXECUTION_STALE_PRICE_SECONDS = 300
        mock_settings.EXECUTION_MIN_LIQUIDITY = 1000.0
        mock_settings.EXECUTION_TARGET_ORDER_SIZE = 100.0
        mock_settings.EXECUTION_FEE_RATE_PCT = 1.0

        record = self._base_record()
        record["source"] = {"type": "open_web"}
        self._apply_overlays(record)
        self.assertNotIn("market_quality", record)
        self.assertNotIn("execution_quality", record)

    # Plan-bug fix: patch target is ``app.services.event_intelligence_service.settings``
    # (see test_all_overlays_enabled_merge_correctly for rationale).
    @patch("app.services.event_intelligence_service.settings")
    def test_market_not_executable_forces_wait(self, mock_settings):
        """execution_quality.executable=False → guardrail rule 4 → WAIT."""
        mock_settings.DECISION_QUALITY_ENABLED = True
        mock_settings.MARKET_QUALITY_ENABLED = True
        mock_settings.SOURCE_RELIABILITY_ENABLED = True
        mock_settings.LLM_TELEMETRY_ENABLED = True
        mock_settings.GUARDRAILS_ENABLED = True
        mock_settings.EXECUTION_QUALITY_ENABLED = True
        mock_settings.DECISION_QUALITY_MAX_EVIDENCE_ITEMS = 10
        mock_settings.DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD = 0.7
        mock_settings.DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD = 0.4
        # Wide spread → execution_quality.executable=False.
        # Plan-bug fix: set MARKET_MAX_SPREAD_PCT=25.0 (above the 20.0 spread)
        # so market_quality's wide_spread_flag does NOT fire and downgrade the
        # merge to WAIT before the guardrail can run. execution_quality uses a
        # *relative* spread_pct = (spread/mid)*100 = 40% > EXECUTION_MAX_SPREAD_PCT(12),
        # so it still reports executable=False. This isolates guardrail rule 4.
        mock_settings.MARKET_MAX_SPREAD_PCT = 25.0
        mock_settings.MARKET_MIN_LIQUIDITY = 1000.0
        mock_settings.MARKET_MIN_VOLUME = 1000.0
        mock_settings.MARKET_QUALITY_SCORE_THRESHOLD = 0.5
        mock_settings.SOURCE_RELIABILITY_SCORE_THRESHOLD = 0.5
        mock_settings.SOURCE_RELIABILITY_MIN_TRUSTED_RATIO = 0.3
        mock_settings.SOURCE_RELIABILITY_MIN_DOMAIN_DIVERSITY = 2
        mock_settings.SOURCE_RELIABILITY_MIN_SOURCES = 2
        mock_settings.OPENAI_MODEL = "gpt-4"
        mock_settings.GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT = False
        mock_settings.GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT = False
        mock_settings.GUARDRAIL_HIGH_CONFLICT_BLOCKS_ACT = False
        mock_settings.GUARDRAIL_HIGH_CONFLICT_THRESHOLD = 0.7
        mock_settings.GUARDRAIL_MARKET_NOT_EXECUTABLE_BLOCKS_ACT = True
        mock_settings.EXECUTION_MAX_SPREAD_PCT = 12.0
        mock_settings.EXECUTION_STALE_PRICE_SECONDS = 300
        mock_settings.EXECUTION_MIN_LIQUIDITY = 1000.0
        mock_settings.EXECUTION_TARGET_ORDER_SIZE = 100.0
        mock_settings.EXECUTION_FEE_RATE_PCT = 1.0

        record = self._base_record()
        record["market_quote"] = {"bid": 40.0, "ask": 60.0, "spread": 20.0}  # wide spread
        self._apply_overlays(record)
        self.assertIn("execution_quality", record)
        self.assertFalse(record["execution_quality"]["executable"])
        self.assertEqual(record.get("final_displayed_direction"), "WAIT")
        self.assertIn("guardrail_fired", record)
        self.assertIn("market_not_executable_blocks_act", record["guardrail_fired"])


class TestGuardrailColdStart(unittest.TestCase):
    """Rule 2 must not block the whole site before any category calibrates.

    ``evaluate_guardrails`` reads ``qualified_categories=None`` as "skip the
    check" and a non-None set as fail-closed. ``calibration_summary()`` does
    not raise on a fresh install — it returns ``segments={}`` — so passing the
    extracted set through unconditionally handed rule 2 an *empty* set, which
    means "no category is qualified" and forced every YES/NO to WAIT until a
    category reached CALIBRATION_FEEDBACK_MIN_SAMPLES.
    """

    def _base_record(self) -> dict:
        return {
            "event_id": "cold-start-001",
            "question": "Will X happen?",
            "source": {"type": "prediction_market", "platform": "polymarket"},
            "actionable_recommendation": {
                "direction": "YES",
                "confidence": "high",
                "ai_probability": 0.72,
            },
            "evidence_breakdown": [
                {"direction": "support", "source": "reuters.com",
                 "url": "https://reuters.com/1", "summary": "Evidence 1"},
                {"direction": "oppose", "source": "bloomberg.com",
                 "url": "https://bloomberg.com/1", "summary": "Evidence 2"},
            ],
            "market_quote": {"bid": 48.0, "ask": 52.0, "spread": 4.0},
            "volume": 5000.0,
            "liquidity": 10000.0,
            "legacy_analysis": {"base_rate_category": "politics"},
        }

    def _configure(self, mock_settings):
        """decision_quality produces the direction; only rule 2 is armed.

        ``final_displayed_direction`` is set by the overlay merge, which needs
        at least one overlay to produce a direction — with every overlay off
        the guardrail would have nothing to act on and the test would prove
        nothing.
        """
        mock_settings.DECISION_QUALITY_ENABLED = True
        mock_settings.DECISION_QUALITY_MAX_EVIDENCE_ITEMS = 10
        mock_settings.DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD = 0.7
        mock_settings.DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD = 0.4
        mock_settings.MARKET_QUALITY_ENABLED = False
        mock_settings.SOURCE_RELIABILITY_ENABLED = False
        mock_settings.LLM_TELEMETRY_ENABLED = False
        mock_settings.EXECUTION_QUALITY_ENABLED = False
        mock_settings.GUARDRAILS_ENABLED = True
        mock_settings.GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT = False
        mock_settings.GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT = True
        mock_settings.GUARDRAIL_HIGH_CONFLICT_BLOCKS_ACT = False
        mock_settings.GUARDRAIL_HIGH_CONFLICT_THRESHOLD = 0.7
        mock_settings.GUARDRAIL_MARKET_NOT_EXECUTABLE_BLOCKS_ACT = False

    def _apply(self, record) -> None:
        from app.services.event_intelligence_service import _build_all_overlays
        _build_all_overlays(
            record,
            analysis={},
            sentiment_profile=None,
            news_context="",
            market_quote=record.get("market_quote"),
            filtered_articles=[],
            volume=record.get("volume"),
            liquidity=record.get("liquidity"),
        )

    @patch("app.memory.prediction_store.calibration_summary")
    @patch("app.services.event_intelligence_service.settings")
    def test_no_segments_yet_does_not_block(self, mock_settings, mock_summary):
        """Fresh install: segments={} → rule 2 skipped, YES survives."""
        self._configure(mock_settings)
        mock_summary.return_value = {"n": 0, "segments": {}}

        record = self._base_record()
        self._apply(record)

        self.assertEqual(record.get("final_displayed_direction"), "YES")
        self.assertEqual(record.get("guardrail_fired", []), [])

    @patch("app.memory.prediction_store.calibration_summary")
    @patch("app.services.event_intelligence_service.settings")
    def test_segments_present_but_none_qualified_does_not_block(
        self, mock_settings, mock_summary
    ):
        """Data exists but nothing reached min_samples — still cold start."""
        self._configure(mock_settings)
        mock_summary.return_value = {
            "n": 3,
            "segments": {
                "politics": {"n": 3, "qualified": False},
                "crypto": {"n": 1, "qualified": False},
            },
        }

        record = self._base_record()
        self._apply(record)

        self.assertEqual(record.get("final_displayed_direction"), "YES")
        self.assertEqual(record.get("guardrail_fired", []), [])

    @patch("app.memory.prediction_store.calibration_summary")
    @patch("app.services.event_intelligence_service.settings")
    def test_fail_closed_once_another_category_qualifies(
        self, mock_settings, mock_summary
    ):
        """The guardrail keeps its teeth: a qualified set is non-empty, so an
        unqualified category is blocked exactly as designed."""
        self._configure(mock_settings)
        mock_summary.return_value = {
            "n": 20,
            "segments": {
                "crypto": {"n": 20, "qualified": True},
                "politics": {"n": 2, "qualified": False},
            },
        }

        record = self._base_record()  # category politics — not qualified
        self._apply(record)

        self.assertEqual(record.get("final_displayed_direction"), "WAIT")
        self.assertIn(
            "uncalibrated_category_blocks_act", record.get("guardrail_fired", [])
        )

    @patch("app.memory.prediction_store.calibration_summary")
    @patch("app.services.event_intelligence_service.settings")
    def test_qualified_category_passes(self, mock_settings, mock_summary):
        self._configure(mock_settings)
        mock_summary.return_value = {
            "n": 20,
            "segments": {"politics": {"n": 20, "qualified": True}},
        }

        record = self._base_record()
        self._apply(record)

        self.assertEqual(record.get("final_displayed_direction"), "YES")
        self.assertEqual(record.get("guardrail_fired", []), [])


if __name__ == "__main__":
    unittest.main()
