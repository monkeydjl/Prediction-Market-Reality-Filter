"""The mutation inventory must be well-formed before a manual run finds out.

``scripts/mutation_verify.py`` only checks a needle when someone runs it, and a
full ``verify`` over every set takes minutes. Two failure modes it therefore
reports too late:

* a needle that no longer occurs in its file (the code it pointed at moved), and
* a needle that occurs more than once -- ``apply`` refuses it, but only after the
  run has already started.

Both are static properties of the inventory, so they are asserted here instead.
The line-ending rule is pinned for the same reason: this repo commits LF and
checks out CRLF, so a needle containing a line break must carry CRLF or it can
never match (and the failure looks like "needle appears 0x", not like a bug in
the script).

The same reasoning covers ``verify --index``: which mutation a narrowed run will
reach is decidable without driving pytest, so ``VerifySelectionTests`` decides it
here rather than after twelve minutes of a manual run.
"""
import argparse
import dataclasses
import unittest
from pathlib import Path

from scripts import mutation_verify

REPO_ROOT = Path(mutation_verify.REPO_ROOT)


def _every_mutation():
    for mutation_set in mutation_verify.SETS:
        for index, mutation in enumerate(mutation_set.mutations, 1):
            yield mutation_set.key, index, mutation


class InventoryTests(unittest.TestCase):
    def test_the_six_sets_are_present(self):
        """Pins the inventory, so a whole set cannot be dropped silently.

        Deliberately exact and ordered: adding a set means editing this test,
        which is the point -- an inventory nobody has to touch is an inventory
        that can rot. Renamed from ``test_the_five_sets_are_present`` when
        ``restore-loopback-probe`` was added (audit section 41), the same way
        it was renamed from ``test_the_four_sets_are_present`` before.
        """
        self.assertEqual(
            [s.key for s in mutation_verify.SETS],
            [
                "daily-digest",
                "review-queue",
                "probability-probe",
                "voided-trade",
                "whitelist-fixtures",
                "restore-loopback-probe",
            ],
        )

    def test_set_keys_are_unique(self):
        keys = [s.key for s in mutation_verify.SETS]
        self.assertEqual(len(keys), len(set(keys)), keys)

    def test_every_needle_occurs_exactly_once_in_its_file(self):
        offenders = []
        for key, index, mutation in _every_mutation():
            path = REPO_ROOT / mutation.path
            if not path.is_file():
                offenders.append(f"{key} {index}: missing file {mutation.path}")
                continue
            found = path.read_bytes().count(mutation.old)
            if found != 1:
                offenders.append(
                    f"{key} {index}: needle occurs {found}x in {mutation.path}"
                )
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_no_needle_carries_a_bare_lf(self):
        offenders = [
            f"{key} {index}: {mutation.label}"
            for key, index, mutation in _every_mutation()
            if mutation.old.count(b"\n") != mutation.old.count(b"\r\n")
        ]
        self.assertEqual(
            offenders,
            [],
            "a needle with a bare LF can never match a CRLF checkout:\n"
            + "\n".join(offenders),
        )

    def test_every_guard_file_exists_and_every_guard_is_named(self):
        offenders = []
        for key, index, mutation in _every_mutation():
            if not (mutation_verify.BACKEND / mutation.guard_file).is_file():
                offenders.append(f"{key} {index}: missing {mutation.guard_file}")
            if not mutation.guards:
                offenders.append(f"{key} {index}: no guards selected")
            for guard in mutation.guards:
                if not guard.startswith("test_"):
                    offenders.append(f"{key} {index}: {guard!r} is not a test name")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_grouped_mutations_rewrite_the_same_bytes(self):
        """``apply <set>`` skips later members of a group because applying both
        would fail the second with "expected 1 occurrence, found 0" -- which only
        holds while a group's members share one path and one needle."""
        grouped: dict[tuple[str, str], list] = {}
        for key, _index, mutation in _every_mutation():
            if mutation.group:
                grouped.setdefault((key, mutation.group), []).append(mutation)
        self.assertTrue(grouped, "no group is declared, so this test proves nothing")
        for (key, group), members in grouped.items():
            with self.subTest(set=key, group=group):
                self.assertEqual({m.path for m in members}, {members[0].path})
                self.assertEqual({m.old for m in members}, {members[0].old})

    def test_every_mutation_actually_changes_something(self):
        offenders = [
            f"{key} {index}: old == new"
            for key, index, mutation in _every_mutation()
            if mutation.old == mutation.new
        ]
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_every_mutation_carries_a_note(self):
        offenders = [
            f"{key} {index}: {mutation.label}"
            for key, index, mutation in _every_mutation()
            if not mutation.note.strip()
        ]
        self.assertEqual(offenders, [], "\n".join(offenders))


