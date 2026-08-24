"""Tests for model_eval_set_service — pinned eval sets (Q1).

Four properties this file exists to pin:

  1. **Selection is order-independent.** The defect it replaced,
     ``random.Random(42).sample``, picks positions: reordering the population
     changed 36 of 50 members. ``test_position_sampling_is_the_defect_being_fixed``
     documents that, so reverting to ``rng.sample`` turns the invariant red
     rather than quietly passing.
  2. **A fingerprint covers exactly the graded fields.** Every field in
     FINGERPRINT_FIELDS must change the digest and nothing outside it may, so a
     field dropped from the tuple fails loudly instead of making drift
     undetectable for that field only.
  3. **A hand-edited manifest is rejected, not repaired.** Recomputing the
     digest would launder an edited membership into an authoritative artifact.
  4. **A drifted event stays in the set.** Dropping it would shrink the
     denominator and read as "we evaluated the whole set".
"""
from __future__ import annotations

import json
import random
import unittest

from app.services.model_eval_set_service import (
    EVAL_SET_SCHEMA_VERSION,
    FINGERPRINT_FIELDS,
    _selection_digest,
    build_manifest,
    manifest_digest,
    record_fingerprint,
    resolve_eval_set,
    select_event_ids,
    validate_manifest,
)


def _item(event_id="evt-001", **overrides):
    """One extract_model_metrics-shaped item."""
    item = {
        "event_id": event_id,
        "source_type": "prediction_market",
        "analysis_quality": "llm",
        "edge_bucket": "10-20",
        "source_reliability_bucket": "high(0.6-0.8)",
        "direction_correct": True,
        "brier_score": 0.16,
        "estimated_probability": 72.0,
        "actual_outcome": 100.0,
        "model": "gpt-4o-mini",
        "degraded_mode": False,
        "degraded_mode_label": "normal",
        "estimated_token_cost": 0.02,
        "guardrail_fired": [],
    }
    item.update(overrides)
    return item


def _population(n=200, prefix="evt-"):
    return [f"{prefix}{i:04d}" for i in range(n)]


class TestSelectEventIds(unittest.TestCase):
    def test_reordering_the_population_cannot_change_membership(self):
        pop = _population(200)
        forward = select_event_ids(pop, seed="s", size=50)
        backward = select_event_ids(list(reversed(pop)), seed="s", size=50)
        shuffled = list(pop)
        random.Random(7).shuffle(shuffled)
        self.assertEqual(forward, select_event_ids(shuffled, seed="s", size=50))
        self.assertEqual(forward, backward)

    def test_position_sampling_is_the_defect_being_fixed(self):
        """Documents why select_event_ids exists at all.

        If someone replaces the digest ranking with rng.sample, the invariant
        above goes red -- and this test is the reason the invariant is not
        vacuous: position sampling really does move membership on a reorder.
        """
        pop = _population(200)
        forward = set(random.Random(42).sample(pop, 50))
        backward = set(random.Random(42).sample(list(reversed(pop)), 50))
        self.assertNotEqual(forward, backward)
        # And it was not a near miss: most of the set moved.
        self.assertLess(len(forward & backward), 25)

    def test_a_new_event_displaces_at_most_one_incumbent(self):
        pop = _population(200)
        before = set(select_event_ids(pop, seed="s", size=50))
        added = [f"new-{i}" for i in range(20)]
        after = set(select_event_ids(pop + added, seed="s", size=50))
        removed = before - after
        self.assertLessEqual(len(removed), len(added))
        self.assertEqual(len(after), 50)

    def test_duplicate_id_cannot_occupy_two_slots(self):
        picked = select_event_ids(["a", "a", "b", "c"], seed="s", size=3)
        self.assertEqual(picked, ["a", "b", "c"])

    def test_size_over_population_returns_all(self):
        self.assertEqual(select_event_ids(["a", "b"], seed="s", size=99), ["a", "b"])

    def test_size_zero_returns_empty(self):
        self.assertEqual(select_event_ids(["a", "b"], seed="s", size=0), [])

    def test_negative_size_raises(self):
        with self.assertRaises(ValueError):
            select_event_ids(["a"], seed="s", size=-1)

    def test_seed_changes_membership(self):
        pop = _population(200)
        self.assertNotEqual(
            select_event_ids(pop, seed="a", size=50),
            select_event_ids(pop, seed="b", size=50),
        )

    def test_separator_prevents_seed_id_ambiguity(self):
        """seed 'a' + id 'bc' must not hash like seed 'ab' + id 'c'."""
        self.assertNotEqual(_selection_digest("a", "bc"), _selection_digest("ab", "c"))

    def test_blank_and_non_string_ids_are_dropped(self):
        self.assertEqual(
            select_event_ids(["a", "", None, 7, "b"], seed="s", size=9), ["a", "b"],
        )

    def test_output_is_sorted_by_event_id(self):
        picked = select_event_ids(_population(50), seed="s", size=10)
        self.assertEqual(picked, sorted(picked))


