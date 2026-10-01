"""The mutation inventory must be well-formed before a manual run finds out.

``scripts/mutation_verify.py`` only checks a needle when someone runs it, and a
full ``verify`` over every set takes minutes. Two failure modes it therefore
reports too late:

* a needle that no longer occurs in its file (the code it pointed at moved), and
* a needle that occurs more than once -- ``apply`` refuses it, but only after the
  run has already started.

Both are static properties of the inventory, so they are asserted here instead.
Line endings are pinned for the same reason, but as a property rather than a
spelling: this repo commits LF and checks out CRLF, so a needle containing a
line break has to match under *either* convention. Spelling CRLF out made that
true only on a developer machine, and the failure reads like "needle appears
0x" -- like a bug in the script rather than a rule that was only ever exercised
on one checkout.

The same reasoning covers ``verify --index``: which mutation a narrowed run will
reach is decidable without driving pytest, so ``VerifySelectionTests`` decides it
here rather than after twelve minutes of a manual run.
"""
import argparse
import dataclasses
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from scripts import mutation_verify

REPO_ROOT = Path(mutation_verify.REPO_ROOT)


def _every_mutation():
    for mutation_set in mutation_verify.SETS:
        for index, mutation in enumerate(mutation_set.mutations, 1):
            yield mutation_set.key, index, mutation


