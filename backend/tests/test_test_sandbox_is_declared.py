"""The test sandbox declares what it is, and the declaration stays true.

``tests/conftest.py`` creates a per-process temp directory and redirects every
file-path setting into it.  It never removes that directory -- deliberately,
because the sandboxed ledger inside it is cited as evidence by audit findings
(``docs/reviews/system-health-audit-2026-09-26.md`` sections 53.15 / 53.19 /
53.22).  So the directory is not a cache: it is a small, growing, un-declared
evidence repository.

What section 53.22 diagnosed was the *declaration*, not the retention.
"Should this be deleted?" had no answer inside the directory itself, which made
~3 MB/day of growth invisible and made cleanup a judgement call nobody had the
context to make.  conftest now writes a manifest into the sandbox, and this
module keeps that manifest from rotting into a comment.

*Why the assertions are about the manifest's machine-read block.*
The prose in the manifest is for a human and will drift; pinning its wording
would be a test that fails for the wrong reason and passes for the wrong reason
too.  The leading key/value block is the part a machine reads, so that is what
is pinned here -- and each field is checked against something *outside* the
manifest:

* ``sandbox_dir`` / ``evidence_ledger`` -- against the live settings and the
  live temp directory, so a manifest describing a sandbox that is no longer the
  sandbox goes red;
* ``pid`` -- against ``os.getpid()``, so a leftover manifest from an earlier
  session cannot satisfy this module (see ``test_the_manifest_is_from_this_session``);
* ``reclaim_glob`` -- against the directory it is written into, so a recipe that
  would not actually match these directories goes red.  A cleanup recipe that
  has silently stopped matching is worse than no recipe: it reads as permission.

The positive control in ``TestTheDeclarationIsAFunctionOfItsArgument`` keeps the
whole module from passing on a hardcoded string.
"""
import datetime
import os
import pathlib

from app.core.config import settings

from tests import conftest


def _manifest_path() -> pathlib.Path:
    return pathlib.Path(conftest._TEST_DATA_DIR) / conftest._SANDBOX_MANIFEST_NAME


def _manifest_text() -> str:
    return _manifest_path().read_text(encoding="utf-8")


def _field(text: str, name: str) -> str:
    """Value of a ``name : value`` line in the manifest's key/value block."""
    prefix = f"{name} : "
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    raise AssertionError(
        f"the manifest has no {name!r} line; its key/value block is the part "
        f"other tooling reads, so a missing field is a real regression.  "
        f"Manifest begins:\n{chr(10).join(text.splitlines()[:8])}"
    )


def _normalized(path) -> str:
    """Backslash-insensitive, case-insensitive form for containment checks."""
    return str(path).replace("\\", "/").lower()


class TestTheSandboxIsDeclared:
    def test_the_sandbox_holds_a_manifest(self):
        """The behaviour under test: conftest writes the declaration itself."""
        path = _manifest_path()
        assert path.is_file(), (
            f"{path} is missing -- conftest no longer declares the sandbox, so "
            f"{conftest._TEST_DATA_DIR!r} is once again an unlabelled evidence "
            "repository that a directory listing cannot explain"
        )

    def test_the_manifest_describes_this_sandbox(self):
        """Fields checked against the live settings and the live temp dir."""
        text = _manifest_text()

        declared_dir = _field(text, "sandbox_dir")
        assert _normalized(declared_dir) == _normalized(conftest._TEST_DATA_DIR), (
            f"the manifest says it is about {declared_dir!r} but lives in "
            f"{conftest._TEST_DATA_DIR!r}"
        )

        declared_ledger = _field(text, "evidence_ledger")
        assert _normalized(declared_ledger) == _normalized(settings.LOOP_DB_FILE), (
            f"the manifest names {declared_ledger!r} as the evidence artifact "
            f"but LOOP_DB_FILE is {settings.LOOP_DB_FILE!r}; the declaration has "
            "drifted away from the redirect it describes"
        )
        assert _normalized(conftest._TEST_DATA_DIR) in _normalized(declared_ledger), (
            f"{declared_ledger!r} is not inside the sandbox it is declared for -- "
            "the declared evidence is not the evidence this sandbox holds"
        )

    def test_the_manifest_is_from_this_session(self):
        """A stale manifest from an earlier run must not satisfy this module.

        The manifest is written at conftest import, in this very process, so the
        recorded pid is ``os.getpid()``.  Without this the whole module could
        pass against a file left behind months ago -- the sandbox outlives the
        process by design, so "the file exists" is not by itself evidence that
        anything still writes it.
        """
        text = _manifest_text()
        assert _field(text, "pid") == str(os.getpid()), (
            "the manifest was not written by this process; it is a leftover, and "
            "nothing in the current session re-declares the sandbox"
        )

        created = _field(text, "created_utc")
        try:
            stamp = datetime.datetime.fromisoformat(created)
        except ValueError as exc:  # pragma: no cover - message carries the value
            raise AssertionError(
                f"created_utc is {created!r}, which is not ISO-8601: {exc}"
            ) from exc
        assert stamp.tzinfo is not None, (
            f"created_utc is {created!r} with no offset; a timestamp that does "
            "not say which zone it is in cannot order these directories"
        )
        assert stamp.utcoffset() == datetime.timedelta(0), (
            f"created_utc is {created!r}; the recipe sorts by LastWriteTime, so "
            "the manifest should be recording UTC"
        )