class TestRecordFingerprint(unittest.TestCase):
    def test_every_graded_field_moves_the_fingerprint(self):
        base = record_fingerprint(_item())
        sentinels = {
            "estimated_probability": 71.0,
            "actual_outcome": 0.0,
            "direction_correct": False,
            "brier_score": 0.17,
            "model": "other-model",
            "analysis_quality": "deterministic_fallback",
            "degraded_mode": True,
            "estimated_token_cost": 0.03,
        }
        self.assertEqual(set(sentinels), set(FINGERPRINT_FIELDS))
        for field, value in sentinels.items():
            with self.subTest(field=field):
                self.assertNotEqual(
                    base, record_fingerprint(_item(**{field: value})),
                    f"{field} is in FINGERPRINT_FIELDS but changing it did nothing",
                )

    def test_ungraded_fields_do_not_move_the_fingerprint(self):
        base = record_fingerprint(_item())
        for field, value in (
            ("event_id", "evt-999"),
            ("source_type", "sports_event"),
            ("edge_bucket", "20+"),
            ("guardrail_fired", ["cap"]),
            ("degraded_mode_label", "degraded"),
        ):
            with self.subTest(field=field):
                self.assertEqual(base, record_fingerprint(_item(**{field: value})))

    def test_minus_zero_and_zero_agree(self):
        self.assertEqual(
            record_fingerprint(_item(actual_outcome=0.0)),
            record_fingerprint(_item(actual_outcome=-0.0)),
        )

    def test_bool_and_int_do_not_collide(self):
        self.assertNotEqual(
            record_fingerprint(_item(direction_correct=True)),
            record_fingerprint(_item(direction_correct=1)),
        )

    def test_nan_and_inf_stay_distinct(self):
        nan = record_fingerprint(_item(brier_score=float("nan")))
        inf = record_fingerprint(_item(brier_score=float("inf")))
        self.assertNotEqual(nan, inf)
        self.assertNotEqual(nan, record_fingerprint(_item(brier_score=None)))

    def test_missing_field_equals_explicit_none(self):
        without = _item()
        del without["brier_score"]
        self.assertEqual(
            record_fingerprint(without), record_fingerprint(_item(brier_score=None)),
        )

    def test_int_and_float_of_equal_value_agree(self):
        """72 and 72.0 are the same estimate; they must not read as drift.

        JSON ``72`` parses to an int and ``72.0`` to a float, so a store rewrite
        that only changed a value's spelling would otherwise mark every pinned
        event as re-graded.
        """
        self.assertEqual(
            record_fingerprint(_item(estimated_probability=72)),
            record_fingerprint(_item(estimated_probability=72.0)),
        )
        self.assertEqual(
            record_fingerprint(_item(actual_outcome=100)),
            record_fingerprint(_item(actual_outcome=100.0)),
        )

    def test_an_oversized_int_does_not_crash_a_mint(self):
        big = 10 ** 400  # no double holds it
        self.assertNotEqual(
            record_fingerprint(_item(estimated_probability=big)),
            record_fingerprint(_item(estimated_probability=big + 1)),
        )