class VerifySelectionTests(unittest.TestCase):
    """Which mutation a ``verify`` run reaches is decidable without running pytest.

    ``--index`` exists so specific targets can be re-taken without a twelve-minute
    pass over the whole set, which makes the selection rules load-bearing: an
    off-by-one here would quietly re-take the *neighbouring* mutation and report
    success. Pinned statically for the same reason the needles are.

    ``_parse_index_list`` is pinned next to it because argparse only surfaces its
    errors as an exit code: without these tests a parser that read ``27,,30`` as
    ``27,30`` -- dropping the empty entry instead of rejecting the typo -- would
    look exactly like a working one.
    """

    def test_no_index_covers_every_mutation_in_inventory_order(self):
        expected = [
            (mutation_set.key, position)
            for mutation_set in mutation_verify.SETS
            for position, _ in enumerate(mutation_set.mutations, 1)
        ]
        got = [
            (mutation_set.key, position)
            for mutation_set, position, _ in mutation_verify._select_verify_targets(
                [], None
            )
        ]
        self.assertEqual(got, expected)

    def test_the_selected_mutation_is_the_inventory_object(self):
        for mutation_set, position, mutation in mutation_verify._select_verify_targets(
            [], None
        ):
            with self.subTest(set=mutation_set.key, position=position):
                self.assertIs(mutation, mutation_set.mutations[position - 1])

    def test_index_one_selects_the_first_mutation_of_that_set(self):
        key = "review-queue"
        mutation_set = mutation_verify.SETS_BY_KEY[key]
        targets = mutation_verify._select_verify_targets([key], (1,))
        self.assertEqual(len(targets), 1)
        got_set, position, mutation = targets[0]
        self.assertIs(got_set, mutation_set)
        self.assertEqual(position, 1)
        self.assertIs(mutation, mutation_set.mutations[0])

    def test_index_at_the_last_position_selects_the_last_mutation(self):
        key = "whitelist-fixtures"
        mutation_set = mutation_verify.SETS_BY_KEY[key]
        last = len(mutation_set.mutations)
        got_set, position, mutation = mutation_verify._select_verify_targets(
            [key], (last,)
        )[0]
        self.assertIs(got_set, mutation_set)
        self.assertEqual(position, last)
        self.assertIs(mutation, mutation_set.mutations[last - 1])

    def test_multiple_indexes_run_in_the_order_typed(self):
        """``--index 2,1`` must not be silently sorted into inventory order.

        The closing count is the run's receipt, and a receipt that reorders what
        the operator typed is harder to line up against the command line.
        """
        key = "review-queue"
        mutation_set = mutation_verify.SETS_BY_KEY[key]
        targets = mutation_verify._select_verify_targets([key], (2, 1))
        self.assertEqual(
            [(position, mutation.label) for _, position, mutation in targets],
            [
                (2, mutation_set.mutations[1].label),
                (1, mutation_set.mutations[0].label),
            ],
        )

    def test_duplicate_index_is_rejected_instead_of_de_duplicated(self):
        key = "voided-trade"
        with self.assertRaises(SystemExit) as caught:
            mutation_verify._select_verify_targets([key], (1, 2, 1))
        self.assertIn("duplicate", str(caught.exception))

    def test_one_bad_index_rejects_the_whole_multi_index_request(self):
        """A partially valid list is an error, not a shorter run.

        Reporting the offending position is what makes the error actionable; a
        run that quietly dropped it would still pass its own four phases.
        """
        key = "voided-trade"
        size = len(mutation_verify.SETS_BY_KEY[key].mutations)
        with self.assertRaises(SystemExit) as caught:
            mutation_verify._select_verify_targets([key], (1, size + 1))
        self.assertIn(str(size + 1), str(caught.exception))

    def test_index_needs_exactly_one_set(self):
        with self.assertRaises(SystemExit):
            mutation_verify._select_verify_targets(
                ["daily-digest", "review-queue"], (1,)
            )

    def test_index_without_a_set_key_says_so_instead_of_counting_all_of_them(self):
        """``verify --index 1`` must not report "got <every set>, ...".

        An empty ``keys`` defaults to every set, and reporting that defaulted
        count reads as if the operator had typed five keys. The two mistakes get
        two messages; each is asserted so neither can collapse into the other.
        """
        with self.assertRaises(SystemExit) as caught:
            mutation_verify._select_verify_targets([], (1,))
        message = str(caught.exception)
        self.assertIn("set key", message)
        self.assertNotIn(f"got {len(mutation_verify.SETS)}", message)

        all_keys = [s.key for s in mutation_verify.SETS]
        with self.assertRaises(SystemExit) as caught:
            mutation_verify._select_verify_targets(all_keys, (1,))
        self.assertIn(f"got {len(all_keys)}", str(caught.exception))

    def test_index_out_of_range_is_rejected_at_both_ends(self):
        key = "voided-trade"
        size = len(mutation_verify.SETS_BY_KEY[key].mutations)
        for bad in (0, -1, size + 1):
            with self.subTest(index=bad):
                with self.assertRaises(SystemExit):
                    mutation_verify._select_verify_targets([key], (bad,))

    def test_unknown_set_is_rejected(self):
        with self.assertRaises(SystemExit):
            mutation_verify._select_verify_targets(["no-such-set"], None)


