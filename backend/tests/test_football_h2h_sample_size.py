"""P1-F4: the h2h factor voted a raw empirical rate with no sample size.

Measured on the live kernel DB (read-only copy, backfilled by the real
``backfill_results_from_fixtures`` from PR #73) on 2026-08-30.

**The sample size was computed and thrown away.**
``aggregate_h2h_meetings`` returns ``matches_played``; every producer runs
through it; the enricher wrote ``h2h_home_win_rate`` and ``h2h_draw_rate`` and
dropped the count. So the engine could not tell a 2-match club record from a
20-match national one.

**What the engine then did with it.** h2h was the only one of the thirteen
factors that fed a raw rate straight into the blend. Every sibling either goes
through ``_adjust_home_edge`` -- a bounded nudge away from neutral, floored at
(0.01, 0.05, 0.01) -- or bounds itself: the xG share cannot leave [0.25, 0.75]
and the referee rate is clamped to [0.20, 0.80]. Driving all thirteen to their
legal extremes, the widest sibling vote was elo at ``H=0.590``; h2h reached
``H=1.000``.

**The corpus.** Of 1446 upcoming football fixtures, 512 get an h2h vote and
**every one of them is built on exactly n=2** -- a league pairing meets twice a
season, so the rate is quantised to {0, 0.5, 1}:

    rate (home/draw)   fixtures   vote today          -> after this fix (n=2)
    0.0 / 0.0                71   0.000/0.000/1.000      0.200/0.150/0.650
    0.0 / 0.5               108   0.000/0.500/0.500      0.200/0.400/0.400
    0.0 / 1.0                26   0.000/1.000/0.000      0.200/0.650/0.150
    0.5 / 0.0               128   0.500/0.000/0.500      0.450/0.150/0.400
    0.5 / 0.5               108   0.500/0.500/0.000      0.450/0.400/0.150
    1.0 / 0.0                71   1.000/0.000/0.000      0.700/0.150/0.150

All 512 were degenerate (an arm at exactly 0.0 or 1.0) and **168 had an arm at
exactly 1.0** -- the factor asserting certainty from two matches. Engine effect
at weight 0.05 on a real fixture (Elo 1900/1700, no odds): ``home_win`` moved
-0.118 to +0.082 before, -0.078 to +0.022 after.

**Why a ramp and not a clamp.** National pairings in the historical corpus
(49,465 rows, 336 team keys, 7,535 pairings) are genuinely varied: 26.6% have
one meeting, 43.6% two or fewer, but 23.8% have eight or more and 6.8% have 20+.
A flat clamp would treat those the same. ``w = n / (n + k)`` with ``k = 2``
states "one full season of head-to-head is worth as much as the prior, and no
more": a 100%-home record votes 0.700 at n=2 and 0.924 at n=20.

**No existing test pinned any of this.** The engine suite's h2h fixture is
``0.45 / 0.28`` -- an interior point that is never degenerate -- and the
venue-split tests all assert *relative* order, which monotone shrinkage
preserves. 484 football/adapter tests passed against the defect.
"""
from __future__ import annotations

import ast
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.kernel.domain import (
    CompetitionIdentity,
    FeatureSet,
    MatchIdentity,
    SeasonIdentity,
    SportIdentity,
    TeamIdentity,
)
from app.sports.football.engines import football_multi_factor_engine as fmfe
from app.sports.football.engines.football_multi_factor_engine import (
    FootballMultiFactorEngine,
)
from app.sports.football.feature_builder import FootballFeatureBuilder

_ENGINE_SRC = Path("app/sports/football/engines/football_multi_factor_engine.py")
_SHARED_SRC = Path("app/sports/football/adapters/_shared.py")

#: The six (home_rate, draw_rate) pairs a 2-match sample can produce, with the
#: number of live upcoming fixtures measured on each.
_LIVE_VECTORS = (
    (0.0, 0.0, 71),
    (0.0, 0.5, 108),
    (0.0, 1.0, 26),
    (0.5, 0.0, 128),
    (0.5, 0.5, 108),
    (1.0, 0.0, 71),
)

_DETAIL_RE = re.compile(r"H=([0-9.]+) D=([0-9.]+) A=([0-9.]+)")


