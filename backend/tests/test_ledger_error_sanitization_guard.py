"""Raw exception text must never reach the loop_runs ``error`` column.

Why this is a public surface and not an internal detail: the column is returned
verbatim to any caller of ``GET /quality-metrics/anomalies``, and that route
carries **zero auth dependencies** (``app/api/routes/quality_metrics.py``). It is
also what ``/api/health`` derives its 503 verdict from, so a leak here is a leak
in the thing operators read first.

The only thing standing between a bare ``str(exc)`` and that column is
``_finish_run`` (``app/core/scheduler.py``), which overwrites its argument with
``f"Scheduler job failed: {exc_type}"`` -- **but only when the caller forwards
the exception through ``exc=``**. So "did you forward ``exc=``" is a property of
the *call site*, and a property of a call site cannot be pinned by testing the
behaviour of the handful of jobs that happen to have tests (six files do:
``test_scheduler.py``, ``test_scheduler_ledger_alarm_gate.py``,
``test_optimization_cleanup_job.py``, ``test_drift_alert_job.py``,
``test_scheduler_live_update_ledger.py``, ``test_sentry_integration.py``).

``error=str(exc)`` appears 22 times today and every one of them forwards
``exc=`` -- which is precisely why the defect would be invisible: the 23rd one
would ship, and no existing test names it. This scan reads the source instead.

Documented coupling (§4.3): if ``_finish_run``'s override is deleted, the six
behaviour tests above go red -- not this file. This file pins the *other*
direction: that every call site keeps holding up its end of that bargain.

The second half of this module answers the follow-up that this one cannot:
"what about ``error=result.get("error")``, which forwards no exception at all?"
That shape is the one that actually leaked. It needs a different definition --
of "raw exception text" and of where it may come from -- and gets its own
invariant, constants and tests further down.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import NamedTuple

from app.core import scheduler

_APP_DIR = Path(scheduler.__file__).resolve().parents[1]

# The two functions that write the `error` column: `_finish_run` is the
# sanitizing wrapper in `app/core/scheduler.py`; `loop_run_store.finish_run` is
# the raw writer every other module calls. Matched by callee name so both the
# attribute form (`loop_run_store.finish_run(...)`) and the bare form
# (`_finish_run(...)`) are covered -- the ledger's three write helpers
# (`_finish_audit_run`, `_finish_consistency_repair_audit_run`, `_finish`) take
# `(run_id, result)` and carry no `error=` keyword, so excluding them loses no
# site and avoids matching the unrelated query helper `_finished_unscored_matches`.
_WRITERS = frozenset({"finish_run", "_finish_run"})

# Reads that mention the exception object but print only its *class*.
_SAFE_EXCEPTION_WRAPPERS = frozenset({"type"})

# `_finish_run` only sanitizes on this status, so a call that forwards `exc=` to
# hide a raw `str(exc)` while computing the status at runtime can still write the
# raw text on the success path.
_SANITIZED_STATUS = "failed"

# Exception attributes that yield a number, not a message. Part of the definition
# of "raw exception text" -- `f"HTTP {exc.code}"` is a status code -- and not an
# exemption for a site. Leaving them out taints both `validate_*_pipeline`
# functions, and `events.py:812` through them: three false positives from one
# missing clause, which is how a guard turns into an exemption list.
_NUMERIC_EXCEPTION_ATTRS = frozenset({"code", "status_code", "errno"})

# ---------------------------------------------------------------------------
# Second invariant: where the value that reaches the column came from.
#
# `_finish_run` sanitizes an exception it is *given*. The ledger's audit sinks --
# `_finish_audit_run`, `_finish_consistency_repair_audit_run`, `_finish` -- take
# `(run_id, result)` and forward no `exc=` at all, so they depend entirely on
# their caller having put a safe token in `result`. A caller that fills that key
# by copying another mapping's key moves the string beyond the reach of every
# sanitizer: the exception is not in scope where the copy happens either.
#
# That is the whole distinction, and it is why the two lookalike sites below need
# no named exemption to tell apart:
#
#   events.py:812   error=str(result.get("error") or "") ...
#                   producer: validate_world_cup_api_football_pipeline, whose
#                   "error" values are literals and class names     -> allowed
#
#   post_match:212  "error": sync_result.get("error")
#                   producer: sync_world_cup_fixtures, whose "error" is `str(e)`
#                   -> a leak (fixed in this change; see §53.32)
# ---------------------------------------------------------------------------

# Names that end up calling `loop_run_store.finish_run`.
_LEDGER_WRITERS = frozenset({
    "finish_run",
    "_finish_run",
    "_finish_audit_run",
    "_finish_consistency_repair_audit_run",
    "_finish",
})

# The audit sinks. Spelled out so the keys they consume can be *read off them*
# rather than hardcoded here -- a sink added tomorrow changes the population
# instead of silently escaping it.
_AUDIT_SINKS = frozenset({
    "_finish_audit_run",
    "_finish_consistency_repair_audit_run",
    "_finish",
})

# The one site that inherits a sink key from a producer that can emit raw
# exception text. Recorded, not exempted: the property is still evaluated below
# and this tuple is compared exactly, so a *second* relay fails, and so does the
# removal of this one.
#
# Why it is allowed to stand: `world_cup_post_match_backfill_service` relays the
# sync failure reason into the ledger verbatim, and two behaviour tests pin that
# text -- `test_sync_error_short_circuits_without_scoring` pins the ledger row,
# `test_post_match_backfill_does_not_expose_or_persist_provider_error_body` pins
# the row *and* that the provider's response body is excluded. The safety of the
# relay is therefore exactly the safety of `sync_world_cup_fixtures`, whose
# `except Exception` branch is where `str(e)` enters -- and that branch is pinned
# in turn by `test_api_football_error_returns_error`.
#
# Whether the ledger should keep carrying the producer's free-text reason is a
# contract question (§53.32), not something this file decides. What it does
# decide is that the coupling cannot grow unnoticed. The weight on the other
# side is recorded there: five tests in `test_world_cup_analytics_routes.py`
# (`..._failure_is_safe...` / `..._hides_and_does_not_persist...`, at :141, :187,
# :912, :1013, :1070, :1173) require the column to hold a declared token, with
# `f"...: {type(exc).__name__}"` as the repo's own idiom for the class name, and
# they list a URL host (`"upstream.example"`) among the fragments that must not
# reach the ledger or `/quality-metrics/anomalies`.
_RECORDED_INHERITING_SITES = (
    "services/world_cup_post_match_backfill_service.py (run_post_match_backfill): "
    "'error' inherits 'error' from sync_world_cup_fixtures",
)


class Analysis(NamedTuple):
    """What one module contributes, so the tests can assert populations."""

    writer_calls: int
    calls_with_error: int
    raw_exception_sites: tuple[int, ...]
    missing_exc_forward: tuple[str, ...]
    non_literal_failed: tuple[str, ...]


def _callee_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    """id(child) -> parent, because `ast.walk` yields the same node objects."""
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _message_reads(node: ast.expr, names: set[str], parents: dict[int, ast.AST]) -> list[str]:
    """Exception names read in a way that can carry the *message*.

    ``type(exc)`` / ``exc.__class__.__name__`` print the class, and ``exc.code`` /
    ``exc.status_code`` / ``exc.errno`` print a number, so none of them is a
    message read. Everything else that names the exception -- ``str(exc)``,
    ``repr(exc)``, ``f"{exc}"``, ``exc.args`` -- is.
    """
    reads: list[str] = []
    for child in ast.walk(node):
        if not (isinstance(child, ast.Name) and child.id in names):
            continue
        parent = parents.get(id(child))
        if isinstance(parent, ast.Call) and _callee_name(parent) in _SAFE_EXCEPTION_WRAPPERS:
            continue
        if isinstance(parent, ast.Attribute) and parent.value is child:
            if parent.attr == "__class__" or parent.attr in _NUMERIC_EXCEPTION_ATTRS:
                continue
        reads.append(child.id)
    return reads


def _names_carrying_the_message(handler: ast.ExceptHandler, parents: dict[int, ast.AST]) -> set[str]:
    """Local names in this handler that hold message-bearing exception text.

    Covers the indirect spelling (``error = str(exc)`` then ``error=error``),
    which a purely textual search for ``str(exc)`` would miss; the fixpoint loop
    follows chains of such assignments.
    """
    if not handler.name:
        return set()
    bound = {handler.name}
    carried: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(handler):
            target: str | None = None
            value: ast.expr | None = None
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                target, value = node.targets[0].id, node.value
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.value is not None
            ):
                target, value = node.target.id, node.value
            if target is None or value is None or target in carried:
                continue
            if _message_reads(value, bound | carried, parents):
                carried.add(target)
                changed = True
    return carried


def _handler_scopes(tree: ast.AST, parents: dict[int, ast.AST]) -> list[tuple[int, int, set[str]]]:
    """(first line, last line, names whose message could be read) per handler."""
    scopes: list[tuple[int, int, set[str]]] = []
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler):
            continue
        names = _names_carrying_the_message(handler, parents)
        if handler.name:
            names.add(handler.name)
        last = max(
            (getattr(node, "lineno", handler.lineno) for node in ast.walk(handler)),
            default=handler.lineno,
        )
        scopes.append((handler.lineno, last, names))
    return scopes


def _status_text(call: ast.Call) -> str:
    keyword = _keyword(call, "status")
    if keyword is not None:
        return ast.unparse(keyword)
    if len(call.args) >= 2:
        return ast.unparse(call.args[1])
    return "<absent>"


def analyse_module(source: str, filename: str = "<scan>") -> Analysis:
    """Scan one module's source. Pure function of the text, so it can be tested."""
    tree = ast.parse(source, filename=filename)
    parents = _parent_map(tree)
    scopes = _handler_scopes(tree, parents)

    writer_calls = 0
    calls_with_error = 0
    raw_sites: list[int] = []
    missing_forward: list[str] = []
    non_literal: list[str] = []

    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or _callee_name(call) not in _WRITERS:
            continue
        writer_calls += 1
        error_arg = _keyword(call, "error")
        if error_arg is None:
            continue
        calls_with_error += 1

        in_scope: set[str] = set()
        for first, last, names in scopes:
            if first <= call.lineno <= last:
                in_scope |= names
        if not in_scope:
            continue
        reads = _message_reads(error_arg, in_scope, parents)
        if not reads:
            continue

        raw_sites.append(call.lineno)
        forwarded = _keyword(call, "exc") is not None
        if not forwarded:
            missing_forward.append(
                f"{filename}:{call.lineno}: error= reads {sorted(set(reads))} without "
                f"forwarding exc=, so _finish_run's override never fires"
            )
        elif _status_text(call) != repr(_SANITIZED_STATUS):
            non_literal.append(
                f"{filename}:{call.lineno}: relies on the override (forwards exc=) but "
                f"passes status={_status_text(call)} instead of a literal "
                f"{_SANITIZED_STATUS!r}"
            )

    return Analysis(
        writer_calls=writer_calls,
        calls_with_error=calls_with_error,
        raw_exception_sites=tuple(raw_sites),
        missing_exc_forward=tuple(missing_forward),
        non_literal_failed=tuple(non_literal),
    )


