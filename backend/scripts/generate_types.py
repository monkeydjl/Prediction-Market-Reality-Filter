"""Generate TypeScript types from Pydantic models.

Source of truth: backend/app/models/_frontend_export.py (allowlist of 14
root models). Output: frontend/src/lib/generated-types.ts.

Usage:
    python -m scripts.generate_types           # Write generated-types.ts
    python -m scripts.generate_types --check   # Exit 1 if stale (CI mode)

Requires:
    - pydantic-to-typescript (pip install pydantic-to-typescript>=2.0.0)
    - json2ts CLI (npm install json-schema-to-typescript)

The pydantic2ts library only supports exclude (blacklist), not allowlist.
We achieve allowlist by pointing it at _frontend_export.py, which imports
only the 14 allowed models. pydantic2ts auto-discovers BaseModel subclasses
via inspect.getmembers, so imported classes are included but their parent
module (event.py) is NOT descended into (it's not a submodule of
_frontend_export).
"""
from __future__ import annotations

import argparse
import difflib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
_OUTPUT_PATH = _REPO_ROOT / "frontend" / "src" / "lib" / "generated-types.ts"
_EXPORT_MODULE = "app.models._frontend_export"

# json2ts CLI: prefer frontend's local install, fall back to global PATH.
_FRONTEND_JSON2TS = _REPO_ROOT / "frontend" / "node_modules" / ".bin" / "json2ts"


def _ensure_python_userbase_on_path() -> None:
    """Make pip --user installs visible when Python omits PYTHONUSERBASE."""
    userbase = os.getenv("PYTHONUSERBASE")
    if not userbase:
        return
    candidate = (
        Path(userbase)
        / f"Python{sys.version_info.major}{sys.version_info.minor}"
        / "site-packages"
    )
    if candidate.exists():
        candidate_str = str(candidate)
        if candidate_str not in sys.path:
            sys.path.append(candidate_str)


def _find_json2ts_cmd() -> str:
    """Return the json2ts executable path (local frontend install or PATH).

    On Windows, npm creates ``json2ts.cmd`` (batch file) alongside the Unix
    shell script ``json2ts``. We prefer the ``.cmd`` variant because it is
    executable via ``subprocess.run`` on Windows; the extensionless ``json2ts``
    is a Unix shell script that Windows cannot execute directly.
    """
    import platform

    is_windows = platform.system() == "Windows"

    # On Windows, prefer json2ts.cmd; on Unix, the extensionless shell script.
    candidate = _FRONTEND_JSON2TS.with_suffix(".cmd") if is_windows else _FRONTEND_JSON2TS

    if candidate.exists():
        return str(candidate)

    # Fall back to PATH lookup (shutil.which resolves PATHEXT on Windows)
    which = shutil.which("json2ts")
    if which:
        return which

    raise RuntimeError(
        "json2ts CLI not found. Install with: cd frontend && npm install --save-dev json-schema-to-typescript"
    )