def _identity(competition: str = "epl", home: str = "H", away: str = "A") -> MatchIdentity:
    sport = SportIdentity(code="football", name="Football")
    comp = CompetitionIdentity(code=competition, name=competition.upper(), sport=sport)
    return MatchIdentity(
        match_id=f"{competition}-probe",
        season=SeasonIdentity(competition=comp, season_key="2025-26"),
        stage="regular",
        round=None,
        home=TeamIdentity(code=home, name=home, competition=comp),
        away=TeamIdentity(code=away, name=away, competition=comp),
        kickoff_utc=datetime(2026, 9, 15, tzinfo=timezone.utc),
    )


def _raw(
    *,
    h2h_home: float | None = None,
    h2h_draw: float | None = None,
    custom: dict | None = None,
) -> dict:
    """Elo-only baseline, so any movement is attributable to h2h."""
    team: dict = {"elo_home": 1900.0, "elo_away": 1700.0}
    if h2h_home is not None:
        team["h2h_home_win_rate"] = h2h_home
        team["h2h_draw_rate"] = h2h_draw
    return {
        "team": team,
        "market": {},
        "player": {},
        "environment": {},
        "general": {},
        "custom": {"elo_source": "measured", **(custom or {})},
        "meta": {},
    }


def _predict(**kwargs) -> tuple[dict[str, float], float, dict[str, float] | None]:
    """Return (outcome probabilities, confidence, the h2h factor's own vote)."""
    match = _identity()
    raw = _raw(**kwargs)
    result = FootballMultiFactorEngine().predict(
        FootballFeatureBuilder().build(match, raw), match,
    )
    vote = None
    for item in result.explanation:
        if item.factor == "h2h" and item.available:
            found = _DETAIL_RE.search(item.detail or "")
            if found:
                vote = {
                    "home_win": float(found.group(1)),
                    "draw": float(found.group(2)),
                    "away_win": float(found.group(3)),
                }
    return result.outcome_probabilities, result.confidence, vote


class TheFloorsAreOneDeclarationTests(unittest.TestCase):
    """Both vote paths must share one floor table, not two sets of literals."""

    def test_the_floor_table_is_not_empty_and_names_every_outcome(self):
        self.assertEqual(
            set(fmfe._VOTE_FLOORS),
            {"home_win", "draw", "away_win"},
        )
        for outcome, floor in fmfe._VOTE_FLOORS.items():
            self.assertGreater(floor, 0.0, f"{outcome} floor must be positive")

    def test_floored_3way_leaves_no_arm_at_zero(self):
        """The floors are applied *before* normalising, so the guarantee is
        strict positivity, not "at least the floor" -- dividing by a total
        above 1.0 pushes the floored arm back down. That is the pre-existing
        semantics of ``_adjust_home_edge`` and is what the shrinker inherits.
        """
        voted = fmfe._floored_3way({"home_win": 0.0, "draw": 0.0, "away_win": 1.0})
        for outcome, value in voted.items():
            self.assertGreater(value, 0.0, outcome)
        self.assertAlmostEqual(sum(voted.values()), 1.0, places=9)

    def test_adjust_home_edge_leaves_no_arm_at_zero(self):
        # An edge far past anything a factor can produce, both directions.
        for delta in (5.0, -5.0):
            voted = fmfe._adjust_home_edge(dict(fmfe._NEUTRAL_3WAY), delta)
            for outcome, value in voted.items():
                self.assertGreater(value, 0.0, f"delta={delta} {outcome}")

    def _min_arm(self, raise_floors: bool) -> tuple[float, float]:
        """Smallest arm each path produces, optionally with the floors raised."""
        original = dict(fmfe._VOTE_FLOORS)
        try:
            if raise_floors:
                fmfe._VOTE_FLOORS.update(
                    {"home_win": 0.30, "draw": 0.30, "away_win": 0.30},
                )
            edge = min(
                fmfe._adjust_home_edge(dict(fmfe._NEUTRAL_3WAY), 5.0).values(),
            )
            shrunk = min(fmfe._shrink_h2h(1.0, 0.0, {"h2h_matches": 500.0}))
        finally:
            fmfe._VOTE_FLOORS.clear()
            fmfe._VOTE_FLOORS.update(original)
        self.assertEqual(fmfe._VOTE_FLOORS, original, "floors were not restored")
        return edge, shrunk

    def test_both_paths_read_the_table_rather_than_their_own_literals(self):
        """Raising the shared floors must move *both* paths, or they are not
        sharing -- which is exactly the state this refactor removed: the floors
        used to be inline literals in ``_adjust_home_edge`` and the h2h vote
        never reached them.
        """
        base_edge, base_shrunk = self._min_arm(raise_floors=False)
        high_edge, high_shrunk = self._min_arm(raise_floors=True)
        self.assertGreater(
            high_edge, base_edge,
            "_adjust_home_edge ignored the shared floor table",
        )
        self.assertGreater(
            high_shrunk, base_shrunk,
            "_shrink_h2h ignored the shared floor table",
        )


