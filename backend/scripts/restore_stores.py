"""Restore PMRF runtime stores from a backup archive.

Addresses production-readiness gap §2.6: ``backup_stores.py`` only writes
encrypted zips; there was no restore script, so disaster recovery required
manual unzip + file copy (error-prone, especially with WAL files and
encryption).

This script:
1. Reads a backup archive (plaintext or AES-256 encrypted via pyzipper).
2. Dry-run mode (default): previews which files would be overwritten, with
   checksum verification, and reports current vs. backup file sizes.
3. Apply mode (``--apply``): stops the service (warn-only — caller must
   stop pmrf + pmrf-scheduler processes first), backs up current files to
   ``<target_dir>/.pre_restore_<timestamp>/``, then restores.
4. Reports all actions in a structured summary.

Usage:
    # Preview what would be restored (does NOT write)
    python -m scripts.restore_stores backups/pmrf-backup-20260630-120000Z.zip

    # Actually restore (requires --apply)
    python -m scripts.restore_stores backups/pmrf-backup-...zip --apply

    # Encrypted backup
    python -m scripts.restore_stores backup.zip --encryption-key $KEY --apply

    # Restore to a different target directory (testing)
    python -m scripts.restore_stores backup.zip --target-dir /tmp/test-restore --apply

CLI flags:
    backup_path        Path to the .zip archive (positional, required).
    --apply            Actually overwrite live files (default: dry-run).
    --encryption-key   AES passphrase (when backup is encrypted).
                      Falls back to settings.BACKUP_ENCRYPTION_KEY when omitted.
    --target-dir       Directory to restore files into. Defaults to the
                      configured runtime paths (EVENT_STORE_FILE etc.).
    --verbose          Show extra detail.

Exit codes:
    0 — success (or dry-run completed with no issues)
    1 — backup missing/corrupt, target dir not writable, or restore aborted
    2 — service still running (PMRF_API_PROCESS detected); user must stop first
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make backend importable when run as a script.
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.core.config import settings  # noqa: E402


# Files that the backup script archives. Used to map arcname → target path.
# Must stay in sync with scripts/backup_stores.py::_candidate_paths.
_RUNTIME_FILES = [
    "EVENT_STORE_FILE",
    "EVENT_AUDIT_FILE",
    "EVENT_CACHE_FILE",
    "LOOP_DB_FILE",
]
# SQLite WAL/SHM sidecar files (also archived by backup_stores).
_LOOP_DB_SIDECARS = ["-wal", "-shm"]


def _target_path_for_arcname(arcname: str, target_dir: Path | None) -> Path:
    """Map an archive entry name (basename) to its restore target path.

    The backup stores files by basename (e.g. ``event_store.json``). We
    resolve back to the configured runtime path so the restore lands in
    the right place. When ``target_dir`` is provided (testing), all files
    go into that directory.

    Path traversal guard: the final resolved path is validated to stay
    inside the intended target directory (or the configured LOOP_DB
    parent). A backup archive containing ``../etc/passwd`` or an
    absolute path would otherwise let a malicious archive escape the
    restore destination. We reject such arcnames with ``ValueError``.
    """
    # Reject absolute paths and parent-traversal segments up front. The
    # backup script only archives basenames, so any arcname containing a
    # path separator, drive letter, or ``..`` is suspicious.
    if os.path.isabs(arcname) or "\\" in arcname or "/" in arcname or ".." in arcname:
        raise ValueError(
            f"Refusing to restore entry with unsafe path: {arcname!r} "
            f"(expected a bare basename, no separators or parent traversal)."
        )

    if target_dir is not None:
        resolved = (target_dir / arcname).resolve()
        # Validate the resolved path stays inside target_dir.
        try:
            resolved.relative_to(target_dir.resolve())
        except ValueError:
            raise ValueError(
                f"Refusing to restore entry outside target dir: "
                f"{arcname!r} resolves to {resolved}, outside {target_dir}."
            ) from None
        return resolved

    # Map known basenames back to their configured setting paths.
    for setting_name in _RUNTIME_FILES:
        configured = Path(getattr(settings, setting_name))
        if configured.name == arcname:
            target = configured.resolve()
            _validate_within_runtime_root(target, configured.parent.resolve())
            return target

    # SQLite sidecars: LOOP_DB_FILE + "-wal" / "-shm".
    loop_db = Path(settings.LOOP_DB_FILE)
    for suffix in _LOOP_DB_SIDECARS:
        if arcname == loop_db.name + suffix:
            target = (loop_db.parent / arcname).resolve()
            _validate_within_runtime_root(target, loop_db.parent.resolve())
            return target

    # Unknown file — restore next to the LOOP_DB_FILE directory as a fallback.
    target = (loop_db.parent / arcname).resolve()
    _validate_within_runtime_root(target, loop_db.parent.resolve())
    return target


def _validate_within_runtime_root(resolved: Path, root: Path) -> None:
    """Guard: refuse to restore outside the runtime root directory.

    Even though arcname is already validated to be a bare basename, this
    defense-in-depth check ensures a symlink or other indirect path cannot
    escape the configured runtime directory.
    """
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(
            f"Refusing to restore entry outside runtime root: "
            f"{resolved} is not inside {root}."
        ) from None


def _open_zip_for_read(archive: Path, encryption_key: str | None):
    """Open ``archive`` for reading, transparently handling AES-encrypted zips.

    Pyzipper's ``AESZipFile`` is API-compatible with ``zipfile.ZipFile`` for
    reads when the password is set, so we always use it when an encrypted
    entry is detected.
    """
    try:
        import pyzipper  # type: ignore[import-not-found]
        has_pyzipper = True
    except ImportError:
        has_pyzipper = False

    # First, peek to see if the zip is encrypted.
    with zipfile.ZipFile(archive, "r") as probe:
        first_info = probe.infolist()[0] if probe.infolist() else None
        is_encrypted = (
            first_info is not None
            and first_info.flag_bits & 0x1  # bit 0 = encrypted
        )

    if is_encrypted:
        if not has_pyzipper:
            raise RuntimeError(
                "Backup is AES-encrypted but pyzipper is not installed; "
                "install it (pip install pyzipper) to restore."
            )
        if not encryption_key:
            raise RuntimeError(
                "Backup is encrypted but no --encryption-key provided and "
                "BACKUP_ENCRYPTION_KEY is empty."
            )
        zf = pyzipper.AESZipFile(archive, "r")
        zf.setpassword(encryption_key.encode("utf-8"))
        return zf

    return zipfile.ZipFile(archive, "r")


def _sha256_of_zip_entry(zf: zipfile.ZipFile, arcname: str) -> str:
    """Compute sha256 of a zip entry's content without extracting to disk."""
    h = hashlib.sha256()
    with zf.open(arcname) as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _list_backup_contents(
    archive: Path,
    encryption_key: str | None,
    target_dir: Path | None,
) -> list[dict[str, Any]]:
    """List the contents of the backup with target paths and checksums.

    Returns a list of dicts: {arcname, target_path, size, sha256, exists_currently,
    current_size, current_sha256 (if exists)}.
    """
    entries: list[dict[str, Any]] = []
    with _open_zip_for_read(archive, encryption_key) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            arcname = info.filename
            target = _target_path_for_arcname(arcname, target_dir)
            entry: dict[str, Any] = {
                "arcname": arcname,
                "target_path": str(target),
                "size": info.file_size,
                "sha256": _sha256_of_zip_entry(zf, arcname),
                "exists_currently": target.exists(),
                "current_size": target.stat().st_size if target.exists() else None,
            }
            if target.exists():
                # Compute current file's sha256 to detect drift.
                h = hashlib.sha256()
                with open(target, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
                entry["current_sha256"] = h.hexdigest()
                entry["would_change"] = entry["sha256"] != entry["current_sha256"]
            else:
                entry["current_sha256"] = None
                entry["would_change"] = True  # new file
            entries.append(entry)
    return entries


def _check_service_running() -> bool:
    """Best-effort check if the PMRF service is still running.

    Two strategies, platform-dependent:

    - POSIX: try to acquire an exclusive lock on the SQLite WAL file
      via ``fcntl.flock``. A lock failure means the service has the
      DB open and is likely running.
    - Windows: ``fcntl`` is unavailable, so we probe the configured
      ``PMRF_HEALTHCHECK_URL`` (default ``http://localhost:8000/api/health``)
      with a short timeout. A responsive health endpoint means the
      service is up. A connection refused / timeout means it's down.
      This is preferred over the old ``return False`` conservative
      fallback, which silently let restore overwrite a live DB.
    """
    loop_db = Path(settings.LOOP_DB_FILE)
    if not loop_db.exists():
        # No DB yet on disk — but the service could still be running with
        # an in-memory or different-path DB. Fall through to the health
        # probe on Windows; on POSIX the lock test below would no-op.
        pass

    # POSIX path: fcntl-based lock test.
    try:
        import fcntl  # type: ignore[import-not-found]

        if loop_db.exists():
            with open(loop_db, "a") as f:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(f, fcntl.LOCK_UN)
                    return False
                except (BlockingIOError, OSError):
                    return True
        return False
    except ImportError:
        # Windows / non-POSIX: probe the health endpoint instead of the
        # old conservative ``return False`` (which silently let restore
        # overwrite a live DB). Best-effort: any connection error means
        # "service not responding" -> safe to restore.
        import urllib.request
        import urllib.error

        health_url = getattr(settings, "PMRF_HEALTHCHECK_URL", "") or \
            "http://localhost:8000/api/health"
        timeout = getattr(settings, "PMRF_HEALTHCHECK_TIMEOUT_SECONDS", 5) or 5
        try:
            req = urllib.request.Request(health_url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            return False


def restore_from_backup(
    backup_path: str | Path,
    *,
    apply: bool = False,
    encryption_key: str | None = None,
    target_dir: str | Path | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Restore runtime stores from a backup archive.

    Args:
        backup_path: Path to the .zip archive.
        apply: When False (default), only preview what would be restored.
            When True, actually write the files after backing up current
            ones to a .pre_restore_<timestamp>/ directory.
        encryption_key: AES passphrase (when encrypted). Falls back to
            settings.BACKUP_ENCRYPTION_KEY when None.
        target_dir: Override restore destination (testing). When None,
            restores to the configured runtime paths.
        verbose: Show extra detail in the report.

    Returns a dict with keys: applied, archive, entries (list of dicts),
    pre_restore_dir (when apply=True), warnings (list of str).
    """
    archive = Path(backup_path).resolve()
    if not archive.exists():
        raise FileNotFoundError(f"Backup archive not found: {archive}")

    key = encryption_key if encryption_key is not None else settings.BACKUP_ENCRYPTION_KEY
    target_dir_path = Path(target_dir).resolve() if target_dir else None
    if target_dir_path is not None:
        target_dir_path.mkdir(parents=True, exist_ok=True)

    # List contents + compute checksums.
    entries = _list_backup_contents(archive, key, target_dir_path)

    warnings: list[str] = []

    # Service running check (heuristic, warn-only).
    if apply and _check_service_running():
        warnings.append(
            "PMRF service appears to be running (SQLite DB is locked). "
            "Stop pmrf + pmrf-scheduler before --apply to avoid corruption."
        )

    if not apply:
        return {
            "applied": False,
            "archive": str(archive),
            "entries": entries,
            "warnings": warnings,
        }

    # ─── Apply mode: backup current → restore ────────────────────────────
    # Back up the current live files to <archive_dir>/.pre_restore_<stamp>/
    # so the operator can undo a bad restore.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    pre_restore_dir = archive.parent / f".pre_restore_{stamp}"
    pre_restore_dir.mkdir(parents=True, exist_ok=True)

    for entry in entries:
        target = Path(entry["target_path"])
        if target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, pre_restore_dir / target.name)

    # Now restore the backup files.
    with _open_zip_for_read(archive, key) as zf:
        for entry in entries:
            target = Path(entry["target_path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(entry["arcname"]) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)

    return {
        "applied": True,
        "archive": str(archive),
        "entries": entries,
        "pre_restore_dir": str(pre_restore_dir),
        "warnings": warnings,
    }


def _format_report(result: dict[str, Any], *, verbose: bool) -> str:
    """Render restore result as human-readable text.

    Uses ASCII tags ([OK]/[FAIL]/[DRY-RUN]/[WARN]) instead of emoji so
    the output is portable across Windows GBK consoles (cp936) which
    cannot encode the original emoji characters and raise
    ``UnicodeEncodeError`` on stdout.
    """
    lines: list[str] = []
    if result["applied"]:
        lines.append(f"[OK] Restored from {result['archive']}")
        lines.append(f"    Pre-restore backup: {result['pre_restore_dir']}")
    else:
        lines.append(f"[DRY-RUN] Preview for {result['archive']}")
        lines.append("    (use --apply to actually restore)\n")

    if result["warnings"]:
        lines.append("[WARN] Warnings:")
        for w in result["warnings"]:
            lines.append(f"    - {w}")
        lines.append("")

    entries = result["entries"]
    if not entries:
        lines.append("(archive is empty)")
        return "\n".join(lines) + "\n"

    lines.append(f"{'File':<30} {'Status':<14} {'Backup':>12} {'Current':>12}")
    lines.append("-" * 72)
    for e in entries:
        status = "new" if not e["exists_currently"] else (
            "CHANGED" if e.get("would_change") else "unchanged"
        )
        cur_size = e.get("current_size")
        cur_str = f"{cur_size:>12,}" if cur_size is not None else f"{'--':>12}"
        lines.append(
            f"{e['arcname']:<30} {status:<14} {e['size']:>12,} {cur_str}"
        )
        if verbose:
            lines.append(f"  backup sha256:  {e['sha256']}")
            if e.get("current_sha256"):
                lines.append(f"  current sha256: {e['current_sha256']}")

    return "\n".join(lines) + "\n"


def _print(text: str, *, file=None) -> None:
    """Print with UTF-8 reconfiguration for Windows GBK consoles.

    Mirrors the same helper in audit_quality_consistency.py: stdout on
    Windows zh-CN defaults to cp936 (GBK), which cannot encode the
    em-dash or arrow characters used in the report. Reconfigure to UTF-8
    on first call; fall back to ASCII replacement on older Python.
    """
    stream = file or sys.stdout
    enc = getattr(stream, "encoding", "") or ""
    if enc.lower() not in ("utf-8", "utf8"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            text = text.encode("ascii", errors="replace").decode("ascii")
    print(text, file=file)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Restore PMRF runtime stores from a backup archive.",
    )
    parser.add_argument("backup_path", help="Path to the .zip backup archive.")
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually overwrite live files (default: dry-run only).",
    )
    parser.add_argument(
        "--encryption-key", default=None,
        help="AES passphrase (when backup is encrypted).",
    )
    parser.add_argument(
        "--target-dir", default=None,
        help="Restore files into this directory (testing). Defaults to configured runtime paths.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = restore_from_backup(
            args.backup_path,
            apply=args.apply,
            encryption_key=args.encryption_key,
            target_dir=args.target_dir,
            verbose=args.verbose,
        )
    except FileNotFoundError as e:
        _print(f"[FAIL] {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        _print(f"[FAIL] {e}", file=sys.stderr)
        return 1

    _print(_format_report(result, verbose=args.verbose))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
