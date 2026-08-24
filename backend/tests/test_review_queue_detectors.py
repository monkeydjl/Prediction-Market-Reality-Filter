"""Unit tests for review_queue_detectors (Plan 4 §6.2, Q7).

Record shapes here must be shapes production can produce. Before Q7 this file
used ``outcome="NO"`` (a bare string) for the mismatch tests and
``outcome={...}`` (an ``Outcome`` dict) for the auto-resolve tests twenty lines
later — same field, same file, two shapes, and the string one existed only in
``tests/``. That is what kept two dead detectors green, including the queue's
only ERROR-severity trigger:

- ``_detect_outcome_prediction_mismatch`` compared ``record["outcome"]`` to the
  strings ``"YES"``/``"NO"`` and read ``actionable_recommendation.ai_probability``
- ``_detect_high_value_downgraded`` gated on
  ``actionable_recommendation.signal in {"act", "provisional_act"}``

``signal`` and ``ai_probability`` are not fields of ``ActionableRecommendation``
and no producer writes them (0 of 235 records in the live store).
``TestDetectorFieldShapes`` below scans the detector source and asserts every
field it reads is declared on the corresponding model, so the next such gate
fails at test time instead of going quietly dead.
"""
from __future__ import annotations

import ast
import inspect
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.memory import review_queue_store
from app.models.event import ActionableRecommendation, Outcome, Probability
from app.services import review_queue_detectors
from app.services.review_queue_detectors import (
    detect_auto_resolve_low_confidence,
    detect_review_candidates,
)

_BANNED = ("long", "short", "buy", "sell", "position", "kelly", "order")


def _recommendation(**overrides):
    """An ``actionable_recommendation`` with exactly the producer's seven keys.

    ``TestProducedRecordShape`` pins this against the real producer, so a
    field added or renamed there shows up as a failure here rather than as a
    test fixture that drifts away from production.
    """
    rec = {
        "direction": "YES",
        "confidence": "high",
        "suggested_allocation_pct": 2.0,
        "edge": 5.0,
        "risk_level": "medium",
        "rationale": "测试用理由",
        "calibration_status": "uncalibrated_provisional",
    }
    rec.update(overrides)
    return rec


def _probability(estimated: float = 90.0, **overrides):
    prob = {
        "baseline": 50.0,
        "estimated": estimated,
        "change": estimated - 50.0,
        "direction": "up" if estimated >= 50.0 else "down",
    }
    prob.update(overrides)
    return prob


def _resolved(actual: float, *, source: str = "manual", confidence: float = 1.0):
    """An ``Outcome`` dict as ``event_store.resolve_event`` stores it."""
    return {
        "status": "resolved",
        "actual_outcome": actual,
        "confidence": confidence,
        "resolved_at": "2026-08-24T00:00:00+00:00",
        "source": source,
        "notes": "",
    }


def _base_record(**overrides):
    """Minimal record that triggers no detectors by default."""
    rec = {
        "event_id": "evt-001",
        "actionable_recommendation": _recommendation(),
        "probability": _probability(),
        "final_displayed_direction": "YES",
        "final_downgrade_reason": None,
        "source_reliability": None,
        "market_quality": None,
        "outcome": None,
    }
    rec.update(overrides)
    return rec