class NoFactorCallsAnOutcomeImpossibleTests(unittest.TestCase):
    """The census that names the defect, read from production's own output.

    Every factor reports its vote in its ``ContributionItem.detail`` as
    ``H=x D=y A=z``, so this needs no mock and no spy -- it parses what an
    operator sees. Driven to their legal extremes, the widest sibling was elo
    at ``H=0.590`` while h2h reached ``H=1.000``.
    """

    #: Inputs that push every factor as far from neutral as it can legally go.
    _EXTREME = {
        "team": {
            "elo_home": 1900.0, "elo_away": 1700.0,
            "form_home": 1.0, "form_away": 0.0,
            "h2h_home_win_rate": 1.0, "h2h_draw_rate": 0.0,
            "market_value_home": 9e8, "market_value_away": 1e7,
        },
        "market": {"home_odds": 1.5, "draw_odds": 4.0, "away_odds": 7.0},
        "player": {"injury_impact_home": 0.0, "injury_impact_away": 1.0},
        "environment": {"altitude_m": 3000.0},
        "general": {
            "rest_days_home": 7, "rest_days_away": 1,
            "travel_distance_km": 9000.0,
        },
        "custom": {
            "elo_source": "measured",
            "xg_home": 5.0, "xg_away": 0.0,
            "referee_home_win_rate": 0.95,
            "travel_km_away": 9000.0, "timezone_offset_hours_away": 8.0,
            "possession_home": 0.9, "possession_away": 0.1,
        },
        "meta": {},
    }

    def _votes(self) -> dict[str, dict[str, float]]:
        match = _identity()
        raw = {key: dict(value) for key, value in self._EXTREME.items()}
        result = FootballMultiFactorEngine().predict(
            FootballFeatureBuilder().build(match, raw), match,
        )
        votes: dict[str, dict[str, float]] = {}
        for item in result.explanation:
            if not item.available:
                continue
            found = _DETAIL_RE.search(item.detail or "")
            if found:
                votes[item.factor] = {
                    "home_win": float(found.group(1)),
                    "draw": float(found.group(2)),
                    "away_win": float(found.group(3)),
                }
        return votes

    def test_the_census_reaches_enough_factors_to_mean_anything(self):
        votes = self._votes()
        self.assertGreaterEqual(
            len(votes), 9, f"census too small to be evidence: {sorted(votes)}",
        )
        self.assertIn("h2h", votes, "the factor under test must be in the census")

    def test_no_factor_votes_an_arm_at_zero_or_one(self):
        for factor, vote in self._votes().items():
            with self.subTest(factor=factor):
                for outcome, value in vote.items():
                    self.assertGreater(value, 0.0, f"{factor}/{outcome} is 0")
                    self.assertLess(value, 1.0, f"{factor}/{outcome} is 1")

    def test_h2h_is_no_longer_the_most_extreme_voter(self):
        votes = self._votes()
        h2h_spread = max(votes["h2h"].values()) - min(votes["h2h"].values())
        others = {
            factor: max(vote.values()) - min(vote.values())
            for factor, vote in votes.items() if factor != "h2h"
        }
        self.assertTrue(others, "nothing to compare h2h against")
        widest = max(others, key=lambda key: others[key])
        self.assertLessEqual(
            h2h_spread,
            others[widest] * 1.10,
            f"h2h spread {h2h_spread:.3f} still exceeds "
            f"{widest} at {others[widest]:.3f}",
        )


