"""Tests for the evidence reader (`scripts/verdict.py`).

The script exists because two exit codes in this environment lie: pytest's rc is
pinned to 1 by the sandbox's delete guard, and `gh` reports success for empty
output. So the tests are about the *distinctions* the script draws -- pass vs
"nothing measured", and ran vs "printed the wrong thing" -- rather than about
XML or log syntax.
"""

from __future__ import annotations

import io
import pathlib
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from scripts import verdict

SUITE_OK = """<testsuites>
  <testsuite tests="3" failures="0" errors="0" skipped="1">
    <testcase classname="a" name="one"/>
    <testcase classname="a" name="two"/>
  </testsuite>
</testsuites>"""

SUITE_FAIL = """<testsuites>
  <testsuite tests="2" failures="1" errors="0" skipped="0">
    <testcase classname="a" name="good"/>
    <testcase classname="a" name="bad"><failure>boom</failure></testcase>
  </testsuite>
</testsuites>"""


def _judge(xml: str) -> tuple[list[str], list[str], dict[str, int]]:
    return verdict.judge_report(ET.fromstring(xml))


class TestJunitVerdict(unittest.TestCase):
    def test_clean_report_passes(self) -> None:
        problems, _notes, counters = _judge(SUITE_OK)
        self.assertEqual(problems, [])
        self.assertEqual(counters["tests"], 3)

    def test_failure_is_named(self) -> None:
        problems, _notes, _counters = _judge(SUITE_FAIL)
        self.assertEqual(len(problems), 1)
        # Named, because the summary line a human would read is exactly what
        # gets swallowed in this environment.
        self.assertIn("a::bad", problems[0])

    def test_zero_collected_tests_is_not_a_pass(self) -> None:
        # §5 of the project conventions: a 0 must say one thing. "No tests ran"
        # and "no tests failed" are different claims.
        xml = (
            '<testsuites><testsuite tests="0" failures="0" errors="0" skipped="0"/>'
            "</testsuites>"
        )
        problems, _notes, _counters = _judge(xml)
        self.assertEqual(len(problems), 1)
        self.assertIn("collected nothing", problems[0])

    def test_missing_counter_is_unreadable_not_zero(self) -> None:
        # Defaulting a missing `errors` attribute to 0 is the empty assertion
        # this script exists to prevent.
        xml = (
            '<testsuites><testsuite tests="1" failures="0" skipped="0">'
            '<testcase classname="a" name="b"/></testsuite></testsuites>'
        )
        with self.assertRaises(verdict.Unreadable) as ctx:
            _judge(xml)
        self.assertIn("errors", str(ctx.exception))

    def test_declared_failures_without_elements_is_inconsistent(self) -> None:
        # A report can claim failures without carrying them; trusting the
        # attribute alone would pass a red run.
        xml = (
            '<testsuites><testsuite tests="1" failures="1" errors="0" skipped="0">'
            '<testcase classname="a" name="b"/></testsuite></testsuites>'
        )
        problems, _notes, _counters = _judge(xml)
        self.assertTrue(any("no <failure>" in p for p in problems))

    def test_subtest_element_shortfall_is_a_note_not_a_problem(self) -> None:
        # pytest-subtests counts passes in `tests` without emitting a
        # <testcase>; treating that as data loss has cost time before.
        xml = (
            '<testsuites><testsuite tests="4" failures="0" errors="0" skipped="1">'
            '<testcase classname="a" name="one"/>'
            '<testcase classname="a" name="two"/></testsuite></testsuites>'
        )
        problems, notes, counters = _judge(xml)
        self.assertEqual(problems, [])
        self.assertEqual(counters["tests"], 4)
        self.assertTrue(any("subtest" in note for note in notes))

    def test_unknown_root_is_unreadable(self) -> None:
        with self.assertRaises(verdict.Unreadable):
            _judge("<report/>")

    def test_empty_file_is_unreadable(self) -> None:
        with tempfile.NamedTemporaryFile("wb", suffix=".xml", delete=False) as handle:
            path = pathlib.Path(handle.name)
        try:
            with self.assertRaises(verdict.Unreadable) as ctx:
                verdict.judge_junit(path)
            self.assertIn("0 bytes", str(ctx.exception))
        finally:
            path.unlink()

    def test_malformed_xml_is_unreadable(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as handle:
            handle.write("<testsuites>")
            path = pathlib.Path(handle.name)
        try:
            with self.assertRaises(verdict.Unreadable):
                verdict.judge_junit(path)
        finally:
            path.unlink()


# A verbatim-shaped excerpt: three tab fields, timestamp separated from content
# by a space, BOM before the first timestamp of a step block.
LOG = (
    "Type Sync Check\tSet up job\t\ufeff2026-11-23T16:47:31.2697183Z Current runner version\n"
    "backend-tests\tLint backend\t2026-11-23T16:47:59.1044554Z ##[group]Run ruff check .\n"
    "backend-tests\tLint backend\t2026-11-23T16:47:59.1446261Z All checks passed!\n"
    "backend-tests\tVerify the lint gate's scan set\t2026-11-23T16:47:59.2471777Z \x1b[32m[OK]\x1b[0m matches\n"
)


class TestLogParsing(unittest.TestCase):
    def test_three_field_shape_is_parsed(self) -> None:
        rows = verdict.parse_log(LOG)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1][:2], ("backend-tests", "Lint backend"))
        self.assertEqual(rows[1][2], "##[group]Run ruff check .")

    def test_bom_before_the_timestamp_does_not_swallow_the_content(self) -> None:
        rows = verdict.parse_log(LOG)
        self.assertEqual(rows[0][2], "Current runner version")

    def test_ansi_is_stripped(self) -> None:
        rows = verdict.parse_log(LOG)
        self.assertEqual(rows[3][2], "[OK] matches")

    def test_tabs_inside_content_are_preserved(self) -> None:
        rows = verdict.parse_log("j\ts\t2026-11-23T00:00:00.0000000Z a\tb\tc\n")
        self.assertEqual(rows[0][2], "a\tb\tc")

    def test_non_log_text_is_unreadable(self) -> None:
        with self.assertRaises(verdict.Unreadable) as ctx:
            verdict.parse_log("hello\nworld\n")
        self.assertIn("piped", str(ctx.exception))