class TestBuildManifest(unittest.TestCase):
    def _items(self, n=10):
        return [_item(f"evt-{i:03d}") for i in range(n)]

    def test_shape_and_version(self):
        m = build_manifest(
            self._items(), name="baseline", revision="1", seed="s", size=4,
            created_at="2026-08-24T00:00:00+00:00",
        )
        self.assertEqual(m["eval_set_schema_version"], EVAL_SET_SCHEMA_VERSION)
        self.assertEqual(m["name"], "baseline")
        self.assertEqual(m["revision"], "1")
        self.assertEqual(len(m["event_ids"]), 4)
        self.assertEqual(m["selection"]["population"], 10)
        self.assertEqual(m["selection"]["size"], 4)
        self.assertEqual(set(m["fingerprints"]), set(m["event_ids"]))
        self.assertEqual(validate_manifest(m), [])

    def test_same_inputs_are_byte_identical(self):
        kwargs = dict(
            name="baseline", revision="1", seed="s", size=4,
            created_at="2026-08-24T00:00:00+00:00",
        )
        a = build_manifest(self._items(), **kwargs)
        b = build_manifest(list(reversed(self._items())), **kwargs)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_digest_covers_membership(self):
        m = build_manifest(self._items(), name="b", revision="1", seed="s", size=4)
        tampered = dict(m)
        tampered["event_ids"] = m["event_ids"][:2]
        self.assertNotEqual(manifest_digest(tampered), m["digest"])

    def test_digest_is_a_tamper_seal_not_a_membership_identity(self):
        """Same 4 events, minted twice a minute apart -> different digests.

        Verified on the live store: two ``--write-eval-set`` runs over the same
        130 events produced identical ``event_ids`` and digests 8e2a22bc... vs
        e7d27b02..., because ``created_at`` is inside the seal. An operator
        diffing two reports must not read that as "the set changed", so the
        property is pinned here rather than left to be rediscovered.
        """
        kwargs = dict(name="b", revision="1", seed="s", size=4)
        a = build_manifest(
            self._items(), created_at="2026-08-24T00:00:00+00:00", **kwargs
        )
        b = build_manifest(
            self._items(), created_at="2026-08-24T00:01:00+00:00", **kwargs
        )
        self.assertEqual(a["event_ids"], b["event_ids"])
        self.assertEqual(a["fingerprints"], b["fingerprints"])
        self.assertNotEqual(a["digest"], b["digest"])
        # Membership identity is name+revision, and both agree on it.
        self.assertEqual(
            (a["name"], a["revision"]), (b["name"], b["revision"])
        )

    def test_digest_excludes_itself(self):
        m = build_manifest(self._items(), name="b", revision="1", seed="s", size=4)
        self.assertEqual(manifest_digest(m), m["digest"])

    def test_revision_bump_changes_the_digest(self):
        kwargs = dict(seed="s", size=4, created_at="2026-08-24T00:00:00+00:00")
        a = build_manifest(self._items(), name="b", revision="1", **kwargs)
        b = build_manifest(self._items(), name="b", revision="2", **kwargs)
        self.assertEqual(a["event_ids"], b["event_ids"])
        self.assertNotEqual(a["digest"], b["digest"])

    def test_duplicate_event_id_raises(self):
        with self.assertRaises(ValueError):
            build_manifest(
                [_item("dup"), _item("dup", brier_score=0.9)],
                name="b", revision="1", seed="s", size=2,
            )

    def test_no_usable_items_raises(self):
        """An empty manifest would mint fine and fail on load instead."""
        for items in ([], [{"no_event_id": 1}], [_item("")]):
            with self.subTest(items=items):
                with self.assertRaises(ValueError):
                    build_manifest(items, name="b", revision="1", seed="s", size=4)

    def test_blank_metadata_and_bad_size_raise(self):
        good = dict(name="b", revision="1", seed="s", size=4)
        for field, value in (
            ("name", ""), ("name", "   "), ("name", None),
            ("revision", ""), ("revision", None),
            ("seed", ""), ("seed", None),
            ("size", 0), ("size", -1),
        ):
            with self.subTest(field=field, value=value):
                kwargs = {**good, field: value}
                with self.assertRaises(ValueError):
                    build_manifest(self._items(), **kwargs)

    def test_items_without_event_id_are_skipped_not_fatal(self):
        m = build_manifest(
            [*self._items(3), {"model": "x"}], name="b", revision="1", seed="s", size=9,
        )
        self.assertEqual(m["selection"]["population"], 3)

    def test_created_at_may_be_omitted(self):
        m = build_manifest(self._items(), name="b", revision="1", seed="s", size=2)
        self.assertIsNone(m["created_at"])
        self.assertEqual(validate_manifest(m), [])