class TheSixLiveVectorsTests(unittest.TestCase):
    """Exhaustive over the rate pairs a 2-match sample can actually produce.

    Not imagined: these six are every value measured across the 512 upcoming
    club fixtures that get an h2h vote, with the fixture count on each. All six
    were degenerate before; three of them had an arm at exactly 1.0.
    """

    def test_the_vector_table_covers_the_measured_corpus(self):
        self.assertEqual(len(_LIVE_VECTORS), 6, "the measured corpus had six")
        self.assertEqual(
            sum(count for _, _, count in _LIVE_VECTORS), 512,
            "fixture counts must add up to the 512 measured voting fixtures",
        )
        # Every one is degenerate as a raw rate -- that is the defect.
        for home, draw, _ in _LIVE_VECTORS:
            away = round(1.0 - home - draw, 6)
            self.assertTrue(
                {home, draw, away} & {0.0, 1.0},
                f"{home}/{draw} is not degenerate, so it is not the defect",
            )

    def test_none_of_the_six_still_calls_an_outcome_impossible(self):
        for home, draw, count in _LIVE_VECTORS:
            with self.subTest(home=home, draw=draw, fixtures=count):
                voted = fmfe._shrink_h2h(home, draw, {"h2h_matches": 2.0})
                for value in voted:
                    self.assertGreater(value, 0.0)
                    self.assertLess(value, 1.0)
                self.assertAlmostEqual(sum(voted), 1.0, places=9)

    def test_the_six_shrink_to_the_measured_vectors(self):
        expected = {
            (0.0, 0.0): (0.200, 0.150, 0.650),
            (0.0, 0.5): (0.200, 0.400, 0.400),
            (0.0, 1.0): (0.200, 0.650, 0.150),
            (0.5, 0.0): (0.450, 0.150, 0.400),
            (0.5, 0.5): (0.450, 0.400, 0.150),
            (1.0, 0.0): (0.700, 0.150, 0.150),
        }
        self.assertEqual(
            set(expected),
            {(home, draw) for home, draw, _ in _LIVE_VECTORS},
            "the expectation table drifted from the measured corpus",
        )
        for (home, draw), want in expected.items():
            with self.subTest(home=home, draw=draw):
                got = fmfe._shrink_h2h(home, draw, {"h2h_matches": 2.0})
                for index, outcome in enumerate(("home", "draw", "away")):
                    self.assertAlmostEqual(
                        got[index], want[index], places=3, msg=outcome,
                    )

    def test_the_engine_movement_from_two_matches_is_bounded(self):
        """|delta home_win| was up to 0.118 before; state the new bound."""
        base, _, base_vote = _predict()
        self.assertIsNone(base_vote, "the baseline must have no h2h vote")
        deltas = []
        for home, draw, _ in _LIVE_VECTORS:
            probs, _, vote = _predict(
                h2h_home=home, h2h_draw=draw, custom={"h2h_matches": 2.0},
            )
            self.assertIsNotNone(vote, "the h2h factor must have voted")
            deltas.append(abs(probs["home_win"] - base["home_win"]))
        self.assertLess(
            max(deltas), 0.09,
            f"two matches still move home_win by {max(deltas):.4f}",
        )


