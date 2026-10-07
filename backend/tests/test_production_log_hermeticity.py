"""No test may append to the production log.

``tests/conftest.py`` redirects every file-path setting to a session temp
directory "so no test reads or writes real production data".  ``LOG_FILE`` was
the last path setting outside that promise, and the only one whose leak left a
permanent mark.

*Why the value at import time is the value for the whole session.*
``app/main.py`` calls ``setup_logging()`` at module scope (main.py:29), and
``setup_logging()`` reads ``settings.LOG_FILE`` once to build the root
``RotatingFileHandler``.  It installs that handler only when the root logger
has none -- ``if root.handlers: return`` -- so it is bound exactly once and
**never re-bound**.  A patch to ``settings.LOG_FILE`` after ``app.main`` has
been imported therefore has no effect on where records go.  That is what makes
the fix an ordering requirement rather than a value requirement: conftest has
to set ``LOG_FILE`` *before* it imports ``app.main`` (line 113), which it now
does at line 87.

*What the leak measured.*  Before the redirect, every run of this suite
appended to ``backend/logs/app.log``:

* 10,357 lines added on 2026-10-03 alone;
* one single test -- ``test_production_preflight.py -k
  api_lifespan_boots_a_correctly_configured_production`` -- appended **1673
  bytes**; after the redirect the same run appends there **0** bytes and a file
  of exactly 1673 bytes appears in the session temp dir instead (measured, and
  the mechanical form of that pairing is the end-to-end test below);
* the added volume rotated the real log down to three files (09-02..10-03),
  which is how the 2026-07-16 ``Kernel DB initialized at %s`` line -- the only
  surviving record of which file the Phase-12 futures jobs wrote their
  snapshots into -- was destroyed.

*Why the paths below come from the repo layout and not from ``settings``.*
Same reason as ``test_production_db_hermeticity.py``: reading
``settings.LOG_FILE`` and comparing it to the handler would compare the sandbox
with itself and pass no matter what the code does, which is the vacuous shape
this module exists to prevent.  The production directory is derived from
``config.py``'s own location, so the assertions are about the real
``backend/logs/`` tree.

*Pytest installs a decoy file handler of its own.*  At session start its logging
plugin adds ``_pytest.logging._FileHandler`` pointed at ``os.devnull`` -- on
Windows that shows up as ``\\.\nul``.  So "the root logger has a file handler"
is true even for a run that writes no log file at all, and a guard phrased that
way would accept it.  The assertions below are therefore about *where* the
targets are (never inside the checkout) and about a marker that has to be read
back out of one of them -- which a device that discards everything can never
satisfy.  Both halves are load-bearing: "the production log did not grow" is
satisfied by a run with nowhere to write, and a record that goes nowhere must
not look like a record that goes to the right place.
"""
import hashlib
import logging
import pathlib
import uuid

import app.core.config as config_module

from tests import conftest

