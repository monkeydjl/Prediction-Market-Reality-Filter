"""Report line-ending damage across every file git currently sees as changed.

Why this exists
---------------
This repo commits LF (``core.autocrlf=true`` normalises on the way into the
object database) and checks the working tree out as CRLF. So "HEAD blob is LF,
tree file is CRLF" is the **normal** state, not damage -- and a rule written the
other way round reports the whole tree as broken.

The two states that ARE damage:

1. a file that was pure CRLF at HEAD and now has bare LFs -- what a text-mode
   ``Path.read_text()`` / ``write_text()`` round trip does, because
   ``read_text`` applies universal-newline translation and only the *write* side
   was ever guarded with ``newline=""``;
2. a file that is now mixed, which no checkout produces.

``git diff`` shows neither: ``core.autocrlf`` normalises before diffing, so a
whole-file line-ending rewrite still diffs as the handful of real changes.

Usage
-----
    cd backend && python scripts/eol_audit.py [--all]

Without ``--all`` only files git reports as changed are audited. With ``--all``
every tracked file is checked, which is slower but catches damage in a file that
happens to have no other differences.
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def _git(*args: str) -> str:
    return subprocess.run(
        ("git",) + args,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def profile(data: bytes) -> tuple[int, int]:
    """Return ``(crlf_count, bare_lf_count)``."""
    crlf = data.count(b"\r\n")
    return crlf, data.count(b"\n") - crlf


def _head_bytes(path: str) -> bytes | None:
    result = subprocess.run(
        ["git", "show", f"HEAD:{path}"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    return result.stdout if result.returncode == 0 else None


def _candidates(include_all: bool) -> list[str]:
    if include_all:
        return [p for p in _git("ls-files").splitlines() if p]
    out: list[str] = []
    for line in _git("status", "--porcelain").splitlines():
        rel = line[3:].strip().strip('"')
        if rel:
            out.append(rel)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="audit every tracked file")
    args = parser.parse_args()

    damaged: list[str] = []
    for rel in _candidates(args.all):
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        tree_crlf, tree_lf = profile(path.read_bytes())
        head = _head_bytes(rel)

        if head is None:
            state = f"NEW  CRLF={tree_crlf} bareLF={tree_lf}"
        else:
            head_crlf, head_lf = profile(head)
            if head_crlf > 0 and tree_lf > 0:
                state = "DAMAGE: was pure CRLF at HEAD, now has bare LF"
                damaged.append(rel)
            elif tree_crlf > 0 and tree_lf > 0:
                state = "DAMAGE: mixed line endings"
                damaged.append(rel)
            elif head_crlf == 0 and tree_crlf == 0:
                state = "ok (LF both sides)"
            else:
                state = "ok (LF -> CRLF checkout)"
            state = (
                f"HEAD(crlf={head_crlf},lf={head_lf}) "
                f"TREE(crlf={tree_crlf},lf={tree_lf})  {state}"
            )
        print(f"{rel:64s} {state}")

    print()
    if damaged:
        print(f"LINE-ENDING DAMAGE in {len(damaged)} file(s):")
        for rel in damaged:
            print("  -", rel)
        return 1
    print("line-ending damage: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