class TestLogScoping(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = verdict.parse_log(LOG)

    def test_step_that_never_ran_is_not_a_pass(self) -> None:
        problems, _notes, _counts = verdict.check_log(self.rows, "No Such Step", [], None)
        self.assertEqual(len(problems), 1)
        self.assertIn("never ran", problems[0])
        # The message lists what did run, so a renamed step is diagnosable.
        self.assertIn("Lint backend", problems[0])

    def test_scoping_does_not_leak_across_steps(self) -> None:
        problems, notes, counts = verdict.check_log(
            self.rows, "Lint backend", ["All checks passed"], None
        )
        self.assertEqual(problems, [])
        self.assertEqual(counts["All checks passed"], 1)
        self.assertEqual(notes, ["2 line(s) from step 'Lint backend'"])

    def test_missing_expectation_names_the_step(self) -> None:
        problems, _notes, counts = verdict.check_log(
            self.rows, "Lint backend", ["Findings:"], None
        )
        self.assertEqual(counts["Findings:"], 0)
        self.assertIn("Lint backend", problems[0])

    def test_job_filter_narrows(self) -> None:
        problems, _notes, _counts = verdict.check_log(
            self.rows, "Lint backend", [], "Type Sync Check"
        )
        self.assertIn("never ran", problems[0])


class TestCliExitCodes(unittest.TestCase):
    def _run(self, argv: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            code = verdict.main(argv)
        return code, out.getvalue()

    def test_unreadable_is_two(self) -> None:
        with tempfile.NamedTemporaryFile("wb", suffix=".xml", delete=False) as handle:
            path = handle.name
        try:
            code, text = self._run(["junit", path])
        finally:
            pathlib.Path(path).unlink()
        self.assertEqual(code, 2)
        self.assertIn("[UNREADABLE]", text)

    def test_contradiction_is_one(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as handle:
            handle.write(SUITE_FAIL)
            path = handle.name
        try:
            code, text = self._run(["junit", path])
        finally:
            pathlib.Path(path).unlink()
        self.assertEqual(code, 1)
        self.assertIn("[FAIL]", text)

    def test_verified_is_zero(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as handle:
            handle.write(SUITE_OK)
            path = handle.name
        try:
            code, text = self._run(["junit", path])
        finally:
            pathlib.Path(path).unlink()
        self.assertEqual(code, 0)
        self.assertIn("[OK]", text)


if __name__ == "__main__":
    unittest.main()