class TestValidateManifest(unittest.TestCase):
    def _manifest(self):
        return build_manifest(
            [_item(f"evt-{i:03d}") for i in range(6)],
            name="baseline", revision="1", seed="s", size=3,
            created_at="2026-08-24T00:00:00+00:00",
        )

    def test_clean_manifest_has_no_problems(self):
        self.assertEqual(validate_manifest(self._manifest()), [])

    def test_not_a_dict(self):
        self.assertEqual(validate_manifest([1, 2]), ["manifest is not a JSON object"])

    def test_unsupported_schema_version(self):
        m = self._manifest()
        m["eval_set_schema_version"] = 99
        problems = validate_manifest(m)
        self.assertTrue(any("eval_set_schema_version" in p for p in problems))

    def test_digest_mismatch_is_reported_not_repaired(self):
        m = self._manifest()
        m["digest"] = "0" * 64
        self.assertTrue(any("digest mismatch" in p for p in validate_manifest(m)))

    def test_hand_edited_membership_is_rejected(self):
        m = self._manifest()
        m["event_ids"] = m["event_ids"][:1]
        problems = validate_manifest(m)
        self.assertTrue(any("fingerprints do not cover" in p for p in problems))
        self.assertTrue(any("digest mismatch" in p for p in problems))

    def test_duplicate_event_ids(self):
        m = self._manifest()
        m["event_ids"] = [m["event_ids"][0]] * 2
        self.assertTrue(any("duplicates" in p for p in validate_manifest(m)))

    def test_empty_event_ids(self):
        m = self._manifest()
        m["event_ids"] = []
        self.assertTrue(any("non-empty list" in p for p in validate_manifest(m)))

    def test_blank_name_and_revision(self):
        m = self._manifest()
        m["name"] = ""
        m["revision"] = None
        problems = validate_manifest(m)
        self.assertTrue(any(p.startswith("name") for p in problems))
        self.assertTrue(any(p.startswith("revision") for p in problems))

    def test_every_problem_is_reported_at_once(self):
        """The contract: fix one file once, not one problem per run."""
        m = self._manifest()
        m["eval_set_schema_version"] = 99
        m["name"] = ""
        m["fingerprints"] = "not a dict"
        del m["digest"]
        problems = validate_manifest(m)
        self.assertGreaterEqual(len(problems), 4)

    def test_missing_fingerprints_key(self):
        m = self._manifest()
        del m["fingerprints"]
        self.assertTrue(any("fingerprints" in p for p in validate_manifest(m)))


