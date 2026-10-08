"""Tests for the TypeScript type generation script.

Verifies:
- generate_types.py main() produces generated-types.ts containing every model
  listed in the allowlist (read from ``_frontend_export.__all__``).
- --check mode exits 0 when generated-types.ts is up to date.
- --check mode exits 1 when generated-types.ts is stale.
- Output file contains a header comment.
- Output is deterministic (running twice produces identical files).
"""
from __future__ import annotations

import subprocess
import sys
import typing
import unittest
from pathlib import Path


_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
_OUTPUT_PATH = _REPO_ROOT / "frontend" / "src" / "lib" / "generated-types.ts"


def _referenced_models(roots: list[type]) -> set[type]:
    """Every Pydantic model reachable from ``roots`` through field annotations.

    Producer-driven on purpose: rather than reading the generated file, this asks
    the models what they actually reference, unwrapping ``Optional[...]``,
    ``list[...]`` and friends with ``typing.get_args``. ``_frontend_export`` is
    the entry point because ``pydantic2ts`` collects exactly the BaseModel
    subclasses sitting in that module's namespace.
    """
    from pydantic import BaseModel

    def _walk(annotation: object):
        yield annotation
        for arg in typing.get_args(annotation):
            yield from _walk(arg)

    seen: set[type] = set()
    stack: list[type] = list(roots)
    while stack:
        model = stack.pop()
        if model in seen:
            continue
        seen.add(model)
        for field in model.model_fields.values():
            for candidate in _walk(field.annotation):
                if (
                    isinstance(candidate, type)
                    and issubclass(candidate, BaseModel)
                    and candidate is not BaseModel
                ):
                    stack.append(candidate)
    return seen


def _json2ts_available() -> bool:
    """Whether the json2ts CLI the script shells out to is installed.

    The generator needs ``json2ts`` from ``frontend/node_modules``, so these
    tests only run where the frontend deps are installed. The CI backend job
    installs Python only (Node lives in the frontend and type-sync jobs), and
    without this guard every case here fails on a RuntimeError that says
    nothing about the code under test. The dedicated ``type-sync-check`` job
    keeps the generator itself covered in CI.
    """
    from scripts.generate_types import _find_json2ts_cmd

    try:
        _find_json2ts_cmd()
    except RuntimeError:
        return False
    return True


