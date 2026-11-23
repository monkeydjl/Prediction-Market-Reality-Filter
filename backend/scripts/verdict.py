#!/usr/bin/env python
"""Read a test run and a CI log as evidence, instead of reading an exit code.

Two things in this repository make exit codes unreliable, and both have already
produced a wrong conclusion at least once:

  * `pytest` run from this shell reports `RAW_EXIT=1` whenever the sandbox's
    delete guard blocks temporary-directory cleanup. That is not a test
    failure, and the summary line that would say so gets swallowed by the same
    guard. The only trustworthy signal is the JUnit XML's `failures`/`errors`
    attributes.
  * `gh run watch` and `gh run view --log` report success for empty output.
    A 0-byte log has been read as "no findings" here before; the run had simply
    produced nothing yet.

So both subcommands separate "nothing went wrong" from "nothing was measured":

    junit  -- a run that collected zero tests is NOT a pass
    log    -- a step that never ran is NOT a step that passed

Exit codes: 0 verified, 1 the evidence contradicts a pass, 2 the evidence could
not be read at all (distinct on purpose: 1 means look at the tree, 2 means fix
the harness).
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# Measured against a real `gh run view --log` dump (4 484 lines): the piped
# format is `job<TAB>step<TAB><timestamp> <content>` -- three tab-separated
# fields, and the timestamp is separated from the content by a SPACE, not a
# tab. The first line of every step block carries a BOM before the timestamp.
# Assuming a fourth tab (the obvious reading of "tab-separated") yields three
# fields on 4 474 of 4 484 lines and silently matches nothing.
_LOG_LINE = re.compile(r"^\ufeff?(\d{4}-\d{2}-\d{2}T[\d:.]+Z)[ ]?(.*)$", re.S)


class Unreadable(Exception):
    """The evidence is missing or malformed: exit 2, not a verdict."""


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


# --------------------------------------------------------------------------- junit


def _suites(root: ET.Element) -> list[ET.Element]:
    if root.tag == "testsuite":
        return [root]
    if root.tag == "testsuites":
        suites = root.findall("testsuite")
        if not suites:
            raise Unreadable("<testsuites> contains no <testsuite>")
        return suites
    raise Unreadable(f"unexpected root element <{root.tag}>")


def _count(suites: list[ET.Element], name: str) -> int:
    """Sum one counter across suites, refusing to invent a zero.

    A missing attribute is not zero: an old or hand-made report without
    `errors` would otherwise read as "no errors", which is the exact assertion
    this script exists to avoid.
    """
    total = 0
    for suite in suites:
        if name not in suite.attrib:
            raise Unreadable(f"<testsuite> has no `{name}` attribute: cannot tell 0 from unknown")
        total += int(suite.get(name, "0"))
    return total


def judge_junit(path: pathlib.Path) -> tuple[list[str], list[str], dict[str, int]]:
    """Return (blocking problems, notes, counters)."""
    if not path.exists():
        raise Unreadable(f"no such file: {path}")
    if path.stat().st_size == 0:
        raise Unreadable(
            f"{path} is empty (0 bytes) -- an empty report is not a clean run; "
            f"re-run rather than reading it as a pass"
        )

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise Unreadable(f"not valid XML: {exc}") from exc

    return judge_report(root)


def judge_report(root: ET.Element) -> tuple[list[str], list[str], dict[str, int]]:
    """Judge an already-parsed report: pure, so the tests can pass literals."""
    suites = _suites(root)
    counters = {
        name: _count(suites, name) for name in ("tests", "failures", "errors", "skipped")
    }

    problems: list[str] = []
    notes: list[str] = []

    if counters["tests"] == 0:
        problems.append(
            "`tests=0`: the run collected nothing, which is not the same as passing"
        )

    elements = [case for suite in suites for case in suite.iter("testcase")]
    bad = [
        case
        for case in elements
        if case.find("failure") is not None or case.find("error") is not None
    ]
    for case in bad:
        problems.append(f"{case.get('classname', '?')}::{case.get('name', '?')}")

    if counters["failures"] or counters["errors"]:
        if not bad:
            problems.append(
                f"report declares failures={counters['failures']} errors={counters['errors']} "
                f"but contains no <failure>/<error> element"
            )
    else:
        declared = counters["tests"] - counters["skipped"]
        if len(elements) != declared:
            # pytest-subtests reports a passing subtest without emitting a
            # <testcase>, so the element count is legitimately lower. Recorded
            # because "elements != tests" has been misread as data loss before.
            notes.append(
                f"{len(elements)} <testcase> elements for tests={counters['tests']} "
                f"(skipped={counters['skipped']}); subtest passes do not emit elements"
            )

    return problems, notes, counters


# ----------------------------------------------------------------------------- log


def parse_log(text: str) -> list[tuple[str, str, str]]:
    """Split `gh run view --log` output into (job, step, content).

    See `_LOG_LINE` for the shape. A line that does not match is skipped, but a
    file where *nothing* matches is an error rather than an empty list: an
    unscoped empty list would make every `--expect` fail with "the step never
    ran", which is the wrong diagnosis for "this is not a gh log".
    """
    rows: list[tuple[str, str, str]] = []
    for line in strip_ansi(text).splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        job, step, rest = parts
        match = _LOG_LINE.match(rest)
        if match is None:
            continue
        rows.append((job.strip(), step.strip(), match.group(2)))
    if not rows:
        raise Unreadable(
            "no `job<TAB>step<TAB>timestamp content` lines -- this is not the piped "
            "log format (`gh run view --log > file`), so nothing can be scoped"
        )
    return rows


def check_log(
    rows: list[tuple[str, str, str]], step: str, expectations: list[str], job: str | None
) -> tuple[list[str], list[str], dict[str, int]]:
    scoped = [r for r in rows if r[1] == step and (job is None or r[0] == job)]
    problems: list[str] = []
    notes: list[str] = []
    counts: dict[str, int] = {}

    if not scoped:
        steps = sorted({r[1] for r in rows})
        problems.append(
            f"step {step!r} produced no lines -- it never ran, or its name changed. "
            f"Steps present: {', '.join(steps[:6])}"
            + (" ..." if len(steps) > 6 else "")
        )
        return problems, notes, counts

    notes.append(f"{len(scoped)} line(s) from step {step!r}")
    for needle in expectations:
        hits = sum(1 for _j, _s, content in scoped if needle in content)
        counts[needle] = hits
        if hits == 0:
            problems.append(
                f"{needle!r} never appears in step {step!r} -- the step ran but did "
                f"not print the line that proves what it did"
            )
    return problems, notes, counts


# ---------------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)

    p_junit = sub.add_parser("junit", help="judge a pytest --junitxml report")
    p_junit.add_argument("path", type=pathlib.Path)

    p_log = sub.add_parser("log", help="check that a CI step ran and said what it should")
    p_log.add_argument("path", type=pathlib.Path)
    p_log.add_argument("--step", required=True)
    p_log.add_argument("--job", default=None)
    p_log.add_argument("--expect", action="append", default=[], metavar="TEXT")

    args = parser.parse_args(argv)

    try:
        if args.mode == "junit":
            problems, notes, counters = judge_junit(args.path)
            summary = " ".join(f"{k}={v}" for k, v in counters.items())
        else:
            rows = parse_log(args.path.read_text(encoding="utf-8", errors="replace"))
            problems, notes, counts = check_log(rows, args.step, args.expect, args.job)
            summary = " ".join(f"{needle!r}x{n}" for needle, n in counts.items())
    except Unreadable as exc:
        print(f"[UNREADABLE] {exc}")
        return 2
    except OSError as exc:
        print(f"[UNREADABLE] {exc}")
        return 2

    for note in notes:
        print(f"  note: {note}")
    if summary:
        print(f"  {summary}")

    if problems:
        print(f"[FAIL] {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("[OK] evidence matches a pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
