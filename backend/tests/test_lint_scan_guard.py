"""Tests for the lint scan-set guard.

The guard exists because a narrowing `extend-exclude` leaves `ruff check .`
green: exit zero is what both success and blindness look like. So the tests
here are about the two directions being *reported*, not about ruff's output --
ruff is taken as given, the question is whether the comparison notices.
"""

from __future__ import annotations

import io
import pathlib
import subprocess
import unittest
from unittest import mock

from scripts.lint_scan_guard import _to_abs, compare, linted_python, tracked_python

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _paths(*names: str) -> set[pathlib.Path]:
    return {(ROOT / name).resolve() for name in names}


class TestCompare(unittest.TestCase):
    def test_equal_sets_are_silent(self) -> None:
        files = _paths("app/main.py", "tests/test_main.py")
        self.assertEqual(compare(files, files, ROOT), [])

    def test_tracked_but_not_linted_is_a_blind_spot(self) -> None:
        linted = _paths("app/main.py")
        tracked = _paths("app/main.py", "app/services/ledger.py")

        problems = compare(linted, tracked, ROOT)

        self.assertEqual(len(problems), 1)
        self.assertIn("1 tracked .py file(s) are NOT linted", problems[0])
        # The message must name the file: "some files" sends the reader back to
        # diffing two 845-line listings by hand.
        self.assertIn("app/services/ledger.py", problems[0])

    def test_linted_but_not_tracked_is_a_local_only_red(self) -> None:
        linted = _paths("app/main.py", "scripts/_probe.py")
        tracked = _paths("app/main.py")

        problems = compare(linted, tracked, ROOT)

        self.assertEqual(len(problems), 1)
        self.assertIn("1 linted .py file(s) are NOT tracked", problems[0])
        self.assertIn("scripts/_probe.py", problems[0])

    def test_both_directions_are_reported_not_just_the_first(self) -> None:
        # Returning early would hide the second direction, and the two have
        # different causes: one is a widened exclude, the other is scratch files.
        problems = compare(_paths("app/main.py"), _paths("app/ledger.py"), ROOT)
        self.assertEqual(len(problems), 2)

    def test_long_mismatch_is_truncated_but_still_counted(self) -> None:
        linted: set[pathlib.Path] = set()
        tracked = _paths(*[f"app/m{i:02d}.py" for i in range(20)])

        problems = compare(linted, tracked, ROOT)

        self.assertIn("20 tracked .py file(s)", problems[0])
        self.assertIn("... and 12 more", problems[0])


class TestNormalisation(unittest.TestCase):
    def test_relative_and_absolute_entries_land_in_one_form(self) -> None:
        # ruff prints absolute paths, `git ls-files` prints them relative to the
        # working directory. If these did not normalise, the two sets would
        # never intersect and the guard would fail on a perfectly good tree.
        mixed = _to_abs([str(ROOT / "app" / "main.py"), "app/main.py"], ROOT)
        self.assertEqual(len(mixed), 1)
        self.assertTrue(next(iter(mixed)).is_absolute())

    def test_blank_lines_are_dropped(self) -> None:
        self.assertEqual(_to_abs(["", "  "], ROOT), set())


class TestMain(unittest.TestCase):
    def test_missing_precondition_exits_with_its_own_code(self) -> None:
        # "could not establish what to compare" is not the same failure as
        # "the sets differ": the first needs the environment fixed, the second
        # needs the tree looked at. A shared exit code erases that difference,
        # and a traceback would read as a bug in the guard.
        from scripts import lint_scan_guard as guard

        with mock.patch.object(guard, "linted_python", side_effect=RuntimeError("boom")):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                code = guard.main()

        self.assertEqual(code, 2)
        self.assertIn("[FAIL] cannot establish the scan set", out.getvalue())
        self.assertIn("boom", out.getvalue())


class TestRealTree(unittest.TestCase):
    """End-to-end against this repository: the sets must already be equal."""

    @classmethod
    def setUpClass(cls) -> None:
        probe = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            raise unittest.SkipTest("not a git checkout")
        cls.linted = linted_python(ROOT)
        cls.tracked = tracked_python(ROOT)

    def test_both_sides_are_populated(self) -> None:
        # A guard comparing two empty sets passes and guards nothing.
        self.assertGreater(len(self.linted), 100)
        self.assertGreater(len(self.tracked), 100)

    def test_this_repository_matches(self) -> None:
        self.assertEqual(compare(self.linted, self.tracked, ROOT), [])

    def test_config_file_is_not_treated_as_code(self) -> None:
        # ruff lists `--show-files` entries for config too; counting those would
        # put a non-.py path on one side of an equality that is about .py.
        self.assertTrue((ROOT / "ruff.toml").exists())
        self.assertNotIn((ROOT / "ruff.toml").resolve(), self.linted)


if __name__ == "__main__":
    unittest.main()