@unittest.skipUnless(_json2ts_available(), "json2ts CLI not installed (frontend deps missing)")
class TestGenerateTypesScript(unittest.TestCase):
    """Tests run the actual generate_types.py script via subprocess."""

    def _run_script(self, *args: str) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "-m", "scripts.generate_types", *args]
        return subprocess.run(
            cmd,
            cwd=_BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_generates_file_with_all_allowlist_models(self):
        """Default mode writes generated-types.ts containing every allowlisted model.

        The names are read from ``_frontend_export.__all__`` rather than
        hard-coded. The old hard-coded list had drifted to 16 names while the
        allowlist held 23, so it had quietly stopped covering everything added
        since (``SimTrade`` / ``SimTradeListResponse`` / ``TradeStatBucket`` /
        ``TradeStats`` and the three ``MarketSnapshot*`` models) -- audit
        section 53.50.
        """
        from app.models import _frontend_export

        result = self._run_script()
        self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")
        self.assertTrue(_OUTPUT_PATH.exists(), "generated-types.ts not created")
        content = _OUTPUT_PATH.read_text(encoding="utf-8")
        for model_name in _frontend_export.__all__:
            self.assertIn(model_name, content, f"{model_name} missing from generated types")

    def test_output_contains_header_comment(self):
        """Generated file should have a 'do not edit' header."""
        result = self._run_script()
        self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")
        content = _OUTPUT_PATH.read_text(encoding="utf-8")
        self.assertIn("generated", content.lower())
        self.assertIn("do not edit", content.lower())

    def test_check_mode_exit_0_when_up_to_date(self):
        """--check exits 0 when generated-types.ts matches fresh generation."""
        # First ensure file is up to date
        self._run_script()
        # Then check
        result = self._run_script("--check")
        self.assertEqual(result.returncode, 0, f"--check failed on up-to-date file: {result.stderr}")

    def test_check_mode_exit_1_when_stale(self):
        """--check exits 1 when generated-types.ts differs from fresh generation."""
        # Ensure file is up to date first
        self._run_script()
        original = _OUTPUT_PATH.read_text(encoding="utf-8")
        # Tamper: append a fake interface
        _OUTPUT_PATH.write_text(original + "\nexport interface FakeTampered { x: number; }\n", encoding="utf-8")
        try:
            result = self._run_script("--check")
            self.assertEqual(result.returncode, 1, f"--check should exit 1 on stale file, got {result.returncode}")
        finally:
            # Restore
            self._run_script()

    def test_output_is_deterministic(self):
        """Running generate twice produces identical output."""
        self._run_script()
        first = _OUTPUT_PATH.read_text(encoding="utf-8")
        self._run_script()
        second = _OUTPUT_PATH.read_text(encoding="utf-8")
        self.assertEqual(first, second, "Generated output is not deterministic")

    def test_nested_models_included(self):
        """Nested models (DecisionQuality, LLMTelemetry, etc.) appear in output."""
        result = self._run_script()
        self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")
        content = _OUTPUT_PATH.read_text(encoding="utf-8")
        for nested_model in ["DecisionQuality", "MarketQuality", "SourceReliability", "LLMTelemetry", "CrossValidation"]:
            self.assertIn(nested_model, content, f"Nested model {nested_model} missing")


class AllowlistCompositionTests(unittest.TestCase):
    """What the allowlist *exports*, asking nothing of json2ts.

    Kept out of the skip-decorated class above so it still runs where the CLI is
    not installed -- it only inspects the module, and it is the guard that keeps
    the snapshot interfaces closed.
    """

    def test_market_snapshot_models_are_export_roots(self):
        """The three snapshot models must be *imported*, not merely referenced.

        ``pydantic2ts`` collects the models in this module's namespace
        (``inspect.getmembers``), not its ``__all__``. Being in that set is what
        makes a model a root, and only root models get ``extra='forbid'`` during
        schema generation -- which is what suppresses the ``[k: string]:
        unknown`` tail ``json2ts`` otherwise appends. Reachable-by-reference is
        not enough: ``MarketSnapshotSeries`` / ``MarketSnapshotPoint`` are also
        fields of ``MarketSnapshotsResponse``, so dropping them from this module
        would still emit interfaces, just looser ones, and ``tsc`` would stay
        green (audit section 53.49).
        """
        from app.models import _frontend_export

        for name in (
            "MarketSnapshotsResponse",
            "MarketSnapshotSeries",
            "MarketSnapshotPoint",
        ):
            self.assertTrue(
                hasattr(_frontend_export, name),
                f"{name} is not a root model in {_frontend_export.__name__}",
            )

    def test_every_reachable_plain_model_is_registered(self):
        """Every reachable model that did *not* opt into ``extra='allow'`` must be a root.

        A model that is merely referenced renders as a *nested* interface: no
        ``additionalProperties: false``, so ``json2ts`` appends a
        ``[k: string]: unknown`` tail and the generated type is looser than the
        backend contract -- a plain ``BaseModel`` ignores extra keys, so "closed"
        is the faithful shape. Only models that opt into ``extra='allow'`` (the
        ``FlexibleResponse`` family, plus the overlay models declaring
        ``ConfigDict(extra="allow")``) may legitimately stay open.

        So everything the frontend can reach and that has *not* opted in has to
        be imported into ``_frontend_export`` (audit sections 53.49, 53.50). This
        asks the models what they reference rather than reading the generated
        file, so it fires the moment a model is dropped from the allowlist --
        before anyone regenerates.
        """
        from pydantic import BaseModel

        from app.models import _frontend_export

        roots = [
            value
            for value in vars(_frontend_export).values()
            if isinstance(value, type) and issubclass(value, BaseModel) and value is not BaseModel
        ]
        self.assertTrue(roots, "no root models found -- _frontend_export looks empty")
        unregistered = sorted(
            model.__name__
            for model in _referenced_models(roots)
            if (model.model_config or {}).get("extra") != "allow"
            and not hasattr(_frontend_export, model.__name__)
        )
        self.assertEqual(
            unregistered,
            [],
            "these models are reachable from the allowlist but are neither "
            f"registered as roots nor opted into extra='allow': {unregistered}",
        )


if __name__ == "__main__":
    unittest.main()
