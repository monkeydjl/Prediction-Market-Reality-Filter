"""Order-independent sampling of event ids by hash rank.

Why this exists: seven call sites in this repo sampled a population with
``random.Random(42).sample(population, k)`` (or, worse, ``random.seed(42)``
followed by ``random.sample``) under a comment claiming "deterministic
sampling for reproducibility". ``sample`` picks *positions*, so membership
depends on the population's length and order, and both change as the store
grows or gets rewritten. Measured on a 200-event population at size 50:
adding one event churned 2 members, adding 5 churned 7, adding 20 churned 10,
and reversing the order left an overlap of only 14/50. A report built that way
is not comparable to itself, which is precisely what "reproducible" promised.

Ranking by ``sha256(seed || NUL || event_id)`` instead makes the choice a
function of the ids alone: order cannot matter, and a growing population can
only displace an incumbent one-for-one.

The two ``random.seed(42)`` sites additionally reseeded the **process-global**
RNG from inside a read-only diagnostic — a side effect on every later
``random`` call in the same process.

Not a substitute for a pinned set. This makes *minting* a subset reproducible;
writing the membership down is what makes it fixed
(``model_eval_set_service.build_manifest``).

Pure: no I/O, no clock, no settings.
"""
from __future__ import annotations

import hashlib

# Recorded in artifacts that pin a selection, so a future strategy change is
# visible in the artifact instead of silently re-minting a different set.
SELECTION_STRATEGY = "sha256-rank"

# Separator between seed and id when hashing. Without it, seed "a" + id "bc"
# and seed "ab" + id "c" hash to the same digest, so two different selections
# could rank identically.
_SEP = "\x00"


def selection_digest(seed: str, event_id: str) -> str:
    """The rank key for one id under one seed."""
    return hashlib.sha256(f"{seed}{_SEP}{event_id}".encode("utf-8")).hexdigest()


def stable_sample(event_ids: list[str], *, seed: str, size: int) -> list[str]:
    """The ``size`` event ids a given seed selects, sorted by event id.

    Order-independent and duplicate-safe: the caller's list order never affects
    membership, and a repeated id cannot occupy two slots. ``size`` larger than
    the population returns the whole population rather than raising — a subset
    drawn from a small store is legitimate, and the caller records the
    population it was drawn from.
    """
    if size < 0:
        raise ValueError("size must be >= 0")
    unique = {eid for eid in event_ids if isinstance(eid, str) and eid}
    # Tie-break on the id so two colliding digests still rank deterministically.
    ranked = sorted(unique, key=lambda eid: (selection_digest(seed, eid), eid))
    return sorted(ranked[:size])
