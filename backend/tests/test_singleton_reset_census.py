"""The isolation list must not be hand-maintained.

``tests/conftest.py`` resets module-level singletons around every test.  That
list used to be a hand-written sequence of pokes, which meant a new stateful
global in ``app/`` escaped isolation silently: the suite stays green until a
collection-order change surfaces the leak, and nothing about the diff that
added the global looks wrong.

This module closes that hole by rebuilding the real set from the source with an
AST scan and asserting that conftest's two tables partition it EXACTLY:

    census == {reset by conftest} union {exempt with a written reason}

Both directions matter.  A subset check in one direction lets a new global slip
through; a subset check in the other lets a stale row rot in the table after the
global it names is renamed or deleted.
"""
import ast
import importlib
import inspect
import pathlib
from unittest.mock import MagicMock

import pytest

from tests import conftest

APP_ROOT = pathlib.Path(__file__).resolve().parent.parent / "app"

_MUTABLE_CALLS = {"dict", "list", "set", "defaultdict", "Counter", "OrderedDict"}
_CACHE_DECORATORS = {"lru_cache", "cache"}


def _stateful_kind(node) -> str | None:
    """Classify a module-level assignment's value as runtime state, or not.

    The discriminator is emptiness at the point of definition.  A constant table
    is written with its contents (``_NBA_CITIES = {"BOS": ...}``); a state
    container is written empty and filled at runtime (``_SNAPSHOT_CACHE = {}``).
    Without that distinction the scan returns 150 rows, ~90 of them static
    lookup tables, and the exemption table becomes noise nobody reads.
    """
    if node is None:
        return None
    if isinstance(node, ast.Constant) and node.value is None:
        return "None"
    if isinstance(node, ast.Dict) and not node.keys:
        return "dict"
    if isinstance(node, ast.List) and not node.elts:
        return "list"
    if isinstance(node, ast.Set) and not node.elts:
        return "set"
    if isinstance(node, ast.Call):
        target = node.func
        name = getattr(target, "id", None) or getattr(target, "attr", None)
        if name in _MUTABLE_CALLS and not node.args and not node.keywords:
            return name
    return None


def _is_cache_decorated(node) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = getattr(target, "id", None) or getattr(target, "attr", None)
        if name in _CACHE_DECORATORS:
            return True
    return False


def _census() -> dict[str, str]:
    """Map ``module.global`` -> kind for every stateful module-level name in app/.

    Reads with ``utf-8-sig``: at least one source file carries a UTF-8 BOM, which
    Python's import machinery strips but ``ast.parse`` rejects outright.  There is
    no try/except here on purpose -- a file this scanner cannot read must break
    the test, not vanish from the census.
    """
    found: dict[str, str] = {}
    for path in sorted(APP_ROOT.rglob("*.py")):
        module = path.relative_to(APP_ROOT.parent).as_posix()[:-3].replace("/", ".")
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in tree.body:
            names: list[str] = []
            if isinstance(node, ast.Assign):
                names = [
                    t.id for t in node.targets
                    if isinstance(t, ast.Name) and t.id.startswith("_")
                ]
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                target = node.target
                names = (
                    [target.id]
                    if isinstance(target, ast.Name) and target.id.startswith("_")
                    else []
                )
                value = node.value
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _is_cache_decorated(node):
                    found[f"{module}.{node.name}"] = "lru_cache"
                continue
            else:
                continue

            kind = _stateful_kind(value)
            if kind:
                for name in names:
                    found[f"{module}.{name}"] = kind
    return found


def _reset_names() -> set[str]:
    return {f"{mod}.{attr}" for mod, attr, _ in conftest._SINGLETON_RESETS}


class TestCensusPartition:
    def test_scanner_reads_the_file_with_a_utf8_bom(self):
        """Pins the utf-8-sig read.  app/services/llm_gateway_service.py starts
        with U+FEFF; decoding it as plain utf-8 makes ast.parse raise, and a
        scanner that swallowed the error would drop the whole file from the
        census -- along with the _client_cache global asserted on below."""
        census = _census()
        assert "app.services.llm_gateway_service._client_cache" in census

    def test_every_stateful_global_is_reset_or_exempt(self):
        census = set(_census())
        accounted = _reset_names() | set(conftest._RESET_EXEMPT)
        escaped = sorted(census - accounted)
        assert not escaped, (
            "these module-level globals in app/ hold runtime state that no test "
            "teardown clears, so they leak across tests:\n  "
            + "\n  ".join(escaped)
            + "\nAdd each to conftest._SINGLETON_RESETS, or to "
            "conftest._RESET_EXEMPT with the reason it is safe to keep."
        )

    def test_no_table_row_names_a_global_that_is_gone(self):
        census = set(_census())
        stale = sorted((_reset_names() | set(conftest._RESET_EXEMPT)) - census)
        assert not stale, (
            "conftest names these globals but the AST census no longer finds "
            "them -- renamed, deleted, or no longer initialised empty:\n  "
            + "\n  ".join(stale)
        )

    def test_a_global_is_never_both_reset_and_exempt(self):
        overlap = sorted(_reset_names() & set(conftest._RESET_EXEMPT))
        assert not overlap, f"listed in both tables: {overlap}"

    def test_every_exemption_carries_a_real_reason(self):
        thin = sorted(
            name for name, reason in conftest._RESET_EXEMPT.items()
            if len(reason.strip()) < 20
        )
        assert not thin, (
            "an exemption without a reason is indistinguishable from an "
            f"oversight: {thin}"
        )