class TestReviewQueueDetectors(unittest.TestCase):
    def test_no_candidates_for_clean_record(self):
        candidates = detect_review_candidates(_base_record())
        self.assertEqual(candidates, [])

    def test_high_value_downgraded_when_committed_call_becomes_wait(self):
        rec = _base_record(
            final_displayed_direction="WAIT",
            final_downgrade_reason="护栏触发",
        )
        candidates = detect_review_candidates(rec)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["trigger"], "high_value_downgraded")
        self.assertEqual(candidates[0]["severity"], "WARN")
        context = candidates[0]["context"]
        self.assertEqual(context["raw_direction"], "YES")
        self.assertEqual(context["final_direction"], "WAIT")
        self.assertEqual(context["downgrade_reason"], "护栏触发")
        self.assertEqual(context["estimated_probability"], 90.0)

    def test_high_value_downgraded_fires_for_a_no_call_too(self):
        rec = _base_record(
            actionable_recommendation=_recommendation(direction="NO"),
            probability=_probability(estimated=10.0),
            final_displayed_direction="AVOID",
        )
        candidates = detect_review_candidates(rec)
        self.assertEqual([c["trigger"] for c in candidates],
                         ["high_value_downgraded"])

    def test_high_value_downgraded_skips_abstentions(self):
        """WAIT/AVOID was never a committed call, so a WAIT display is not a
        downgrade of anything."""
        for direction in ("WAIT", "AVOID"):
            with self.subTest(direction=direction):
                rec = _base_record(
                    actionable_recommendation=_recommendation(direction=direction),
                    final_displayed_direction="WAIT",
                )
                self.assertEqual(detect_review_candidates(rec), [])

    def test_high_value_downgraded_requires_a_downgraded_display(self):
        """The pre-Q7 gate was dead at ``signal``; the second gate is live and
        must still be required — an undowngraded YES is not a candidate."""
        rec = _base_record(final_displayed_direction="YES")
        self.assertEqual(detect_review_candidates(rec), [])
        rec = _base_record(final_displayed_direction=None)
        self.assertEqual(detect_review_candidates(rec), [])

    def test_high_value_downgraded_does_not_gate_on_a_signal_key(self):
        """Regression: the old gate required ``signal in {act, provisional_act}``,
        a key no producer writes. A record without it must still fire."""
        rec = _base_record(final_displayed_direction="WAIT")
        self.assertNotIn("signal", rec["actionable_recommendation"])
        self.assertEqual([c["trigger"] for c in detect_review_candidates(rec)],
                         ["high_value_downgraded"])

    def test_source_market_conflict(self):
        rec = _base_record(
            source_reliability={
                "suggested_direction": "WAIT",
                "downgraded": True,
                "downgrade_reason": "来源可靠性不足",
            },
            market_quality={
                "suggested_direction": "YES",
                "downgraded": False,
            },
        )
        candidates = detect_review_candidates(rec)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["trigger"], "source_market_conflict")