class InventoryTests(unittest.TestCase):
    def test_the_seven_sets_are_present(self):
        """Pins the inventory, so a whole set cannot be dropped silently.

        Deliberately exact and ordered: adding a set means editing this test,
        which is the point -- an inventory nobody has to touch is an inventory
        that can rot. Renamed from ``test_the_six_sets_are_present`` when
        ``multi-guard-file`` was added (audit section 43), the same way it was
        renamed from ``test_the_five_sets_are_present`` before.
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
                "multi-guard-file",
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
            found = mutation_verify.count_needle(path.read_bytes(), mutation.old)
            if found != 1:
                offenders.append(
                    f"{key} {index}: needle occurs {found}x in {mutation.path}"
                )
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_every_needle_matches_whichever_line_ending_the_checkout_uses(self):
        """The property the old bare-LF rule was really about.

        ``test_no_needle_carries_a_bare_lf`` required CRLF in every
        line-terminated needle, which encoded "the checkout is CRLF" -- true on a
        developer machine and false on CI, where that same rule left seven
        needles matching nothing. What has to hold is the property, not the
        spelling, so both conventions are rebuilt here from each file's own
        bytes. Recasting rather than reading the checkout is the point: this runs
        the *other* convention on whichever machine happens to run it.
        """
        offenders = []
        for key, index, mutation in _every_mutation():
            path = REPO_ROOT / mutation.path
            if not path.is_file():
                continue
            content = path.read_bytes()
            for eol in (b"\n", b"\r\n"):
                recast = content.replace(b"\r\n", b"\n").replace(b"\n", eol)
                if mutation_verify.count_needle(recast, mutation.old) != 1:
                    offenders.append(
                        f"{key} {index}: needle misses a checkout using {eol!r}"
                    )
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_every_guard_file_exists_and_every_guard_is_named(self):
        offenders = []
        for key, index, mutation in _every_mutation():
            guard_files = mutation.guard_files()
            # A mutation that names no file would run nothing while every phase
            # still passed: ``GuardRun(())`` is vacuously green, and
            # ``every_file_red`` is only False there because it checks for it.
            if not guard_files:
                offenders.append(f"{key} {index}: names no guard file")
            for guard_file in guard_files:
                if not (mutation_verify.BACKEND / guard_file).is_file():
                    offenders.append(f"{key} {index}: missing {guard_file}")
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


class NeedleLineEndingTests(unittest.TestCase):
    """A needle's line ending must not decide whether it matches.

    The inventory is written for one convention and CI checks out the other, so
    the check has to hold on both. These pin the three properties that make the
    difference harmless: both spellings are tried, the count stays byte-exact
    (tolerance for the line break is not tolerance for the code), and a rewrite
    inherits the *file's* convention rather than the replacement's -- without
    that last one, a mutation that deletes a line could leave a file mixed, which
    neither a diff nor the sha256 restore check would show.
    """

    def test_a_single_line_needle_has_one_spelling(self):
        self.assertEqual(mutation_verify._needle_variants(b"abc"), (b"abc",))

    def test_a_line_terminated_needle_has_both_spellings(self):
        self.assertEqual(
            mutation_verify._needle_variants(b"a\r\nb"), (b"a\nb", b"a\r\nb")
        )

    def test_a_crlf_needle_counts_once_in_a_lf_checkout(self):
        """The exact shape that made seven needles miss on CI."""
        content = b"alpha\nbeta\ngamma\n"
        self.assertEqual(
            mutation_verify.count_needle(content, b"alpha\r\nbeta\r\n"), 1
        )

    def test_a_lf_needle_counts_once_in_a_crlf_checkout(self):
        content = b"alpha\r\nbeta\r\ngamma\r\n"
        self.assertEqual(mutation_verify.count_needle(content, b"alpha\nbeta\n"), 1)

    def test_a_needle_that_is_really_absent_still_counts_zero(self):
        """Line-ending tolerance must not decay into substring tolerance."""
        content = b"alpha\nbeta\ngamma\n"
        self.assertEqual(
            mutation_verify.count_needle(content, b"alpha\r\nDELTA\r\n"), 0
        )

    def test_a_crlf_files_rewrite_stays_pure_crlf(self):
        content = b"one\r\ntwo\r\nthree\r\n"
        after = mutation_verify.rewrite_needle(content, b"two\r\n", b"")
        self.assertEqual(after, b"one\r\nthree\r\n")
        self.assertEqual(after.count(b"\r\n"), 2)
        self.assertEqual(after.count(b"\n") - after.count(b"\r\n"), 0)

    def test_a_lf_file_stays_pure_lf_even_through_a_crlf_needle(self):
        content = b"one\ntwo\nthree\n"
        after = mutation_verify.rewrite_needle(content, b"two\r\n", b"")
        self.assertEqual(after, b"one\nthree\n")
        self.assertEqual(after.count(b"\r\n"), 0)

    def test_a_replacement_inherits_the_files_line_endings(self):
        """``old`` spells LF and ``new`` spells CRLF; the file must not gain one."""
        content = b"one\ntwo\n"
        after = mutation_verify.rewrite_needle(content, b"two\n", b"TWO\r\nEXTRA\r\n")
        self.assertEqual(after, b"one\nTWO\nEXTRA\n")

    def test_a_single_line_needle_falls_back_to_the_files_convention(self):
        """With no line break in the needle, the *file* decides the convention.

        This is the other half of ``_eol_of``'s call sites: without it the rule
        is only exercised when the needle itself carries a break, and a
        replacement that inserts one would inherit whatever ``new`` spelled --
        exactly the mixed-file outcome the inheritance rule exists to prevent.
        """
        content = b"one\ntwo\n"
        after = mutation_verify.rewrite_needle(content, b"two", b"two\r\nthree")
        self.assertEqual(after, b"one\ntwo\nthree\n")
        self.assertEqual(after.count(b"\r\n"), 0)

    def test_a_needle_that_is_absent_is_reported_rather_than_ignored(self):
        with self.assertRaises(ValueError):
            mutation_verify.rewrite_needle(b"one\ntwo\n", b"nope", b"x")


class InterpreterResolutionTests(unittest.TestCase):
    """Which interpreter the guard subprocesses run under.

    ``_run_guards`` refuses to start when that interpreter is missing, so the
    resolver has to work on a checkout with no ``.venv`` at all: CI installs the
    dependencies into the runner's own interpreter and checks the project out
    bare. The fallback branch is therefore unreachable on a developer machine --
    ``.venv`` is right there -- so these tests patch ``is_file`` for the two
    candidate paths rather than leaving CI as the only place that covers it.
    Both layouts are named because the venv's shape depends on how the checkout
    was provisioned, not only on the platform.
    """

    WINDOWS = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    POSIX = REPO_ROOT / ".venv" / "bin" / "python"

    @staticmethod
    def _present(*paths):
        """An ``is_file`` that answers only for ``paths`` and denies the rest."""
        allowed = {Path(path) for path in paths}
        return lambda self: self in allowed

    def test_the_windows_layout_wins_when_it_exists(self):
        with mock.patch.object(Path, "is_file", self._present(self.WINDOWS)):
            self.assertEqual(mutation_verify._resolve_interpreter(), self.WINDOWS)

    def test_the_posix_layout_is_accepted_too(self):
        with mock.patch.object(Path, "is_file", self._present(self.POSIX)):
            self.assertEqual(mutation_verify._resolve_interpreter(), self.POSIX)

    def test_no_venv_falls_back_to_the_running_interpreter(self):
        """The property that matters is the environment, not the file's path.

        ``sys.executable`` is patched because on a developer machine it *is* the
        venv path -- without that, the fallback and a hit would be the same
        string and the assertion would hold even if the fallback were never
        reached.
        """
        with mock.patch.object(Path, "is_file", self._present()), mock.patch.object(
            mutation_verify.sys, "executable", "/ci/toolcache/python"
        ):
            self.assertEqual(
                mutation_verify._resolve_interpreter(), Path("/ci/toolcache/python")
            )

    def test_the_resolved_interpreter_is_what_the_subprocess_runs(self):
        """A resolution that never reaches the argv would fix nothing."""
        mutation = mutation_verify.SETS_BY_KEY["voided-trade"].mutations[0]
        interpreter = Path("/ci/toolcache/python")
        with mock.patch.object(
            Path, "is_file", self._present(interpreter)
        ), mock.patch.object(
            mutation_verify, "PY", interpreter
        ), mock.patch.object(
            mutation_verify.subprocess, "run"
        ) as runner:
            runner.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="1 passed", stderr=""
            )
            mutation_verify._run_guards(mutation)
        self.assertEqual(
            [call.args[0][0] for call in runner.call_args_list],
            [str(interpreter)] * len(mutation.guard_files()),
        )


class MultiGuardFileTests(unittest.TestCase):
    """A mutation may name several guard files, not only one.

    The single-file contract sufficed while every needle sat next to the code it
    changed. It stops sufficing the moment a needle targets a *shared*
    definition: the behaviour that must notice the change then lives in every
    module that consumes the definition, so anchoring the mutation to one file
    would silently drop the rest (audit section 42.4). ``guard_files()`` is the
    seam that normalises the two spellings and ``_run_guards`` is its only
    consumer -- asserted through the invocations it makes, because a version that
    ran only the first file still reports a plausible outcome for that one file,
    so the returned flags alone cannot tell the two apart.
    """

    def _mutation(self, guard_file):
        base = mutation_verify.SETS_BY_KEY["voided-trade"].mutations[0]
        return dataclasses.replace(base, guard_file=guard_file)

    def test_a_single_guard_file_normalises_to_a_one_tuple(self):
        self.assertEqual(
            self._mutation("tests/test_one.py").guard_files(),
            ("tests/test_one.py",),
        )

    def test_several_guard_files_keep_their_order(self):
        self.assertEqual(
            self._mutation(("tests/a.py", "tests/b.py")).guard_files(),
            ("tests/a.py", "tests/b.py"),
        )

    def test_run_guards_passes_every_named_file_to_pytest(self):
        """One pytest invocation per named file, and none of them dropped.

        Asserted through the invocations rather than through the returned flag: a
        version that stopped after the first file still reports a plausible
        outcome for the file it ran, so the flags alone cannot tell the two
        apart. ``-k`` still carries the selector, joined exactly as before.
        """
        mutation = self._mutation(("tests/a.py", "tests/b.py"))
        with mock.patch.object(mutation_verify.subprocess, "run") as runner:
            runner.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="2 passed", stderr=""
            )
            result = mutation_verify._run_guards(mutation)
        invocations = [call.args[0] for call in runner.call_args_list]
        self.assertEqual(len(invocations), 2)
        for argv, guard_file in zip(invocations, ("tests/a.py", "tests/b.py")):
            self.assertEqual(argv[argv.index("pytest") + 1], guard_file)
            self.assertEqual(argv[argv.index("-k") + 1], " or ".join(mutation.guards))
        self.assertTrue(result.all_green)

    def test_run_guards_keeps_one_record_per_file(self):
        """Each file gets its own record, in ``guard_files()`` order.

        The record is what phase 2 reads: a combined return code says "something
        went red", while ``per_file`` says *which* files did. Order is pinned
        because the receipt prints them in it.
        """
        mutation = self._mutation(("tests/a.py", "tests/b.py"))
        with mock.patch.object(mutation_verify.subprocess, "run") as runner:
            runner.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="1 failed\nFAILED tests/a.py::test_x",
                stderr="",
            )
            result = mutation_verify._run_guards(mutation)
        self.assertEqual(
            [record.guard_file for record in result.per_file],
            ["tests/a.py", "tests/b.py"],
        )
        self.assertFalse(result.all_green)
        self.assertTrue(result.every_file_red)
        self.assertEqual(result.tally(), "2/2 files red")
        # The tail is the last line of that file's own run, not of a combined one.
        self.assertEqual(result.per_file[0].tail, "FAILED tests/a.py::test_x")

    def test_a_single_file_run_records_its_pass_flag_and_tail(self):
        with mock.patch.object(mutation_verify.subprocess, "run") as runner:
            runner.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="3 passed", stderr=""
            )
            result = mutation_verify._run_guards(self._mutation("tests/one.py"))
        self.assertEqual(
            [record.guard_file for record in result.per_file], ["tests/one.py"]
        )
        self.assertTrue(result.per_file[0].passed)
        self.assertEqual(result.per_file[0].tail, "3 passed")
        self.assertEqual(result.tally(), "0/1 files red")


class GuardRunAggregateTests(unittest.TestCase):
    """The two questions a phase asks, and why they are not one question.

    ``all_green`` (phases 1 and 3) and ``every_file_red`` (phase 2) stop being
    each other's negation as soon as a mutation names several files, and that gap
    *is* the blind spot audit section 45.5 named: one file firing is enough to
    make a combined return code non-zero, so "at least one red" would let a guard
    that had gone vacuous hide behind its neighbours. H2 (set
    ``multi-guard-file``) asserts that weakening ``every_file_red`` turns this
    class red.
    """

    @staticmethod
    def _run(*outcomes):
        return mutation_verify.GuardRun(
            tuple(
                mutation_verify.FileGuardRun(f"tests/{name}.py", passed, tail)
                for name, passed, tail in outcomes
            )
        )

    def test_every_file_red_requires_every_file_not_just_one(self):
        mixed = self._run(("a", False, "1 failed"), ("b", True, "2 passed"))
        self.assertFalse(mixed.every_file_red)
        self.assertEqual([r.guard_file for r in mixed.red_files], ["tests/a.py"])
        self.assertEqual([r.guard_file for r in mixed.green_files], ["tests/b.py"])

        all_red = self._run(("a", False, "1 failed"), ("b", False, "1 failed"))
        self.assertTrue(all_red.every_file_red)
        self.assertFalse(all_red.all_green)

    def test_all_green_needs_every_file_to_pass(self):
        self.assertFalse(self._run(("a", True, "2 passed"), ("b", False, "1 failed")).all_green)
        self.assertTrue(self._run(("a", True, "2 passed"), ("b", True, "2 passed")).all_green)

    def test_an_empty_run_is_green_but_does_not_prove_every_file_red(self):
        """Nothing checked must not read as everything noticed.

        A mutation that named no guard file would otherwise pass every phase
        while running nothing, so ``every_file_red`` is False for an empty run.
        The inventory test below keeps the case unreachable for real mutations.
        """
        empty = mutation_verify.GuardRun(())
        self.assertTrue(empty.all_green)
        self.assertFalse(empty.every_file_red)

    def test_the_tally_counts_red_over_total(self):
        run = self._run(
            ("a", False, "1 failed"), ("b", True, "2 passed"), ("c", False, "1 failed")
        )
        self.assertEqual(run.tally(), "2/3 files red")


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
