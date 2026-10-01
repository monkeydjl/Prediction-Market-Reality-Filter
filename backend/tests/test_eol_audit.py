"""Guard tests for ``scripts/eol_audit.py``.

The tool is the receipt for "the working tree's line endings are intact", so a
false red is not harmless: it teaches the next reader to distrust a green run.
It had one.  ``frontend/src/app/favicon.ico`` carries one CRLF and 29 bare LFs
as ICO padding, was profiled as text, and came back as "was pure CRLF at HEAD,
now has bare LF" while its HEAD blob and its worktree file were the same 25931
bytes -- a verdict the tool's own printed profile already contradicted.

Both halves of the fix are pinned here, and so are the two damage states the
tool exists to catch.  That second half is not decoration: without it, a version
that simply stopped reporting damage would satisfy every binary test below.

One pinned case is not about binaries.  The "was pure CRLF at HEAD" rule tested
only ``head_crlf > 0``, so a file that was already mixed at HEAD satisfied it and
was handed a sentence its own profile contradicted.  The rule now also requires
``head_lf == 0``, and such a file falls through to the mixed rule instead.
"""
from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_SCRIPTS_DIR = _BACKEND_DIR / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import eol_audit  # noqa: E402

_ICO = "frontend/src/app/favicon.ico"
_NON_ASCII = "docs/user/中文使用教程.md"


class TestClassify(unittest.TestCase):
    """The decision function, on the profiles that matter."""

    def test_a_new_file_is_reported_new(self):
        self.assertEqual(eol_audit.classify(None, b"a\nb\n"), eol_audit.NEW_FILE)

    def test_pure_crlf_at_head_then_bare_lf_is_damage(self):
        # The read_text()/write_text() round trip: universal newlines on the read
        # side, newline="" only on the write side, whole file converted at once.
        label = eol_audit.classify(b"a\r\nb\r\n", b"a\nb\n")
        self.assertEqual(label, eol_audit.DAMAGE_HEAD_CRLF_TO_TREE_LF)

    def test_a_head_that_was_already_mixed_is_not_called_pure_crlf(self):
        # Without the head_lf == 0 conjunct this returned "was pure CRLF at
        # HEAD" for a file whose own profile reads crlf=1 lf=1.
        label = eol_audit.classify(b"a\r\nb\n", b"a\r\nb\n")
        self.assertEqual(label, eol_audit.DAMAGE_MIXED)

    def test_a_mixed_worktree_is_damage(self):
        self.assertEqual(
            eol_audit.classify(b"a\nb\n", b"a\r\nb\n"), eol_audit.DAMAGE_MIXED
        )

    def test_a_crlf_checkout_is_not_damage(self):
        self.assertEqual(
            eol_audit.classify(b"a\nb\n", b"a\r\nb\r\n"), eol_audit.OK_CRLF_CHECKOUT
        )

    def test_lf_on_both_sides_is_not_damage(self):
        self.assertEqual(eol_audit.classify(b"a\nb\n", b"a\nb\n"), eol_audit.OK_LF_BOTH)

    def test_every_damage_label_is_registered(self):
        # main() counts damage by membership in DAMAGE_LABELS.  A label that
        # starts with "DAMAGE" but is missing from the set would be printed and
        # then not counted -- the one reading this tool must never produce.
        found = {
            value
            for name, value in vars(eol_audit).items()
            if name.isupper() and isinstance(value, str) and value.startswith("DAMAGE")
        }
        self.assertEqual(found, set(eol_audit.DAMAGE_LABELS))


class TestBinaryExclusion(unittest.TestCase):
    """Binary is git's call, not a NUL-byte guess."""

    def test_the_favicon_is_a_binary_path(self):
        self.assertIn(_ICO, eol_audit.binary_paths())

    def test_the_binary_set_is_the_index_text_token(self):
        # .gitattributes marks the ico, png, woff and pdf families binary, and
        # backend/data/gbm_*.txt is CRLF text marked binary because the LightGBM
        # parser is sensitive to it -- a case a NUL-byte heuristic would miss.
        tokens = {path: token for token, path in eol_audit.tracked_eol()}
        self.assertEqual(tokens[_ICO], eol_audit.BINARY_TOKEN)

    def test_tracked_paths_come_back_unquoted(self):
        # Without -z git renders this path as "docs/user/\344\270\255...", which
        # resolves to no file on disk and is then skipped in silence.
        paths = {path for _, path in eol_audit.tracked_eol()}
        self.assertIn(_NON_ASCII, paths)


class TestFullWalkOnASyntheticRepo(unittest.TestCase):
    """End to end: what the walk reports, and what it exits with.

    A throwaway repository, so the readings do not depend on whatever the real
    working tree happens to look like while the suite runs.

    ``* text=auto`` is deliberately absent.  With it, git would normalise the
    CRLF seed on ``add`` and there would be no way to build a file whose HEAD
    blob is pure CRLF.  The attribute this suite needs is the one that marks a
    file binary, and that one is present.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._git("init", "-q")
        # false, so the bytes committed are the bytes written.
        self._git("config", "core.autocrlf", "false")
        self._git("config", "user.name", "eol-audit-test")
        self._git("config", "user.email", "eol-audit-test@example.invalid")

        (self.root / ".gitattributes").write_bytes(b"*.ico binary\n")
        (self.root / "clean.txt").write_bytes(b"a\nb\n")
        # One CRLF and one bare LF, inside a file git stores as binary.
        (self.root / "marked.ico").write_bytes(b"\x00\x01\r\n\x02\n\x03")
        self._commit("seed")

    def _git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, check=True
        )

    def _commit(self, message):
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message)

    def _walk(self):
        buffer = io.StringIO()
        with patch.object(eol_audit, "REPO_ROOT", self.root):
            with contextlib.redirect_stdout(buffer):
                code = eol_audit.main(["--all"])
        return code, buffer.getvalue()

    def test_a_binary_file_is_skipped_and_the_walk_stays_green(self):
        code, out = self._walk()
        self.assertEqual(code, 0, out)
        self.assertIn("marked.ico", out)
        self.assertIn(eol_audit.SKIPPED_BINARY, out)

    def test_a_text_file_rewritten_from_crlf_to_lf_fails_the_walk(self):
        (self.root / "rewritten.txt").write_bytes(b"a\r\nb\r\n")
        self._commit("add a CRLF file")
        (self.root / "rewritten.txt").write_bytes(b"a\nb\n")

        code, out = self._walk()

        self.assertEqual(code, 1, out)
        self.assertIn(eol_audit.DAMAGE_HEAD_CRLF_TO_TREE_LF, out)
        # Exactly one: the binary file beside it is skipped, not counted.
        self.assertIn("LINE-ENDING DAMAGE in 1 file(s)", out)
        self.assertIn("  - rewritten.txt", out)


if __name__ == "__main__":
    unittest.main()
