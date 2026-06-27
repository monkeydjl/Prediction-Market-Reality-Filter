from __future__ import annotations

import argparse
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings


def _candidate_paths() -> list[Path]:
    paths = [
        Path(settings.EVENT_STORE_FILE),
        Path(settings.EVENT_AUDIT_FILE),
        Path(settings.EVENT_CACHE_FILE),
        Path(settings.LOOP_DB_FILE),
        Path(settings.LOOP_DB_FILE + "-wal"),
        Path(settings.LOOP_DB_FILE + "-shm"),
    ]
    return [path.resolve() for path in paths if path.exists()]


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
    """Create a backup archive of the runtime stores.

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
    parser = argparse.ArgumentParser(description="Back up PMRF runtime stores.")
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
