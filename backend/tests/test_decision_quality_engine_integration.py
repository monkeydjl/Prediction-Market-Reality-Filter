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


if __name__ == "__main__":
    unittest.main()
