"""Tests for extract_best_odds — no-odds must yield None, never placeholders.

Regression guard: the function used to return a hardcoded {2.5, 3.2, 3.0}
"fallback" whenever it could not parse a line, and rejected any h2h market
that did not have exactly 3 outcomes. Every two-way sport in
COMPETITION_TO_ODDS_API_SPORT (nba/mlb/nhl) therefore hit that path and had
invented prices stored as real market data — including a "draw" for
basketball.
"""
from app.services.odds_api_service import extract_best_odds


def _fixture(outcomes, *, bookmaker="pinnacle", home="Lakers", away="Celtics"):
    return {
        "home_team": home,
        "away_team": away,
        "bookmakers": [
            {
                "key": bookmaker,
                "title": bookmaker.title(),
                "last_update": "2026-01-01T00:00:00Z",
                "markets": [{"key": "h2h", "outcomes": outcomes}],
            }
        ],
    }


def test_no_bookmakers_returns_none():
    assert extract_best_odds({"home_team": "A", "away_team": "B", "bookmakers": []}) is None


def test_unparseable_line_returns_none_not_placeholder():
    # Outcome names match neither team, so nothing can be mapped.
    fixture = _fixture([{"name": "Someone Else", "price": 1.9},
                        {"name": "Another", "price": 2.1}])
    assert extract_best_odds(fixture) is None


def test_two_way_market_is_accepted_with_no_draw():
    fixture = _fixture([{"name": "Lakers", "price": 1.85},
                        {"name": "Celtics", "price": 2.05}])
    odds = extract_best_odds(fixture)
    assert odds is not None
    assert odds["home"] == 1.85
    assert odds["away"] == 2.05
    assert odds["draw"] is None
    assert odds["bookmakers_count"] == 1


def test_three_way_market_keeps_draw():
    fixture = _fixture(
        [{"name": "Arsenal", "price": 2.1},
         {"name": "Draw", "price": 3.4},
         {"name": "Chelsea", "price": 3.6}],
        home="Arsenal",
        away="Chelsea",
    )
    odds = extract_best_odds(fixture)
    assert odds is not None
    assert (odds["home"], odds["draw"], odds["away"]) == (2.1, 3.4, 3.6)


def test_non_positive_price_is_dropped_and_yields_none():
    fixture = _fixture([{"name": "Lakers", "price": 0},
                        {"name": "Celtics", "price": 2.05}])
    assert extract_best_odds(fixture) is None


def test_average_across_books_skips_missing_draw():
    fixture = {
        "home_team": "Lakers",
        "away_team": "Celtics",
        "bookmakers": [
            {"key": "bookA", "title": "A", "last_update": "2026-01-01T00:00:00Z",
             "markets": [{"key": "h2h", "outcomes": [
                 {"name": "Lakers", "price": 1.80}, {"name": "Celtics", "price": 2.10}]}]},
            {"key": "bookB", "title": "B", "last_update": "2026-01-01T00:00:00Z",
             "markets": [{"key": "h2h", "outcomes": [
                 {"name": "Lakers", "price": 1.90}, {"name": "Celtics", "price": 2.00}]}]},
        ],
    }
    odds = extract_best_odds(fixture)
    assert odds is not None
    assert odds["home"] == 1.85
    assert odds["away"] == 2.05
    assert odds["draw"] is None
    assert odds["source"] == "average_2_bookmakers"