def analyse_app() -> dict[str, Analysis]:
    analysed: dict[str, Analysis] = {}
    for path in sorted(_APP_DIR.rglob("*.py")):
        # utf-8-sig, not utf-8: a BOM is legal Python source that `ast.parse`
        # rejects, and a file that fails to parse by accident reads as "clean".
        analysed[path.relative_to(_APP_DIR).as_posix()] = analyse_module(
            path.read_text(encoding="utf-8-sig"), filename=path.name
        )
    return analysed


class Module(NamedTuple):
    """A parsed module. The tree and the parent map must come from one parse."""

    rel: str
    tree: ast.AST
    parents: dict[int, ast.AST]


class ProvenanceAnalysis(NamedTuple):
    """What one run over the whole tree contributes."""

    writer_functions: int
    sink_keys: tuple[str, ...]
    sites: tuple[str, ...]
    producers: tuple[str, ...]
    inheriting: tuple[str, ...]
    offenders: tuple[str, ...]


def _parse_modules(sources: dict[str, str]) -> list[Module]:
    """Parse each module exactly once.

    `_parent_map` is keyed by `id(node)`, so the parents map and the tree have to
    come from the *same* parse. Parsing twice does not raise -- every lookup
    returns None, which silently disables the `type(exc)` / `.__class__` / `.code`
    exclusions and turns three safe sites into offenders. It is pinned by
    ``test_it_does_not_flag_a_class_name_only_read_under_a_sink_key``.
    """
    modules: list[Module] = []
    for rel, text in sources.items():
        tree = ast.parse(text, filename=rel)
        modules.append(Module(rel, tree, _parent_map(tree)))
    return modules


