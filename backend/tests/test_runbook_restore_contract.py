"""The RUNBOOK is the only thing an operator reads during a restore.

Measured 2026-08-29, before this file existed: `restore_stores.py` appeared in
tests, `config.py`, `backup_stores.py`, the CHANGELOG and the backlog — and **zero
times in `docs/ops/RUNBOOK.md`**. The only restore procedure the RUNBOOK
documented was a raw `pyzipper ... extractall(...)`, and only on the encrypted
branch; an unencrypted archive had no restore instructions at all.

That raw path forfeits seven mechanisms the script already had: the
`.pre_restore_<stamp>/` rollback snapshot, `_validate_within_runtime_root`,
`_target_path_for_arcname` (which maps an archive member back to its configured
path — `extractall` flattens all eight stores into one directory), the
service-running check, the dry-run preview, per-entry sha256, and the warning
list. This is the reachability defect class applied to documentation: the
capability existed and nothing routed to it.

No other test in this repo asserts anything about a document, so this file is the
first of its kind. It is deliberately narrow — it pins the *decisions* that were
wrong, not the prose, so the RUNBOOK stays editable:

  1. the restore script is named at all
  2. the census is named on both sides of the restore
  3. no bare `extractall` is handed to an operator
  4. the commands it prints are ones that actually exist
"""
import io
import re
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNBOOK = _REPO_ROOT / "docs" / "ops" / "RUNBOOK.md"


def _runbook() -> str:
    return io.open(_RUNBOOK, encoding="utf-8").read()


def _restore_section(text: str) -> str:
    """Just the restore procedure, so ordering assertions cannot match elsewhere.

    Raises rather than returning the whole document if the heading is gone: a
    fallback to `text` would make every assertion below weaker without saying so.
    """
    start = text.find("### Restoring from a backup")
    if start < 0:
        raise AssertionError(
            "the 'Restoring from a backup' section is gone from the RUNBOOK"
        )
    nxt = text.find("\n## ", start)
    return text[start:nxt] if nxt > 0 else text[start:]


def _restore_invocations(section: str) -> list[str]:
    """The actual `scripts.restore_stores` commands, prose mentions excluded.

    Ordering has to be judged on invocations. The sentence "Without `--apply`
    nothing is written" legitimately precedes the first command, so comparing
    the offsets of the words `--apply` and `scripts.restore_stores` reports a
    violation that is not one — the second thing this test got wrong.
    """
    commands: list[str] = []
    for block in re.findall(r"```bash\n(.*?)```", section, re.DOTALL):
        joined = block.replace("\\\n", " ")
        for line in joined.split("\n"):
            if "scripts.restore_stores" in line:
                commands.append(" ".join(line.split()))
    return commands


class RunbookExistsTests(unittest.TestCase):
    """Guard the instrument: every assertion below is vacuous on an empty read."""

    def test_the_runbook_is_present_and_substantial(self):
        self.assertTrue(_RUNBOOK.exists(), f"{_RUNBOOK} not found; the path is wrong")
        text = _runbook()
        self.assertGreater(
            len(text), 10_000,
            "the RUNBOOK is suspiciously short; every other test here would pass "
            "against a truncated file",
        )
        self.assertIn("## Backups", text, "the Backups section is gone")


class RestoreProcedureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = _runbook()

    def test_the_restore_script_is_named(self):
        """The defect this file exists for: it was mentioned nowhere."""
        self.assertIn(
            "scripts.restore_stores", self.text,
            "the RUNBOOK does not tell an operator how to restore; "
            "restore_stores.py exists and nothing routes to it",
        )

    def test_no_bare_extractall_is_offered_to_an_operator(self):
        """A hand-unzip bypasses the rollback snapshot and the path mapping."""
        self.assertNotIn(
            "extractall(", self.text,
            "the RUNBOOK hands out a raw extractall; that flattens eight stores "
            "into one directory and skips every safety mechanism",
        )

    def test_the_restore_is_previewed_before_it_is_applied(self):
        """Scoped to the restore section on purpose.

        A global `text.index("--apply")` matches `migrate_event_ids.py --apply`,
        which appears earlier and has nothing to do with restores — the first
        version of this test failed for that reason, not because the RUNBOOK was
        wrong.
        """
        invocations = _restore_invocations(_restore_section(self.text))
        self.assertGreaterEqual(
            len(invocations), 2,
            "expected a preview invocation and an --apply invocation, found: "
            f"{invocations}",
        )
        self.assertNotIn(
            "--apply", invocations[0],
            "the first documented restore command writes; the preview must come first",
        )
        self.assertTrue(
            any("--apply" in cmd for cmd in invocations[1:]),
            "no --apply invocation is documented, so the restore is never performed",
        )

    def test_the_rollback_directory_is_named(self):
        self.assertIn(
            ".pre_restore_", self.text,
            "an operator who restored the wrong archive needs to be told what the "
            "undo is",
        )

    def test_the_service_is_stopped_before_a_restore(self):
        self.assertIn("systemctl stop prediction-market-reality-filter", self.text)

    def test_the_encrypted_path_uses_the_script_flag(self):
        self.assertIn("--encryption-key", self.text)


class CountVerificationTests(unittest.TestCase):
    """A restore's success is not evidence the records came back."""

    def setUp(self) -> None:
        self.text = _runbook()

    def test_the_census_is_documented_on_both_sides_of_the_restore(self):
        section = _restore_section(self.text)
        self.assertIn("--save", section, "no before-census is documented")
        self.assertIn("--compare", section, "no after-census is documented")
        save = section.index("verify_store_counts --save")
        apply_at = section.index("--apply")
        compare = section.index("verify_store_counts --compare")
        self.assertLess(save, apply_at, "the before-census must precede the restore")
        self.assertLess(apply_at, compare, "the after-census must follow the restore")

    def test_the_partial_archive_hazard_is_stated(self):
        """The reason a checksum-clean restore can still be wrong.

        Whitespace-tolerant because Markdown wraps prose: the literal
        "four of the eight stores" is split across a line break in the source.
        """
        self.assertIn("2026-08-28", _restore_section(self.text))
        self.assertRegex(
            self.text,
            r"four\s+of\s+the\s+eight\s+stores",
            "the RUNBOOK must say why a clean restore can still be incomplete",
        )


class CommandsResolveTests(unittest.TestCase):
    """Every `python -m` the RUNBOOK prints must name a module that exists.

    A documented command for a module that was renamed is the same defect as no
    documentation, discovered at the worst possible moment.
    """

    def test_every_documented_module_exists(self):
        text = _runbook()
        modules = set(re.findall(r"python\s+-m\s+([\w.]+)", text))
        self.assertTrue(modules, "no `python -m` commands found; the regex is wrong")
        backend = _REPO_ROOT / "backend"
        for module in sorted(modules):
            with self.subTest(module=module):
                if module.startswith("scripts.") or module.startswith("app."):
                    target = backend / (module.replace(".", "/") + ".py")
                    self.assertTrue(
                        target.exists(), f"{module} is documented but {target} does not exist"
                    )