class ARealHistoryStillSpeaksTests(unittest.TestCase):
    """A ramp, not a clamp: the national corpus has 23.8% of pairings at n>=8.

    A flat bound would treat a 20-meeting record the same as a 2-meeting one.
    These tests are the reason the fix is shrinkage toward a prior.
    """

    #: Meeting counts spanning the measured national distribution.
    _LADDER = (1.0, 2.0, 3.0, 4.0, 8.0, 20.0)

    def test_the_ladder_spans_the_measured_corpus(self):
        self.assertGreaterEqual(len(self._LADDER), 5)
        self.assertEqual(min(self._LADDER), 1.0, "26.6% of pairings have n=1")
        self.assertGreaterEqual(
            max(self._LADDER), 20.0, "6.8% of pairings have n>=20",
        )

    def test_a_thicker_sample_votes_strictly_closer_to_its_own_rate(self):
        previous = None
        for n in self._LADDER:
            with self.subTest(n=n):
                home = fmfe._shrink_h2h(1.0, 0.0, {"h2h_matches": n})[0]
                if previous is not None:
                    self.assertGreater(
                        home, previous,
                        f"n={n} did not vote more strongly than the step below",
                    )
                previous = home

    def test_twenty_meetings_are_close_to_the_raw_rate(self):
        home = fmfe._shrink_h2h(1.0, 0.0, {"h2h_matches": 20.0})[0]
        self.assertGreater(
            home, 0.90, f"a 20-match record was damped to {home:.4f}",
        )
        self.assertLess(home, 1.0, "and still may not call an outcome impossible")

    def test_one_full_season_is_worth_exactly_the_prior(self):
        """``k`` states the prior's weight in matches; a league pairing's
        complete same-season history is exactly two matches, so at n=2 the
        empirical rate and the prior must carry equal weight.
        """
        self.assertEqual(fmfe._H2H_PRIOR_MATCHES, 2.0)
        neutral = fmfe._NEUTRAL_3WAY["home_win"]
        home = fmfe._shrink_h2h(1.0, 0.0, {"h2h_matches": 2.0})[0]
        self.assertAlmostEqual(home, (1.0 + neutral) / 2.0, places=6)

    def test_the_engine_ranks_a_long_record_above_a_short_one(self):
        base, _, _ = _predict()
        thin, _, _ = _predict(
            h2h_home=1.0, h2h_draw=0.0, custom={"h2h_matches": 2.0},
        )
        thick, _, _ = _predict(
            h2h_home=1.0, h2h_draw=0.0, custom={"h2h_matches": 20.0},
        )
        self.assertLess(base["home_win"], thin["home_win"])
        self.assertLess(thin["home_win"], thick["home_win"])


class AnUnmeasuredSampleSizeIsNotALargeOneTests(unittest.TestCase):
    """Absent or unusable ``h2h_matches`` must not buy full trust.

    With the floors alone a 100%-home record still voted ``H=0.943``, more
    extreme than any sibling factor can reach, so the fallback is the weakest
    weight the ramp can express rather than none.
    """

    def test_absent_sample_size_matches_the_declared_fallback(self):
        absent = fmfe._shrink_h2h(1.0, 0.0, {})
        declared = fmfe._shrink_h2h(
            1.0, 0.0, {"h2h_matches": fmfe._UNKNOWN_H2H_MATCHES},
        )
        self.assertEqual(absent, declared)

    def test_the_fallback_is_the_thinnest_meaningful_sample(self):
        self.assertEqual(fmfe._UNKNOWN_H2H_MATCHES, 1.0)

    def test_absent_sample_size_is_damped_below_a_real_pair(self):
        absent = fmfe._shrink_h2h(1.0, 0.0, {})[0]
        two = fmfe._shrink_h2h(1.0, 0.0, {"h2h_matches": 2.0})[0]
        self.assertLess(absent, two)
        self.assertLess(
            absent, 0.65,
            f"an unmeasured sample still voted {absent:.4f}",
        )

    def test_unusable_values_fall_back_rather_than_raising(self):
        absent = fmfe._shrink_h2h(1.0, 0.0, {})
        # NaN included deliberately: ``n > 0`` is False for it, so it reaches
        # the same fallback rather than propagating through the arithmetic.
        for bad in (None, 0, 0.0, -1.0, -7, "", "abc", [], {}, float("nan")):
            with self.subTest(value=bad):
                got = fmfe._shrink_h2h(1.0, 0.0, {"h2h_matches": bad})
                for value in got:
                    self.assertGreater(value, 0.0)
                    self.assertLess(value, 1.0)
                self.assertEqual(got, absent, f"{bad!r} was not treated as absent")

    def test_a_numeric_string_is_honoured(self):
        self.assertEqual(
            fmfe._shrink_h2h(1.0, 0.0, {"h2h_matches": "6"}),
            fmfe._shrink_h2h(1.0, 0.0, {"h2h_matches": 6.0}),
        )