def _functions(tree: ast.AST) -> list[ast.AST]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _own_nodes(container: ast.AST) -> list[ast.AST]:
    """Nodes in a container's own body -- never descending into nested defs.

    `run_post_match_backfill` holds a nested `finish(result)` closure that calls
    the sink. Walking through it would read the closure's `result` parameter as if
    it were the outer function's, which is precisely the binding under test.
    """
    out: list[ast.AST] = []
    stack = list(getattr(container, "body", []))
    while stack:
        node = stack.pop()
        out.append(node)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)
    return out


def _params(func: ast.AST) -> set[str]:
    args = func.args  # type: ignore[attr-defined]
    names = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    return names


def _writes_the_ledger(func: ast.AST) -> bool:
    """Does running this function reach `loop_run_store.finish_run`?

    Nested defs count (`ast.walk` descends): in
    `world_cup_post_match_backfill_service` the sink is reached through a local
    `finish` closure, which is not a module-level name anything could point at.
    """
    return any(
        isinstance(node, ast.Call) and _callee_name(node) in _LEDGER_WRITERS
        for node in ast.walk(func)
    )


def _value_positions(expr: ast.expr) -> list[ast.expr]:
    """The sub-expressions that can supply the bound value.

    `result.get("reason") if result.get("status") == "error" else None` supplies
    its value from the *body*. Walking the whole expression also reads the test,
    which is how `"status"` -- a condition, never a value -- starts looking like a
    key the sinks consume.
    """
    if isinstance(expr, ast.IfExp):
        return _value_positions(expr.body) + _value_positions(expr.orelse)
    if isinstance(expr, ast.BoolOp):
        out: list[ast.expr] = []
        for operand in expr.values:
            out += _value_positions(operand)
        return out
    return [expr]


