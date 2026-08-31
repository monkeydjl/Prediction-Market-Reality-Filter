"""E22: the calibration shrinkage target must be the prior stage 1 anchored on.

`analyze_market` anchors an `unknown`-category estimate on the market probability
rather than the static 50, because 50 is a max-entropy stand-in for ignorance
("无法分类，使用最大熵先验") rather than a measured base rate;
`anchor_probability`'s comment states the purpose outright ("so unknown markets
are not forced toward 50"). It publishes that choice as `base_rate_effective_prior`.

`_apply_calibration_feedback` then shrank the fused estimate toward the SIBLING
key `base_rate_prior` - the raw static value - undoing exactly what stage 1 had
done. These tests pin the prior actually handed to `adjust_probability`, because
every pre-existing test patched `adjust_probability` and asserted nothing about
its arguments, so a correct call on the wrong argument was invisible.

The categories the fixtures rely on are asserted in `ClassificationFixtureTests`,
so a base-rate table edit fails loudly here instead of silently voiding a test.
"""

import asyncio
import math
import unittest
from unittest.mock import AsyncMock, patch

import app.services.ai_analysis_service as ai
import app.services.event_intelligence_service as eis
from app.services.base_rate_service import classify_market
from app.services.calibration_feedback_service import (
    category_shrinkage,
    component_weights,
    fuse_components,
    shrink_to_prior,
)
from tests.test_calibration_feedback_service import _record

# unknown -> static prior 50, and stage 1 substitutes the market probability.
UNKNOWN_Q = "Will the widget flurb by Tuesday?"
# entertainment_awards -> static prior 35. Chosen because 35 differs from BOTH
# unknown's 50 and from every baseline used below, so a test that passes here
# cannot be passing by coincidence.
AWARDS_Q = "Best Picture at the academy award 2027?"
AWARDS_PRIOR = 35.0

NEWS = "direction: support\nstrength: 0.5\nsource_count: 3\n"


def _run(coro):
    return asyncio.run(coro)


def _analyze(question, baseline, *, enabled=True, resolved=None, spy=None):
    """Drive analyze_event network-free: the LLM raises so the deterministic
    fallback runs, and cross-validation is disabled. `resolved` replaces the
    store seam; `spy` replaces adjust_probability outright.
    """
    stack = [
        patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", enabled),
        patch.object(ai, "_ask_ai", new=AsyncMock(side_effect=RuntimeError())),
        patch("app.services.cross_validation_service.cross_validate",
              new=AsyncMock(return_value=None)),
    ]
    if resolved is not None:
        stack.append(patch(
            "app.services.calibration_feedback_service._load_resolved_records",
            return_value=resolved))
    if spy is not None:
        stack.append(patch(
            "app.services.calibration_feedback_service.adjust_probability",
            new=spy))
    for ctx in stack:
        ctx.start()
    try:
        return _run(eis.analyze_event(
            question, baseline_probability=baseline, news_context=NEWS))
    finally:
        for ctx in reversed(stack):
            ctx.stop()


class _Spy:
    """Captures every adjust_probability call and returns a fixed result."""

    def __init__(self, result=33.0):
        self.calls = []
        self._result = result

    def __call__(self, components, category, prior, **kwargs):
        self.calls.append({"components": components, "category": category,
                           "prior": prior, "kwargs": kwargs})
        return self._result, {"weights": {}, "shrinkage": 0.0,
                              "prior": prior, "fused": self._result,
                              "samples": 0}

    @property
    def only(self):
        """The single captured call. Asserting the count here rather than at each
        use site: "if we captured anything" is a vacuous shape, and a duplicated
        call would otherwise hide behind an idempotent-looking assertion.
        """
        assert len(self.calls) == 1, f"expected exactly 1 call, got {len(self.calls)}"
        return self.calls[0]


class ClassificationFixtureTests(unittest.TestCase):
    """The fixtures' discriminating power, asserted rather than assumed."""

    def test_unknown_question_classifies_unknown_with_prior_50(self):
        br = classify_market(UNKNOWN_Q)
        self.assertEqual(br.category, "unknown")
        self.assertEqual(float(br.prior), 50.0)

    def test_awards_question_has_a_prior_distinct_from_50(self):
        br = classify_market(AWARDS_Q)
        self.assertEqual(br.category, "entertainment_awards")
        self.assertEqual(float(br.prior), AWARDS_PRIOR)
        # The whole point of this fixture: it can tell the static prior apart
        # from unknown's 50 and from the baselines the tests pass in.
        self.assertNotEqual(float(br.prior), 50.0)

    def test_stage_one_substitutes_the_market_only_for_unknown(self):
        """The rule the fix mirrors, pinned at its source."""
        for question, expect_substitution in ((UNKNOWN_Q, True), (AWARDS_Q, False)):
            br = classify_market(question)
            self.assertEqual(br.category == "unknown", expect_substitution, question)