class TheSampleSizeTravelsWithItsOwnRatesTests(unittest.TestCase):
    """The count written must be the denominator of the rates written.

    ``aggregate_h2h_meetings`` caps at ``max_matches``, so ``matches_played``
    is ``len(selected)`` -- the same denominator the two rates use. Writing a
    different number (the raw meeting count, say) would shrink by a sample the
    rates were not computed over.
    """

    def test_matches_played_is_the_rates_denominator(self):
        from app.sports.football.h2h import H2HMeeting, aggregate_h2h_meetings

        meetings = [
            H2HMeeting(played_on=None, home_goals=2, away_goals=0,
                       current_home_hosted=True),
            H2HMeeting(played_on=None, home_goals=1, away_goals=1,
                       current_home_hosted=False),
            H2HMeeting(played_on=None, home_goals=0, away_goals=3,
                       current_home_hosted=True),
        ]
        agg = aggregate_h2h_meetings(meetings, max_matches=20, data_source="t")
        self.assertIsNotNone(agg)
        played = agg["matches_played"]
        self.assertEqual(played, 3)
        self.assertEqual(
            agg["home_wins"] + agg["draws"] + agg["away_wins"], played,
            "the three outcome counts must partition matches_played",
        )

    def test_the_cap_moves_the_denominator_with_the_rates(self):
        from app.sports.football.h2h import H2HMeeting, aggregate_h2h_meetings

        meetings = [
            H2HMeeting(played_on=None, home_goals=2, away_goals=0,
                       current_home_hosted=True)
            for _ in range(30)
        ]
        agg = aggregate_h2h_meetings(meetings, max_matches=20, data_source="t")
        self.assertIsNotNone(agg)
        self.assertEqual(
            agg["matches_played"], 20,
            "the count must be the capped sample, not the raw meeting total",
        )

    def test_the_enricher_writes_the_count_in_the_same_block_as_the_rates(self):
        """An AST partition over the one writer: both must be reachable from
        the same ``if h2h:`` body, or a future edit can keep the rates and drop
        the count again -- which is the defect this closes.
        """
        tree = ast.parse(_SHARED_SRC.read_text(encoding="utf-8-sig"))
        rate_targets = {"h2h_home_win_rate", "h2h_draw_rate"}
        # ``ast.unparse`` normalises quoting, so match the bare identifier
        # rather than a quoted literal -- the first version of this scan looked
        # for '"h2h_matches"' and found nothing in correct code.
        blocks = 0
        found_count = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            body_src = "\n".join(ast.unparse(stmt) for stmt in node.body)
            if not all(name in body_src for name in rate_targets):
                continue
            blocks += 1
            found_count += body_src.count("h2h_matches")
        self.assertEqual(
            blocks, 1,
            "expected exactly one block writing both h2h rates",
        )
        self.assertGreaterEqual(
            found_count, 1,
            "the rates are written without h2h_matches beside them",
        )

    def test_the_rates_have_exactly_one_writer(self):
        """If a second writer appears it needs its own count, so this pins the
        population the test above is asserting over (the football adapter has
        two composition roots; the World Cup one does not write h2h at all).
        """
        writers = [
            path for path in Path("app").rglob("*.py")
            if 'h2h_home_win_rate"]' in path.read_text(encoding="utf-8-sig")
        ]
        self.assertEqual(
            [str(p).replace("\\", "/") for p in writers],
            ["app/sports/football/adapters/_shared.py"],
            "a new h2h rate writer must also write h2h_matches",
        )