def _mapping_reads(expr: ast.expr) -> list[tuple[str, str]]:
    """Every `m.get(K)` / `m[K]` with a constant string K, as (source, key)."""
    found: list[tuple[str, str]] = []
    for position in _value_positions(expr):
        for node in ast.walk(position):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "get" and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        found.append((ast.unparse(func.value), first.value))
            elif isinstance(node, ast.Subscript):
                sl = node.slice
                if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                    found.append((ast.unparse(node.value), sl.value))
    return found


def _value_message_reads(
    expr: ast.expr, names: set[str], parents: dict[int, ast.AST]
) -> list[str]:
    """`_message_reads`, restricted to the positions that can supply the value."""
    reads: list[str] = []
    for position in _value_positions(expr):
        reads += _message_reads(position, names, parents)
    return reads


def _subscript_binding(
    target: ast.expr, value: ast.expr, line: int
) -> list[tuple[str, ast.expr, int]]:
    if (
        isinstance(target, ast.Subscript)
        and isinstance(target.slice, ast.Constant)
        and isinstance(target.slice.value, str)
    ):
        return [(target.slice.value, value, line)]
    return []


def _bindings(func: ast.AST) -> list[tuple[str, ast.expr, int]]:
    """A function's own (key, value, line) for dict entries and `x[key] = value`.

    Both spellings matter: `world_cup_api_football_source` builds its failures with
    `result["error"] = ...`, and a detector that only saw dict literals would miss
    that producer entirely.
    """
    out: list[tuple[str, ast.expr, int]] = []
    for node in _own_nodes(func):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    out.append((key.value, value, node.lineno))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                out += _subscript_binding(target, node.value, node.lineno)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            out += _subscript_binding(node.target, node.value, node.lineno)
    return out


def _handler_names_at(module: Module, func: ast.AST, line: int) -> set[str]:
    """Exception names whose message could be read at `line`, inside `func`."""
    names: set[str] = set()
    for handler in ast.walk(func):
        if not isinstance(handler, ast.ExceptHandler):
            continue
        carried = _names_carrying_the_message(handler, module.parents)
        if handler.name:
            carried.add(handler.name)
        last = max(
            (getattr(n, "lineno", handler.lineno) for n in ast.walk(handler)),
            default=handler.lineno,
        )
        if handler.lineno <= line <= last:
            names |= carried
    return names


def _local_producer(func: ast.AST, name: str) -> str | None:
    """If `name` is assigned from a call in this function, the callee's name.

    `await` is unwrapped: `result = await asyncio.to_thread(f)` is a call, and
    reading it as an unknown producer would let `events.py:812` pass by accident
    rather than by analysis.
    """
    for node in _own_nodes(func):
        target: str | None = None
        value: ast.expr | None = None
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target, value = node.targets[0].id, node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            target, value = node.target.id, node.value
        if target != name or value is None:
            continue
        if isinstance(value, ast.Await):
            value = value.value
        if isinstance(value, ast.Call):
            return _callee_name(value)
    return None