class PriorArgumentTests(unittest.TestCase):
    """WHICH prior reaches adjust_probability. Pins the argument, not the call."""

    def test_unknown_shrinks_toward_the_market_not_the_static_50(self):
        spy = _Spy()
        record = _analyze(UNKNOWN_Q, 88.0, spy=spy)
        call = spy.only
        self.assertEqual(call["category"], "unknown")
        self.assertEqual(call["prior"], record["probability"]["baseline"])
        self.assertEqual(call["prior"], 88.0)
        # The defect's value, asserted absent by equality against the correct
        # value above rather than by a range check that both could satisfy.
        self.assertNotEqual(call["prior"], 50.0)

    def test_a_market_below_50_moves_the_prior_down_not_up(self):
        """Direction, not just distance: a symmetric fixture would pass either way."""
        spy = _Spy()
        _analyze(UNKNOWN_Q, 7.0, spy=spy)
        self.assertEqual(spy.only["prior"], 7.0)
        self.assertLess(spy.only["prior"], 50.0)

    def test_a_non_unknown_category_still_uses_its_static_prior(self):
        """The load-bearing negative: the market must NOT be substituted here.

        Baseline 88 is far from the static 35, so reading the market would be
        plainly visible. This is what makes the fix a no-op outside `unknown`.
        """
        spy = _Spy()
        _analyze(AWARDS_Q, 88.0, spy=spy)
        call = spy.only
        self.assertEqual(call["category"], "entertainment_awards")
        self.assertEqual(call["prior"], AWARDS_PRIOR)
        self.assertNotEqual(call["prior"], 88.0)

    def test_the_market_and_the_static_prior_are_distinguishable_in_the_fixture(self):
        """Guards the two tests above against a fixture where they coincide."""
        self.assertNotEqual(88.0, AWARDS_PRIOR)
        self.assertNotEqual(88.0, 50.0)

    def test_the_prior_is_the_third_positional_argument(self):
        """`adjust_probability(components, category, prior)` - pinned so a
        signature reorder cannot silently pass the category as the prior."""
        captured = {}

        def positional_spy(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return 33.0, {"weights": {}, "shrinkage": 0.0, "prior": args[2],
                          "fused": 33.0, "samples": 0}

        _analyze(UNKNOWN_Q, 61.0, spy=positional_spy)
        self.assertEqual(len(captured["args"]), 3)
        self.assertEqual(captured["kwargs"], {})
        self.assertIsInstance(captured["args"][0], dict)
        self.assertEqual(captured["args"][1], "unknown")
        self.assertEqual(captured["args"][2], 61.0)

    def test_no_call_at_all_when_the_feature_is_disabled(self):
        """The default-off gate still precedes the shrinkage, so the flag being
        live is itself pinned - otherwise every test above could be passing
        against a disabled feature."""
        spy = _Spy()
        record = _analyze(UNKNOWN_Q, 88.0, enabled=False, spy=spy)
        self.assertEqual(spy.calls, [])
        self.assertNotIn("calibration_feedback", record)


def _minimal_record(baseline=60.0, estimated=70.0):
    """The subset of a record `_apply_calibration_feedback` touches."""
    return {
        "probability": {"baseline": baseline, "estimated": estimated,
                        "change": 0.0, "direction": "flat"},
        "credibility": {"score": 50},
        "intelligence_report": {},
    }


def _apply_direct(analysis, baseline=60.0):
    """Call _apply_calibration_feedback with adjust_probability spied.

    Returns (record, the single captured call).
    """
    spy = _Spy()
    record = _minimal_record(baseline=baseline)
    with patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", True), \
            patch("app.services.calibration_feedback_service.adjust_probability",
                  new=spy):
        eis._apply_calibration_feedback(record, analysis, None)
    return record, spy.only


