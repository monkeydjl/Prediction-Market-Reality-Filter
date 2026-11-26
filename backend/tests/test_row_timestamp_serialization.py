"""One owner for the "never format a stored timestamp bare" rule.

SQLite hands ``DateTime`` columns back naive -- including columns declared
``timezone=True`` -- so ``row.timestamp.isoformat()`` produces
``2026-06-20T12:00:00`` with no marker, and a browser's ``new Date(...)`` reads
that as *local* time. In UTC+8 that is an eight-hour error with no symptom other
than a wrong clock. Audit 53.171 found the first site, 53.172 five more, 53.176
eight in the two world-cup route modules, 53.177 six in ``sport_edges``.

The rule lives here rather than next to any one module's tests, because it is a
rule about *modules*, and because most of those call sites are unreachable from
the tests that cover the endpoints they sit in (seven of the 53.176 eight; all
six of 53.177's are reachable, which is why they also have endpoint tests).
``MODULE_NAMES`` grows as each module is swept; a module that is not listed is
not yet clean, and adding it to this tuple is the last step of sweeping it.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

# Swept so far. Extending this tuple is what "sweeping a module" means; the
# rule cannot be applied tree-wide until the remaining route modules are clean
# (see audit 53.176's open list).
MODULE_NAMES = (
    "app.api.routes.world_cup_analytics",
    "app.api.routes.world_cup_predictions",
    "app.api.routes.sport_edges",
)

def _is_row_timestamp(name: str) -> bool:
    """True for the attribute names that carry a wall-clock instant.

    A *family*, not a list of the names swept so far. The first version of this
    rule enumerated six names and missed ``prediction_timestamp`` -- so a mutant
    that reverted ``sport_edges``' ``/detect`` key stayed invisible to the rule
    and was caught only by an endpoint assertion. Enumerating names means every
    new site is a silent hole until someone remembers to add it.

    ``kickoff_utc`` is the one name that does not fit either suffix rule, and it
    is kept explicit for that reason.
    """
    return (
        name.endswith("_at")
        or name.endswith("_timestamp")
        or name in {"timestamp", "last_updated", "last_update", "kickoff_utc"}
    )


def _source_of(module_name: str) -> tuple[str, ast.Module]:
    module = importlib.import_module(module_name)
    path = Path(module.__file__)  # type: ignore[arg-type]
    source = path.read_bytes().decode("utf-8")
    return path.name, ast.parse(source)


def test_no_swept_module_formats_a_row_timestamp_bare() -> None:
    """``<row attribute>.isoformat()`` must not reappear in a swept module."""
    offenders = []
    for module_name in MODULE_NAMES:
        filename, tree = _source_of(module_name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "isoformat"):
                continue
            receiver = func.value
            if isinstance(receiver, ast.Attribute) and _is_row_timestamp(receiver.attr):
                offenders.append(f"{filename}:{node.lineno} ({receiver.attr})")

    assert offenders == [], (
        "these call sites format a stored timestamp without an offset; use "
        "app.utils.helpers.utc_isoformat: " + ", ".join(offenders)
    )


def test_every_swept_module_is_really_swept() -> None:
    """Guard the guard: an empty or wrong ``MODULE_NAMES`` walks nothing.

    The rule above passes trivially when the tuple is empty or names modules
    that never call ``utc_isoformat``. This asserts each listed module exists at
    the expected path and already routes at least one timestamp through the
    helper, so a typo fails here instead of reading as a clean sweep.
    """
    for module_name in MODULE_NAMES:
        filename, tree = _source_of(module_name)
        assert module_name.startswith("app.api.routes."), module_name
        assert filename == f"{module_name.rsplit('.', 1)[-1]}.py", (
            f"{module_name} resolved to {filename}"
        )

        converted = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "utc_isoformat")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "utc_isoformat")
            )
        ]
        assert converted, (
            f"{filename} has no utc_isoformat(...) call, so listing it here "
            "proves nothing -- either the sweep was reverted or this module "
            "does not belong in MODULE_NAMES"
        )
