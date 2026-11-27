"""One owner for the "never format a stored timestamp bare" rule.

SQLite hands ``DateTime`` columns back naive -- including columns declared
``timezone=True`` -- so ``row.timestamp.isoformat()`` produces
``2026-06-20T12:00:00`` with no marker, and a browser's ``new Date(...)`` reads
that as *local* time. In UTC+8 that is an eight-hour error with no symptom other
than a wrong clock. Audit 53.171 found the first site, 53.172 five more, 53.176
eight in the two world-cup route modules, 53.177 six in ``sport_edges``,
53.182 six in ``predictions`` / ``sport_recommendations``.

The rule lives here rather than next to any one module's tests, because it is a
rule about *modules*, and because most of those call sites are unreachable from
the tests that cover the endpoints they sit in (seven of the 53.176 eight; all
six of 53.177's are reachable, which is why they also have endpoint tests).
``MODULE_NAMES`` grows as each module is swept; a module that is not listed is
not yet clean, and adding it to this tuple is the last step of sweeping it.

A second ring exists, and it is where five sweeps stopped short: the module
that *formats* the stamp need not be the module that *returns* it.
``app/kernel/`` held seven such stamps behind live endpoints, and this rule
could not reach any of them: it asserted an ``app.api.routes.`` prefix
(audit 53.184). ``kernel_db`` is now listed below; ``market_settlement_store``
cannot be, for a reason recorded next to ``MODULE_NAMES``.
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
    "app.api.routes.predictions",
    "app.api.routes.sport_recommendations",
    # Second ring (audit 53.184): kernel_db builds the ``/predictions/history``
    # body itself, so the route modules above never contained the code that
    # formats its ``created_at``.
    "app.kernel.kernel_db",
)

# Two modules are deliberately absent, and the difference matters:
# ``market_settlement_store`` converts with ``as_utc`` and never calls
# ``.isoformat()``, so this rule is green on it whatever the code does: listing
# it would be an empty guard, not a clean one. Its protection is the wire
# assertions in ``tests/test_kernel_timestamp_wire.py`` (53.184).
# ``market_snapshot_store`` and ``backtest/match_loader`` have one ``.isoformat()``
# each that is a *sort key* and never reaches a body; the rule would red on the
# only correct line in the function (53.183/53.184).


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
        offenders.extend(_offenders_in(filename, tree))

    assert offenders == [], (
        "these call sites format a stored timestamp without an offset; use "
        "app.utils.helpers.utc_isoformat: " + ", ".join(offenders)
    )


def _offenders_in(filename: str, tree: ast.Module) -> list[str]:
    """Every bare ``.isoformat()`` on a timestamp-shaped receiver in ``tree``."""
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "isoformat"):
            continue
        receiver = func.value
        # Two receiver shapes, not one. ``row.created_at`` is an attribute;
        # ``timestamp.isoformat()`` is a *local* that a serializer rebinds from
        # a row column. The first version of this rule matched only attributes,
        # so ``predictions._prediction_to_dict`` sat outside its reach while the
        # module read as governed (audit 53.182). Matching names costs nothing:
        # ``utc_isoformat`` on an already-aware value returns the same string, so
        # an over-match is a no-op rewrite while a miss is an eight-hour error.
        name = (
            receiver.attr
            if isinstance(receiver, ast.Attribute)
            else receiver.id
            if isinstance(receiver, ast.Name)
            else None
        )
        if name is not None and _is_row_timestamp(name):
            offenders.append(f"{filename}:{node.lineno} ({name})")
    return offenders


def test_the_rule_sees_both_receiver_shapes() -> None:
    """Positive control for the rule itself, not for the modules it sweeps.

    Once ``_prediction_to_dict`` was converted, the ``ast.Name`` branch became
    unfalsifiable: deleting it changed no assertion anywhere in the suite, so
    the rule would have quietly gone back to attributes only. This runs it over
    a synthetic snippet where both shapes appear, which is the only way to keep
    a scanner honest (same reason audit §53.4 requires a positive control on
    every vocabulary scan).
    """
    source = (
        "def serialize(row, timestamp):\n"
        "    a = row.created_at.isoformat()\n"
        "    b = timestamp.isoformat()\n"
        "    c = row.name.upper()\n"
        "    return a, b, c\n"
    )
    assert _offenders_in("probe.py", ast.parse(source)) == [
        "probe.py:2 (created_at)",
        "probe.py:3 (timestamp)",
    ]


def test_the_family_covers_every_shape_in_use() -> None:
    """Pin the family's *definition*, not the sites swept with it.

    ``_is_row_timestamp`` is a heuristic over names, so the only way to tell a
    narrowed rule from a clean sweep is to assert membership directly: dropping
    ``kickoff_utc`` from the set makes the offender test pass on every module
    while silently ceasing to cover two real sites, and nothing else in the
    suite would move.
    """
    for name in (
        "created_at",
        "captured_at",
        "prediction_timestamp",
        "timestamp",
        "last_updated",
        "kickoff_utc",
    ):
        assert _is_row_timestamp(name), name
    for name in ("match_id", "sport", "upper", "captured", "isoformat"):
        assert not _is_row_timestamp(name), name


def test_every_swept_module_is_really_swept() -> None:
    """Guard the guard: an empty or wrong ``MODULE_NAMES`` walks nothing.

    The rule above passes trivially when the tuple is empty or names modules
    that never call ``utc_isoformat``. This asserts each listed module exists at
    the expected path and already routes at least one timestamp through the
    helper, so a typo fails here instead of reading as a clean sweep.
    """
    for module_name in MODULE_NAMES:
        filename, tree = _source_of(module_name)
        assert module_name.startswith(("app.api.routes.", "app.kernel.")), module_name
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
