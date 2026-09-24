from __future__ import annotations

import argparse
import sys
import zipfile
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any

# Make backend importable when run as a script. The backup systemd unit's
# ExecStart is `python scripts/backup_stores.py`, which puts `backend/scripts`
# on sys.path[0] rather than `backend`, so without this the unit dies at import
# with `No module named 'app'` before writing anything. Same line as
# restore_stores.py, this script's twin.
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

try:
    from app.core.config import settings  # noqa: E402
except RuntimeError as exc:
    print(
        f"Configuration refused startup: {type(exc).__name__}",
        file=sys.stderr,
    )
    raise SystemExit(1) from None

from app.core import preflight, runtime_stores  # noqa: E402


def _candidate_paths() -> list[Path]:
    """Existing files to archive, derived from the declared store table.

    The membership decision lives in `app.core.runtime_stores`, not here. This
    list used to be typed out and named four settings while four more held live
    state (33882 kernel prediction rows among them); a test now asserts an exact
    partition of every path setting, so a new store cannot quietly miss a
    backup. See that module's docstring.
    """
    paths = runtime_stores.backup_paths()
    _reject_arcname_collisions(paths)
    return paths


def _reject_arcname_collisions(paths: list[Path]) -> None:
    """Fail when two stores would claim the same archive member name.

    Members are stored under their basename, and `restore_stores` maps that
    basename back to a setting. Two stores sharing one would make the archive
    ambiguous in a way a restore cannot detect, so refuse to write it. Reachable
    only by pointing two path settings at same-named files in different
    directories, but silent data loss is the failure it would otherwise cause.
    """
    seen: dict[str, Path] = {}
    for path in paths:
        clash = seen.get(path.name)
        if clash is not None:
            raise ValueError(
                f"two runtime stores share the archive member name {path.name!r}; "
                "a restore could not tell them apart. Point one of the "
                "corresponding *_FILE/*_PATH settings at a differently named "
                "file."
            )
        seen[path.name] = path


def _prune_backups(backup_dir: Path, keep: int | None) -> None:
    if keep is None:
        return
    if keep < 1:
        raise ValueError("keep must be at least 1")

    archives = sorted(
        backup_dir.glob("pmrf-backup-*.zip"),
        key=lambda path: path.name,
        reverse=True,
    )
    for archive in archives[keep:]:
        archive.unlink()


def _open_zip(archive: Path, encryption_key: str | None) -> AbstractContextManager[Any]:
    """Open ``archive`` for writing.

    When ``encryption_key`` is non-empty a pyzipper AES-256 encrypted zip is
    produced; otherwise a plaintext zipfile. Returns a context manager.

    Annotated `AbstractContextManager[Any]` rather than `...[zipfile.ZipFile]`
    because the encrypted branch yields a `pyzipper.AESZipFile`, and pyzipper
    ships no stubs -- `ignore_missing_imports` makes it `Any`, so a narrower
    annotation here would be a claim this file cannot check.
    """
    if encryption_key:
        password = encryption_key.encode("utf-8")
        try:
            import pyzipper
        except ImportError as exc:  # pragma: no cover - env misconfig
            raise RuntimeError(
                "BACKUP_ENCRYPTION_KEY is set but pyzipper is not installed; "
                "install it (pip install pyzipper) or unset BACKUP_ENCRYPTION_KEY"
            ) from exc
        ctx = pyzipper.AESZipFile(
            archive,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES,
        )
        # Use a context manager wrapper so the caller signature stays uniform
        # whether or not encryption is active.
        class _Ctx:
            def __enter__(self_inner) -> Any:
                self_inner._zf = ctx.__enter__()
                self_inner._zf.setpassword(password)
                return self_inner._zf

            def __exit__(
                self_inner,
                exc_type: type[BaseException] | None,
                exc: BaseException | None,
                tb: TracebackType | None,
            ) -> bool | None:
                result: bool | None = ctx.__exit__(exc_type, exc, tb)
                return result

        return _Ctx()
    return zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED)


