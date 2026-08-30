# backend/tests/test_factor_vote_no_drift.py
"""The published votes and the scored votes must be the same list (E20).

Before E20 each binary engine wrote the vote expression three times -- once for
``ContributionItem.predicted_outcome``, once for ``compute_confidence`` and once
for ``confidence_breakdown``. Three copies of one rule can drift, and a drift
would be invisible: the UI reads the explanation rows while ``agreement`` is
computed from the confidence call's list.

The spy patches the **engine module's** binding, not
``app.kernel.engines.confidence``: the engines do
``from app.kernel.engines.confidence import compute_confidence``, so patching
the source module would be a no-op that leaves this test vacuous.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.kernel.engines.confidence import (
    compute_confidence as real_compute_confidence,
)
from app.kernel.engines.confidence import (
    confidence_breakdown as real_confidence_breakdown,
)
from app.sports.baseball.engines.baseball_engine import BaseballEngine
from app.sports.basketball.engines.basketball_engine import BasketballEngine
from app.sports.hockey.engines.hockey_engine import HockeyEngine
from tests.test_factor_vote_engines import _features

_ENGINES = [
    ("baseball", "mlb", BaseballEngine, "app.sports.baseball.engines.baseball_engine"),
    (
        "basketball",
        "nba",
        BasketballEngine,
        "app.sports.basketball.engines.basketball_engine",
    ),
    ("hockey", "nhl", HockeyEngine, "app.sports.hockey.engines.hockey_engine"),
]


class TestVotesDoNotDrift:
    @pytest.mark.parametrize("sport,comp,engine_cls,module", _ENGINES)
    @pytest.mark.parametrize(
        "rest_home,rest_away",
        [(2.0, 2.0), (4.0, 2.0), (2.0, 4.0), (None, None)],
        ids=["level", "home_rested", "away_rested", "missing"],
    )
    def test_both_confidence_calls_get_the_published_votes(
        self, sport, comp, engine_cls, module, rest_home, rest_away
    ):
        seen: dict[str, list[list[str | None]]] = {"conf": [], "break": []}

        def spy_conf(*a, **kw):
            seen["conf"].append(list(kw["predicted_outcomes"]))
            return real_compute_confidence(*a, **kw)

        def spy_break(*a, **kw):
            seen["break"].append(list(kw["predicted_outcomes"]))
            return real_confidence_breakdown(*a, **kw)

        feats = _features(
            sport_code=sport,
            comp_code=comp,
            rest_home=rest_home,
            rest_away=rest_away,
        )
        with (
            patch(f"{module}.compute_confidence", side_effect=spy_conf),
            patch(f"{module}.confidence_breakdown", side_effect=spy_break),
        ):
            result = engine_cls().predict(feats, feats.match)

        # Exactly one call each: "if we captured anything" would let a silent
        # second call through, and a zero-call run would make the rest vacuous.
        assert len(seen["conf"]) == 1, seen["conf"]
        assert len(seen["break"]) == 1, seen["break"]

        published = [item.predicted_outcome for item in result.explanation]
        assert published, "engine published no explanation rows"
        assert seen["conf"][0] == published
        assert seen["break"][0] == published

    @pytest.mark.parametrize("sport,comp,engine_cls,module", _ENGINES)
    def test_the_level_case_actually_puts_a_none_in_the_scored_list(
        self, sport, comp, engine_cls, module
    ):
        """Guards the tests above: the fixture must exercise a ``None``.

        Without this, all four rest settings could yield an all-``home_win``
        list and the equality assertions would still hold.
        """
        seen: list[list[str | None]] = []

        def spy_conf(*a, **kw):
            seen.append(list(kw["predicted_outcomes"]))
            return real_compute_confidence(*a, **kw)

        feats = _features(
            sport_code=sport, comp_code=comp, rest_home=2.0, rest_away=2.0
        )
        with patch(f"{module}.compute_confidence", side_effect=spy_conf):
            result = engine_cls().predict(feats, feats.match)

        assert len(seen) == 1
        rest_index = [e.factor for e in result.explanation].index("rest")
        assert seen[0][rest_index] is None
        # And the row is available, so the None is the level case rather than
        # an absent factor.
        assert result.explanation[rest_index].available is True

    @pytest.mark.parametrize("sport,comp,engine_cls,module", _ENGINES)
    def test_the_patch_target_is_live(self, sport, comp, engine_cls, module):
        """A patch on the wrong module would silently no-op.

        Raising from the spy proves the engine really calls the patched name.
        """
        feats = _features(
            sport_code=sport, comp_code=comp, rest_home=2.0, rest_away=2.0
        )
        with patch(f"{module}.compute_confidence", side_effect=RuntimeError("spy hit")):
            with pytest.raises(RuntimeError, match="spy hit"):
                engine_cls().predict(feats, feats.match)
