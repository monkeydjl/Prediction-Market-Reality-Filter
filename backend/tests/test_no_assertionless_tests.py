"""Guard: no test function may be added without an assertion.

Why this file exists
--------------------
``test_rule2_official_opposing_downgrades_to_wait`` in
``test_decision_quality_service.py`` ended in ``pass`` with a nine-line comment
explaining why its author could not make the case work. It was counted in every
"N tests passed" line for months and could never fail. A second test claimed in
its name that learning "skips" and asserted nothing at all, so it would have
passed just as well if the three learning writes had run.

An assertion-free test is worse than a missing one: it inflates the count that
makes the suite look trustworthy. So the population is declared here as data and
compared to an AST scan for an *exact* match. Adding a new assertion-free test
goes red; so does leaving a stale entry in the allowlist after a test is fixed,
renamed, or deleted.

Being on the allowlist is not a defect. ``capture_exception`` returning silently
without an initialised Sentry client, or ``model_validate`` accepting a fixture,
are real properties whose only observable is "this did not raise". What the list
forbids is an *undeclared* one.

If a test of yours lands here because it delegates its checks to a helper, name
the helper ``_assert_*`` — the scan counts any call whose name contains
``assert`` as an assertion, wherever it is defined.
"""
from __future__ import annotations

import ast
import functools
import io
import unittest
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent

# A call whose name contains any of these is treated as an assertion.
# ``fail`` covers ``self.fail(...)``; ``raises`` covers both ``pytest.raises``
# and ``assertRaises``; ``skipTest`` is a deliberate non-result.
_ASSERTION_MARKERS = ("assert", "fail", "raises", "skiptest", "warns")

# (file relative to tests/, function name) -> why it legitimately has no assertion.
_ALLOWED_WITHOUT_ASSERTIONS: dict[tuple[str, str], str] = {
    ("test_drift_alert_dispatcher.py", "test_webhook_failure_does_not_raise"):
        "the property is that a raising webhook is swallowed; there is no return value",
    ("test_operational_readiness.py", "test_lifespan_boots_with_key"):
        "the property is that app startup completes; TestClient raises if it does not",
    ("test_operational_readiness.py", "test_lifespan_survives_a_maintain_all_that_raises"):
        "the property is that a raising maintain_all does not abort startup",
    ("test_quality_metrics.py", "test_counter_inc_and_observe_are_noop_safe_after_clear"):
        "the property is that metric writes are no-op safe; the shim exposes no state",
    ("test_review_queue_detectors.py", "test_fixtures_validate_against_their_models"):
        "model_validate raises on a bad fixture; a return value would add nothing",
    ("test_sentry_integration.py", "test_capture_exception_does_not_raise_without_init"):
        "the property is that capture is a silent no-op with no client configured",
    ("test_sentry_integration.py", "test_capture_message_does_not_raise_without_init"):
        "the property is that capture is a silent no-op with no client configured",
    ("test_smoke_check.py", "test_validate_event_payload_accepts_backend_categories"):
        "validate_event_payload raises on a rejected payload; acceptance is the assertion",
}

# The scan is only meaningful if it actually found the suite. Well under the
# real count (about 5,000 collected) but far above anything a broken walk or a
# wrong root directory would produce.
_MIN_PLAUSIBLE_TEST_FUNCTIONS = 3000