def _clean_output_file_utf8(output_filename: str) -> None:
    """Clean pydantic2ts output using UTF-8, avoiding Windows default GBK."""
    with open(output_filename, "r", encoding="utf-8") as f:
        lines = f.readlines()

    start, end = None, None
    for i, line in enumerate(lines):
        if line.rstrip("\r\n") == "export interface _Master_ {":
            start = i
        elif (start is not None) and line.rstrip("\r\n") == "}":
            end = i
            break

    if start is None:
        raise RuntimeError("Could not find the start of the _Master_ interface.")
    if end is None:
        raise RuntimeError("Could not find the end of the _Master_ interface.")

    banner_comment_lines = [
        "/* tslint:disable */\n",
        "/* eslint-disable */\n",
        "/**\n",
        "/* This file was automatically generated from pydantic models by running pydantic2ts.\n",
        "/* Do not modify it by hand - just update the pydantic models and then re-run the script\n",
        "*/\n\n",
    ]

    new_lines = banner_comment_lines + lines[:start] + lines[(end + 1):]
    with open(output_filename, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def _generate_to_file(output_path: str) -> None:
    """Generate TypeScript types to the given output path.

    Bypasses ``pydantic2ts.generate_typescript_defs()`` because that function
    uses ``os.system()`` with an f-string that does not quote paths — it breaks
    when the ``json2ts`` executable path or temp dirs contain spaces (common on
    Windows, e.g. ``C:\\Users\\My Name\\...`` or repo paths like
    ``E:\\Github\\Prediction Market Reality Filter\\...``).

    Instead, we replicate the logic using ``subprocess.run`` with a list of
    arguments, which handles spaces correctly without shell quoting.
    """
    _ensure_python_userbase_on_path()
    from pydantic2ts.cli.script import (
        _extract_pydantic_models,
        _generate_json_schema,
        _import_module,
    )

    json2ts_cmd = _find_json2ts_cmd()
    module = _import_module(_EXPORT_MODULE)
    models = _extract_pydantic_models(module)

    if not models:
        raise RuntimeError(f"No pydantic models found in {_EXPORT_MODULE}")

    schema = _generate_json_schema(models)

    schema_dir = tempfile.mkdtemp()
    schema_file_path = os.path.join(schema_dir, "schema.json")

    try:
        with open(schema_file_path, "w", encoding="utf-8") as f:
            f.write(schema)

        result = subprocess.run(
            [
                json2ts_cmd,
                "-i", schema_file_path,
                "-o", output_path,
                "--bannerComment", "",
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"json2ts failed with exit code {result.returncode}.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

        _clean_output_file_utf8(output_path)
    finally:
        shutil.rmtree(schema_dir, ignore_errors=True)


def _add_header(content: str) -> str:
    """Prepend a 'do not edit' header if pydantic2ts didn't add one."""
    header = (
        "// AUTO-GENERATED by `python -m scripts.generate_types`.\n"
        "// Do not edit by hand — update Pydantic models in backend/app/models/event.py\n"
        "// and re-run the generator.\n\n"
    )
    if "do not edit" not in content.lower():
        return header + content
    return content


def generate() -> int:
    """Default mode: write generated-types.ts."""
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".ts", dir=tempfile.gettempdir())
    os.close(tmp_fd)
    try:
        _generate_to_file(tmp_path)
        with open(tmp_path, "r", encoding="utf-8") as f:
            content = f.read()
        content = _add_header(content)
        _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_OUTPUT_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[INFO] Generated {_OUTPUT_PATH}")
        return 0
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def check() -> int:
    """Check mode: compare generated output with committed file, exit 1 if stale."""
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".ts", dir=tempfile.gettempdir())
    os.close(tmp_fd)
    try:
        _generate_to_file(tmp_path)
        with open(tmp_path, "r", encoding="utf-8") as f:
            fresh_content = _add_header(f.read())
        if not _OUTPUT_PATH.exists():
            print(f"[ERROR] {_OUTPUT_PATH} does not exist. Run without --check first.", file=sys.stderr)
            return 1
        committed_content = _OUTPUT_PATH.read_text(encoding="utf-8")
        if fresh_content == committed_content:
            print(f"[INFO] {_OUTPUT_PATH.name} is up to date.")
            return 0
        print(f"[ERROR] {_OUTPUT_PATH.name} is stale. Run: python -m scripts.generate_types", file=sys.stderr)
        diff = difflib.unified_diff(
            committed_content.splitlines(keepends=True),
            fresh_content.splitlines(keepends=True),
            fromfile="committed",
            tofile="fresh",
            n=3,
        )
        sys.stderr.writelines(diff)
        return 1
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate TypeScript types from Pydantic models."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode: compare generated output with committed file, exit 1 if stale. Does not modify workspace.",
    )
    args = parser.parse_args()
    if args.check:
        return check()
    return generate()


if __name__ == "__main__":
    sys.exit(main())
