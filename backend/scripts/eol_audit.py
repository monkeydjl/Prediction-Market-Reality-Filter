"""Report line-ending damage across the files git is asked to audit.

Why this exists
---------------
This repo commits LF (``core.autocrlf=true`` normalises on the way into the
object database) and checks the working tree out as CRLF. So "HEAD blob is LF,
tree file is CRLF" is the **normal** state, not damage -- and a rule written the
other way round reports the whole tree as broken.

The two states that ARE damage:

1. a file that was **pure CRLF at HEAD** and now has bare LFs -- what a text-mode
   ``Path.read_text()`` / ``write_text()`` round trip does, because
   ``read_text`` applies universal-newline translation and only the *write* side
   was ever guarded with ``newline=""``;
2. a file that is now mixed, which no checkout produces.

``git diff`` shows neither: ``core.autocrlf`` normalises before diffing, so a
whole-file line-ending rewrite still diffs as the handful of real changes.

Binary files are not profiled
-----------------------------
CR and LF bytes occur inside binaries for reasons that have nothing to do with
line endings, so reading them as text invents damage that is not there.  The
first version of this tool did exactly that: ``frontend/src/app/favicon.ico``
carries one CRLF and 29 bare LFs as padding, and was reported as "was pure CRLF
at HEAD, now has bare LF" while its HEAD blob and its worktree file were the
same 25931 bytes.  The exclusion is git's own verdict -- the ``i/-text`` token
from ``git ls-files --eol`` -- rather than a NUL-byte guess, because
``.gitattributes`` marks files binary *without* a NUL in them:
``backend/data/gbm_*.txt`` is CRLF text the LightGBM parser is sensitive to, and
is marked ``binary`` for exactly that reason.

Every git call that can return a path uses ``-z``.  Without it git quotes
non-ASCII paths as ``"docs/user/\\344\\270\\255..."``, which no longer resolves to
a file on disk, so the file is skipped in silence -- two tracked paths here are
affected.

Usage
-----
    cd backend && python scripts/eol_audit.py [--all]

Without ``--all`` only files git reports as changed are audited. With ``--all``
every tracked file is checked, which is slower but catches damage in a file that
happens to have no other differences.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

DAMAGE_HEAD_CRLF_TO_TREE_LF = "DAMAGE: was pure CRLF at HEAD, now has bare LF"
DAMAGE_MIXED = "DAMAGE: mixed line endings"
OK_LF_BOTH = "ok (LF both sides)"
OK_CRLF_CHECKOUT = "ok (LF -> CRLF checkout)"
NEW_FILE = "NEW"
SKIPPED_BINARY = "skip (git stores it as binary)"

DAMAGE_LABELS = frozenset({DAMAGE_HEAD_CRLF_TO_TREE_LF, DAMAGE_MIXED})

BINARY_TOKEN = "i/-text"


def _git_bytes(*args: str) -> bytes:
    return subprocess.run(
        ("git",) + args,
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout


def profile(data: bytes) -> tuple[int, int]:
    """Return ``(crlf_count, bare_lf_count)``."""
    crlf = data.count(b"\r\n")
    return crlf, data.count(b"\n") - crlf


def classify(head: bytes | None, tree: bytes) -> str:
    """State of one *text* file, given its HEAD blob and its worktree bytes.

    Pure and separate from the walk so both damage states -- and the near-miss
    that used to be mislabelled as one of them -- can be pinned directly.  The
    caller must exclude binaries first; see ``binary_paths``.

    The first rule needs ``head_lf == 0`` as well as ``head_crlf > 0``: its
    wording claims the file *was pure CRLF*, and a file that was already mixed
    at HEAD satisfies the weaker test while contradicting the sentence.  Such a
    file now falls through to the mixed rule, which is what it is.
    """
    tree_crlf, tree_lf = profile(tree)
    if head is None:
        return NEW_FILE
    head_crlf, head_lf = profile(head)
    if head_crlf > 0 and head_lf == 0 and tree_lf > 0:
        return DAMAGE_HEAD_CRLF_TO_TREE_LF
    if tree_crlf > 0 and tree_lf > 0:
        return DAMAGE_MIXED
    if head_crlf == 0 and tree_crlf == 0:
        return OK_LF_BOTH
    return OK_CRLF_CHECKOUT


def tracked_eol() -> list[tuple[str, str]]:
    """``(index eol token, path)`` for every tracked file, unquoted.

    One ``git ls-files --eol -z`` does double duty: ``--eol`` supplies the
    ``i/-text`` token that marks a file git stores as binary, and ``-z`` stops
    git quoting the paths that contain non-ASCII characters.
    """
    rows: list[tuple[str, str]] = []
    for record in _git_bytes("ls-files", "--eol", "-z").split(b"\0"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        tokens = meta.split()
        token = tokens[0].decode("ascii", "replace") if tokens else ""
        rows.append((token, os.fsdecode(path)))
    return rows


def binary_paths() -> frozenset[str]:
    """Paths git stores as binary, which are never profiled as text."""
    return frozenset(path for token, path in tracked_eol() if token == BINARY_TOKEN)


def changed_paths() -> list[str]:
    """Paths git reports as changed, unquoted.

    With ``-z`` a rename or a copy emits the destination first and the source as
    a second, status-less record; that record is consumed here rather than being
    read as a path whose first three characters belong to a status column.
    """
    records = _git_bytes("status", "--porcelain", "-z").split(b"\0")
    out: list[str] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4:
            continue
        status = record[:2].decode("ascii", "replace")
        if "R" in status or "C" in status:
            index += 1
        out.append(os.fsdecode(record[3:]))
    return out


def _head_bytes(path: str) -> bytes | None:
    result = subprocess.run(
        ["git", "show", f"HEAD:{path}"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    return result.stdout if result.returncode == 0 else None


def _candidates(include_all: bool) -> list[str]:
    if include_all:
        return [path for _, path in tracked_eol()]
    return changed_paths()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="audit every tracked file")
    args = parser.parse_args(argv)

    binary = binary_paths()
    damaged: list[str] = []
    skipped = 0

    for rel in _candidates(args.all):
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        if rel in binary:
            skipped += 1
            print(f"{rel:64s} {SKIPPED_BINARY}")
            continue

        tree = path.read_bytes()
        head = _head_bytes(rel)
        label = classify(head, tree)
        if label in DAMAGE_LABELS:
            damaged.append(rel)

        if head is None:
            tree_crlf, tree_lf = profile(tree)
            state = f"NEW  CRLF={tree_crlf} bareLF={tree_lf}"
        else:
            head_crlf, head_lf = profile(head)
            tree_crlf, tree_lf = profile(tree)
            state = (
                f"HEAD(crlf={head_crlf},lf={head_lf}) "
                f"TREE(crlf={tree_crlf},lf={tree_lf})  {label}"
            )
        print(f"{rel:64s} {state}")

    print()
    if damaged:
        print(f"LINE-ENDING DAMAGE in {len(damaged)} file(s):")
        for rel in damaged:
            print("  -", rel)
        return 1
    print("line-ending damage: none")
    if skipped:
        print(f"  ({skipped} file(s) skipped: git stores them as binary)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