class TheVenueBlendStillWorksTests(unittest.TestCase):
    """Order matters: blend chooses *which* rate, shrinkage how far to trust it.

    ``_blend_h2h_venue`` (flag-gated, default OFF) mixes the overall rate with
    the current home team's own-venue subset using its own
    ``clamp(n / _MIN_VENUE_SAMPLES)`` ramp. That mechanism must be untouched --
    its existing tests assert relative order, which monotone shrinkage
    preserves, so they cannot catch a wrong composition order on their own.
    """

    _VENUE_STRONG = {
        "h2h_home_venue_matches": 6.0,
        "h2h_home_venue_win_rate": 0.90,
        "h2h_home_venue_draw_rate": 0.05,
        "h2h_matches": 8.0,
    }

    def _home(self, custom: dict) -> float:
        probs, _, _ = _predict(h2h_home=0.45, h2h_draw=0.28, custom=custom)
        return probs["home_win"]

    def test_the_flag_is_off_by_default(self):
        from app.core import config

        self.assertFalse(config.settings.FOOTBALL_H2H_VENUE_SPLIT_ENABLED)

    def test_with_the_flag_off_venue_keys_change_nothing(self):
        plain = self._home({"h2h_matches": 8.0})
        with_keys = self._home(dict(self._VENUE_STRONG))
        self.assertAlmostEqual(plain, with_keys, places=9)

    def test_with_the_flag_on_the_venue_record_still_moves_the_vote(self):
        from unittest.mock import patch

        from app.core import config

        with patch.object(
            config.settings, "FOOTBALL_H2H_VENUE_SPLIT_ENABLED", True,
        ):
            plain = self._home({"h2h_matches": 8.0})
            with_keys = self._home(dict(self._VENUE_STRONG))
        self.assertGreater(
            with_keys, plain,
            "shrinkage swallowed the venue blend instead of composing with it",
        )

    def test_the_shrinkage_applies_to_the_blended_rate_not_the_raw_one(self):
        """With the flag on, changing only the *total* sample size must still
        move the output -- which is only true if the shrinkage runs after the
        blend and reads the total, not the venue subset.
        """
        from unittest.mock import patch

        from app.core import config

        thin = dict(self._VENUE_STRONG, h2h_matches=2.0)
        thick = dict(self._VENUE_STRONG, h2h_matches=20.0)
        with patch.object(
            config.settings, "FOOTBALL_H2H_VENUE_SPLIT_ENABLED", True,
        ):
            self.assertLess(self._home(thin), self._home(thick))

    def test_the_two_ramps_are_deliberately_different_shapes(self):
        """``clamp(n / k)`` reaches the subset outright; ``n / (n + k)`` never
        fully discards the prior. Both constants exist and are not the same
        mechanism -- if a future edit unifies them, this states the reason.
        """
        self.assertEqual(fmfe._MIN_VENUE_SAMPLES, 4.0)
        self.assertEqual(fmfe._H2H_PRIOR_MATCHES, 2.0)
        # The venue ramp saturates; the shrinkage ramp does not.
        self.assertEqual(
            fmfe._clamp(fmfe._MIN_VENUE_SAMPLES / fmfe._MIN_VENUE_SAMPLES, 0.0, 1.0),
            1.0,
        )
        big = 10_000.0
        self.assertLess(big / (big + fmfe._H2H_PRIOR_MATCHES), 1.0)


