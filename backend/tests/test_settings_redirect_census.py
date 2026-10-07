"""Every path-shaped setting must be redirected, or exempted with a reason.

`tests/conftest.py` promises hermetic runs by pointing file-path settings at a
session temp directory.  That promise has now had to be repaired by hand twice:

* `KERNEL_DB_FILE` was not a setting at all -- it was hardcoded, so a redirect
  that works by overwriting settings could not reach it
  (`test_production_db_hermeticity.py`, E10);
* `LOG_FILE` was a setting the redirect never named
  (`test_production_log_hermeticity.py`), and the suite appended to
  `backend/logs/app.log` for as long as it did.

Both times the finding was "one path setting is missing", and both times the
answer came from re-deriving the list by hand.  That re-derivation is the thing
this module removes.

`runtime_stores.path_setting_names()` already derives the population by scanning
`Settings` for path-shaped attribute names, and `tests/test_runtime_stores.py`
already asserts that the STATE / DERIVED / EPHEMERAL partition accounts for
exactly that population.  What was missing is the other half -- that the
*redirect* covers it too.  So this is a partition as well:

    {redirected} | {exempted}  ==  path_setting_names()

with every exemption written down and carrying a reason, in the same shape as
conftest's own `_RESET_EXEMPT` table.  A path setting added to `Settings` in
future fails here instead of quietly deciding where the suite reads and writes.

"The redirect covers it" is measured **behaviourally** -- the setting's resolved
value lies inside the temp dir -- not by parsing conftest's source.  A source
scan would pass for a name whose assignment sits *after* the point where the
value is first read, which is precisely the ordering trap `LOG_FILE` fell into.

Measured when this was written (2026-10-04): the population is 13, the redirect
covered 10 of them, and two of the three remaining ones were reaching the repo.
Neither was firing yet -- `backend/event_audit.jsonl` (175,150 bytes) and
`backend/v2_loop.db.scheduler.lock` (5 bytes) both carried mtimes of
2026-09-11, i.e. no test in that day's red / green / subset / full-suite runs
wrote them -- so this closes a hole no test had tripped, which is the kind of
hole a census catches and a bug report does not.  The third
(`LOL_DRY_RUN_FIXTURES_PATH`) is an input and must stay empty; see
`_REDIRECT_EXEMPT`.
"""
import hashlib
import os
import pathlib
import uuid
from unittest.mock import patch

import app.core.config as config_module
from app.core import runtime_stores, scheduler
from app.core.config import settings
from app.services import event_audit_service

from tests import conftest

# backend/app/core/config.py -> backend/ -> the checkout root
_REPO_ROOT = pathlib.Path(config_module.__file__).resolve().parents[2]

# Path-shaped settings that must NOT be pointed at the temp dir, each with the
# reason.  `test_runtime_stores.py` requires the store tables to be exhaustive;
# this requires the same of the redirect.
_REDIRECT_EXEMPT: dict[str, str] = {
    "LOL_DRY_RUN_FIXTURES_PATH": (
        "an *input* whose empty default is a posture, not a path: "
        "`lol_adapter._resolve_dry_run_path()` falls back to the built-in sample "
        "when it is empty, so a temp path that does not exist switches the "
        "dry-run import off instead of making the run hermetic"
    ),
}


def _sandbox_root() -> pathlib.Path:
    return pathlib.Path(conftest._TEST_DATA_DIR).resolve()


def _normalized(path) -> str:
    """Backslash-insensitive, case-insensitive form for containment checks.

    `tempfile` and a setting do not have to agree on the case or the 8.3 short
    name of a Windows path, and a comparison that depends on that agreement
    fails for a reason that has nothing to do with redirection.
    """
    return str(path).replace("\\", "/").lower()


def _inside(child, ancestor) -> bool:
    """Containment, inclusive: a path is inside itself."""
    text = _normalized(child)
    root = _normalized(ancestor).rstrip("/")
    return text == root or text.startswith(root + "/")


def _redirected_names(names) -> set[str]:
    """Which of ``names`` currently resolve inside the session temp dir."""
    sandbox = _sandbox_root()
    redirected = set()
    for name in names:
        value = getattr(settings, name, "")
        if not isinstance(value, str) or not value:
            continue
        if _inside(pathlib.Path(value).resolve(), sandbox):
            redirected.add(name)
    return redirected