class TestResolveEvalSet(unittest.TestCase):
    def setUp(self):
        self.items = [_item(f"evt-{i:03d}") for i in range(8)]
        self.manifest = build_manifest(
            self.items, name="baseline", revision="1", seed="s", size=4,
            created_at="2026-08-24T00:00:00+00:00",
        )
        self.pinned = self.manifest["event_ids"]

    def test_clean_resolve(self):
        selected, summary = resolve_eval_set(self.manifest, self.items)
        self.assertEqual([i["event_id"] for i in selected], self.pinned)
        self.assertEqual(summary["matched"], 4)
        self.assertEqual(summary["event_count"], 4)
        self.assertEqual(summary["missing_event_ids"], [])
        self.assertEqual(summary["drifted_event_ids"], [])
        self.assertEqual(summary["ignored"], 4)
        self.assertEqual(summary["coverage"], 1.0)
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["name"], "baseline")
        self.assertEqual(summary["revision"], "1")
        self.assertEqual(summary["digest"], self.manifest["digest"])

    def test_summary_keys_are_exactly_the_documented_block(self):
        """The block goes verbatim into a JSON report, so its shape is a
        contract: no items may ride along, and a new key has to be added here
        deliberately rather than appearing in stored reports unannounced."""
        _, summary = resolve_eval_set(self.manifest, self.items)
        self.assertEqual(set(summary), {
            "name", "revision", "digest", "created_at", "selection",
            "event_count", "matched", "missing_event_ids", "drifted_event_ids",
            "ignored", "coverage", "complete",
        })
        # Nothing in the block is a list of items (only lists of ids).
        for key, value in summary.items():
            if isinstance(value, list):
                self.assertTrue(
                    all(isinstance(v, str) for v in value),
                    f"{key} carries non-id payload into the report",
                )

    def test_missing_event_is_reported_and_lowers_coverage(self):
        thinner = [i for i in self.items if i["event_id"] != self.pinned[0]]
        selected, summary = resolve_eval_set(self.manifest, thinner)
        self.assertEqual(summary["missing_event_ids"], [self.pinned[0]])
        self.assertEqual(summary["matched"], 3)
        self.assertEqual(summary["coverage"], 0.75)
        self.assertFalse(summary["complete"])
        self.assertEqual(len(selected), 3)

    def test_drifted_event_is_reported_and_kept(self):
        """Dropping it would shrink the denominator and read as a full run."""
        drifted = [
            _item(i["event_id"], actual_outcome=0.0)
            if i["event_id"] == self.pinned[0] else i
            for i in self.items
        ]
        selected, summary = resolve_eval_set(self.manifest, drifted)
        self.assertEqual(summary["drifted_event_ids"], [self.pinned[0]])
        self.assertEqual(summary["matched"], 4)
        self.assertEqual(summary["coverage"], 1.0)
        self.assertFalse(summary["complete"])
        self.assertIn(self.pinned[0], [i["event_id"] for i in selected])

    def test_items_outside_the_set_are_ignored_and_counted(self):
        _, summary = resolve_eval_set(self.manifest, self.items + [_item("evt-999")])
        self.assertEqual(summary["ignored"], 5)
        self.assertEqual(summary["matched"], 4)

    def test_duplicate_item_does_not_double_count(self):
        dupes = self.items + [_item(self.pinned[0])]
        selected, summary = resolve_eval_set(self.manifest, dupes)
        self.assertEqual(summary["matched"], 4)
        self.assertEqual(len(selected), 4)

    def test_manifest_order_is_preserved(self):
        shuffled = list(self.items)
        random.Random(3).shuffle(shuffled)
        selected, _ = resolve_eval_set(self.manifest, shuffled)
        self.assertEqual([i["event_id"] for i in selected], self.pinned)

    def test_manifest_without_fingerprints_reports_no_drift(self):
        """Degrade rather than crash: coverage is still meaningful."""
        m = dict(self.manifest)
        del m["fingerprints"]
        selected, summary = resolve_eval_set(m, self.items)
        self.assertEqual(summary["drifted_event_ids"], [])
        self.assertEqual(len(selected), 4)

    def test_empty_manifest_is_not_complete(self):
        m = dict(self.manifest)
        m["event_ids"] = []
        _, summary = resolve_eval_set(m, self.items)
        self.assertIsNone(summary["coverage"])
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["event_count"], 0)

    def test_nothing_matches(self):
        _, summary = resolve_eval_set(self.manifest, [_item("other")])
        self.assertEqual(summary["matched"], 0)
        self.assertEqual(len(summary["missing_event_ids"]), 4)
        self.assertEqual(summary["coverage"], 0.0)
        self.assertFalse(summary["complete"])


if __name__ == "__main__":
    unittest.main()
