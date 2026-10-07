"""The *shipped* default of three posture switches, which no test observed.

``StartupGuardTests`` pins the *branch*: it sets the switch to a literal and
asserts what happens then (``test_operational_readiness.py:224``, ``:352``,
``:430``).  What nothing pinned is the *default* -- the value a deployment gets
when neither the process environment nor a readable ``.env`` supplies the name.
Measured 2026-10-05 (audit section 53.35): flipping ``ALLOW_OPEN_WRITES``'s
shipped default from ``"false"`` to ``"true"`` -- a keyless deployment coming up
with every mutating endpoint public -- left 7 files / 178 tests green on the
guard set, and 33 files / 611 tests green across every file that mentions the
name.  Every assertion about the name was about a branch, or about a template;
none was about the default.

Two layers, because the obvious form is conditionally empty.

``assertIs(settings.ALLOW_OPEN_WRITES, False)`` reads the *effective* value, and
an environment or ``.env`` that assigns the name satisfies it whether or not the
default is still ``false`` -- on exactly the machines that configure the switch,
that form guards nothing.  ``TestTheObservationIsAFunctionOfItsInputs`` pins
that mechanism rather than asserting it in prose.

* ``shipped_default_literals()`` reads the literal at the definition site.  It
  depends on no environment, so the fail-open default stays pinned even if the
  behavioural layer below ever has to be skipped.
* ``TestTheDefaultIsObservedNotOnlyDeclared`` reloads ``app.core.config`` with
  the name popped *and* ``dotenv.load_dotenv`` neutralised, so no skip is needed
  when a developer's ``.env`` assigns the name: what is observed is the default
  by definition, not a file's value.  The precedent for reloading under a
  patched environment is ``test_openapi_docs_switch.py``; the precedent for the
  skip this avoids is ``test_run_entrypoint.py:112``.

Three separate tests rather than one loop: each switch's default is its own
finding, and a mutation of one must name the one it broke.
"""
from __future__ import annotations

import ast
import functools
import importlib
import os
import pathlib
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import app.core.config as config_module

# `_env_bool`'s truthy set (config.py:122-123), duplicated rather than imported
# on purpose: a test that imported it would agree with any later change to it,
# including one that made "false" mean true.
_TRUTHY = frozenset({"1", "true", "yes", "on"})

# name -> (shipped default literal, what a flip would do).  The direction is part
# of the entry because the three are not the same direction: one flip opens the
# deployment, one silences it, and one only makes it stricter.
_PINNED: dict[str, tuple[str, str]] = {
    "ALLOW_OPEN_WRITES": (
        "false",
        "fail-open: with no API_WRITE_KEY every mutating endpoint is public, "
        "which is the state app/main.py:224-232 refuses to boot in",
    ),
    "LLM_STARTUP_CHECK_ENABLED": (
        "false",
        "fail-closed: a deployment whose credential is rejected would refuse to "
        "boot instead of coming up degraded",
    ),
    "SCHEDULER_ENABLED": (
        "true",
        "silent: every scheduled job stops while /api/health still answers 200",
    ),
}


@functools.lru_cache(maxsize=1)
def _config_source() -> str:
    return pathlib.Path(config_module.__file__).read_bytes().decode("utf-8")


def shipped_default_literals(source: str) -> dict[str, str]:
    """``name -> default literal`` for each ``_env_bool("NAME", "literal")``.

    A second argument that is not a string literal -- computed elsewhere, or a
    single-argument call -- and a name defined more than once are recorded as
    markers rather than skipped, so rewriting the definition site fails the
    lookups below instead of dropping a name silently out of the population.
    """
    found: dict[str, str] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        called = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
        if called != "_env_bool" or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        name = first.value
        if name in found:
            found[name] = "<defined more than once>"
            continue
        default = node.args[1] if len(node.args) > 1 else None
        found[name] = (
            default.value
            if isinstance(default, ast.Constant) and isinstance(default.value, str)
            else "<not a string literal>"
        )
    return found


