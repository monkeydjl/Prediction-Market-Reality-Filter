"""Elo provenance for the prediction FeatureSet.

One decision point, called by every sport's feature builder: an Elo rating whose
source is not a real source must not reach an engine.

Why this exists
---------------
``elo_ratings_service.get_elo_rating`` never fails.  For a team it does not know
it returns ``{"elo_rating": 1500.0, "source": "default"}``, and for a team it
only has a FIFA rank for it returns a value computed from that rank with
``source": "estimated"``.  The two football adapters read ``elo_rating`` and
dropped ``source`` on the floor, so those invented ratings arrived at the engines
indistinguishable from measured ones.  Measured on the production code path
(``Atlantis`` vs ``Freedonia``, neither in the 49-entry hardcoded table):

===================================  ==========  ================  ============
state                                confidence  data_completeness data_quality
===================================  ==========  ================  ============
invented 1500/1500, no odds              0.5475             0.400  partial
Elo absent, no odds                      0.4138             0.000  partial
invented 1500/1500 + odds                0.6673             1.000  **real**
Elo absent + odds                        0.5736             0.400  partial
===================================  ==========  ================  ============

So the invented pair bought +0.134 of confidence with no odds and +0.094 with
odds, and with odds present it promoted the whole prediction to
``data_quality="real"`` while the explanation shown to a user read
``"Elo 1500.0 vs 1500.0"`` as a supporting factor.

The legacy World Cup pipeline has always checked this
(``world_cup_prediction_pipeline.py`` builds ``has_real_elo`` from
``all_sources_look_real`` and caps the fusion weight when the source is
``estimated``/``default``); the kernel migration replaced that with a not-None
test.  This module restores the check for the kernel, reusing the same predicate
rather than a second copy of the token list.

What it does *not* do
---------------------
It does not damp a weight or invent a new confidence term.  A rating with a
non-real source is simply dropped, which puts the FeatureSet in the state it
would have had if the service had returned nothing -- exactly what the club Elo
path (``club_elo_service.get_club_elo``) already produces by returning ``None``.
Every engine already handles that state.  Two teams with no usable rating now
look the same whichever service was asked.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, NamedTuple

from app.services.world_cup_data_quality import all_sources_look_real

logger = logging.getLogger(__name__)

#: Quality note added when a rating is dropped for provenance.
ELO_SOURCE_NOT_REAL_NOTE = "elo_source_not_real"


class EloProvenance(NamedTuple):
    """The Elo values a feature builder should use, after the provenance check."""

    elo_home: float | None
    elo_away: float | None
    elo_source: str | None
    notes: tuple[str, ...]


def resolve_elo_provenance(team_raw: Mapping[str, Any]) -> EloProvenance:
    """Return the Elo pair to hand the engines, dropping invented ratings.

    ``team_raw`` is an adapter's ``raw["team"]`` sub-dict.

    A missing ``elo_source`` key means the adapter does not report provenance,
    which is **not** the same as reporting a non-real one: the MLB/NBA/NHL/LoL
    adapters read a ratings table that returns ``None`` for an unknown team, so
    they cannot invent a value and have nothing to label.  Treating absence as
    non-real would silently delete every rating in those three sports.
    """
    elo_home = team_raw.get("elo_home")
    elo_away = team_raw.get("elo_away")
    raw_source = team_raw.get("elo_source")
    source = str(raw_source) if raw_source not in (None, "") else None

    if source is None:
        return EloProvenance(elo_home, elo_away, None, ())

    if all_sources_look_real(source):
        return EloProvenance(elo_home, elo_away, source, ())

    if elo_home is None and elo_away is None:
        # Nothing to drop; keep the label so the reason stays visible.
        return EloProvenance(None, None, source, (ELO_SOURCE_NOT_REAL_NOTE,))

    logger.info(
        "Dropping Elo pair %s/%s: source %r is not a real source, so the "
        "engines would have scored an invented rating as measured evidence.",
        elo_home, elo_away, source,
    )
    return EloProvenance(None, None, source, (ELO_SOURCE_NOT_REAL_NOTE,))