class TestOutcomePredictionMismatch(unittest.TestCase):
    """The queue's only ERROR trigger. Every record here is an ``Outcome`` dict:
    the string form these tests used to pass never existed in production."""

    def test_fires_when_a_confident_yes_call_resolves_no(self):
        rec = _base_record(
            probability=_probability(estimated=90.0),
            outcome=_resolved(0.0),
        )
        candidates = detect_review_candidates(rec)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["trigger"], "outcome_prediction_mismatch")
        self.assertEqual(candidates[0]["severity"], "ERROR")
        context = candidates[0]["context"]
        self.assertEqual(context["actual_outcome"], 0.0)
        self.assertEqual(context["predicted_direction"], "YES")
        self.assertEqual(context["estimated_probability"], 90.0)
        self.assertAlmostEqual(context["conviction"], 0.90)

    def test_fires_when_a_confident_no_call_resolves_yes(self):
        """A NO call's conviction is the complement: P(YES)=8 is a 0.92 call.

        Reading 0.08 as the conviction would silently exempt every confident NO
        from the only ERROR trigger in the queue.
        """
        rec = _base_record(
            actionable_recommendation=_recommendation(direction="NO"),
            probability=_probability(estimated=8.0),
            outcome=_resolved(100.0),
        )
        candidates = detect_review_candidates(rec)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["trigger"], "outcome_prediction_mismatch")
        self.assertAlmostEqual(candidates[0]["context"]["conviction"], 0.92)

    def test_silent_when_the_call_was_right(self):
        for direction, actual in (("YES", 100.0), ("NO", 0.0)):
            with self.subTest(direction=direction):
                rec = _base_record(
                    actionable_recommendation=_recommendation(direction=direction),
                    probability=_probability(estimated=90.0 if direction == "YES"
                                             else 10.0),
                    outcome=_resolved(actual),
                )
                self.assertEqual(detect_review_candidates(rec), [])

    def test_a_partial_resolution_above_zero_counts_as_yes(self):
        """``actual_outcome`` is 0-100 with 0=NO, so 40 still contradicts a
        confident NO call even though the market did not settle at 100."""
        rec = _base_record(
            actionable_recommendation=_recommendation(direction="NO"),
            probability=_probability(estimated=5.0),
            outcome=_resolved(40.0),
        )
        self.assertEqual([c["trigger"] for c in detect_review_candidates(rec)],
                         ["outcome_prediction_mismatch"])

    def test_threshold_is_a_0_to_1_conviction_not_the_0_to_100_estimate(self):
        """The scale trap. ``estimated`` is 0-100 and the threshold is 0-1.

        Comparing the raw estimate against 0.75 would make every wrong call an
        ERROR: a 40%-confident wrong call would clear ``40 >= 0.75``. Measured on
        the live 235-event store, that fires on 11 of 11 wrong calls versus 0 at
        the intended default.
        """
        rec = _base_record(
            probability=_probability(estimated=40.0),
            outcome=_resolved(0.0),
        )
        self.assertEqual(detect_review_candidates(rec), [])
        # 0.40 conviction clears a 0.40 threshold and nothing lower.
        self.assertEqual(
            len(detect_review_candidates(rec, mismatch_confidence_threshold=0.40)),
            1,
        )

    def test_threshold_boundary_is_inclusive(self):
        rec = _base_record(
            probability=_probability(estimated=75.0),
            outcome=_resolved(0.0),
        )
        self.assertEqual(len(detect_review_candidates(rec)), 1)
        rec = _base_record(
            probability=_probability(estimated=74.9),
            outcome=_resolved(0.0),
        )
        self.assertEqual(detect_review_candidates(rec), [])

    def test_skips_unresolved_and_non_resolved_statuses(self):
        """The live store holds one ``status="invalid"`` outcome; only
        ``resolved`` may be graded."""
        for status in ("pending", "invalid", ""):
            with self.subTest(status=status):
                outcome = _resolved(0.0)
                outcome["status"] = status
                rec = _base_record(outcome=outcome)
                self.assertEqual(detect_review_candidates(rec), [])

    def test_skips_a_string_outcome(self):
        """Regression on the shape these tests used to supply: a bare string
        must be ignored, not compared."""
        for value in ("NO", "YES", "resolved"):
            with self.subTest(value=value):
                rec = _base_record(outcome=value)
                self.assertEqual(detect_review_candidates(rec), [])

    def test_skips_unusable_actual_outcome(self):
        for actual in (None, "0", True, float("nan"), float("inf"), -1.0):
            with self.subTest(actual=actual):
                outcome = _resolved(0.0)
                outcome["actual_outcome"] = actual
                rec = _base_record(outcome=outcome)
                self.assertEqual(detect_review_candidates(rec), [])

    def test_skips_abstentions_even_when_the_outcome_is_known(self):
        for direction in ("WAIT", "AVOID"):
            with self.subTest(direction=direction):
                rec = _base_record(
                    actionable_recommendation=_recommendation(direction=direction),
                    outcome=_resolved(0.0),
                )
                self.assertEqual(detect_review_candidates(rec), [])

    def test_cannot_fire_without_a_numeric_estimate(self):
        """No estimate means no conviction to compare, so no ERROR is raised on
        a guess — the same posture ``domain_reliability_service`` takes when the
        committed estimate is missing."""
        for probability in (None, {}, {"estimated": None}, {"estimated": "90"},
                            {"estimated": float("nan")}, "90"):
            with self.subTest(probability=probability):
                rec = _base_record(probability=probability, outcome=_resolved(0.0))
                self.assertEqual(detect_review_candidates(rec), [])

    def test_out_of_range_estimate_is_clamped_not_rejected(self):
        rec = _base_record(
            probability=_probability(estimated=140.0),
            outcome=_resolved(0.0),
        )
        candidates = detect_review_candidates(rec)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["context"]["conviction"], 1.0)
        self.assertEqual(candidates[0]["context"]["estimated_probability"], 140.0)