class FallbackChainTests(unittest.TestCase):
    """effective -> static -> baseline, and the label names the field used.

    Driven directly rather than through analyze_event, because the fallback rungs
    exist for records written before `base_rate_effective_prior` was published
    (49 of the 235 live records) - a shape a fresh analysis can never produce.
    """

    _apply = staticmethod(_apply_direct)

    def test_effective_prior_wins_when_present(self):
        record, call = self._apply({
            "base_rate_category": "unknown",
            "base_rate_prior": 50,
            "base_rate_effective_prior": 91.5,
        })
        self.assertEqual(call["prior"], 91.5)
        self.assertEqual(record["calibration_feedback"]["prior_source"],
                         "base_rate_effective_prior")

    def test_falls_back_to_the_static_prior_when_the_key_is_absent(self):
        record, call = self._apply({
            "base_rate_category": "unknown",
            "base_rate_prior": 50,
        })
        self.assertEqual(call["prior"], 50.0)
        self.assertEqual(record["calibration_feedback"]["prior_source"],
                         "base_rate_prior")

    def test_falls_back_to_the_baseline_when_both_keys_are_absent(self):
        record, call = self._apply({"base_rate_category": "unknown"}, baseline=42.0)
        self.assertEqual(call["prior"], 42.0)
        self.assertEqual(record["calibration_feedback"]["prior_source"], "baseline")

    def test_each_rung_yields_a_different_prior_on_this_fixture(self):
        """Without this, all three tests above could agree by coincidence."""
        analysis = {"base_rate_category": "unknown", "base_rate_prior": 50,
                    "base_rate_effective_prior": 91.5}
        self.assertEqual(len({91.5, 50.0, 42.0}), 3)
        _, call = self._apply(analysis, baseline=42.0)
        self.assertEqual(call["prior"], 91.5)

    def test_a_non_numeric_effective_prior_falls_through(self):
        for bad in (None, "", "abc", float("nan"), float("inf"), float("-inf"), [1]):
            with self.subTest(bad=bad):
                record, call = self._apply({
                    "base_rate_category": "unknown",
                    "base_rate_prior": 50,
                    "base_rate_effective_prior": bad,
                })
                self.assertEqual(call["prior"], 50.0)
                self.assertEqual(record["calibration_feedback"]["prior_source"],
                                 "base_rate_prior")

    def test_a_numeric_string_is_accepted_as_the_effective_prior(self):
        """`safe_float` accepts it, so the label must too - the two predicates
        agreeing is the whole reason the source cannot lie."""
        record, call = self._apply({
            "base_rate_category": "unknown",
            "base_rate_prior": 50,
            "base_rate_effective_prior": "12.5",
        })
        self.assertEqual(call["prior"], 12.5)
        self.assertEqual(record["calibration_feedback"]["prior_source"],
                         "base_rate_effective_prior")

    def test_the_label_never_disagrees_with_the_value_used(self):
        """Sweeps the acceptance boundary: whatever the label says supplied the
        prior, that field's float must be the prior actually passed."""
        candidates = [None, "", "abc", "12.5", 0, 0.0, -3, 100, 50, float("nan"),
                      float("inf"), True, [1], {"a": 1}]
        for eff in candidates:
            for static in (50, None, "oops"):
                with self.subTest(eff=eff, static=static):
                    analysis = {"base_rate_category": "unknown"}
                    if eff is not None:
                        analysis["base_rate_effective_prior"] = eff
                    if static is not None:
                        analysis["base_rate_prior"] = static
                    record, call = self._apply(analysis, baseline=42.0)
                    source = record["calibration_feedback"]["prior_source"]
                    expected = {
                        "base_rate_effective_prior": eff,
                        "base_rate_prior": static,
                        "baseline": 42.0,
                    }[source]
                    self.assertEqual(call["prior"], float(expected))
                    self.assertTrue(math.isfinite(call["prior"]))


