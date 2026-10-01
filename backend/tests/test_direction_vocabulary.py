"""The shared direction vocabulary: eight names, eight decisions.

Audit section 45 merged the six ``_STRONG_DIRECTIONS`` copies into one
definition; section 47 moved the remaining seven names in beside it, so the
whole vocabulary is one import away. What the consolidation was *not* allowed to
change is the point of these tests: the names, the members, the containers, and
the fact that nothing defines any of them anywhere else.

The one container that is load-bearing rather than cosmetic is
``_REPORTED_DIRECTIONS`` -- see
``test_reported_directions_stays_an_ordered_sequence``.
"""
from __future__ import annotations

import pathlib
import re

from app.utils import direction_vocabulary

MODULE = pathlib.Path(direction_vocabulary.__file__).resolve()
APP = MODULE.parents[1]

# Every name the consolidation produced, in the module's own declaration order so
# this tuple can be diffed against the file top to bottom.
DIRECTION_CONSTANTS = (
    "STRONG_DIRECTIONS",
    "_DIRECTIONAL",
    "_CALLED_DIRECTIONS",
    "_VALID_DIRECTIONS",
    "_STRONG_DISPLAY_DIRECTIONS",
    "_REPORTED_DIRECTIONS",
    "_TRADABLE_DIRECTIONS",
    "_STRONG_EVENT_DIRECTIONS",
)

# The expected value of each name, written out rather than read back from the
# module -- otherwise the assertion would only restate whatever the module says.
EXPECTED_VALUES = {
    "STRONG_DIRECTIONS": ("YES", "NO"),
    "_DIRECTIONAL": ("YES", "NO"),
    "_CALLED_DIRECTIONS": frozenset({"YES", "NO"}),
    "_VALID_DIRECTIONS": {"YES", "NO"},
    "_STRONG_DISPLAY_DIRECTIONS": ("YES", "NO"),
    "_REPORTED_DIRECTIONS": ("YES", "NO"),
    "_TRADABLE_DIRECTIONS": ("YES", "NO"),
    "_STRONG_EVENT_DIRECTIONS": ("YES", "NO"),
}

# ``evidence_aggregation_service`` keeps a ``_VALID_DIRECTIONS`` of its own holding
# ``{"support", "oppose"}`` for a different field. Same name, different
# vocabulary, deliberately left where it was: section 47's move was by *meaning*,
# and merging by name would have silently changed what that module accepts.
FOREIGN_DEFINITIONS = {("evidence_aggregation_service.py", "_VALID_DIRECTIONS")}


def test_every_expected_name_is_declared():
    for name in DIRECTION_CONSTANTS:
        assert hasattr(direction_vocabulary, name), name


def test_the_module_declares_nothing_else_that_looks_like_a_direction():
    # ``hasattr`` alone would pass on a module that had also grown stray copies;
    # this pins the surface at exactly the eight names.
    declared = {
        name
        for name in vars(direction_vocabulary)
        if not name.startswith("__") and "DIRECTION" in name
    }
    assert declared == set(DIRECTION_CONSTANTS)


def test_each_name_holds_the_members_it_had_before_the_move():
    for name, expected in EXPECTED_VALUES.items():
        assert getattr(direction_vocabulary, name) == expected, name


def test_the_container_types_are_pinned():
    for name in DIRECTION_CONSTANTS:
        expected = EXPECTED_VALUES[name]
        assert type(getattr(direction_vocabulary, name)) is type(expected), name


def test_reported_directions_stays_an_ordered_sequence():
    """W32's guard: the only constant with an *iterating* consumer.

    ``simulated_trade_store.trade_stats`` walks it (``for d in
    _REPORTED_DIRECTIONS:``) to build the ``by_direction`` payload, and this repo
    pins no ``PYTHONHASHSEED`` -- as a ``set`` the JSON key order would vary from
    run to run. Type first: a set also compares unequal to the tuple, but when
    this fails the reader should be told the reason, not only that two objects
    differed.
    """
    assert isinstance(direction_vocabulary._REPORTED_DIRECTIONS, tuple)
    assert direction_vocabulary._REPORTED_DIRECTIONS == ("YES", "NO")


def test_no_other_module_under_app_defines_any_of_these_names():
    """The whole point of the move: one place to change a direction.

    Every definition site of every name is enumerated and compared to what is
    expected, so a fresh copy anywhere under ``app/`` fails here. The single
    allowed foreign definition is named in ``FOREIGN_DEFINITIONS`` rather than
    skipped by an unstated rule, so widening the exception shows up in the diff.
    """
    pattern = re.compile(
        r"^\s*(" + "|".join(re.escape(name) for name in DIRECTION_CONSTANTS) + r")\s*="
    )
    found: list[tuple[pathlib.Path, str]] = []
    for path in sorted(APP.rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = pattern.match(line)
            if match:
                found.append((path.resolve(), match.group(1)))

    # Positive control: the enumeration has to have found the module, or the
    # "no strays" assertion below would hold on an empty scan.
    assert sorted(name for path, name in found if path == MODULE) == sorted(
        DIRECTION_CONSTANTS
    )

    stray = sorted(
        (path.relative_to(APP).as_posix(), name)
        for path, name in found
        if path != MODULE and (path.name, name) not in FOREIGN_DEFINITIONS
    )
    assert stray == []


def test_the_module_imports_nothing_from_the_application():
    """It is a leaf, which is what lets app/memory and app/api import from it."""
    source = MODULE.read_text(encoding="utf-8")
    assert "STRONG_DIRECTIONS" in source  # the file we read is the one we mean
    assert re.search(r"^\s*(?:from|import)\s+app(?:[.\s]|$)", source, re.M) is None
