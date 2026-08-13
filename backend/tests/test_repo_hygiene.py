"""Repo-level hygiene guards that no unit test would otherwise catch.

Lives in the backend suite because that is the only Python test runner in the
repo; ``test_env_overlay_examples.py`` already reaches outside ``backend/`` the
same way.
"""
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


@unittest.skipUnless(
    shutil.which("git") and (REPO_ROOT / ".git").exists(),
    "needs a git checkout",
)
class TestNoTrackedFileIsIgnored(unittest.TestCase):
    """A path in .gitignore that is also tracked is a silent contradiction.

    .gitignore never untracks anything, so adding a rule for files already in
    the index does nothing: they keep being committed, and every later reader
    believes the rule took effect. ``docs/PIC/`` sat in exactly that state for
    ~6 weeks — 87 third-party screenshots the ignoring commit (0be68d8) said
    "should not be published" were published on every push.

    ``git ls-files -i -c --exclude-standard`` is the right primitive here:
    unlike ``check-ignore``, it does not report negation rules
    (``!.env.example``) as matches.
    """

    def test_no_tracked_file_matches_an_ignore_rule(self):
        result = _git("ls-files", "-i", "-c", "--exclude-standard")
        if result.returncode != 0:
            self.skipTest(f"git ls-files failed: {result.stderr.strip()}")
        offenders = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(
            offenders[:20],
            [],
            f"{len(offenders)} tracked file(s) match a .gitignore rule. Either "
            f"drop the rule or run `git rm --cached` on them — leaving both in "
            f"place means the rule is a no-op that reads as if it works.",
        )


if __name__ == "__main__":
    unittest.main()