class TestDetectorInteraction(unittest.TestCase):
    def test_multiple_detectors_can_fire(self):
        rec = _base_record(
            probability=_probability(estimated=80.0),
            final_displayed_direction="WAIT",
            final_downgrade_reason="护栏",
            outcome=_resolved(0.0),
        )
        triggers = [c["trigger"] for c in detect_review_candidates(rec)]
        self.assertIn("high_value_downgraded", triggers)
        self.assertIn("outcome_prediction_mismatch", triggers)

    def test_handles_missing_fields_gracefully(self):
        """Detectors must not crash on records missing optional fields."""
        self.assertEqual(detect_review_candidates({"event_id": "x"}), [])

    def test_handles_wrongly_typed_blocks_without_crashing(self):
        """A detector must never raise: the orchestrator swallows exceptions, so
        a crash would silently disable the whole queue for that event."""
        for key in ("actionable_recommendation", "probability", "outcome",
                    "source_reliability", "market_quality"):
            for value in ([], "x", 3, 0.5):
                with self.subTest(key=key, value=value):
                    rec = _base_record(**{key: value})
                    self.assertIsInstance(detect_review_candidates(rec), list)

    def test_every_detector_reason_passes_the_store_vocabulary_gate(self):
        """``enqueue_item`` raises on a banned term and the orchestrator logs and
        drops the failure, so a banned word in a reason template silently loses
        the item. Asserted against the store's own checker, not a copy of the
        list."""
        firing = [
            _base_record(final_displayed_direction="WAIT",
                         final_downgrade_reason="护栏触发"),
            _base_record(probability=_probability(estimated=95.0),
                         outcome=_resolved(0.0)),
            _base_record(source_reliability={"suggested_direction": "WAIT",
                                             "downgraded": True,
                                             "downgrade_reason": "来源不足"},
                         market_quality={"suggested_direction": "YES",
                                         "downgraded": False}),
            _base_record(outcome=_resolved(100.0, source="auto_market",
                                           confidence=0.5)),
        ]
        seen: set[str] = set()
        for rec in firing:
            candidates = detect_review_candidates(rec)
            self.assertTrue(candidates, "fixture must fire a detector")
            for candidate in candidates:
                seen.add(candidate["trigger"])
                review_queue_store._check_vocabulary(candidate["reason"])
                for term in _BANNED:
                    self.assertNotIn(term, candidate["reason"].lower())
        self.assertEqual(seen, {
            "high_value_downgraded",
            "source_market_conflict",
            "outcome_prediction_mismatch",
            "auto_resolve_low_confidence",
        })

    def test_every_detector_severity_is_in_the_store_vocabulary(self):
        rec = _base_record(
            probability=_probability(estimated=95.0),
            final_displayed_direction="WAIT",
            outcome=_resolved(0.0, source="auto_market", confidence=0.5),
            source_reliability={"suggested_direction": "WAIT", "downgraded": True},
            market_quality={"suggested_direction": "YES", "downgraded": False},
        )
        candidates = detect_review_candidates(rec)
        self.assertEqual(len(candidates), 4)
        for candidate in candidates:
            self.assertIn(candidate["severity"],
                          review_queue_store.VALID_SEVERITIES)