def _shipped_literals() -> dict[str, str]:
    return shipped_default_literals(_config_source())


def _default_bool(name: str) -> bool:
    return _shipped_literals()[name].strip().lower() in _TRUTHY


@contextmanager
def _reloaded_settings(
    *,
    env: dict[str, str] | None = None,
    drop: str | None = None,
    ignore_dotenv: bool = False,
):
    """Yield ``settings`` as a reload builds it under these environment inputs.

    The restoring reload sits *outside* the ``patch.dict`` block on purpose:
    reload binds ``config.settings`` to an instance built from whatever
    ``os.environ`` holds at that moment, so reloading while the patched value is
    still in place would leave that value in the process-wide object for every
    later test.  ``test_config.py:107-135`` records that trap from a leak that
    really happened and asserts its own cleanup for the same reason.
    """
    try:
        with patch.dict(os.environ, env or {}):
            if drop is not None:
                os.environ.pop(drop, None)
            if not ignore_dotenv:
                yield importlib.reload(config_module).settings
                return
            with patch("dotenv.load_dotenv") as load:
                reloaded = importlib.reload(config_module)
                # config.py does `from dotenv import load_dotenv`, so the reload
                # re-binds the name and picks up this patch.  Change that import
                # to `import dotenv` and the patch stops landing -- this would
                # read a .env value while still looking green, so say it.
                assert load.called, (
                    "neutralising dotenv.load_dotenv did not reach "
                    "config._load_env_files, so the .env layer is still in play "
                    "and nothing here is observing a shipped default"
                )
                yield reloaded.settings
    finally:
        importlib.reload(config_module)


class TestTheLiteralReaderIsAFunctionOfItsInput(unittest.TestCase):
    """Guard the instrument.  A reader that answered "false" to everything, or
    nothing at all, would satisfy two of the three pinned defaults -- and the
    third failing is the only reason anyone would notice."""

    def test_it_reads_the_literal(self):
        self.assertEqual(
            shipped_default_literals('_env_bool("X", "false")'), {"X": "false"}
        )

    def test_it_can_say_true_as_well(self):
        self.assertEqual(
            shipped_default_literals('_env_bool("X", "true")'), {"X": "true"}
        )

    def test_one_of_the_real_definitions_is_split_over_lines(self):
        """A line-based reader would miss it and the name would look undefined."""
        source = '_env_bool(\n        "X", "false"\n    )'
        self.assertEqual(shipped_default_literals(source), {"X": "false"})

    def test_a_name_with_no_call_is_absent(self):
        """Emptiness has to be representable, or a scan that found nothing
        would report every name as present."""
        self.assertEqual(shipped_default_literals("OTHER = 1"), {})

    def test_a_duplicate_definition_is_reported_not_resolved(self):
        source = '_env_bool("X", "false")\n_env_bool("X", "true")'
        self.assertEqual(
            shipped_default_literals(source)["X"], "<defined more than once>"
        )

    def test_a_computed_default_is_reported_not_resolved(self):
        source = '_env_bool("X", SOME_CONSTANT)'
        self.assertEqual(
            shipped_default_literals(source)["X"], "<not a string literal>"
        )


class TestThePinnedPopulationIsComplete(unittest.TestCase):
    def test_the_pinned_names_are_exactly_the_three_switches(self):
        """Dropping a name has to be an edit here, not a silent omission."""
        self.assertEqual(
            sorted(_PINNED),
            ["ALLOW_OPEN_WRITES", "LLM_STARTUP_CHECK_ENABLED", "SCHEDULER_ENABLED"],
        )

    def test_every_entry_says_which_direction_a_flip_would_be(self):
        for name, (_, why) in sorted(_PINNED.items()):
            with self.subTest(name=name):
                self.assertGreater(
                    len(why.strip()), 40,
                    f"{name} needs a reason that names the dangerous direction, "
                    f"not a restatement of the value",
                )


