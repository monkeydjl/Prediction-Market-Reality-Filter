"""Tests for the sport market detector."""
from datetime import date


def test_detect_nba_market():
    from app.services.sport_market_detector import detect_sport_market
    info = detect_sport_market(
        contract_id="poly-1",
        question="Will the Lakers beat the Celtics on January 1, 2025?",
        source="polymarket",
    )
    assert info is not None
    assert info.detected_competition == "nba"
    assert info.detected_sport == "basketball"
    assert "los_angeles_lakers" in info.detected_teams
    assert "boston_celtics" in info.detected_teams
    assert info.market_type == "single_match_binary"


def test_detect_mlb_market():
    from app.services.sport_market_detector import detect_sport_market
    info = detect_sport_market(
        contract_id="poly-2",
        question="Will the Yankees defeat the Red Sox tonight?",
        source="polymarket",
    )
    assert info is not None
    assert info.detected_competition == "mlb"
    assert info.detected_sport == "baseball"
    assert "new_york_yankees" in info.detected_teams


def test_detect_nhl_market():
    from app.services.sport_market_detector import detect_sport_market
    info = detect_sport_market(
        contract_id="poly-3",
        question="Will the Bruins beat the Maple Leafs?",
        source="polymarket",
    )
    assert info is not None
    assert info.detected_competition == "nhl"
    assert info.detected_sport == "hockey"


def test_detect_epl_market():
    from app.services.sport_market_detector import detect_sport_market
    info = detect_sport_market(
        contract_id="poly-4",
        question="Will Man City beat Arsenal in the Premier League?",
        source="polymarket",
    )
    assert info is not None
    assert info.detected_competition == "epl"
    assert info.detected_sport == "football"
    assert "manchester_city" in info.detected_teams


def test_futures_market_filtered_out():
    from app.services.sport_market_detector import detect_sport_market
    # Championship/futures keyword -> not a single-match market
    info = detect_sport_market(
        contract_id="poly-5",
        question="Will the Lakers win the NBA Championship 2025?",
        source="polymarket",
    )
    assert info is None


def test_date_extraction():
    from app.services.sport_market_detector import detect_sport_market
    info = detect_sport_market(
        contract_id="poly-6",
        question="Will the Lakers beat the Celtics on 2025-01-15?",
        source="polymarket",
    )
    assert info is not None
    assert info.detected_date == date(2025, 1, 15)


def test_non_sport_market_returns_none():
    from app.services.sport_market_detector import detect_sport_market
    info = detect_sport_market(
        contract_id="poly-7",
        question="Will Bitcoin reach $100k by end of year?",
        source="polymarket",
    )
    assert info is None


def test_traditional_odds_passthrough():
    from app.services.sport_market_detector import detect_sport_market
    # The Odds API source is pre-structured; detector tags it directly.
    info = detect_sport_market(
        contract_id="oddsapi-lal-bos-20250101",
        question="Lakers vs Celtics",
        source="the_odds_api",
    )
    assert info is not None
    assert info.source == "the_odds_api"
    assert info.market_type == "traditional_odds"