def _sink_keys(modules: list[Module]) -> set[str]:
    """Keys the audit sinks copy into the column -- read off the sinks themselves.

    The sinks all spell it `error=result.get(<key>) if ... else None`, and they
    read `"error"` three times and `"reason"` once. Deriving the set instead of
    writing `{"error"}` down is what makes `_finish`'s `"reason"` show up at all.
    """
    keys: set[str] = set()
    for module in modules:
        for func in _functions(module.tree):
            if func.name not in _AUDIT_SINKS:
                continue
            if not any(
                isinstance(node, ast.Call) and _callee_name(node) == "finish_run"
                for node in _own_nodes(func)
            ):
                continue
            params = _params(func)
            for node in _own_nodes(func):
                if not (isinstance(node, ast.Call) and _callee_name(node) == "finish_run"):
                    continue
                argument = _keyword(node, "error")
                if argument is None:
                    continue
                keys.update(k for src, k in _mapping_reads(argument) if src in params)
    return keys


def _producer_taint(modules: list[Module]) -> dict[str, set[str]]:
    """Function name -> keys under which it can emit raw exception text.

    Two rules, iterated to a fixpoint: a function emits K when it binds K to a
    message read, and it emits K when it binds K to a mapping read whose producer
    emits the key being read. Without the second rule `validate_world_cup_api_football_pipeline`
    would look clean while actually relaying `conn.get("error")`.
    """
    funcs = [(module, func) for module in modules for func in _functions(module.tree)]
    taint: dict[str, set[str]] = {func.name: set() for _, func in funcs}

    for module, func in funcs:
        for key, value, line in _bindings(func):
            if _value_message_reads(value, _handler_names_at(module, func, line), module.parents):
                taint[func.name].add(key)

    changed = True
    while changed:
        changed = False
        for module, func in funcs:
            params = _params(func)
            for key, value, _line in _bindings(func):
                for source, read_key in _mapping_reads(value):
                    if source in params:
                        continue
                    producer = _local_producer(func, source)
                    if not producer or read_key not in taint.get(producer, set()):
                        continue
                    if key not in taint[func.name]:
                        taint[func.name].add(key)
                        changed = True
    return taint


def _provenance_offenders(
    modules: list[Module], keys: set[str], taint: dict[str, set[str]]
) -> list[tuple[str, str]]:
    """(stable identifier, message) per site that binds a tainted sink key.

    The stable identifier carries no line number: it names the file, the function,
    the key and the producer, so a recorded site can be compared across edits.
    """
    offenders: list[tuple[str, str]] = []
    for module in modules:
        for func in _functions(module.tree):
            if not _writes_the_ledger(func):
                continue
            params = _params(func)
            for key, value, line in _bindings(func):
                if key not in keys:
                    continue
                if _value_message_reads(
                    value, _handler_names_at(module, func, line), module.parents
                ):
                    stable = (
                        f"{module.rel} ({func.name}): {key!r} is built from a caught "
                        f"exception's message"
                    )
                    offenders.append((stable, f"{module.rel}:{line} {stable}"))
                    continue
                for source, read_key in _mapping_reads(value):
                    if source in params:
                        continue
                    producer = _local_producer(func, source)
                    if producer and read_key in taint.get(producer, set()):
                        stable = (
                            f"{module.rel} ({func.name}): {key!r} inherits {read_key!r} "
                            f"from {producer}"
                        )
                        offenders.append(
                            (
                                stable,
                                f"{module.rel}:{line} {stable} (relayed through {source}, "
                                f"which can emit raw exception text under {read_key!r})",
                            )
                        )
            for node in _own_nodes(func):
                if not isinstance(node, ast.Call):
                    continue
                argument = _keyword(node, "error")
                if argument is None:
                    continue
                for source, read_key in _mapping_reads(argument):
                    # Reading one's own parameter is the sinks' declared contract:
                    # `_finish_audit_run(run_id, result)` copies from `result`
                    # because its caller promises a safe token. The promise is
                    # checked where the caller builds the dict.
                    if source in params:
                        continue
                    producer = _local_producer(func, source)
                    if producer and read_key in taint.get(producer, set()):
                        stable = (
                            f"{module.rel} ({func.name}): error= inherits {read_key!r} "
                            f"from {producer}"
                        )
                        offenders.append(
                            (stable, f"{module.rel}:{node.lineno} {stable}")
                        )
    return offenders