class PublishedProbabilityTests(unittest.TestCase):
    """The number that actually ships, with the real shrinkage math running.

    No spy here: `adjust_probability` runs for real against a synthetic history,
    so these fail if the fusion, the shrinkage, or the prior selection is wrong.
    """

    # 8 records at brier 0.125 -> ratio 0.5 -> shrinkage 0.25 for the category.
    @staticmethod
    def _history(category, n=8):
        return [_record(market=10, llm=90, actual=100, brier=0.125, category=category)
                for _ in range(n)]

    def _expected(self, components, category, prior, resolved):
        weights = component_weights(resolved, 8)
        fused = fuse_components(components, weights)
        shrink = category_shrinkage(resolved, category, 8)
        return round(shrink_to_prior(fused, prior, shrink), 2), fused, shrink

    def test_the_published_estimate_uses_the_market_prior_for_unknown(self):
        resolved = self._history("unknown")
        baseline = 88.0
        record = _analyze(UNKNOWN_Q, baseline, resolved=resolved)
        info = record["calibration_feedback"]
        components = record["calibration_components"]

        correct, fused, shrink = self._expected(components, "unknown", baseline, resolved)
        defect, _, _ = self._expected(components, "unknown", 50.0, resolved)

        # The fixture must be live: with shrinkage 0 or a market at 50 the two
        # would coincide and this test would prove nothing.
        self.assertGreater(shrink, 0.0)
        self.assertNotEqual(correct, defect)

        self.assertEqual(record["probability"]["estimated"], correct)
        self.assertNotEqual(record["probability"]["estimated"], defect)
        self.assertEqual(info["prior"], baseline)
        self.assertEqual(info["prior_source"], "base_rate_effective_prior")
        self.assertAlmostEqual(info["shrinkage"], shrink, places=4)
        self.assertEqual(info["fused"], round(fused, 2))

    def test_change_and_direction_are_recomputed_from_the_published_value(self):
        resolved = self._history("unknown")
        record = _analyze(UNKNOWN_Q, 88.0, resolved=resolved)
        prob = record["probability"]
        self.assertEqual(prob["change"], round(prob["estimated"] - prob["baseline"], 2))

    def test_a_non_unknown_category_publishes_the_static_prior_result(self):
        resolved = self._history("entertainment_awards")
        record = _analyze(AWARDS_Q, 88.0, resolved=resolved)
        components = record["calibration_components"]
        correct, _, shrink = self._expected(
            components, "entertainment_awards", AWARDS_PRIOR, resolved)
        market_variant, _, _ = self._expected(
            components, "entertainment_awards", 88.0, resolved)

        self.assertGreater(shrink, 0.0)
        self.assertNotEqual(correct, market_variant)
        self.assertEqual(record["probability"]["estimated"], correct)
        self.assertEqual(record["calibration_feedback"]["prior"], AWARDS_PRIOR)

    def test_zero_shrinkage_makes_the_prior_choice_irrelevant(self):
        """17 of the 20 live categories have shrinkage 0.0, where the prior is
        inert. Documents that the fix cannot perturb them."""
        resolved = [_record(market=50, llm=100, actual=100, brier=0.0,
                            category="unknown") for _ in range(8)]
        record = _analyze(UNKNOWN_Q, 88.0, resolved=resolved)
        info = record["calibration_feedback"]
        self.assertEqual(info["shrinkage"], 0.0)
        self.assertEqual(record["probability"]["estimated"], info["fused"])

    def test_dormant_history_is_still_a_noop(self):
        """Below min_samples the whole loop is inert, so enabling the feature and
        this fix together change nothing until outcomes accumulate."""
        disabled = _analyze(UNKNOWN_Q, 88.0, enabled=False)["probability"]["estimated"]
        record = _analyze(UNKNOWN_Q, 88.0, resolved=[])
        self.assertEqual(record["probability"]["estimated"], disabled)
        self.assertEqual(record["calibration_feedback"]["shrinkage"], 0.0)


class ObservabilityTests(unittest.TestCase):
    """The shrinkage target is published, so a wrong prior is visible."""

    def test_info_carries_both_the_prior_and_its_source(self):
        record = _analyze(UNKNOWN_Q, 73.0, resolved=[])
        info = record["calibration_feedback"]
        self.assertEqual(info["prior"], 73.0)
        self.assertEqual(info["prior_source"], "base_rate_effective_prior")
        self.assertEqual(
            set(info),
            {"weights", "shrinkage", "prior", "prior_source", "fused", "samples"},
        )

    def test_the_caller_passes_the_prior_unrounded(self):
        """Rounding belongs to the published field, not to the math."""
        _, call = _apply_direct({"base_rate_category": "unknown",
                                 "base_rate_effective_prior": 12.3456})
        self.assertEqual(call["prior"], 12.3456)

    def test_the_published_prior_is_rounded_to_two_places_like_its_siblings(self):
        from app.services.calibration_feedback_service import adjust_probability
        _, info = adjust_probability({"llm": 70.0}, "unknown", 12.3456,
                                     resolved_records=[], min_samples=8)
        self.assertEqual(info["prior"], 12.35)

    def test_prior_source_is_absent_when_the_feature_is_off(self):
        record = _analyze(UNKNOWN_Q, 73.0, enabled=False)
        self.assertNotIn("calibration_feedback", record)


if __name__ == "__main__":
    unittest.main()
