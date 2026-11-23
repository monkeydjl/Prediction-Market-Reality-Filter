#!/usr/bin/env python
"""Guard the lint gate's own scan set.

`ruff check .` covers whatever it is pointed at, so an `extend-exclude` entry
silently narrows it: the gate stays green while a whole directory stops being
linted. That failure is invisible by construction -- exit code zero is exactly
what success looks like -- and it already nearly happened here, when the two
gitignored scratch trees (`backend/.workbench/`, `backend/backend/`) had to be
excluded so local runs would agree with CI.

The property this pins is an equality, not a threshold:

    linted .py files == tracked .py files

Both directions matter and they fail differently:

  * A file tracked but not linted is a blind spot. The gate passes over code
    that exists in the repository.
  * A file linted but not tracked is the "red locally, green in CI" split --
    the developer sees a finding nobody else will, and learns to ignore it.

No absolute counts on purpose. A number like "845 files" needs updating every
time an unrelated file is added, so it gets lowered instead of investigated;
the point of the guard is to be maintained for free. Config files that ruff
lists (e.g. `ruff.toml`) are ignored: only `.py` is compared, because the
question is which *code* the gate sees.

Runs as a check (exit 1 on mismatch) and is importable for unit tests.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
from collections.abc import Iterable

# A real mismatch lists every file; a message that prints 844 lines is
# unreadable, and the count plus the first few is enough to locate the cause.
_SHOWN = 8


def _to_abs(paths: Iterable[str], root: pathlib.Path) -> set[pathlib.Path]:
    """Turn whatever ruff or git printed into one comparable absolute form.

    ruff prints absolute paths, `git ls-files` prints paths relative to the
    working directory, and Windows mixes separators. Comparing without this
    step produces two sets that never intersect and a guard that always fails.
    """
    out: set[pathlib.Path] = set()
    for raw in paths:
        text = raw.strip()
        if not text:
            continue
        candidate = pathlib.Path(text)
        if not candidate.is_absolute():
            candidate = root / candidate
        out.add(candidate.resolve())
    return out


def linted_python(root: pathlib.Path) -> set[pathlib.Path]:
    """Every `.py` file `ruff check .` would visit from `root`."""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", ".", "--show-files"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Measured: `--show-files` exits 0 both when the tree is clean and when
        # it has findings -- listing files is not the check. So a non-zero here
        # means ruff failed to run at all, and an empty set would otherwise be
        # mistaken for "nothing to lint".
        raise RuntimeError(f"could not list linted files: {result.stderr.strip()}")
    return {p for p in _to_abs(result.stdout.splitlines(), root) if p.suffix == ".py"}


def tracked_python(root: pathlib.Path) -> set[pathlib.Path]:
    """Every `.py` file git tracks under `root`."""
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"could not list tracked files: {result.stderr.strip()}")
    return {p for p in _to_abs(result.stdout.split("\0"), root) if p.suffix == ".py"}


def _listing(paths: Iterable[pathlib.Path], root: pathlib.Path) -> str:
    # `as_posix`, not `str`: on Windows the relative form is `app\main.py`,
    # which would make the same message look different from CI's and would
    # defeat any assertion written against the forward-slash form.
    names = sorted(p.relative_to(root).as_posix() for p in paths)
    lines = [f"    {name}" for name in names[:_SHOWN]]
    extra = len(names) - len(lines)
    if extra > 0:
        lines.append(f"    ... and {extra} more")
    return "\n".join(lines)


def compare(
    linted: set[pathlib.Path], tracked: set[pathlib.Path], root: pathlib.Path
) -> list[str]:
    """Return one message per violated direction; empty means the gate is honest."""
    problems: list[str] = []

    blind = tracked - linted
    if blind:
        problems.append(
            f"{len(blind)} tracked .py file(s) are NOT linted -- the gate passes over "
            f"code that exists in the repository:\n{_listing(blind, root)}\n"
            f"  Look for an `extend-exclude` entry in ruff.toml that is wider than "
            f"intended, or a pathspec typo in the CI command."
        )

    stray = linted - tracked
    if stray:
        problems.append(
            f"{len(stray)} linted .py file(s) are NOT tracked by git -- local and CI "
            f"disagree, and the local red is a finding nobody else can see:\n"
            f"{_listing(stray, root)}\n"
            f"  Untracked scratch that gets linted will teach people to ignore the "
            f"gate. Remove it, or exclude its directory in ruff.toml."
        )

    return problems


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    try:
        linted = linted_python(root)
        tracked = tracked_python(root)
    except RuntimeError as exc:
        # Not a traceback: both failures here mean the guard could not establish
        # what to compare (ruff missing, or no git checkout), and a stack trace
        # reads as a bug in the guard rather than a missing precondition.
        print(f"[FAIL] cannot establish the scan set: {exc}")
        return 2
    problems = compare(linted, tracked, root)
    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}")
        return 1
    print(f"[OK] lint scan set matches git: {len(linted)} .py files, both directions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