class IndexListParsingTests(unittest.TestCase):
    """``--index`` is one comma-separated token; the parser is asserted directly.

    ``keys`` on the ``verify`` subcommand is ``nargs='*'``, which is why the
    space-separated form is deliberately unsupported -- ``--index 27 30`` would be
    read as two set keys. These tests pin the single-token contract.
    """

    def test_single_value_parses_to_a_one_tuple(self):
        self.assertEqual(mutation_verify._parse_index_list("30"), (30,))

    def test_multiple_values_parse_in_order(self):
        self.assertEqual(mutation_verify._parse_index_list("27,30"), (27, 30))
        self.assertEqual(mutation_verify._parse_index_list("30,27"), (30, 27))

    def test_surrounding_whitespace_is_tolerated(self):
        self.assertEqual(mutation_verify._parse_index_list(" 27 , 30 "), (27, 30))

    def test_empty_entry_is_rejected(self):
        for bad in ("27,", ",27", "27,,30", "", ","):
            with self.subTest(value=bad):
                with self.assertRaises(argparse.ArgumentTypeError):
                    mutation_verify._parse_index_list(bad)

    def test_non_integer_entry_is_rejected(self):
        for bad in ("a", "27,b", "2.5", "1 2"):
            with self.subTest(value=bad):
                with self.assertRaises(argparse.ArgumentTypeError):
                    mutation_verify._parse_index_list(bad)

    def test_range_expands_inclusively(self):
        self.assertEqual(
            mutation_verify._parse_index_list("27-30"), (27, 28, 29, 30)
        )
        self.assertEqual(mutation_verify._parse_index_list("27-27"), (27,))

    def test_range_combines_with_commas(self):
        self.assertEqual(mutation_verify._parse_index_list("1-3,7"), (1, 2, 3, 7))
        self.assertEqual(mutation_verify._parse_index_list("7,1-3"), (7, 1, 2, 3))

    def test_reversed_range_is_rejected_with_the_repair_spelled_out(self):
        with self.assertRaises(argparse.ArgumentTypeError) as caught:
            mutation_verify._parse_index_list("30-27")
        message = str(caught.exception)
        self.assertIn("reversed", message)
        self.assertIn("27-30", message)

    def test_malformed_range_falls_through_to_the_integer_error(self):
        for bad in ("1-", "-1-2", "1-2-3"):
            with self.subTest(value=bad):
                with self.assertRaises(argparse.ArgumentTypeError):
                    mutation_verify._parse_index_list(bad)

    def test_range_bounds_are_checked_after_expansion_not_during_parsing(self):
        """``0-2`` parses; the bounds check rejects it, same as a bare ``0``.

        Keeping the two layers apart is what makes ``--index 0`` and
        ``--index 0-2`` fail identically instead of growing two dialects of the
        same complaint.
        """
        self.assertEqual(mutation_verify._parse_index_list("0-2"), (0, 1, 2))
        with self.assertRaises(SystemExit) as caught:
            mutation_verify._select_verify_targets(["voided-trade"], (0, 1, 2))
        self.assertIn("0", str(caught.exception))


