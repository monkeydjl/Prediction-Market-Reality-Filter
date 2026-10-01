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
every tracked file is checked, which catches damage in a file that happens to
have no other differences.

HEAD blobs come back in bulk
----------------------------
The HEAD side used to be read with one ``git show HEAD:<path>`` per file.  On
``--all`` that is one process per tracked file, and the runtime was process
startup, not I/O: several minutes on Windows, which is long enough that the
audit stops being run.  ``git cat-file --batch`` answers a whole chunk of names
in a single process, so the same walk finishes in seconds.  Only the paths the
batch protocol cannot carry -- ones with a newline in them, which would end the
request line early and shift every reply after it -- still go through
``git show``.
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

# Names per ``git cat-file --batch`` request.  Large enough that process spawns
# stop dominating, small enough that one reply is never the whole tree.
HEAD_BATCH_CHUNK = 512


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
    """One ``git show`` -- the fallback for a name the batch protocol cannot carry."""
    result = subprocess.run(
        ["git", "show", f"HEAD:{path}"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    return result.stdout if result.returncode == 0 else None


def _head_blobs(paths: list[str]) -> dict[str, bytes | None]:
    """HEAD blob bytes for every path, one git process per chunk.

    The reply is a stream, not a set of lines: for a name that resolves, git
    writes ``<oid> <type> <size>`` then exactly ``size`` bytes then a newline, and
    the blob itself contains newlines, so the cursor has to advance by the
    declared size.  A name that does not resolve comes back as
    ``<name> missing``, which is the "no blob at HEAD" case -- a file that is
    new, or one that left the index while a stale record still names it.
    Anything that is not a blob (a tree, a commit) is treated the same way, and
    is still consumed so the reads after it stay aligned.

    Names go out as ``os.fsencode`` bytes: git takes the request literally and
    does not apply ``core.quotepath`` to it, so the two non-ASCII tracked paths
    survive the round trip that ``-z`` buys for the other calls.
    """
    blobs: dict[str, bytes | None] = {}
    for start in range(0, len(paths), HEAD_BATCH_CHUNK):
        chunk = paths[start : start + HEAD_BATCH_CHUNK]
        batchable: list[str] = []
        for path in chunk:
            if "\n" in path or "\r" in path:
                blobs[path] = _head_bytes(path)
            else:
                batchable.append(path)
        if not batchable:
            continue

        request = b"".join(b"HEAD:" + os.fsencode(path) + b"\n" for path in batchable)
        output = subprocess.run(
            ("git", "cat-file", "--batch"),
            cwd=REPO_ROOT,
            input=request,
            capture_output=True,
            check=True,
        ).stdout

        cursor = 0
        for path in batchable:
            newline = output.find(b"\n", cursor)
            if newline < 0:
                raise RuntimeError(
                    f"git cat-file --batch stopped after {len(output)} bytes, "
                    f"before the reply for {path!r}"
                )
            header = output[cursor:newline]
            cursor = newline + 1
            fields = header.rsplit(b" ", 2)
            if len(fields) == 3 and fields[2].isdigit():
                size = int(fields[2])
                content = output[cursor : cursor + size]
                cursor += size + 1
                blobs[path] = content if fields[1] == b"blob" else None
            else:
                blobs[path] = None
    return blobs


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

    candidates = _candidates(args.all)
    # Every file needs its HEAD side anyway, so read them all up front: one bulk
    # read is what makes ``--all`` finish in seconds instead of minutes.
    heads = _head_blobs(candidates)

    for rel in candidates:
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        if rel in binary:
            skipped += 1
            print(f"{rel:64s} {SKIPPED_BINARY}")
            continue

        tree = path.read_bytes()
        head = heads[rel]
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