class ThroughTheProductionDoorTests(unittest.TestCase):
    """The real enricher, on a real kernel DB, on the real n=2 club shape.

    Every assertion above supplies ``h2h_matches`` itself, which is exactly the
    seam a stub can hold open. This class never writes that key: it builds two
    finished fixtures plus their result rows, lets
    ``h2h_meetings_from_kernel`` -> ``aggregate_h2h_meetings`` ->
    ``enrich_situational_features`` produce the whole payload, and then asserts
    the engine's own vote.
    """

    _HOME = "Alpha FC"
    _AWAY = "Beta FC"

    def setUp(self):
        from app.kernel import kernel_db

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = str(Path(self._tmp.name) / "kernel.db")
        kernel_db.close_kernel_db()
        kernel_db.init_kernel_db(self.db_path)
        self.addCleanup(kernel_db.close_kernel_db)
        engine = kernel_db._engine
        self.assertIsNotNone(engine, "init_kernel_db left the engine unset")
        self.assertEqual(
            Path(str(engine.url.database)).resolve(),
            Path(self.db_path).resolve(),
            "the kernel engine is not on the temp DB",
        )
        self._seed_two_meetings()

    def _seed_two_meetings(self):
        """One home-and-away pair: the entire h2h history of a league season."""
        from app.kernel.kernel_db import (
            KernelMatchFixture,
            KernelMatchResult,
            get_kernel_session,
        )

        base = datetime(2025, 10, 1, tzinfo=timezone.utc)
        rows = [
            # Alpha hosted and won 2-0.
            ("epl-h1", self._HOME, self._AWAY, 2, 0, "home_win", base),
            # Beta hosted and won 1-0, i.e. Alpha lost away.
            ("epl-h2", self._AWAY, self._HOME, 1, 0, "home_win",
             base + timedelta(days=90)),
        ]
        session = get_kernel_session()
        try:
            for match_id, home, away, hs, aws, outcome, kickoff in rows:
                session.add(KernelMatchFixture(
                    match_id=match_id, competition="epl",
                    season="2025-26", home_team=home, away_team=away,
                    kickoff_utc=kickoff, status="finished",
                    home_score=hs, away_score=aws, created_at=kickoff,
                ))
                session.add(KernelMatchResult(
                    match_id=match_id, home_score=hs, away_score=aws,
                    outcome=outcome, finished_at=kickoff, created_at=kickoff,
                ))
            session.commit()
        finally:
            session.close()

    def _enriched(self) -> dict:
        from app.sports.football.adapters._shared import (
            enrich_situational_features,
        )

        match = _identity(home=self._HOME, away=self._AWAY)
        raw = _raw()
        enrich_situational_features(raw, match)
        return raw

    def test_the_kernel_really_holds_the_two_meetings(self):
        from app.sports.football.club_form import h2h_meetings_from_kernel

        meetings = h2h_meetings_from_kernel(
            self._HOME, self._AWAY, competition="epl",
            before=datetime(2026, 9, 15, tzinfo=timezone.utc),
        )
        self.assertEqual(
            len(meetings), 2,
            "the fixture is not in the measured n=2 state this class is about",
        )

    def test_the_enricher_produces_the_sample_size_unaided(self):
        raw = self._enriched()
        self.assertIn(
            "h2h_home_win_rate", raw["team"],
            "the enricher produced no h2h rates, so there is nothing to shrink",
        )
        self.assertEqual(
            raw["custom"].get("h2h_matches"), 2.0,
            f"h2h_matches missing or wrong: {raw['custom'].get('h2h_matches')!r}",
        )

    def test_the_sample_size_equals_the_rates_denominator(self):
        raw = self._enriched()
        played = raw["custom"]["h2h_matches"]
        # Alpha won at home, lost away: 1 win, 0 draws, 1 loss over 2 meetings.
        self.assertAlmostEqual(raw["team"]["h2h_home_win_rate"], 0.5, places=6)
        self.assertAlmostEqual(raw["team"]["h2h_draw_rate"], 0.0, places=6)
        self.assertEqual(played, 2.0)

    def test_the_engines_own_vote_is_not_degenerate(self):
        raw = self._enriched()
        match = _identity(home=self._HOME, away=self._AWAY)
        result = FootballMultiFactorEngine().predict(
            FootballFeatureBuilder().build(match, raw), match,
        )
        item = next(i for i in result.explanation if i.factor == "h2h")
        self.assertTrue(item.available, "the h2h factor did not vote")
        found = _DETAIL_RE.search(item.detail or "")
        self.assertIsNotNone(found, f"unparseable h2h detail: {item.detail!r}")
        vote = [float(found.group(i)) for i in (1, 2, 3)]
        for value in vote:
            self.assertGreater(value, 0.0, f"vote {vote} calls an outcome out")
            self.assertLess(value, 1.0, f"vote {vote} asserts certainty")
        # The raw 0.5/0.0/0.5 rate would have voted draw at exactly 0.0.
        self.assertAlmostEqual(vote[0], 0.450, places=3)
        self.assertAlmostEqual(vote[1], 0.150, places=3)
        self.assertAlmostEqual(vote[2], 0.400, places=3)

    def test_removing_the_sample_size_changes_the_vote(self):
        """Proves the engine reads the key the enricher wrote, rather than
        happening to agree with it.
        """
        raw = self._enriched()
        match = _identity(home=self._HOME, away=self._AWAY)
        stripped = {**raw, "custom": {
            key: value for key, value in raw["custom"].items()
            if key != "h2h_matches"
        }}
        with_size = FootballMultiFactorEngine().predict(
            FootballFeatureBuilder().build(match, raw), match,
        ).outcome_probabilities
        without = FootballMultiFactorEngine().predict(
            FootballFeatureBuilder().build(match, stripped), match,
        ).outcome_probabilities
        self.assertNotAlmostEqual(
            with_size["home_win"], without["home_win"], places=6,
            msg="the engine ignored h2h_matches",
        )
