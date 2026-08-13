import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Any, IO, Iterator

logger = logging.getLogger(__name__)


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()

# Per-process refcount of paths whose cross-process lock is currently held.
# Keyed by absolute path; value is the open file handle of the .crosslock.
# The cross-process file lock (POSIX flock / Windows msvcrt.locking) is NOT
# reentrant — if the same process opens a second fd on the same lock file and
# tries flock(LOCK_EX), it blocks against itself, deadlocking any code path
# that nests locked_file() calls on the same path (e.g. save_events() calls
# read_json_strict() -> locked_file() then write_json_atomic() -> locked_file(),
# all on event_store.json). We work around this by holding a single fd per
# path and skipping re-acquisition when this process already holds it. The
# in-process RLock guarantees only one thread per process is in this code path
# at a time for a given path, so the refcount tracking is race-free without
# its own lock.
_HELD_CROSS_PROCESS: dict[str, IO[Any]] = {}


def _lock_for(path: str) -> threading.RLock:
    normalized = os.path.abspath(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(normalized)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[normalized] = lock
        return lock


def _acquire_cross_process_lock(handle: IO[Any]) -> None:
    """Block until we hold an exclusive cross-process lock on `handle`.

    POSIX uses fcntl.flock(LOCK_EX) — blocks by default, auto-released when
    the file descriptor is closed (so a crash releases the lock). Windows uses
    msvcrt.locking with a manual poll because LK_LOCK only retries for ~1 sec
    before raising; we want indefinite blocking to match POSIX semantics.

    Same pattern as app/core/scheduler.py:_acquire_process_lock.
    """
    # `sys.platform` rather than `os.name`: a type checker prunes the branch it
    # is not running on only for sys.platform, so os.name made every
    # msvcrt/fcntl attribute look undefined on whichever platform mypy ran.
    if sys.platform == "win32":
        import msvcrt

        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                # Lock held by another process. Brief sleep to avoid a busy
                # spin; contention is expected to be short (writes are atomic
                # renames, reads are sub-second).
                time.sleep(0.05)
    else:
        import fcntl

        # LOCK_EX blocks until the lock is available. No timeout — the cross-
        # process writes this protects are short, and a stuck holder indicates
        # a process that crashed holding it (in which case the kernel releases
        # the flock on file close, so this still unblocks).
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _release_cross_process_lock(handle: IO[Any]) -> None:
    """Release the cross-process lock acquired by _acquire_cross_process_lock.

    Best-effort: the lock is also released automatically when the file
    descriptor is closed (so even if this raises, closing the handle still
    releases the lock).
    """
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        # Already released (e.g. handle was closed). Not an error.
        pass


@contextmanager
def locked_file(path: str) -> Iterator[None]:
    """Acquire both in-process and cross-process locks for `path`.

    The in-process RLock serializes threads inside one process (reentrant so
    nested locked_file calls on the same path don't deadlock). The cross-
    process file lock serializes separate processes — the API service and the
    scheduler service both write event_store.json / event_cache.json, and
    without a cross-process lock their writes can interleave such that one
    process's update is silently lost (the second os.replace overwrites the
    first).

    The cross-process lock is NOT reentrant at the OS level (flock on a second
    fd from the same process blocks), so we track per-path ownership in
    _HELD_CROSS_PROCESS: if this process already holds the lock for `path`,
    nested calls skip acquisition and only the outermost call releases. The
    in-process RLock guarantees the refcount tracking is race-free.

    Acquisition is blocking. OSError from opening the sidecar lock file (e.g.
    parent dir is read-only) propagates so write paths see the failure
    loudly; lenient read paths (read_json) catch it and degrade.
    """
    abspath = os.path.abspath(path)
    # In-process first (cheap, fast). Reentrant for nested calls.
    lock = _lock_for(abspath)
    with lock:
        # Cross-process second. Only acquire if this process doesn't already
        # hold it (nested call). The in-process RLock above guarantees we're
        # the only thread in this process touching this path's tracking entry
        # right now, so the dict read+write is race-free without an extra lock.
        already_held = abspath in _HELD_CROSS_PROCESS
        handle = None
        if not already_held:
            # Open a sidecar .crosslock file in append+read mode so it's
            # created if missing and we can lock it. The lock file's lifetime
            # is independent of the target file (which may not exist yet on
            # first run); don't unlink it — unlinking would race with another
            # process trying to acquire the lock on the same path.
            lock_path = abspath + ".crosslock"
            lock_dir = os.path.dirname(lock_path)
            if lock_dir:
                os.makedirs(lock_dir, exist_ok=True)
            handle = open(lock_path, "a+", encoding="utf-8")
            try:
                _acquire_cross_process_lock(handle)
                _HELD_CROSS_PROCESS[abspath] = handle
            except BaseException:
                # On any failure (OSError from acquisition, KeyboardInterrupt
                # while blocked, etc.) close the sidecar handle so we don't
                # leak the fd. Re-raise so the caller sees the error.
                handle.close()
                raise
        try:
            yield
        finally:
            if handle is not None:
                # Only the outermost call releases. Nested calls have
                # handle=None and skip this block entirely.
                _HELD_CROSS_PROCESS.pop(abspath, None)
                _release_cross_process_lock(handle)
                handle.close()


def _quarantine_corrupt(path: str) -> None:
    """Best-effort: copy a corrupt JSON file aside to <path>.corrupt (latest only)."""
    try:
        shutil.copy2(path, path + ".corrupt")
    except OSError:
        pass


def read_json(path: str, fallback: Any) -> Any:
    """Lenient read for caches / read-only paths.  Never raises.

    Missing file -> fallback.  Corrupt JSON -> log + quarantine + fallback.
    OSError (including failure to acquire the cross-process lock) -> log
    warning + fallback. Cache reads degrade, not crash.
    """
    try:
        with locked_file(path):
            if not os.path.exists(path):
                return fallback
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    return json.load(handle)
            except json.JSONDecodeError:
                logger.error("Corrupt JSON at %s – quarantined, using fallback", path)
                _quarantine_corrupt(path)
                return fallback
            except OSError as exc:
                logger.warning("Could not read %s (%s) – using fallback", path, exc)
                return fallback
    except OSError as exc:
        # Cross-process lock file couldn't be opened (e.g. parent dir is
        # read-only, or builtins.open is patched in tests). Lenient reads
        # must still return the fallback rather than crash the caller.
        logger.warning("Could not acquire lock for %s (%s) – using fallback", path, exc)
        return fallback


def read_json_strict(path: str, fallback: Any) -> Any:
    """Strict read for durable stores' read-modify-write paths.

    Missing file -> fallback (first-run / fresh-deploy must still work).
    Corrupt JSON or OSError (including failure to acquire the cross-process
    lock) -> raise so the caller ABORTS the integral write instead of
    overwriting durable data with an empty fallback.
    """
    with locked_file(path):
        if not os.path.exists(path):
            return fallback
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except json.JSONDecodeError:
            logger.error("Corrupt JSON at %s – quarantined, aborting write", path)
            _quarantine_corrupt(path)
            raise
        except OSError:
            logger.error("I/O error reading %s – aborting write to avoid data loss", path)
            raise


def write_json_atomic(path: str, data: Any, *, indent: int | None = 2) -> None:
    with locked_file(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        directory = os.path.dirname(path)
        fd, temp_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            dir=directory,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=indent, ensure_ascii=False)
                handle.write("\n")
            os.replace(temp_path, path)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise


def rewrite_lines_atomic(path: str, lines: list[str]) -> None:
    """Atomically rewrite a text-line file (e.g. a JSONL audit log).

    Writes all `lines` (each should already end with a newline, or be a single
    logical line) to a temp file in the same directory, then os.replace's it
    onto `path`. This avoids the partial-write / truncation risk of opening the
    file in-place with mode "w" (a crash mid-write would leave a half file and
    lose the prior contents). The lock is held across read-modify-write so
    concurrent writers in the same process do not clobber each other.
    """
    with locked_file(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        directory = os.path.dirname(path)
        fd, temp_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            dir=directory,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                for line in lines:
                    handle.write(line if line.endswith("\n") else line + "\n")
            os.replace(temp_path, path)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