def analyse_provenance(sources: dict[str, str]) -> ProvenanceAnalysis:
    """Scan a whole tree of modules. Pure function of the texts, so it is testable."""
    modules = _parse_modules(sources)
    keys = _sink_keys(modules)
    taint = _producer_taint(modules)

    writer_functions = 0
    sites: list[str] = []
    for module in modules:
        for func in _functions(module.tree):
            if not _writes_the_ledger(func):
                continue
            writer_functions += 1
            for key, _value, line in _bindings(func):
                if key in keys:
                    sites.append(f"{module.rel}:{line} ({func.name}) {key!r}")
            for node in _own_nodes(func):
                if isinstance(node, ast.Call) and _keyword(node, "error") is not None:
                    sites.append(f"{module.rel}:{node.lineno} ({func.name}) error=")

    offenders = _provenance_offenders(modules, keys, taint)
    return ProvenanceAnalysis(
        writer_functions=writer_functions,
        sink_keys=tuple(sorted(keys)),
        sites=tuple(sites),
        producers=tuple(sorted(name for name, emitted in taint.items() if emitted)),
        inheriting=tuple(stable for stable, _message in offenders),
        offenders=tuple(message for _stable, message in offenders),
    )


def analyse_app_provenance() -> ProvenanceAnalysis:
    return analyse_provenance({
        path.relative_to(_APP_DIR).as_posix(): path.read_text(encoding="utf-8-sig")
        for path in sorted(_APP_DIR.rglob("*.py"))
    })


class LedgerErrorMessageScanTests(unittest.TestCase):
    """The invariant, read from the source of `app/`."""

    def test_the_scan_reaches_the_write_surface(self):
        """Guard the guard: an empty or truncated scan passes everything below."""
        analysed = analyse_app()
        self.assertGreaterEqual(len(analysed), 300, len(analysed))
        writer_calls = sum(item.writer_calls for item in analysed.values())
        self.assertGreaterEqual(writer_calls, 55, writer_calls)
        with_error = sum(item.calls_with_error for item in analysed.values())
        self.assertGreaterEqual(with_error, 35, with_error)
        raw = sum(len(item.raw_exception_sites) for item in analysed.values())
        self.assertGreaterEqual(raw, 20, raw)

    def test_no_call_reads_an_exception_message_without_forwarding_exc(self):
        offenders = [
            message
            for item in analyse_app().values()
            for message in item.missing_exc_forward
        ]
        self.assertEqual(offenders, [])

    def test_a_call_relying_on_the_override_passes_a_literal_failed_status(self):
        offenders = [
            message
            for item in analyse_app().values()
            for message in item.non_literal_failed
        ]
        self.assertEqual(offenders, [])


