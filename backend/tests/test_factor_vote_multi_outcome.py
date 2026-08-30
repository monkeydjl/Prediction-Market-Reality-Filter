# backend/tests/test_factor_vote_multi_outcome.py
"""The 3-way and market-only engines must not invent a vote at a tie (E20).

Two levers here are exact, not approximate:

* ``calculate_btd_probabilities(1500, 1500, is_neutral=True)`` returns
  ``home_win == away_win == 0.3743`` -- equal Elo on a neutral venue.
* ``_odds_to_probabilities(2.5, 3.2, 2.5)`` returns
  ``home_win == away_win == 0.3596`` -- a book quoting both sides identically.

``max(probs, key=probs.get)`` returned ``home_win`` for both, because every
engine's dict lists ``home_win`` first.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

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
from app.kernel.engines.btd_model import calculate_btd_probabilities
from app.kernel.engines.confidence import (
    compute_confidence as real_compute_confidence,
)
from app.kernel.engines.confidence import factor_vote
from app.kernel.engines.elo_odds_engine import (
    EloOddsEngine,
    _odds_to_probabilities,
)
from app.sports.football.engines.football_multi_factor_engine import (
    FootballMultiFactorEngine,
)

_FOOTBALL = SportIdentity(code="football", name="Football")
_EPL = CompetitionIdentity(code="epl", name="Premier League", sport=_FOOTBALL)


def _football_features(
    *,
    elo_home: float | None,
    elo_away: float | None,
    odds: tuple[float, float, float] | None,
) -> FeatureSet:
    season = SeasonIdentity(competition=_EPL, season_key="2024-25")
    home = TeamIdentity(code="HHH", name="Home FC", competition=_EPL)
    away = TeamIdentity(code="AAA", name="Away FC", competition=_EPL)
    match = MatchIdentity(
        match_id="epl-vote-1",
        season=season,
        stage="regular_season",
        round=None,
        home=home,
        away=away,
        kickoff_utc=datetime(2025, 3, 1, tzinfo=timezone.utc),
    )
    odds_h, odds_d, odds_a = odds if odds else (None, None, None)
    return FeatureSet(
        match=match,
        general=GeneralFeatures(None, None, None, None),
        team=TeamFeatures(
            elo_rating_home=elo_home,
            elo_rating_away=elo_away,
            form_home=None,
            form_away=None,
            h2h_home_win_rate=None,
            h2h_draw_rate=None,
            market_value_home=None,
            market_value_away=None,
        ),
        market=MarketFeatures(odds_h, odds_d, odds_a, None, bool(odds)),
        player=PlayerFeatures(None, None, None, None),
        environment=EnvironmentFeatures("Some Ground", None, None, True),
        custom={},
        data_quality="real",
        quality_notes=[],
        feature_version="epl-1.0",
    )


def _row(result, factor: str):
    rows = [e for e in result.explanation if e.factor == factor]
    assert len(rows) == 1, f"expected one {factor} row, got {len(rows)}"
    return rows[0]


class TestTheLeversAreExact:
    """If these drift, the engine tests below stop testing the tie."""

    def test_equal_elo_on_neutral_ground_is_an_exact_tie(self):
        probs = calculate_btd_probabilities(
            1500.0, 1500.0, is_neutral=True, is_knockout=False
        )
        assert probs["home_win"] == probs["away_win"]
        assert probs["home_win"] > probs["draw"], "the tie must be at the peak"
        assert factor_vote(probs) is None
        assert max(probs, key=lambda k: probs[k]) == "home_win", (
            "the pre-fix expression; if this ever stops picking home_win the "
            "defect's direction has changed"
        )

    def test_a_symmetric_book_is_an_exact_tie(self):
        probs = _odds_to_probabilities(2.5, 3.2, 2.5)
        assert probs["home_win"] == probs["away_win"]
        assert probs["home_win"] > probs["draw"]
        assert factor_vote(probs) is None


class TestEloOddsEngine:
    def test_equal_elo_votes_nothing(self):
        feats = _football_features(elo_home=1500.0, elo_away=1500.0, odds=None)
        row = _row(EloOddsEngine().predict(feats, feats.match), "elo")
        assert row.available is True
        assert row.predicted_outcome is None

    @pytest.mark.parametrize(
        "eh,ea,expected",
        [(1600.0, 1500.0, "home_win"), (1500.0, 1600.0, "away_win")],
    )
    def test_unequal_elo_votes_the_stronger_side(self, eh, ea, expected):
        feats = _football_features(elo_home=eh, elo_away=ea, odds=None)
        row = _row(EloOddsEngine().predict(feats, feats.match), "elo")
        assert row.available is True
        assert row.predicted_outcome == expected

    def test_missing_elo_is_unavailable_and_votes_nothing(self):
        feats = _football_features(elo_home=None, elo_away=None, odds=None)
        row = _row(EloOddsEngine().predict(feats, feats.match), "elo")
        assert row.available is False
        assert row.predicted_outcome is None

    def test_symmetric_odds_vote_nothing(self):
        feats = _football_features(
            elo_home=1600.0, elo_away=1500.0, odds=(2.5, 3.2, 2.5)
        )
        result = EloOddsEngine().predict(feats, feats.match)
        odds_row = _row(result, "odds")
        assert odds_row.available is True
        assert odds_row.predicted_outcome is None
        # The Elo factor on the same fixture is not level, so this test cannot
        # pass by the engine returning None for everything.
        assert _row(result, "elo").predicted_outcome == "home_win"

    @pytest.mark.parametrize(
        "odds,expected",
        [((2.0, 3.4, 4.2), "home_win"), ((4.2, 3.4, 2.0), "away_win")],
    )
    def test_asymmetric_odds_vote_the_favourite(self, odds, expected):
        feats = _football_features(elo_home=1500.0, elo_away=1500.0, odds=odds)
        row = _row(EloOddsEngine().predict(feats, feats.match), "odds")
        assert row.available is True
        assert row.predicted_outcome == expected

    def test_missing_odds_are_unavailable_and_vote_nothing(self):
        feats = _football_features(elo_home=1600.0, elo_away=1500.0, odds=None)
        row = _row(EloOddsEngine().predict(feats, feats.match), "odds")
        assert row.available is False
        assert row.predicted_outcome is None


class TestFootballMultiFactorEngine:
    """The 13-factor engine shares the same neutral-Elo lever."""

    def test_equal_elo_votes_nothing(self):
        feats = _football_features(elo_home=1500.0, elo_away=1500.0, odds=None)
        row = _row(
            FootballMultiFactorEngine().predict(feats, feats.match), "elo"
        )
        assert row.available is True
        assert row.predicted_outcome is None

    @pytest.mark.parametrize(
        "eh,ea,expected",
        [(1700.0, 1500.0, "home_win"), (1500.0, 1700.0, "away_win")],
    )
    def test_unequal_elo_votes_the_stronger_side(self, eh, ea, expected):
        feats = _football_features(elo_home=eh, elo_away=ea, odds=None)
        row = _row(
            FootballMultiFactorEngine().predict(feats, feats.match), "elo"
        )
        assert row.available is True
        assert row.predicted_outcome == expected

    def test_the_published_rows_match_the_scored_votes(self):
        """One list, three consumers — the drift guard for this engine too."""
        seen: list[list[str | None]] = []

        def spy(*a, **kw):
            seen.append(list(kw["predicted_outcomes"]))
            return real_compute_confidence(*a, **kw)

        feats = _football_features(elo_home=1500.0, elo_away=1500.0, odds=None)
        with patch(
            "app.sports.football.engines.football_multi_factor_engine"
            ".compute_confidence",
            side_effect=spy,
        ):
            result = FootballMultiFactorEngine().predict(feats, feats.match)

        assert len(seen) == 1
        published = [item.predicted_outcome for item in result.explanation]
        assert seen[0] == published
        # And the level Elo really is a None in that list.
        elo_index = [e.factor for e in result.explanation].index("elo")
        assert seen[0][elo_index] is None
        assert result.explanation[elo_index].available is True
