"""Tests for app.utils.stable_sample (Q2).

The module replaced ``random.Random(42).sample`` / ``random.seed(42)`` +
``random.sample`` at the two replay-family call sites. ``sample`` picks
*positions*, so what it selects depends on the population's length and order —
which is exactly what a report claiming "deterministic sampling for
reproducibility" must not depend on.

Every test here that could pass against the old positional sampler is marked as
such, and the ones that could not (order independence, one-for-one displacement)
are the reason the module exists. ``test_random_sample_is_order_dependent``
holds the *counterexample* so the contrast is pinned rather than asserted in a
comment: if someone reverts to ``random.sample``, that test still passes and the
order-independence tests turn red.
"""
import random
import unittest

from app.utils.stable_sample import (
    SELECTION_STRATEGY,
    selection_digest,
    stable_sample,
)


def _ids(n: int, prefix: str = "evt") -> list[str]:
    return [f"{prefix}-{i:04d}" for i in range(n)]


class TestSelectionDigest(unittest.TestCase):
    def test_is_a_sha256_hex_digest(self):
        digest = selection_digest("replay", "evt-0001")
        self.assertEqual(len(digest), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in digest))

    def test_is_stable_across_calls(self):
        self.assertEqual(
            selection_digest("replay", "evt-0001"),
            selection_digest("replay", "evt-0001"),
        )

    def test_seed_changes_the_digest(self):
        self.assertNotEqual(
            selection_digest("replay", "evt-0001"),
            selection_digest("flag-impact", "evt-0001"),
        )

    def test_id_changes_the_digest(self):
        self.assertNotEqual(
            selection_digest("replay", "evt-0001"),
            selection_digest("replay", "evt-0002"),
        )

    def test_separator_prevents_boundary_collision(self):
        """seed "a" + id "bc" must not hash like seed "ab" + id "c".

        Without a separator both concatenate to "abc" and the two different
        selections rank identically — a silent cross-seed collision.
        """
        self.assertNotEqual(
            selection_digest("a", "bc"),
            selection_digest("ab", "c"),
        )

    def test_empty_seed_and_empty_id_are_distinguishable(self):
        self.assertNotEqual(selection_digest("", "x"), selection_digest("x", ""))


class TestStableSampleBasics(unittest.TestCase):
    """Shape contracts. The old positional sampler satisfied most of these."""

    def test_returns_requested_size(self):
        self.assertEqual(len(stable_sample(_ids(100), seed="s", size=10)), 10)

    def test_returns_ids_from_the_population(self):
        population = _ids(50)
        picked = stable_sample(population, seed="s", size=7)
        self.assertTrue(set(picked) <= set(population))

    def test_result_is_sorted_by_id(self):
        picked = stable_sample(_ids(200), seed="s", size=20)
        self.assertEqual(picked, sorted(picked))

    def test_size_zero_returns_empty(self):
        self.assertEqual(stable_sample(_ids(10), seed="s", size=0), [])

    def test_size_larger_than_population_returns_whole_population(self):
        population = _ids(5)
        self.assertEqual(
            stable_sample(population, seed="s", size=99), sorted(population)
        )

    def test_empty_population_returns_empty(self):
        self.assertEqual(stable_sample([], seed="s", size=5), [])

    def test_negative_size_raises(self):
        with self.assertRaises(ValueError):
            stable_sample(_ids(10), seed="s", size=-1)

    def test_duplicate_id_cannot_occupy_two_slots(self):
        """random.sample would happily draw the same record twice from a list
        that repeats it, inflating a metric's denominator with one event."""
        population = ["a", "a", "a", "b", "c", "d"]
        picked = stable_sample(population, seed="s", size=3)
        self.assertEqual(len(picked), len(set(picked)))
        self.assertEqual(len(picked), 3)

    def test_duplicates_shrink_the_effective_population(self):
        picked = stable_sample(["a", "a", "b"], seed="s", size=3)
        self.assertEqual(picked, ["a", "b"])

    def test_blank_and_non_string_ids_are_dropped(self):
        population = ["a", "", None, 7, "b"]
        picked = stable_sample(population, seed="s", size=5)  # type: ignore[list-item]
        self.assertEqual(picked, ["a", "b"])