class TestTheReclaimRecipeMatchesTheseDirectories:
    def test_the_declared_glob_matches_the_directory_it_is_written_into(self):
        """A recipe that no longer matches reads as permission to delete nothing.

        Worse, it reads as *having* been checked.  The glob is therefore pinned
        against the directory's own name rather than against a constant, so
        renaming the sandbox prefix without updating the recipe goes red here.
        """
        text = _manifest_text()
        glob = _field(text, "reclaim_glob")
        assert glob.endswith("*"), (
            f"reclaim_glob is {glob!r}; it names one directory rather than the "
            "family of them, so it cannot be the recipe"
        )

        prefix = glob[:-1]
        name = pathlib.Path(conftest._TEST_DATA_DIR).name
        assert name.startswith(prefix), (
            f"the manifest tells the reader to reclaim {glob!r} but this sandbox "
            f"is named {name!r}, which that glob does not match -- the recipe is "
            "stale and would silently delete nothing"
        )

    def test_the_manifest_points_at_the_findings_that_cite_it(self):
        """The declaration has to name the record that makes it an evidence repo.

        Without the pointer, "this is an evidence repository" is an assertion
        with nothing behind it.  Pinned as a concrete file name (and the section
        that created this manifest) rather than as prose.
        """
        text = _manifest_text()
        assert "docs/reviews/system-health-audit-2026-09-26.md" in text, (
            "the manifest does not name the audit document that cites the "
            "ledger it calls evidence"
        )
        assert "53.22" in text, (
            "the manifest does not name section 53.22 -- the section that "
            "diagnosed this and is therefore where a reader should start"
        )


class TestTheDeclarationIsAFunctionOfItsArgument:
    def test_a_synthetic_sandbox_gets_its_own_paths(self):
        """Positive control: the text must follow its arguments, not a constant.

        Without this, replacing ``sandbox_manifest_text`` with a string literal
        would keep every assertion above green, because the manifest would still
        happen to agree with the one sandbox it is written into.
        """
        synthetic_dir = pathlib.Path("C:/pmrf-synthetic/") / f"{conftest._SANDBOX_PREFIX}control"
        synthetic_ledger = synthetic_dir / "v2_loop.db"

        text = conftest.sandbox_manifest_text(str(synthetic_dir), str(synthetic_ledger))

        assert _field(text, "sandbox_dir") == str(synthetic_dir), (
            "sandbox_manifest_text ignored the directory it was given"
        )
        assert _field(text, "evidence_ledger") == str(synthetic_ledger), (
            "sandbox_manifest_text ignored the ledger path it was given"
        )
        assert _normalized(conftest._TEST_DATA_DIR) not in _normalized(text), (
            "the synthetic manifest still mentions the real sandbox; the text is "
            "partly hardcoded"
        )
        # ...and the fields the other tests read must be present here too, so a
        # missing field cannot hide behind a real-sandbox-only code path.
        for name in ("created_utc", "pid", "reclaim_glob"):
            assert _field(text, name), f"the synthetic manifest has no {name!r}"