class TestProducedRecordShape(unittest.TestCase):
    """Pin the fixtures against the real producers rather than a memory of them."""

    def test_recommendation_fixture_matches_the_sole_producer(self):
        from app.services.event_intelligence_service import (
            _build_actionable_recommendation,
        )

        produced = _build_actionable_recommendation(
            {
                "signal": "LONG",
                "signal_direction": "LONG",
                "signal_strength": "HIGH",
                "position_size": 0.02,
                "expected_edge": 0.05,
                "risk_level": "medium",
                "market_probability": 50.0,
                "ai_probability": 90.0,
                "evidence_strength": 0.8,
            },
            change=5.0,
        )
        self.assertIsNotNone(produced)
        assert produced is not None
        self.assertEqual(set(produced), set(_recommendation()))
        self.assertNotIn("signal", produced)
        self.assertNotIn("ai_probability", produced)

    def test_fixtures_validate_against_their_models(self):
        ActionableRecommendation.model_validate(_recommendation())
        Probability.model_validate(_probability())
        Outcome.model_validate(_resolved(100.0))

    def test_recommendation_model_declares_no_extra_fields(self):
        """``ActionableRecommendation`` sets no ``extra`` policy, so Pydantic's
        default applies and an injected ``signal`` is dropped on any typed
        round-trip. That is why gating on one could never work."""
        loaded = ActionableRecommendation.model_validate(
            {**_recommendation(), "signal": "act", "ai_probability": 0.9}
        )
        dumped = loaded.model_dump()
        self.assertNotIn("signal", dumped)
        self.assertNotIn("ai_probability", dumped)


class TestDetectorFieldShapes(unittest.TestCase):
    """Assert every record field the detectors read is declared on its model.

    A source scan rather than a hand-listed set: a list maintained by hand is
    exactly how ``signal`` and ``ai_probability`` survived. Both halves are
    guarded against scanning nothing, because an empty scan would pass.
    """

    @staticmethod
    def _tree():
        return ast.parse(inspect.getsource(review_queue_detectors))

    @staticmethod
    def _aliases(tree, record_key: str) -> set[str]:
        """Local names bound to ``record.get("<record_key>")``."""
        names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or):
                value = value.values[0]
            if not (isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Attribute)
                    and value.func.attr == "get"
                    and isinstance(value.func.value, ast.Name)
                    and value.func.value.id == "record"
                    and value.args
                    and isinstance(value.args[0], ast.Constant)
                    and value.args[0].value == record_key):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        return names

    @staticmethod
    def _keys_read(tree, names: set[str]) -> set[str]:
        """String keys read off any of ``names`` via ``.get("k")`` or ``["k"]``."""
        keys: set[str] = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in names
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                keys.add(node.args[0].value)
            if (isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in names
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)):
                keys.add(node.slice.value)
        return keys

    def _assert_fields_declared(self, record_key: str, model) -> None:
        tree = self._tree()
        names = self._aliases(tree, record_key)
        self.assertTrue(
            names,
            f"scan found no local bound to record.get({record_key!r}); the "
            f"detector was refactored and this guard is now vacuous",
        )
        keys = self._keys_read(tree, names)
        self.assertTrue(keys, f"scan found no field read off {sorted(names)}")
        undeclared = keys - set(model.model_fields)
        self.assertEqual(
            undeclared, set(),
            f"{record_key} detectors read {sorted(undeclared)}, which "
            f"{model.__name__} does not declare — that gate can never fire",
        )

    def test_recommendation_fields_are_declared(self):
        self._assert_fields_declared(
            "actionable_recommendation", ActionableRecommendation,
        )

    def test_outcome_fields_are_declared(self):
        self._assert_fields_declared("outcome", Outcome)

    def test_probability_fields_are_declared(self):
        self._assert_fields_declared("probability", Probability)

    def test_the_scan_catches_an_undeclared_field(self):
        """Guard on the guard: the assertion must fail for a field the model
        does not declare, or it proves nothing about the real source."""
        source = inspect.getsource(review_queue_detectors).replace(
            'direction = rec.get("direction")',
            'direction = rec.get("signal")',
            1,
        )
        tree = ast.parse(source)
        names = self._aliases(tree, "actionable_recommendation")
        keys = self._keys_read(tree, names)
        self.assertIn("signal", keys)
        self.assertTrue(keys - set(ActionableRecommendation.model_fields))


