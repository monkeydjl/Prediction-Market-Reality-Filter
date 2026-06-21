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


def create_backup(output_dir: str | None = None, keep: int | None = 30) -> Path:
    base = Path(__file__).resolve().parents[1]
    backup_dir = Path(output_dir).resolve() if output_dir else base / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    archive = backup_dir / f"pmrf-backup-{stamp}.zip"

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
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
    args = parser.parse_args()
    if args.keep < 1:
        parser.error("--keep must be at least 1")
    archive = create_backup(args.output_dir, keep=args.keep)
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
