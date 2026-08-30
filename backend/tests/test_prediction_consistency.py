# backend/tests/test_prediction_consistency.py
"""Tests for the prediction self-consistency census (E18).

Two groups:

- unit tests on the pure verdict helpers, including the three-outcome rule
  (an unreadable payload must not be counted as agreement);
- behavioural tests that pin the *cause* on the real engines -- holding Elo
  fixed and flipping only the non-Elo evidence must leave the scoreline
  unchanged for the three Elo-only engines, and must move it for the
  probability-derived path.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

import pytest

from app.services.prediction_consistency_service import (
    ELO_ONLY_SCORE_ENGINES,
    STATUS_NO_PREDICTIONS,
    VERDICT_CONSISTENT,
    VERDICT_CONTRADICTS,
    VERDICT_SCORE_EVEN,
    VERDICT_UNREADABLE,
    WINNER_AWAY,
    WINNER_DRAW,
    WINNER_EVEN,
    WINNER_HOME,
    collect_prediction_consistency,
    consistency_verdict,
    probability_winner,
    score_winner,
)


class ScoreWinnerTests(unittest.TestCase):
    def test_names_the_higher_side(self):
        self.assertEqual(score_winner({"home": 3.4, "away": 2.1}), WINNER_HOME)
        self.assertEqual(score_winner({"home": 2.1, "away": 3.4}), WINNER_AWAY)

    def test_equal_scores_name_nobody(self):
        self.assertEqual(score_winner({"home": 2.8, "away": 2.8}), WINNER_EVEN)

    def test_unreadable_payloads_return_none(self):
        for payload in (
            None,
            [],
            "3-1",
            {},
            {"home": 1.0},
            {"home": 1.0, "away": None},
            {"home": 1.0, "away": "2"},
            {"home": float("nan"), "away": 1.0},
            {"home": float("inf"), "away": 1.0},
        ):
            with self.subTest(payload=payload):
                self.assertIsNone(score_winner(payload))

    def test_bool_is_not_a_score(self):
        # True == 1.0 in Python; a bool here is a corrupt payload, not a 1-0 win.
        self.assertIsNone(score_winner({"home": True, "away": 0.0}))


class ProbabilityWinnerTests(unittest.TestCase):
    def test_binary_argmax(self):
        self.assertEqual(
            probability_winner({"home_win": 0.61, "away_win": 0.39}), WINNER_HOME
        )
        self.assertEqual(
            probability_winner({"home_win": 0.4794, "away_win": 0.5206}), WINNER_AWAY
        )

    def test_three_way_draw_argmax(self):
        self.assertEqual(
            probability_winner({"home_win": 0.30, "draw": 0.42, "away_win": 0.28}),
            WINNER_DRAW,
        )

    def test_three_way_still_reads_home_and_away(self):
        self.assertEqual(
            probability_winner({"home_win": 0.50, "draw": 0.26, "away_win": 0.24}),
            WINNER_HOME,
        )

    def test_exact_tie_names_nobody(self):
        self.assertEqual(
            probability_winner({"home_win": 0.5, "away_win": 0.5}), WINNER_EVEN
        )

    def test_unreadable_payloads_return_none(self):
        for payload in (None, [], {}, {"home_win": 0.6}, {"home_win": 0.6, "away_win": "x"}):
            with self.subTest(payload=payload):
                self.assertIsNone(probability_winner(payload))


class ConsistencyVerdictTests(unittest.TestCase):
    def test_agreement(self):
        verdict, problem = consistency_verdict(
            {"home": 3.4, "away": 2.1}, {"home_win": 0.61, "away_win": 0.39}
        )
        self.assertEqual(verdict, VERDICT_CONSISTENT)
        self.assertIsNone(problem)

    def test_opposite_sides_contradict(self):
        verdict, problem = consistency_verdict(
            {"home": 3.4, "away": 2.1}, {"home_win": 0.4794, "away_win": 0.5206}
        )
        self.assertEqual(verdict, VERDICT_CONTRADICTS)
        assert problem is not None
        self.assertIn(WINNER_HOME, problem)
        self.assertIn(WINNER_AWAY, problem)

    def test_level_scoreline_is_its_own_verdict(self):
        # The Elo-missing degenerate case: margin is exactly 0.0 no matter what
        # the other factors voted, so the scoreline names nobody.
        verdict, problem = consistency_verdict(
            {"home": 2.8, "away": 2.8}, {"home_win": 0.4997, "away_win": 0.5003}
        )
        self.assertEqual(verdict, VERDICT_SCORE_EVEN)
        assert problem is not None
        self.assertIn("level", problem)

    def test_a_draw_argmax_is_not_a_contradiction(self):
        # The football path derives the scoreline *from* these probabilities, so
        # a draw argmax beside a home-leaning scoreline is coherent. Alarming on
        # it would make the check broader than its meaning.
        verdict, _ = consistency_verdict(
            {"home": 1.4, "away": 1.2},
            {"home_win": 0.34, "draw": 0.36, "away_win": 0.30},
        )
        self.assertEqual(verdict, VERDICT_CONSISTENT)

    def test_unreadable_is_not_agreement(self):
        # A check has three outcomes, not two. "Could not evaluate" must never
        # land in the same bucket as "the two fields agree".
        verdict, problem = consistency_verdict(None, {"home_win": 0.6, "away_win": 0.4})
        self.assertEqual(verdict, VERDICT_UNREADABLE)
        self.assertNotEqual(verdict, VERDICT_CONSISTENT)
        assert problem is not None
        self.assertIn("predicted_scores", problem)

    def test_unreadable_names_both_missing_fields(self):
        verdict, problem = consistency_verdict(None, None)
        self.assertEqual(verdict, VERDICT_UNREADABLE)
        assert problem is not None
        self.assertIn("predicted_scores", problem)
        self.assertIn("outcome_probabilities", problem)

    def test_every_verdict_constant_is_distinct(self):
        verdicts = {
            VERDICT_CONSISTENT,
            VERDICT_CONTRADICTS,
            VERDICT_SCORE_EVEN,
            VERDICT_UNREADABLE,
        }
        self.assertEqual(len(verdicts), 4)


def _features(*, sport, elo_home, elo_away, favour):
    """One FeatureSet per sport with Elo fixed and non-Elo evidence swung.

    ``favour`` is "home" or "away" and moves every non-Elo knob the engine
    reads to that side. Elo is passed through untouched so the pair differs
    only in evidence the scoreline is claimed to be blind to.
    """
    from app.kernel.domain import (
        CompetitionIdentity,
        EnvironmentFeatures,
        FeatureSet,
        GeneralFeatures,
        MarketFeatures,
        MatchIdentity,
        PlayerFeatures,
        SeasonIdentity,
        SportIdentity,
        TeamFeatures,
        TeamIdentity,
    )

    good, bad = (0.80, 0.20) if favour == WINNER_HOME else (0.20, 0.80)
    rest_h, rest_a = (3.0, 0.0) if favour == WINNER_HOME else (0.0, 3.0)
    comp_code = {"basketball": "nba", "baseball": "mlb", "hockey": "nhl"}[sport]
    sport_id = SportIdentity(code=sport, name=sport.title())
    comp = CompetitionIdentity(code=comp_code, name=comp_code.upper(), sport=sport_id)
    season = SeasonIdentity(competition=comp, season_key="2024")
    home = TeamIdentity(code="HHH", name="Home Club", competition=comp)
    away = TeamIdentity(code="AAA", name="Away Club", competition=comp)
    match = MatchIdentity(
        match_id=f"{comp_code}-consistency-probe",
        season=season,
        stage="regular_season",
        round=None,
        home=home,
        away=away,
        kickoff_utc=datetime(2024, 1, 15, tzinfo=timezone.utc),
    )
    custom: dict[str, object] = {}
    if sport == "basketball":
        custom = {
            "ortg_home": 118.0 if favour == WINNER_HOME else 100.0,
            "ortg_away": 100.0 if favour == WINNER_HOME else 118.0,
            "drtg_home": 100.0 if favour == WINNER_HOME else 118.0,
            "drtg_away": 118.0 if favour == WINNER_HOME else 100.0,
            "injury_impact_home": 0.0 if favour == WINNER_HOME else 0.4,
            "injury_impact_away": 0.4 if favour == WINNER_HOME else 0.0,
        }
    elif sport == "baseball":
        custom = {
            "pitcher_era_home": 2.0 if favour == WINNER_HOME else 6.0,
            "pitcher_era_away": 6.0 if favour == WINNER_HOME else 2.0,
            "bullpen_era_home": 2.0 if favour == WINNER_HOME else 6.0,
            "bullpen_era_away": 6.0 if favour == WINNER_HOME else 2.0,
            "ops_home": 0.90 if favour == WINNER_HOME else 0.60,
            "ops_away": 0.60 if favour == WINNER_HOME else 0.90,
        }
    else:
        custom = {
            "goalie_save_pct_home": 0.94 if favour == WINNER_HOME else 0.88,
            "goalie_save_pct_away": 0.88 if favour == WINNER_HOME else 0.94,
            "corsi_pct_home": 60.0 if favour == WINNER_HOME else 40.0,
            "corsi_pct_away": 40.0 if favour == WINNER_HOME else 60.0,
        }
    return FeatureSet(
        match=match,
        general=GeneralFeatures(rest_h, rest_a, None, None),
        team=TeamFeatures(
            elo_home, elo_away, good, bad, None, None, None, None
        ),
        market=MarketFeatures(None, None, None, None, False),
        player=PlayerFeatures(True, True, None, None),
        environment=EnvironmentFeatures("Arena", None, None, True),
        custom=custom,
        data_quality="real",
        quality_notes=[],
        feature_version=f"{comp_code}-1.0",
    )


def _engine_for(sport):
    if sport == "basketball":
        from app.sports.basketball.engines.basketball_engine import BasketballEngine

        return BasketballEngine()
    if sport == "baseball":
        from app.sports.baseball.engines.baseball_engine import BaseballEngine

        return BaseballEngine()
    from app.sports.hockey.engines.hockey_engine import HockeyEngine

    return HockeyEngine()


class EloOnlyScoreDerivationTests(unittest.TestCase):
    """Pin the cause: the scoreline moves with Elo and with nothing else.

    If one of these engines is later changed to derive its scoreline from the
    fused probabilities, the matching subtest goes red and
    ``ELO_ONLY_SCORE_ENGINES`` is what needs updating.
    """

    def test_swinging_every_non_elo_factor_leaves_the_scoreline_identical(self):
        for sport in sorted(ELO_ONLY_SCORE_ENGINES):
            with self.subTest(sport=sport):
                engine = _engine_for(sport)
                pro_home = _features(
                    sport=sport, elo_home=1500.0, elo_away=1500.0, favour=WINNER_HOME
                )
                pro_away = _features(
                    sport=sport, elo_home=1500.0, elo_away=1500.0, favour=WINNER_AWAY
                )
                a = engine.predict(pro_home, pro_home.match)
                b = engine.predict(pro_away, pro_away.match)
                self.assertEqual(
                    a.predicted_scores,
                    b.predicted_scores,
                    f"{sport}: scoreline moved, so it is no longer Elo-only",
                )
                self.assertNotEqual(
                    a.outcome_probabilities,
                    b.outcome_probabilities,
                    f"{sport}: the non-Elo swing did not reach the fusion either, "
                    f"so this fixture proves nothing",
                )

    def test_the_two_published_claims_name_opposite_winners(self):
        # The defect, end to end, on the real engines. With Elo level, home
        # advantage alone puts the home side ahead on the scoreline while the
        # fused probabilities name the away side. All three do this.
        for sport in sorted(ELO_ONLY_SCORE_ENGINES):
            with self.subTest(sport=sport):
                engine = _engine_for(sport)
                fs = _features(
                    sport=sport, elo_home=1500.0, elo_away=1500.0, favour=WINNER_AWAY
                )
                result = engine.predict(fs, fs.match)
                self.assertEqual(
                    score_winner(result.predicted_scores), WINNER_HOME, sport
                )
                self.assertEqual(
                    probability_winner(result.outcome_probabilities),
                    WINNER_AWAY,
                    sport,
                )
                verdict, _ = consistency_verdict(
                    result.predicted_scores, result.outcome_probabilities
                )
                self.assertEqual(verdict, VERDICT_CONTRADICTS, sport)


class ProbabilityDerivedScoresAgreeTests(unittest.TestCase):
    """The control: the path that derives scores from the fusion cannot disagree.

    Without this, a census that only ever sees agreement in one direction could
    not tell a working check from one wired to a constant.
    """

    def test_probabilities_to_scores_never_names_the_other_side(self):
        from app.kernel.engines.elo_odds_engine import _probabilities_to_scores

        cases = (
            {"home_win": 0.60, "draw": 0.25, "away_win": 0.15},
            {"home_win": 0.15, "draw": 0.25, "away_win": 0.60},
            {"home_win": 0.20, "draw": 0.60, "away_win": 0.20},
            {"home_win": 0.34, "draw": 0.33, "away_win": 0.33},
            {"home_win": 0.33, "draw": 0.33, "away_win": 0.34},
        )
        for probs in cases:
            with self.subTest(probs=probs):
                scores = _probabilities_to_scores(probs)
                verdict, problem = consistency_verdict(scores, probs)
                self.assertNotEqual(verdict, VERDICT_CONTRADICTS, problem)
                self.assertGreaterEqual(scores["home"], 0.0)
                self.assertGreaterEqual(scores["away"], 0.0)


@pytest.fixture
def kernel(tmp_path):
    """Fresh kernel DB per test, mirroring tests/test_confidence_reliability.py."""
    from app.kernel.kernel_db import (
        close_kernel_session,
        get_kernel_session,
        init_kernel_db,
    )

    init_kernel_db(str(tmp_path / "test_kernel.db"))
    session = get_kernel_session()
    yield session
    session.close()
    close_kernel_session()


def _seed(session, match_id, *, scores, probs, engine, competition="nba"):
    from app.kernel.kernel_db import KernelPrediction

    stamp = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    session.add(
        KernelPrediction(
            match_id=match_id,
            sport=engine,
            competition=competition,
            season="2026",
            engine=engine,
            predicted_scores=scores,
            outcome_probabilities=probs,
            confidence=0.55,
            feature_version=f"{competition}-1.0",
            explanation=[],
            created_at=stamp,
            updated_at=stamp,
        )
    )
    session.commit()


def test_census_names_a_contradicting_row(kernel):
    _seed(
        kernel,
        "nhl-contradiction",
        scores={"home": 3.4, "away": 2.1},
        probs={"home_win": 0.4794, "away_win": 0.5206},
        engine="hockey",
        competition="nhl",
    )
    report = collect_prediction_consistency()
    assert report["total_predictions"] == 1
    assert report["verdicts"][VERDICT_CONTRADICTS] == 1
    assert report["status"] == VERDICT_CONTRADICTS
    assert any("nhl-contradiction" in p for p in report["problems"])
    sample = report["contradicting_samples"][0]
    assert sample["match_id"] == "nhl-contradiction"
    assert sample["scoreline_names"] == WINNER_HOME
    assert sample["probabilities_name"] == WINNER_AWAY
    assert report["engines"]["hockey"]["status"] == VERDICT_CONTRADICTS


def test_an_engine_with_no_rows_is_reported_not_absent(kernel):
    # Seed only hockey. The other two declared engines must still appear, saying
    # they have no rows -- otherwise an engine whose predictions never ran reads
    # as healthy by being missing from the report.
    _seed(
        kernel,
        "nhl-ok",
        scores={"home": 3.4, "away": 2.1},
        probs={"home_win": 0.61, "away_win": 0.39},
        engine="hockey",
        competition="nhl",
    )
    report = collect_prediction_consistency()
    for sport in ELO_ONLY_SCORE_ENGINES:
        assert sport in report["engines"], sport
    assert report["engines"]["baseball"]["status"] == STATUS_NO_PREDICTIONS
    assert report["engines"]["baseball"]["predictions"] == 0
    assert report["engines"]["basketball"]["status"] == STATUS_NO_PREDICTIONS
    assert report["engines"]["hockey"]["status"] == VERDICT_CONSISTENT


def test_an_unreadable_row_is_not_counted_as_agreement(kernel):
    _seed(
        kernel,
        "nhl-corrupt",
        scores={"home": 3.4},
        probs={"home_win": 0.61, "away_win": 0.39},
        engine="hockey",
        competition="nhl",
    )
    report = collect_prediction_consistency()
    assert report["verdicts"][VERDICT_UNREADABLE] == 1
    assert report["verdicts"][VERDICT_CONSISTENT] == 0
    assert report["status"] == VERDICT_UNREADABLE


def test_a_level_scoreline_is_reported_separately(kernel):
    # The live hockey row: Elo was absent, so the margin was exactly 0.0 and the
    # scoreline named nobody while the probabilities named the away side.
    _seed(
        kernel,
        "nhl-2026010012",
        scores={"home": 2.8, "away": 2.8},
        probs={"home_win": 0.4997, "away_win": 0.5003},
        engine="hockey",
        competition="nhl",
    )
    report = collect_prediction_consistency()
    assert report["verdicts"][VERDICT_SCORE_EVEN] == 1
    assert report["verdicts"][VERDICT_CONTRADICTS] == 0
    assert report["verdicts"][VERDICT_CONSISTENT] == 0
    assert report["status"] == VERDICT_SCORE_EVEN


def test_contradiction_outranks_a_level_scoreline(kernel):
    _seed(
        kernel, "a", scores={"home": 2.8, "away": 2.8},
        probs={"home_win": 0.49, "away_win": 0.51}, engine="hockey", competition="nhl",
    )
    _seed(
        kernel, "b", scores={"home": 3.4, "away": 2.1},
        probs={"home_win": 0.47, "away_win": 0.53}, engine="hockey", competition="nhl",
    )
    report = collect_prediction_consistency()
    assert report["total_predictions"] == 2
    assert report["status"] == VERDICT_CONTRADICTS


def test_empty_store_says_no_predictions_not_consistent(kernel):
    report = collect_prediction_consistency()
    assert report["total_predictions"] == 0
    assert report["status"] == STATUS_NO_PREDICTIONS
    assert report["status"] != VERDICT_CONSISTENT


def test_a_probability_derived_engine_is_flagged_as_such(kernel):
    _seed(
        kernel, "epl-1", scores={"home": 1.6, "away": 1.1},
        probs={"home_win": 0.52, "draw": 0.26, "away_win": 0.22},
        engine="elo_odds", competition="epl",
    )
    report = collect_prediction_consistency()
    assert report["engines"]["elo_odds"]["elo_only_scores"] is False
    assert report["engines"]["hockey"]["elo_only_scores"] is True


class RouteIsMountedTests(unittest.TestCase):
    """A census with no route is a value with no reader.

    Asserted against ``app.main``'s own route table rather than a locally built
    ``FastAPI()``, which would be blind to the router never being mounted.
    """

    def test_route_is_registered_on_the_app(self):
        from app.main import app

        paths = {r.path for r in app.routes if hasattr(r, "path")}
        self.assertIn("/api/quality-metrics/prediction-consistency", paths)

    def test_the_handler_returns_the_census_and_not_a_stub(self):
        from app.api.routes.quality_metrics import get_prediction_consistency

        payload = get_prediction_consistency()
        # Pin the keys the census produces, so a handler rewired to a constant
        # or to a different service fails here.
        for key in (
            "generated_at",
            "total_predictions",
            "verdicts",
            "status",
            "problems",
            "engines",
            "contradicting_samples",
        ):
            self.assertIn(key, payload, key)
        self.assertEqual(set(payload["verdicts"]), {
            VERDICT_CONTRADICTS,
            VERDICT_UNREADABLE,
            VERDICT_SCORE_EVEN,
            VERDICT_CONSISTENT,
        })
        for sport in ELO_ONLY_SCORE_ENGINES:
            self.assertIn(sport, payload["engines"], sport)