class TestAutoResolveLowConfidence(unittest.TestCase):
    def test_fires_for_auto_market_below_threshold(self):
        rec = _base_record(
            outcome=_resolved(100.0, source="auto_market", confidence=0.92),
        )
        candidates = detect_auto_resolve_low_confidence(
            rec, confidence_threshold=0.95,
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["trigger"], "auto_resolve_low_confidence")
        self.assertEqual(candidates[0]["severity"], "WARN")
        self.assertEqual(candidates[0]["context"]["outcome_confidence"], 0.92)
        self.assertEqual(candidates[0]["context"]["confidence_threshold"], 0.95)

    def test_is_strictly_below_threshold(self):
        rec = _base_record(
            outcome=_resolved(100.0, source="auto_market", confidence=0.95),
        )
        self.assertEqual(
            detect_auto_resolve_low_confidence(rec, confidence_threshold=0.95), [],
        )

    def test_skips_manual_and_unresolved_records(self):
        manual = _base_record(
            outcome=_resolved(100.0, source="manual", confidence=0.1),
        )
        pending = _resolved(100.0, source="auto_market", confidence=0.1)
        pending["status"] = "pending"
        self.assertEqual(detect_auto_resolve_low_confidence(manual), [])
        self.assertEqual(
            detect_auto_resolve_low_confidence(_base_record(outcome=pending)), [],
        )

    def test_skips_non_numeric_confidence(self):
        outcome = _resolved(100.0, source="auto_market")
        outcome["confidence"] = "0.92"
        self.assertEqual(
            detect_auto_resolve_low_confidence(_base_record(outcome=outcome)), [],
        )

    def test_included_in_detect_review_candidates(self):
        """The auto-resolve record also resolves YES against a YES call, so only
        this detector may fire — the mismatch detector must stay silent on a
        correct call."""
        rec = _base_record(
            outcome=_resolved(100.0, source="auto_market", confidence=0.92),
        )
        candidates = detect_review_candidates(
            rec, auto_resolve_confidence_threshold=0.93,
        )
        self.assertEqual([c["trigger"] for c in candidates],
                         ["auto_resolve_low_confidence"])

    def test_reason_excludes_banned_terms(self):
        rec = _base_record(
            outcome=_resolved(100.0, source="auto_market", confidence=0.92),
        )
        candidates = detect_auto_resolve_low_confidence(rec)
        self.assertEqual(len(candidates), 1)
        for term in _BANNED:
            self.assertNotIn(term, candidates[0]["reason"].lower())


class TestOrchestratorWiring(unittest.TestCase):
    def test_thresholds_are_forwarded_to_the_detectors_that_take_them(self):
        """The env vars only matter if ``detect_review_candidates`` forwards
        them; a default baked in at the call site would ignore the config."""
        with patch.object(review_queue_detectors,
                          "_detect_outcome_prediction_mismatch",
                          return_value=[]) as mismatch, \
             patch.object(review_queue_detectors,
                          "_detect_auto_resolve_low_confidence",
                          return_value=[]) as auto_resolve:
            detect_review_candidates(
                _base_record(),
                mismatch_confidence_threshold=0.11,
                auto_resolve_confidence_threshold=0.22,
            )
        self.assertEqual(mismatch.call_args.kwargs["confidence_threshold"], 0.11)
        self.assertEqual(
            auto_resolve.call_args.kwargs["confidence_threshold"], 0.22,
        )

    def test_non_dict_record_is_ignored(self):
        for value in (None, [], "x", 3):
            with self.subTest(value=value):
                self.assertEqual(detect_review_candidates(value), [])


if __name__ == "__main__":
    unittest.main()