class ApplyPreflightTests(unittest.TestCase):
    """``apply`` must not write anything until the whole selection checks out.

    A partial apply -- first file rewritten, second needle missing -- leaves a
    tree that is neither the original nor the intended state, and with several
    positions in play that state is hard to reason about. The checks are a pure
    function so they can be driven without touching the working tree.
    """

    def _targets(self, key, *indexes):
        return mutation_verify._select_verify_targets([key], tuple(indexes))

    def test_an_ungrouped_selection_is_accepted(self):
        self.assertEqual(
            mutation_verify._apply_preflight(self._targets("voided-trade", 1, 2)), []
        )

    def test_naming_both_members_of_a_group_is_a_problem(self):
        """Two typed positions in one group is a mistake, not a sweep to trim.

        Unlike a whole-set apply, nothing here is a convenience request -- so the
        pair is rejected rather than reduced to its first member.
        """
        problems = mutation_verify._apply_preflight(self._targets("review-queue", 5, 6))
        self.assertEqual(len(problems), 1)
        self.assertIn("cli-flag-guard", problems[0])
        self.assertIn("5, 6", problems[0])

    def test_naming_one_member_of_a_group_is_fine(self):
        for position in (5, 6):
            with self.subTest(position=position):
                self.assertEqual(
                    mutation_verify._apply_preflight(
                        self._targets("review-queue", position)
                    ),
                    [],
                )

    def test_a_needle_that_no_longer_matches_is_a_problem(self):
        mutation_set = mutation_verify.SETS_BY_KEY["voided-trade"]
        stale = dataclasses.replace(
            mutation_set.mutations[0], old=b"this needle occurs nowhere"
        )
        problems = mutation_verify._apply_preflight([(mutation_set, 1, stale)])
        self.assertEqual(len(problems), 1)
        self.assertIn("appears 0x", problems[0])

    def test_a_missing_file_is_a_problem(self):
        mutation_set = mutation_verify.SETS_BY_KEY["voided-trade"]
        gone = dataclasses.replace(
            mutation_set.mutations[0], path="backend/app/memory/no_such_file.py"
        )
        problems = mutation_verify._apply_preflight([(mutation_set, 1, gone)])
        self.assertEqual(len(problems), 1)
        self.assertIn("missing file", problems[0])

    def test_problems_do_not_stop_at_the_first_one(self):
        """Every bad needle is reported, not just the first.

        One at a time would cost a round trip per mistake to fix.
        """
        mutation_set = mutation_verify.SETS_BY_KEY["voided-trade"]
        bad = [
            dataclasses.replace(mutation, old=b"absent needle %d" % offset)
            for offset, mutation in enumerate(mutation_set.mutations[:3], 1)
        ]
        problems = mutation_verify._apply_preflight(
            [(mutation_set, position, mutation) for position, mutation in enumerate(bad, 1)]
        )
        self.assertEqual(len(problems), 3)

    def test_a_needle_that_matches_more_than_once_is_a_problem(self):
        """Not just "does it occur" -- the harness needs exactly one occurrence.

        ``apply`` rewrites the first match only, so a leftover second occurrence
        would leave the file unlike anything the inventory describes. An empty
        needle is the cheapest way to reach that branch: it matches at every
        position without depending on what a particular file happens to contain.
        """
        mutation_set = mutation_verify.SETS_BY_KEY["voided-trade"]
        everywhere = dataclasses.replace(mutation_set.mutations[0], old=b"")
        problems = mutation_verify._apply_preflight([(mutation_set, 1, everywhere)])
        self.assertEqual(len(problems), 1)
        self.assertIn("expected 1", problems[0])
        self.assertNotIn("appears 0x", problems[0])


class WholeSetApplyTests(unittest.TestCase):
    """Which mutations a whole-set ``apply`` skips, and why.

    This is the one place a selection is silently reduced, so it is asserted here
    rather than only being visible in a run's stdout.
    """

    def test_first_member_of_each_group_is_applied_and_the_rest_are_skipped(self):
        mutation_set = mutation_verify.SETS_BY_KEY["review-queue"]
        targets, skipped = mutation_verify._whole_set_targets(mutation_set)
        self.assertEqual([position for _, position, _ in targets], [1, 2, 3, 4, 5])
        self.assertEqual([position for position, _ in skipped], [6])

    def test_a_set_without_groups_applies_everything(self):
        mutation_set = mutation_verify.SETS_BY_KEY["voided-trade"]
        targets, skipped = mutation_verify._whole_set_targets(mutation_set)
        self.assertEqual(len(targets), len(mutation_set.mutations))
        self.assertEqual(skipped, [])

    def test_the_whole_set_selection_passes_preflight(self):
        """The whole-set path must survive its own preflight.

        If it did not, ``apply <set>`` would be dead in exactly the case it
        exists for. Also pins that no group's members are accidentally both kept.
        """
        for mutation_set in mutation_verify.SETS:
            with self.subTest(set=mutation_set.key):
                targets, _ = mutation_verify._whole_set_targets(mutation_set)
                self.assertEqual(mutation_verify._apply_preflight(targets), [])


if __name__ == "__main__":
    unittest.main()