class TestTheShippedDefaultOfEachSwitch(unittest.TestCase):
    """The literal layer: environment-independent, and the place a fail-open
    default stays pinned even if the behavioural layer is ever skipped."""

    def test_every_pinned_name_is_defined_by_a_string_literal(self):
        literals = _shipped_literals()
        for name in sorted(_PINNED):
            with self.subTest(name=name):
                self.assertIn(
                    name, literals,
                    f"{name} is not defined by an `_env_bool({name!r}, ...)` call "
                    f"any more, so its shipped default moved somewhere this "
                    f"guard cannot see it",
                )
                self.assertIn(
                    literals[name], {"true", "false"},
                    f"{name}'s default reads {literals[name]!r}, which is not a "
                    f"boolean literal",
                )

    def test_the_shipped_default_of_allow_open_writes_is_false(self):
        self.assertEqual(
            _shipped_literals()["ALLOW_OPEN_WRITES"], "false", _PINNED["ALLOW_OPEN_WRITES"][1]
        )

    def test_the_shipped_default_of_llm_startup_check_enabled_is_false(self):
        self.assertEqual(
            _shipped_literals()["LLM_STARTUP_CHECK_ENABLED"],
            "false",
            _PINNED["LLM_STARTUP_CHECK_ENABLED"][1],
        )

    def test_the_shipped_default_of_scheduler_enabled_is_true(self):
        self.assertEqual(
            _shipped_literals()["SCHEDULER_ENABLED"], "true", _PINNED["SCHEDULER_ENABLED"][1]
        )


class TestTheObservationIsAFunctionOfItsInputs(unittest.TestCase):
    """Controls for the behavioural layer below.

    ``SCHEDULER_ENABLED`` is used for the first because it is the one whose
    shipped default is ``true``: an environment variable has to be able to turn
    it off, and an observation that returned a constant could not.
    """

    def test_an_environment_variable_moves_the_value(self):
        self.assertTrue(_default_bool("SCHEDULER_ENABLED"), "control assumed a true default")
        with _reloaded_settings(env={"SCHEDULER_ENABLED": "false"}) as settings:
            self.assertIs(settings.SCHEDULER_ENABLED, False)

    def test_the_other_direction_moves_it_too(self):
        """Without both directions, a reader stuck on one value passes."""
        self.assertFalse(_default_bool("ALLOW_OPEN_WRITES"), "control assumed a false default")
        with _reloaded_settings(env={"ALLOW_OPEN_WRITES": "true"}) as settings:
            self.assertIs(settings.ALLOW_OPEN_WRITES, True)

    def test_the_environment_is_restored_afterwards(self):
        """A reload that leaked the patched value would perturb an unrelated
        test in the full suite; assert the cleanup rather than describe it."""
        before = config_module.settings.SCHEDULER_ENABLED
        with _reloaded_settings(env={"SCHEDULER_ENABLED": "false"}) as settings:
            self.assertIs(settings.SCHEDULER_ENABLED, False)
        self.assertIs(config_module.settings.SCHEDULER_ENABLED, before)


class TestTheDefaultIsObservedNotOnlyDeclared(unittest.TestCase):
    """The behavioural layer.  The literals above are declarations; if a name
    were ever resolved another way -- shadowed later, computed from a second
    variable, read with a different helper -- the literal would still sit at the
    definition site while the value moved."""

    def _assert_shipped_default(self, name: str) -> None:
        with _reloaded_settings(drop=name, ignore_dotenv=True) as settings:
            self.assertIs(getattr(settings, name), _default_bool(name), _PINNED[name][1])

    def test_allow_open_writes(self):
        self._assert_shipped_default("ALLOW_OPEN_WRITES")

    def test_llm_startup_check_enabled(self):
        self._assert_shipped_default("LLM_STARTUP_CHECK_ENABLED")

    def test_scheduler_enabled(self):
        self._assert_shipped_default("SCHEDULER_ENABLED")