def _has_assertion(node: ast.AST) -> bool:
    """True if ``node``'s subtree contains a bare assert or an assertion call."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assert):
            return True
        if isinstance(sub, ast.Call):
            func = sub.func
            name = ""
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            low = name.lower()
            if any(marker in low for marker in _ASSERTION_MARKERS):
                return True
    return False


def _collected_test_files() -> list[Path]:
    """Every file pytest collects under tests/ — ``test_*.py``, at any depth."""
    return sorted(_TESTS_DIR.rglob("test_*.py"))


@functools.lru_cache(maxsize=1)
def _scan() -> tuple[int, frozenset[tuple[str, str]]]:
    """Return (functions scanned, {(relative file, name)} with no assertion).

    A file that will not parse raises rather than being skipped: two files in
    this suite carry a UTF-8 BOM, and an earlier version of this scan silently
    dropped them on ``SyntaxError``, which is exactly how a census loses the
    rows it exists to count.
    """
    total = 0
    bare: set[tuple[str, str]] = set()
    for path in _collected_test_files():
        source = io.open(path, encoding="utf-8-sig").read()
        tree = ast.parse(source, filename=str(path))
        rel = path.relative_to(_TESTS_DIR).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test"):
                continue
            total += 1
            if not _has_assertion(node):
                bare.add((rel, node.name))
    # Frozen and cached: three tests below consume the same scan, and the
    # result must not be mutable by any of them.
    return total, frozenset(bare)


class DetectorTests(unittest.TestCase):
    """Guard the instrument. A detector that answers "has an assertion" to
    everything would make the partition test below vacuous."""

    def _first_func(self, src: str) -> ast.AST:
        tree = ast.parse(src)
        return tree.body[0]

    def test_bare_assert_counts(self):
        self.assertTrue(self._has("def test_x():\n    assert 1 == 1\n"))

    def test_unittest_assertion_counts(self):
        self.assertTrue(self._has("def test_x(self):\n    self.assertEqual(1, 1)\n"))

    def test_mock_assertion_counts(self):
        self.assertTrue(self._has("def test_x(self):\n    m.assert_not_called()\n"))

    def test_pytest_raises_counts(self):
        self.assertTrue(self._has(
            "def test_x():\n    with pytest.raises(ValueError):\n        f()\n"))

    def test_self_fail_counts(self):
        self.assertTrue(self._has(
            "def test_x(self):\n    try:\n        f()\n    except E:\n        self.fail('x')\n"))

    def test_assertion_in_a_nested_helper_counts(self):
        self.assertTrue(self._has(
            "def test_x():\n    def inner(v):\n        assert v\n    inner(1)\n"))

    def test_a_body_of_pass_does_not_count(self):
        self.assertFalse(self._has("def test_x():\n    pass\n"))

    def test_a_call_only_body_does_not_count(self):
        self.assertFalse(self._has("def test_x():\n    do_the_thing(1, 2)\n"))

    def test_a_local_binding_does_not_count(self):
        """The exact shape of the two defects this file was written for."""
        self.assertFalse(self._has(
            "def test_x():\n    result = build(1)\n    # TODO figure this out\n"))

    def _has(self, src: str) -> bool:
        return _has_assertion(self._first_func(src))


class AssertionFreePopulationTests(unittest.TestCase):

    def test_the_scan_actually_found_the_suite(self):
        """Guard the denominator: an empty or tiny scan would make the
        partition below pass no matter what is in the tree."""
        total, _ = _scan()
        self.assertGreater(
            total, _MIN_PLAUSIBLE_TEST_FUNCTIONS,
            f"only {total} test functions found under {_TESTS_DIR}; the scan is "
            f"broken or pointed at the wrong directory, and every other "
            f"assertion in this file is therefore vacuous",
        )

    def test_every_assertion_free_test_is_declared(self):
        _, bare = _scan()
        undeclared = sorted(bare - set(_ALLOWED_WITHOUT_ASSERTIONS))
        self.assertEqual(
            undeclared, [],
            "these test functions contain no assertion. A test that cannot fail "
            "inflates the suite count without covering anything. Add an "
            "assertion, or — if the property really is 'this did not raise' — "
            "add an entry to _ALLOWED_WITHOUT_ASSERTIONS with the reason: "
            f"{undeclared}",
        )

    def test_no_allowlist_entry_is_stale(self):
        """The other half of the partition. Without this, a fixed or deleted
        test leaves an entry behind and the list rots into a wish list."""
        _, bare = _scan()
        stale = sorted(set(_ALLOWED_WITHOUT_ASSERTIONS) - bare)
        self.assertEqual(
            stale, [],
            "these allowlist entries no longer name an assertion-free test — "
            "the test was fixed, renamed, or removed. Delete the entry: "
            f"{stale}",
        )

    def test_every_allowlist_entry_names_a_real_file(self):
        for (rel, name) in sorted(_ALLOWED_WITHOUT_ASSERTIONS):
            with self.subTest(entry=f"{rel}::{name}"):
                self.assertTrue(
                    (_TESTS_DIR / rel).is_file(),
                    f"{rel} does not exist under {_TESTS_DIR}",
                )

    def test_every_allowlist_entry_carries_a_reason(self):
        for entry, reason in sorted(_ALLOWED_WITHOUT_ASSERTIONS.items()):
            with self.subTest(entry=entry):
                self.assertGreater(
                    len(reason.strip()), 20,
                    f"{entry} needs a reason a reviewer can check, not a placeholder",
                )

    def test_the_manual_directory_hides_nothing_from_this_scan(self):
        """``tests/manual/`` holds 58 print-style ``test_*`` functions — 47 of
        them assertion-free — that pytest never collects, because the *files*
        are named ``manual_*.py`` rather than ``test_*.py``.
        This scan follows the same rule as pytest, so if a file there were ever
        renamed to ``test_*.py`` it would be collected — and it must then be
        held to the same standard rather than quietly inheriting the exemption.
        """
        manual = _TESTS_DIR / "manual"
        if not manual.is_dir():
            self.skipTest("tests/manual/ has been removed")
        collected_under_manual = sorted(p.name for p in manual.rglob("test_*.py"))
        self.assertEqual(
            collected_under_manual, [],
            "a file under tests/manual/ is now named test_*.py, so pytest "
            "collects it. Either rename it back to manual_*.py or give its "
            f"functions assertions: {collected_under_manual}",
        )