class TestResetRowsResolve:
    """A row that names a missing module, global, or helper is a dead reset.

    ``_reset_all_singletons`` skips modules absent from ``sys.modules``, so a
    typo would otherwise never raise -- it would just quietly reset nothing.
    """

    @pytest.mark.parametrize(
        "row", conftest._SINGLETON_RESETS,
        ids=[f"{m}.{a}" for m, a, _ in conftest._SINGLETON_RESETS],
    )
    def test_row_resolves(self, row):
        module_path, attr, action = row
        module = importlib.import_module(module_path)
        assert hasattr(module, attr), f"{module_path} has no {attr}"

        if action == "none":
            return
        if action == "clear":
            assert callable(getattr(module, attr).clear)
        elif action == "cache_clear":
            wrapped = getattr(module, attr)
            assert hasattr(wrapped, "cache_clear") and hasattr(wrapped, "cache_info"), (
                f"{module_path}.{attr} is not a functools cache, so the "
                "cache_clear row does nothing"
            )
        else:
            helper = getattr(module, action, None)
            assert callable(helper), (
                f"{module_path} has no zero-arg reset helper named {action}"
            )


class TestResetActuallyClears:
    """Dirty each target, run the reset, assert it is clean.

    Asserting on an already-clean global proves nothing, so every case below
    writes a sentinel first.  The split is by what the global IS, not by the
    action declared for it: a functools cache holds its state inside the function
    object, so rebinding the name to a sentinel would test nothing at all.
    """

    @staticmethod
    def _is_cache(value) -> bool:
        return hasattr(value, "cache_clear") and hasattr(value, "cache_info")

    def test_container_and_none_rows_are_cleared(self):
        dirtied: list[tuple[object, str]] = []
        for module_path, attr, _action in conftest._SINGLETON_RESETS:
            module = importlib.import_module(module_path)
            value = getattr(module, attr)
            if self._is_cache(value):
                continue
            if isinstance(value, dict):
                value["__census_sentinel__"] = "dirty"
            elif isinstance(value, set):
                value.add("__census_sentinel__")
            elif isinstance(value, list):
                value.append("__census_sentinel__")
            else:
                # A MagicMock, not a string: several reset helpers call methods on
                # the object they are discarding (close_kernel_session() disposes
                # the engine), and a sentinel that cannot absorb those calls turns
                # this test into a crash instead of a check.
                setattr(module, attr, MagicMock())
            dirtied.append((module, attr))

        assert dirtied, "nothing was dirtied, so this test proves nothing"
        conftest._reset_all_singletons()

        still_dirty = [
            f"{module.__name__}.{attr}"
            for module, attr in dirtied
            if getattr(module, attr)
        ]
        assert not still_dirty, f"reset left these dirty: {still_dirty}"

    def test_warmed_caches_are_dropped(self):
        """Warm every file cache in the table, then check currsize is back to 0."""
        warmed = []
        for module_path, attr, _action in conftest._SINGLETON_RESETS:
            wrapped = getattr(importlib.import_module(module_path), attr)
            if not self._is_cache(wrapped):
                continue
            arity = len(inspect.signature(wrapped).parameters)
            wrapped(*["__census_missing_path__"] * arity)
            assert wrapped.cache_info().currsize > 0, (
                f"{module_path}.{attr} did not cache that call, so this test "
                "cannot prove the reset works"
            )
            warmed.append((module_path, attr, wrapped))

        assert warmed, "no cache was warmed, so this test proves nothing"
        conftest._reset_all_singletons()

        left = [
            f"{m}.{a}" for m, a, wrapped in warmed
            if wrapped.cache_info().currsize > 0
        ]
        assert not left, f"reset left these caches warm: {left}"


class TestConfirmedLeaks:
    """The two globals that were provably dirty for the whole suite."""

    def test_reset_nulls_both_prediction_db_globals(self):
        """_engine was nulled and _SessionLocal was not, in the same module.

        get_prediction_session() guards only on _SessionLocal, so the surviving
        sessionmaker kept handing out sessions bound to the engine conftest had
        just discarded.  A leak probe over the full suite found _engine dirty
        after 10 teardowns and _SessionLocal dirty after all 4584.
        """
        from app.utils import prediction_db

        prediction_db._engine = "dirty-engine"
        prediction_db._SessionLocal = "dirty-sessionmaker"

        conftest._reset_all_singletons()

        assert prediction_db._engine is None
        assert prediction_db._SessionLocal is None

    def test_reset_drops_the_cached_kernel_object(self):
        """The kernel OBJECT singleton is not the kernel DB singleton.

        conftest called close_kernel_db() every teardown while the kernel cached
        on _get_kernel._instance survived, holding a FactorRegistry bound to the
        session factory that had just been torn down.  It is a function
        attribute, not a module global, so the AST census cannot see it and it
        gets its own reset and its own test.
        """
        from app.api.routes import predictions

        predictions._get_kernel._instance = "dirty-kernel"

        conftest._reset_all_singletons()

        assert not hasattr(predictions._get_kernel, "_instance")