def _resolve_encryption_key(encryption_key: str | None) -> str:
    """Resolve the passphrase, refusing a plaintext archive in production.

    ``None`` means "use the configured ``BACKUP_ENCRYPTION_KEY``"; an explicit
    ``""`` means "write plaintext". Off production both resolve as before -- a dev
    box archiving a temp store onto its own disk is not what needs protecting, and
    the restore drill suite depends on the plaintext path.

    In production neither is allowed, because the archive contains every state
    store: the event store, the committed predictions, and the kernel prediction
    history. `deploy/docker-compose.yml` enables the scheduled backup and mounts
    `/app/backups`, so the documented Docker deployment produced one of these
    daily with nothing to protect it.

    Enforced here rather than only in `app.core.preflight` because startup is not
    the only way in: the scheduler's `_job_backup_stores` calls `create_backup`
    directly, in a process whose preflight ran once at startup and cannot speak to
    an individual write. `main()` runs the full preflight as well, and both layers
    are deliberate -- the preflight gates a *process* on all eight production
    rules, this gates the one irreversible *write* at every call site that can
    reach it. Removing either leaves a real path uncovered. Whitespace is not a
    passphrase --
    pyzipper would accept `" "` and produce an archive that reads as encrypted
    everywhere downstream while being trivially openable.

    Raises RuntimeError naming the setting, never its value: the message reaches
    the scheduler's `loop_runs.error` column and from there `/api/health`.
    """
    key = (
        encryption_key
        if encryption_key is not None
        else settings.BACKUP_ENCRYPTION_KEY
    )
    key = str(key or "")
    if preflight.is_production() and not key.strip():
        raise RuntimeError(
            "Refusing to write a plaintext backup archive with "
            "PMRF_ENV=production: BACKUP_ENCRYPTION_KEY is not configured (and "
            "no --encryption-key was given). The archive contains every state "
            "store, including committed predictions and kernel history. Set "
            "BACKUP_ENCRYPTION_KEY to a strong random passphrase, store it "
            "outside the backup volume, and keep it -- an archive whose "
            "passphrase is lost is not a backup."
        )
    return key


def create_backup(
    output_dir: str | None = None,
    keep: int | None = 30,
    encryption_key: str | None = None,
) -> Path:
    """Create a backup archive of the runtime **state** stores.

    Contents are the `STATE_STORES` rows of `app.core.runtime_stores` that exist,
    plus SQLite WAL/SHM sidecars. Derived stores (re-fetchable) and ephemeral
    ones (logs, the scheduler lock) are excluded on purpose and are declared
    there with a reason.

    When ``encryption_key`` is provided (non-empty), the archive is written as
    a pyzipper AES-256 encrypted zip; when empty, a plaintext zip is produced
    (the legacy behavior). Falls back to the configured
    ``settings.BACKUP_ENCRYPTION_KEY`` when ``encryption_key`` is ``None``.

    Under ``PMRF_ENV=production`` the plaintext branch is refused outright --
    see :func:`_resolve_encryption_key`.
    """
    # Resolved first, before any directory is created or any archive path is
    # named, so a refusal leaves nothing behind: a zero-byte or half-written
    # pmrf-backup-*.zip in the volume reads as "the backup ran".
    key = _resolve_encryption_key(encryption_key)

    base = Path(__file__).resolve().parents[1]
    backup_dir = Path(output_dir).resolve() if output_dir else base / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    archive = backup_dir / f"pmrf-backup-{stamp}.zip"

    with _open_zip(archive, key) as zf:
        for path in _candidate_paths():
            zf.write(path, arcname=path.name)

    _prune_backups(backup_dir, keep)
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Back up PMRF runtime state stores (events, audit, cache, loop DB, "
            "kernel DB, World Cup DB, domain reliability, sports facts)."
        )
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--keep",
        type=int,
        default=30,
        help="Number of pmrf-backup-*.zip archives to retain in the output directory.",
    )
    parser.add_argument(
        "--encryption-key",
        default=None,
        help=(
            "AES passphrase for the backup archive. When set the archive is "
            "AES-256 encrypted (pyzipper); when omitted the configured "
            "BACKUP_ENCRYPTION_KEY is used, and when that is also empty a "
            "plaintext zip is produced."
        ),
    )
    args = parser.parse_args()
    if args.keep < 1:
        parser.error("--keep must be at least 1")
    # This process is a production entrypoint in its own right: the backup timer's
    # ExecStart is `python scripts/backup_stores.py`, so it runs neither the API
    # lifespan nor `run_scheduler.py`, the two other places this gate lives. Until
    # this call, the only production rule enforced here was the encryption key
    # (`_resolve_encryption_key`) -- one of eight -- so a deployment the API
    # refuses to serve could still run its nightly backup and report success.
    #
    # Deliberately after `parse_args` and the --keep check: `--help` and a bad
    # argument must still behave normally, or a misconfigured box reports a config
    # error instead of the typo the operator can actually fix. Deliberately before
    # `create_backup`, so a refusal happens before any directory or archive is
    # created. `log_realtime_push_posture()` is *not* called here -- this process
    # serves no sockets, and the posture line belongs to the ones that do.
    preflight.validate_production_config()
    archive = create_backup(
        args.output_dir,
        keep=args.keep,
        encryption_key=args.encryption_key,
    )
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