class TestStableSampleIsOrderIndependent(unittest.TestCase):
    """The property the positional sampler did not have."""

    def test_reversing_the_population_selects_the_same_ids(self):
        population = _ids(200)
        self.assertEqual(
            stable_sample(population, seed="s", size=50),
            stable_sample(list(reversed(population)), seed="s", size=50),
        )

    def test_shuffling_the_population_selects_the_same_ids(self):
        population = _ids(200)
        shuffled = list(population)
        # Fixed seed here only to make the shuffle reproducible; the point is
        # that stable_sample's answer does not depend on it.
        random.Random(7).shuffle(shuffled)
        self.assertNotEqual(shuffled, population)  # the shuffle did something
        self.assertEqual(
            stable_sample(population, seed="s", size=50),
            stable_sample(shuffled, seed="s", size=50),
        )

    def test_random_sample_is_order_dependent(self):
        """The counterexample, kept so the contrast cannot rot into a comment.

        Measured on the live 235-event store at size 8: reversing the order left
        the old sampler an overlap of 0/8. ``event_store.json`` is rewritten
        whole, so reordering is not hypothetical here.
        """
        population = _ids(200)
        forward = sorted(random.Random(42).sample(population, 50))
        backward = sorted(random.Random(42).sample(list(reversed(population)), 50))
        self.assertNotEqual(
            forward, backward,
            "if this passes, random.sample is order-dependent (the bug); if it "
            "fails, the premise of stable_sample needs re-measuring",
        )


class TestStableSampleGrowth(unittest.TestCase):
    def test_growth_displaces_at_most_one_for_one(self):
        """Adding K events can evict at most K incumbents.

        Ranking by hash means a new id only takes a slot by out-ranking the
        current worst member. random.sample gives no such bound: it re-derives
        every position from the new length.
        """
        base_population = _ids(200)
        size = 50
        base = set(stable_sample(base_population, seed="s", size=size))
        for added in (1, 5, 20, 100):
            grown = base_population + _ids(added, prefix="new")
            after = set(stable_sample(grown, seed="s", size=size))
            kept = len(base & after)
            self.assertGreaterEqual(
                kept, size - added,
                f"adding {added} evicted {size - kept} of {size} members",
            )

    def test_shrinking_keeps_every_surviving_member(self):
        """Removing non-members must not change the selection at all."""
        population = _ids(200)
        size = 20
        picked = stable_sample(population, seed="s", size=size)
        # Drop 50 ids that were not selected.
        survivors = [i for i in population if i in set(picked)] + [
            i for i in population if i not in set(picked)
        ][50:]
        self.assertEqual(stable_sample(survivors, seed="s", size=size), picked)


class TestStableSampleSeed(unittest.TestCase):
    def test_same_seed_selects_the_same_ids(self):
        population = _ids(200)
        self.assertEqual(
            stable_sample(population, seed="replay", size=30),
            stable_sample(population, seed="replay", size=30),
        )

    def test_different_seed_selects_different_ids(self):
        """Deterministic, so this either passes forever or the seed is ignored."""
        population = _ids(200)
        a = stable_sample(population, seed="replay", size=30)
        b = stable_sample(population, seed="flag-impact", size=30)
        self.assertNotEqual(a, b)
        # Not merely reordered: the membership differs.
        self.assertNotEqual(set(a), set(b))

    def test_a_bigger_size_is_a_superset_of_a_smaller_one(self):
        """Rank order is fixed, so size N+1 keeps all N and adds one.

        This is what lets an operator widen a run without losing continuity
        with the narrower one. random.sample gives an unrelated draw per k.
        """
        population = _ids(200)
        small = set(stable_sample(population, seed="s", size=20))
        large = set(stable_sample(population, seed="s", size=21))
        self.assertTrue(small < large)
        self.assertEqual(len(large - small), 1)


class TestSelectionStrategyConstant(unittest.TestCase):
    def test_strategy_name_is_pinned(self):
        """The value is written into report artifacts, so changing it is a
        format change: a future strategy must get a new name rather than
        silently re-minting different sets under the old one."""
        self.assertEqual(SELECTION_STRATEGY, "sha256-rank")


if __name__ == "__main__":
    unittest.main()