class TheScanIsNotVacuousTests(unittest.TestCase):
    """Each shape the scan claims to understand, on a snippet that isolates it."""

    _HEAD = 'def job(run_id):\n    try:\n        work()\n    except Exception as exc:\n'

    def _snippet(self, body: str) -> str:
        return self._HEAD + "".join(f"        {line}\n" for line in body.strip("\n").splitlines())

    def test_it_flags_an_unforwarded_message_read(self):
        result = analyse_module(
            self._snippet('_finish_run(run_id, "failed", error=str(exc))'), "synthetic.py"
        )
        self.assertEqual(result.raw_exception_sites, (5,))
        self.assertEqual(len(result.missing_exc_forward), 1)
        self.assertIn("without forwarding exc=", result.missing_exc_forward[0])

    def test_it_accepts_the_same_call_once_exc_is_forwarded(self):
        result = analyse_module(
            self._snippet('_finish_run(run_id, "failed", error=str(exc), exc=exc)'),
            "synthetic.py",
        )
        self.assertEqual(result.raw_exception_sites, (5,))
        self.assertEqual(result.missing_exc_forward, ())
        self.assertEqual(result.non_literal_failed, ())

    def test_it_flags_a_forwarded_call_whose_status_is_not_literal_failed(self):
        result = analyse_module(
            self._snippet('_finish_run(run_id, status, error=str(exc), exc=exc)'),
            "synthetic.py",
        )
        self.assertEqual(result.missing_exc_forward, ())
        self.assertEqual(len(result.non_literal_failed), 1)

    def test_it_does_not_flag_a_class_name_only_read(self):
        """`type(exc).__name__` prints the class, so it is not a message leak."""
        result = analyse_module(
            self._snippet(
                'error = f"boom: {type(exc).__name__}"\n'
                '_finish_run(run_id, "failed", error=error)'
            ),
            "synthetic.py",
        )
        self.assertEqual(result.raw_exception_sites, ())
        self.assertEqual(result.missing_exc_forward, ())

    def test_it_does_not_flag_a_class_only_read_via_dunder_class(self):
        result = analyse_module(
            self._snippet(
                'error = "boom: " + exc.__class__.__name__\n'
                '_finish_run(run_id, "failed", error=error)'
            ),
            "synthetic.py",
        )
        self.assertEqual(result.raw_exception_sites, ())

    def test_it_flags_a_local_name_that_carries_the_message(self):
        """The indirect spelling a textual search for `str(exc)` would miss."""
        result = analyse_module(
            self._snippet(
                'error = f"boom: {exc}"\n_finish_run(run_id, "failed", error=error)'
            ),
            "synthetic.py",
        )
        self.assertEqual(result.raw_exception_sites, (6,))
        self.assertEqual(len(result.missing_exc_forward), 1)

    def test_it_ignores_an_error_argument_with_no_exception_in_scope(self):
        source = (
            "def job(run_id, error, result):\n"
            '    loop_run_store.finish_run(run_id, "failed", error=error, result=result)\n'
        )
        result = analyse_module(source, "synthetic.py")
        self.assertEqual(result.writer_calls, 1)
        self.assertEqual(result.calls_with_error, 1)
        self.assertEqual(result.raw_exception_sites, ())


class LedgerErrorProvenanceTests(unittest.TestCase):
    """The column must hold a value its writer can account for.

    Deliberately not a restatement of `LedgerErrorMessageScanTests`: that class
    pins the call sites that *read* a caught message (today 22, all forwarding
    `exc=`, so all sanitized). This one pins the sites that *inherit* a string
    from a scope the sanitizer cannot reach, where no `exc=` is even available to
    forward.
    """

    def test_the_scan_reaches_the_writers_that_bind_a_sink_key(self):
        """Guard the guard: a truncated scan passes everything below."""
        result = analyse_app_provenance()
        self.assertGreaterEqual(result.writer_functions, 30, result.writer_functions)
        self.assertGreaterEqual(len(result.sites), 40, len(result.sites))
        self.assertGreaterEqual(len(result.producers), 8, result.producers)

    def test_the_sink_keys_are_derived_and_not_widened_by_a_guard_condition(self):
        """`"status"` is read in a test, never bound; only the value position counts.

        An exact assertion on purpose: growth here means either a sink started
        consuming a new key (extend the reader) or the derivation started taking
        the condition's reads too (fix `_value_positions`).
        """
        self.assertEqual(analyse_app_provenance().sink_keys, ("error", "reason"))

    def test_the_inheriting_sites_are_exactly_the_recorded_ones(self):
        """A ratchet. Red when a second relay appears -- and red when this one goes.

        The property is evaluated, not waived: the set is compared exactly. The
        recorded entry and the reason it stands are in
        `_RECORDED_INHERITING_SITES`; the contract question behind it is §53.32.
        """
        result = analyse_app_provenance()
        self.assertEqual(result.inheriting, _RECORDED_INHERITING_SITES)
        self.assertEqual(len(result.offenders), len(result.inheriting))


