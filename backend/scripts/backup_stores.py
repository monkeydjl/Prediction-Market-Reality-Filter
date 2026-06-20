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


def create_backup(output_dir: str | None = None) -> Path:
    base = Path(__file__).resolve().parents[1]
    backup_dir = Path(output_dir).resolve() if output_dir else base / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    archive = backup_dir / f"pmrf-backup-{stamp}.zip"

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in _candidate_paths():
            zf.write(path, arcname=path.name)

    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up PMRF runtime stores.")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()
    archive = create_backup(args.output_dir)
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