# backend/app/core/config.py -> backend/ -> the checkout root
_BACKEND_ROOT = pathlib.Path(config_module.__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_ROOT.parent
_PRODUCTION_LOG_DIR = _BACKEND_ROOT / "logs"


def _production_log_state() -> dict[str, str]:
    """Digest every file in ``backend/logs/``, keyed by name.

    Keyed by name so that *creating* a file counts as a change too, and digest
    rather than size so that a rotation is caught as well.  A rotation leaves
    the same filenames behind (``app.log``, ``app.log.1``, ...) with shifted
    contents, so a check on ``app.log`` alone would miss it whenever the
    rotated-away bytes happen to be replaced by an equal number of new ones.

    In CI the directory does not exist; ``{}`` on both sides is then the correct
    answer, and the positive control in the end-to-end test is what keeps that
    from being a free pass.
    """
    if not _PRODUCTION_LOG_DIR.is_dir():
        return {}
    return {
        path.name: hashlib.md5(path.read_bytes()).hexdigest()
        for path in sorted(_PRODUCTION_LOG_DIR.glob("*"))
        if path.is_file()
    }


def _root_file_handler_targets() -> list[pathlib.Path]:
    """Write targets of the root logger's file handlers.

    ``baseFilename`` is what ``logging.FileHandler`` sets; the console
    ``StreamHandler`` that ``setup_logging()`` always adds, pytest's
    ``_LiveLoggingNullHandler`` and a ``NullHandler`` have no such attribute.
    They are filtered out rather than counted, because a record in a stream is
    not a record in a file in the repo -- which is the only thing under test.

    Note that pytest's own ``_FileHandler(os.devnull)`` *does* survive the
    filter.  That is the point: it is why the assertions below cannot be phrased
    as "there is a file handler".
    """
    targets = []
    for handler in logging.getLogger().handlers:
        base = getattr(handler, "baseFilename", None)
        if base:
            targets.append(pathlib.Path(base))
    return targets


def _normalized(path) -> str:
    """Backslash-insensitive, case-insensitive form for containment checks.

    ``tempfile`` and the handler do not have to agree on the case or the 8.3
    short name of a Windows path, and a comparison that depends on that
    agreement fails for a reason that has nothing to do with logging.
    """
    return str(path).replace("\\", "/").lower()


def _inside(child: pathlib.Path, ancestor: pathlib.Path) -> bool:
    text = _normalized(child)
    prefix = _normalized(ancestor).rstrip("/") + "/"
    return text.startswith(prefix)


def _sandbox_root() -> pathlib.Path:
    return pathlib.Path(conftest._TEST_DATA_DIR).resolve()


class TestProductionLogHermeticity:
    def test_the_setting_points_inside_the_test_temp_dir(self):
        """The conftest-level guarantee: the value, before any handler exists."""
        from app.core.config import settings

        assert _normalized(_sandbox_root()) in _normalized(
            pathlib.Path(settings.LOG_FILE).resolve()
        ), (
            f"settings.LOG_FILE is {settings.LOG_FILE!r}, outside the test temp "
            f"dir {conftest._TEST_DATA_DIR!r} -- conftest's redirect does not "
            "reach the log file"
        )

    def test_no_root_file_handler_writes_inside_the_checkout(self):
        """The effective guarantee: not what the setting says, but where records go.

        This is the assertion that fails when the redirect is moved *after*
        ``import app.main`` in conftest.  The setting still reads correctly by
        the time a test runs, so the test above passes -- but the handler was
        already bound to the repo path at import time, and nothing re-binds it.

        Deliberately a hard failure rather than a skip when nothing writes into
        the sandbox: in that state nothing here can observe where records go, so
        the hermeticity claim is unverifiable and silence would be the wrong
        answer.
        """
        targets = _root_file_handler_targets()
        assert targets, (
            "the root logger has no file handler at all, not even pytest's "
            "devnull one; nothing here can observe where records go"
        )

        in_repo = [t for t in targets if _inside(t, _REPO_ROOT)]
        assert in_repo == [], (
            "a root file handler writes inside the checkout: "
            f"{[str(t) for t in in_repo]} (the production log dir is "
            f"{_PRODUCTION_LOG_DIR}) -- it was bound before conftest redirected "
            "settings.LOG_FILE"
        )

        in_sandbox = [t for t in targets if _inside(t, _sandbox_root())]
        assert in_sandbox, (
            "no root file handler writes into the test temp dir "
            f"{conftest._TEST_DATA_DIR!r}; targets are "
            f"{[str(t) for t in targets]} -- the redirect did not reach the "
            "handler that was installed"
        )

    def test_a_record_logged_by_a_test_reaches_the_sandbox_and_not_the_repo(self):
        """End to end, with a marker no historical line can imitate.

        The marker is checked against the files a handler actually holds open,
        not against ``settings.LOG_FILE``, for the same reason the production
        path is derived from the repo layout: comparing the setting to itself
        proves nothing about where the bytes went.
        """
        marker = f"pmrf-log-hermeticity-probe-{uuid.uuid4().hex}"
        before = _production_log_state()

        probe = logging.getLogger("pmrf.hermeticity.probe")
        root = logging.getLogger()
        saved_root_level, saved_probe_level, saved_propagate = (
            root.level, probe.level, probe.propagate,
        )
        root.setLevel(logging.DEBUG)
        probe.setLevel(logging.NOTSET)
        probe.propagate = True
        try:
            probe.warning(marker)
            for handler in root.handlers:
                flush = getattr(handler, "flush", None)
                if callable(flush):
                    flush()
        finally:
            root.setLevel(saved_root_level)
            probe.setLevel(saved_probe_level)
            probe.propagate = saved_propagate

        targets = _root_file_handler_targets()
        landed_in = [
            path for path in targets
            if marker.encode("utf-8") in path.read_bytes()
        ]
        assert landed_in, (
            f"the probe record {marker!r} reached no file handler "
            f"({[str(p) for p in targets]}); the 'did not reach the repo' half "
            "below would then be true for the wrong reason"
        )

        after = _production_log_state()
        assert after == before, (
            "logging from a test appended to a file in the repo; changed: "
            f"{sorted(set(after.items()) ^ set(before.items()))}"
        )