class TheProvenanceScanIsNotVacuousTests(unittest.TestCase):
    """Each clause, on a synthetic tree that isolates it."""

    _SINK = (
        "def _finish_audit_run(run_id, result):\n"
        '    status = "failed" if result.get("status") == "error" else "success"\n'
        "    loop_run_store.finish_run(\n"
        "        run_id,\n"
        "        status,\n"
        "        result=result,\n"
        '        error=result.get("error") if status == "failed" else None,\n'
        "    )\n"
    )
    _RAW_PRODUCER = (
        "def sync_upstream(source):\n"
        "    try:\n"
        "        return fetch(source)\n"
        "    except Exception as e:\n"
        '        return {"status": "error", "error": str(e)}\n'
    )
    _SAFE_PRODUCER = (
        "def validate_upstream():\n"
        "    try:\n"
        "        return fetch()\n"
        "    except Exception as exc:\n"
        '        return {"status": "error", "error": f"HTTP {exc.code}"}\n'
    )

    def _caller(self, error_value: str) -> str:
        return (
            'def run_job(source="s"):\n'
            "    def finish(result):\n"
            "        _finish_audit_run(run_id, result)\n"
            "        return result\n"
            "\n"
            "    sync_result = sync_upstream(source=source)\n"
            '    return finish({"status": "error", "error": ' + error_value + "})\n"
        )

    def _run(self, caller: str, producer: str) -> ProvenanceAnalysis:
        return analyse_provenance({
            "sink.py": self._SINK,
            "producer.py": producer,
            "caller.py": caller,
        })

    def test_it_flags_inheriting_a_sink_key_from_a_tainted_producer(self):
        result = self._run(self._caller('sync_result.get("error")'), self._RAW_PRODUCER)
        self.assertEqual(len(result.offenders), 1, result.offenders)
        self.assertIn("inherits 'error'", result.offenders[0])
        # The stable identifier is the thing `_RECORDED_INHERITING_SITES` pins, so
        # its shape is asserted here: line-free, file + function + key + producer.
        self.assertEqual(
            result.inheriting,
            ("caller.py (run_job): 'error' inherits 'error' from sync_upstream",),
        )

    def test_it_accepts_a_declared_token(self):
        result = self._run(self._caller('"upstream_sync_failed"'), self._RAW_PRODUCER)
        self.assertEqual(result.offenders, ())

    def test_it_accepts_the_same_read_when_the_producer_emits_no_raw_text(self):
        """The `events.py:812` shape: a local read, and a producer that is clean."""
        result = self._run(self._caller('sync_result.get("error")'), self._SAFE_PRODUCER)
        self.assertEqual(result.offenders, ())

    def test_it_flags_a_message_read_bound_to_a_sink_key(self):
        result = analyse_provenance({
            "job.py": self._SINK
            + "\n\ndef run_job(run_id):\n"
            "    try:\n"
            "        work()\n"
            "    except Exception as exc:\n"
            '        _finish_audit_run(run_id, {"status": "error", "error": str(exc)})\n'
        })
        self.assertEqual(len(result.offenders), 1, result.offenders)
        self.assertIn("caught exception's message", result.offenders[0])

    def test_it_does_not_flag_a_class_name_only_read_bound_to_a_sink_key(self):
        """Pins the parse-once rule: a re-parsed tree kills the `type(exc)` exclusion."""
        result = analyse_provenance({
            "job.py": self._SINK
            + "\n\ndef run_job(run_id):\n"
            "    try:\n"
            "        work()\n"
            "    except Exception as exc:\n"
            "        _finish_audit_run(run_id, {\n"
            '            "status": "error",\n'
            '            "error": f"boom: {type(exc).__name__}",\n'
            "        })\n"
        })
        self.assertEqual(result.offenders, ())

    def test_it_does_not_flag_a_numeric_exception_attribute(self):
        """`f"HTTP {exc.code}"` is a status code, not the message."""
        result = analyse_provenance({
            "job.py": self._SINK
            + "\n\ndef run_job(run_id):\n"
            "    try:\n"
            "        work()\n"
            "    except Exception as exc:\n"
            '        _finish_audit_run(run_id, {"status": "error", "error": f"HTTP {exc.code}"})\n'
        })
        self.assertEqual(result.offenders, ())

    def test_it_does_not_flag_a_read_outside_a_ledger_writer(self):
        """The egress direction. Six sites render a stored row; none is a write."""
        result = analyse_provenance({
            "egress.py": (
                "def render(run):\n"
                '    return {"status": run.get("status"), "error": run.get("error")}\n'
            ),
            "producer.py": self._RAW_PRODUCER,
        })
        self.assertEqual(result.writer_functions, 0)
        self.assertEqual(result.offenders, ())

    def test_it_does_not_flag_the_sinks_own_read_of_its_parameter(self):
        """The contract boundary -- `_finish_audit_run` copies from its own `result`."""
        result = analyse_provenance({
            "sink.py": self._SINK,
            "producer.py": self._RAW_PRODUCER,
        })
        self.assertEqual(result.writer_functions, 1)
        self.assertEqual(result.sink_keys, ("error",))
        self.assertEqual(result.offenders, ())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