def _digest(path: pathlib.Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.md5(path.read_bytes()).hexdigest()


class TestRedirectCensus:
    def test_the_scan_sees_the_settings(self):
        """Instrument control: the population is derived, non-empty and real."""
        names = runtime_stores.path_setting_names()
        assert len(names) >= 13, sorted(names)
        for known in (
            "EVENT_STORE_FILE",
            "EVENT_AUDIT_FILE",
            "LOG_FILE",
            "SCHEDULER_LOCK_FILE",
            "LOL_DRY_RUN_FIXTURES_PATH",
        ):
            assert known in names, f"{known} missing from {sorted(names)}"
        # A scan that matches everything would make the partition vacuous.
        assert "PMRF_NOT_A_REAL_SETTING" not in names

    def test_the_containment_check_discriminates(self):
        """"Inside the temp dir" has to be able to say no."""
        sandbox = _sandbox_root()
        assert sandbox.is_dir(), f"{sandbox} does not exist; containment is moot"
        assert _inside(sandbox, sandbox)
        assert _inside(sandbox / "app.log", sandbox)
        assert not _inside(pathlib.Path.cwd(), sandbox)
        assert not _inside(sandbox, pathlib.Path.cwd())
        assert not _inside(pathlib.Path("nul"), sandbox)

    def test_every_path_setting_is_redirected_or_exempted(self):
        names = runtime_stores.path_setting_names()
        redirected = _redirected_names(names)
        assert redirected, "nothing at all resolved into the temp dir"
        uncovered = sorted(names - redirected - set(_REDIRECT_EXEMPT))
        assert uncovered == [], (
            f"these path settings resolve outside the test temp dir and have no "
            f"exemption: {uncovered}.  Redirect them in tests/conftest.py, or "
            f"add them to _REDIRECT_EXEMPT in this file with the reason.  An "
            f"unredirected path setting decides where the suite reads and "
            f"writes on a developer machine -- and in CI it points at a file "
            f"that is simply absent, so the test that needs it passes for the "
            f"wrong reason or fails for one."
        )

    def test_the_exemption_table_is_not_stale(self):
        """An exemption that is no longer needed is a decision left lying around."""
        names = runtime_stores.path_setting_names()
        redirected = _redirected_names(names)
        both = sorted(set(_REDIRECT_EXEMPT) & redirected)
        assert both == [], (
            f"exempted but actually redirected: {both} -- delete the exemption "
            f"and its reason, or the table stops describing the code"
        )
        unknown = sorted(set(_REDIRECT_EXEMPT) - names)
        assert unknown == [], (
            f"exempted but not a path setting on Settings: {unknown} -- the name "
            f"was renamed or removed and the exemption is now noise"
        )
        for name, reason in _REDIRECT_EXEMPT.items():
            assert reason.strip(), f"{name} is exempted with no reason written"


class TestTheWritesTheRedirectPrevents:
    """The concrete writes behind two of the redirected names.

    The census above proves the *setting* moved.  These two prove that the
    writer which reads it lands in the sandbox -- and each has both halves:
    bytes appeared in the temp file, *and* the repo file is byte-identical.
    "The repo file did not change" on its own is also true of a writer that
    never ran, so it is never asserted alone.

    Unpatched on purpose.  Patching the setting is what the suite already does
    for both of these, and that is exactly why neither was noticed: the patched
    tests could not see the default, and nothing else touched it.

    The write is issued *before* the containment assertion so that a red run
    produces the real thing -- an appended audit row, or a lock file rewritten
    with this process's pid, inside the checkout -- rather than stopping at a
    precondition that only restates the census.
    """

    def test_recording_an_audit_row_writes_into_the_sandbox(self):
        repo_audit = _REPO_ROOT / "event_audit.jsonl"
        before = _digest(repo_audit)
        target = pathlib.Path(settings.EVENT_AUDIT_FILE)
        preexisting = target.read_text(encoding="utf-8") if target.exists() else ""

        event_id = f"redirect-census-{uuid.uuid4().hex}"
        event_audit_service.record_event(
            {"event_id": event_id, "event_title": "redirect census probe"}
        )

        after = _digest(repo_audit)
        written = target.read_text(encoding="utf-8") if target.exists() else ""
        assert after == before, (
            f"an unpatched record_event() appended to {repo_audit} -- a "
            f"fabricated row in an audit trail, not merely a noisy log.  The "
            f"row it added is {event_id}."
        )
        assert _inside(target.resolve(), _sandbox_root()), (
            f"EVENT_AUDIT_FILE is {settings.EVENT_AUDIT_FILE!r}, outside the "
            f"temp dir {conftest._TEST_DATA_DIR!r}"
        )
        assert event_id in written and event_id not in preexisting, (
            "record_event() persisted nothing, so this test proves nothing "
            "about where it persists"
        )

    def test_acquiring_the_scheduler_lock_writes_into_the_sandbox(self):
        repo_lock = _REPO_ROOT / "v2_loop.db.scheduler.lock"
        before = _digest(repo_lock)
        target = pathlib.Path(settings.SCHEDULER_LOCK_FILE)

        scheduler._release_scheduler_lock()
        try:
            with patch.object(scheduler.settings, "SCHEDULER_LOCK_ENABLED", True):
                acquired = scheduler._try_acquire_scheduler_lock()
        finally:
            scheduler._release_scheduler_lock()

        after = _digest(repo_lock)
        assert acquired, (
            "the scheduler lock was not acquired, so this test proves nothing "
            "about the file it acquires"
        )
        assert after == before, (
            f"an unpatched lock acquisition wrote into {repo_lock}; it now holds "
            f"{repo_lock.read_text(encoding='utf-8') if repo_lock.exists() else '<missing>'!r}"
        )
        assert _inside(target.resolve(), _sandbox_root()), (
            f"SCHEDULER_LOCK_FILE is {settings.SCHEDULER_LOCK_FILE!r}, outside "
            f"the temp dir {conftest._TEST_DATA_DIR!r}"
        )
        assert target.exists(), f"{target} was never created"
        assert str(os.getpid()) in target.read_text(encoding="utf-8")
