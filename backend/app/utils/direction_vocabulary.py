"""The direction vocabularies: one named constant per layer that needs one.

Every layer that has an opinion about which directions *mean* something keeps its
own name here, because the opinions are not the same opinion:

* ``STRONG_DIRECTIONS``          -- directions that commit to an outcome; every
                                    layer that may downgrade such a call to WAIT
                                    reads it (``guardrail_service``,
                                    ``market_quality_service``,
                                    ``execution_quality_service``,
                                    ``source_reliability_service``,
                                    ``replay/metrics``, ``decision_quality_service``).
                                    Only the *membership* is shared there: some
                                    rewrite it to WAIT, one only counts the rewrite,
                                    one treats it as the sole input its first stage
                                    may touch -- what each layer *does* stays local
* ``_DIRECTIONAL``               -- directions with a checkable stance
* ``_CALLED_DIRECTIONS``         -- a committed call, as opposed to an abstention
* ``_VALID_DIRECTIONS``          -- directions that may be graded against an outcome
* ``_STRONG_DISPLAY_DIRECTIONS`` -- committed calls the anomalies route surfaces
* ``_REPORTED_DIRECTIONS``       -- directions reported by ``trade_stats()``
* ``_TRADABLE_DIRECTIONS``       -- directions a simulated (paper) trade may open with
* ``_STRONG_EVENT_DIRECTIONS``   -- directions that assert a strong conclusion

**What is shared is the location, not the meaning.** All eight happen to hold
``{"YES", "NO"}`` today, and that is deliberately *not* a reason to collapse them
into one constant: "may a paper trade be opened on HOLD?" and "should HOLD appear
in the anomalies listing?" are separate decisions, free to diverge, and each
constant's own comment below records the decision *its* layer made. Collapsing
them would turn eight decisions into one and delete the per-layer reasoning with
them (audit sections 42.2 and 42.5).

``STRONG_DIRECTIONS`` is public; the other seven keep the ``_``-prefixed names
they had in their original modules, because those names appear in the mutation
inventory, the guard tests and the audit record. They are module-shared despite
the spelling -- here the underscore marks "defined in this module", not "private
to this module".

This module imports nothing from ``app``, so it stays a leaf and no import cycle
can form. That is what makes it safe for ``app/memory/`` and ``app/api/`` to
import out of ``app/utils/``.

History (audit sections 42-47)
-----------------------------
These were thirteen definitions spread over thirteen modules: six copies of
``_STRONG_DIRECTIONS`` (a ``tuple`` in five of them and a ``set`` in
``replay/metrics``) plus seven differently-named single copies. Section 45 merged
the six copies into ``STRONG_DIRECTIONS``. Section 47 moved the remaining seven
here, leaving every name, value, container and comment unchanged -- thirteen
definition sites became eight definitions in one module.

One container is *not* free to change. ``_REPORTED_DIRECTIONS`` has a consumer
that iterates it (``for d in _REPORTED_DIRECTIONS:`` in
``simulated_trade_store.trade_stats``), and this repo pins no ``PYTHONHASHSEED``,
so making it a ``set`` would let the ``by_direction`` JSON key order vary between
runs. It stays a ``tuple``, and ``tests/test_direction_vocabulary.py`` pins that.

Not to be confused with ``evidence_aggregation_service._VALID_DIRECTIONS``, a
*different* vocabulary (``{"support", "oppose"}``, for a different field). It
stays in its own module: merging by name would have been a trap.
"""
from __future__ import annotations

STRONG_DIRECTIONS = ("YES", "NO")

# Directions that have a checkable stance (YES/NO). WAIT/AVOID are
# non-directional — direction_correct is None for them.
_DIRECTIONAL = ("YES", "NO")

# A committed call, as opposed to an abstention. The event-layer equivalent of
# the Decision Gate's act / provisional_act.
_CALLED_DIRECTIONS = frozenset({"YES", "NO"})

# Directions that commit to an outcome and can therefore be graded against
# ``actual_outcome``. ``attribute_evidence`` skips any record outside this set
# (WAIT/AVOID carry no YES/NO call to score), so widening it would start
# grading records that were never committed.
_VALID_DIRECTIONS = {"YES", "NO"}

# final_displayed_direction values that still represent a *committed* call. If a
# wide-spread market displays one of these, the overlay failed to downgrade it to
# WAIT/AVOID, and the anomalies endpoint surfaces the event for operator review.
# Lives here rather than inline so a mutation can target it.
_STRONG_DISPLAY_DIRECTIONS = ("YES", "NO")

# The directions reported in trade_stats()["by_direction"]. Ordering only affects
# the JSON key order; it is the *set* that matters -- every member must be
# queried, or the NO side silently disappears from the stats payload (a gap that
# an `assertNotIn("NO", ...)` test cannot see). Lives here rather than inline so a
# mutation can target it.
#
# Kept a tuple on purpose -- this is the only one of the eight with an *iterating*
# consumer, and the repo pins no PYTHONHASHSEED. See the module docstring.
_REPORTED_DIRECTIONS = ("YES", "NO")

# Directions a simulated (paper) trade may be opened with. Anything else -- a
# WAIT/AVOID recommendation, or an absent field -- falls back to YES.
#
# Deliberately NOT documented as "the direction the trade gets": the edge-sign
# override lower in _persist_events rewrites the direction again (edge > 0 forces
# YES, edge < 0 forces NO), so this fallback is only *observable* when
# entry_edge == 0. Kept named and module-level so a mutation can target it.
_TRADABLE_DIRECTIONS = ("YES", "NO")

# The directions that assert a strong (non-abstaining) conclusion. Two call
# sites read this same membership test: ``_is_strong_event_direction`` (which
# gates the evidence / counterevidence / confidence / actionability checks) and
# ``_check_calculation`` (a "change too small to justify a strong direction"
# soft-fail). WAIT / AVOID abstain, so neither check may fire for them.
_STRONG_EVENT_DIRECTIONS = ("YES", "NO")
