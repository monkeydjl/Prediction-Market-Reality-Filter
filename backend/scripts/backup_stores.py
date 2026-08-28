from __future__ import annotations

import argparse
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.core import runtime_stores
from app.core.config import settings


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
                f"two runtime stores share the archive member name {path.name!r} "
                f"({clash} and {path}); a restore could not tell them apart. "
                "Point one of the corresponding *_FILE/*_PATH settings at a "
                "differently named file."
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


def _open_zip(archive: Path, encryption_key: str | None):
    """Open ``archive`` for writing.

    When ``encryption_key`` is non-empty a pyzipper AES-256 encrypted zip is
    produced; otherwise a plaintext zipfile. Returns a context manager.
    """
    if encryption_key:
        try:
            import pyzipper  # type: ignore[import-not-found]
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
            def __enter__(self_inner):
                self_inner._zf = ctx.__enter__()
                self_inner._zf.setpassword(encryption_key.encode("utf-8"))
                return self_inner._zf

            def __exit__(self_inner, exc_type, exc, tb):
                return ctx.__exit__(exc_type, exc, tb)

        return _Ctx()
    return zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED)


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
    """
    base = Path(__file__).resolve().parents[1]
    backup_dir = Path(output_dir).resolve() if output_dir else base / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    archive = backup_dir / f"pmrf-backup-{stamp}.zip"

    key = encryption_key if encryption_key is not None else settings.BACKUP_ENCRYPTION_KEY

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
    archive = create_backup(
        args.output_dir,
        keep=args.keep,
        encryption_key=args.encryption_key,
    )
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
