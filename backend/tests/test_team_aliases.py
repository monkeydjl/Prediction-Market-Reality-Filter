"""Tests for the team alias registry."""
import pytest


def test_resolve_nba_abbreviation():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("LAL", "nba") == "los_angeles_lakers"
    assert resolve_team("BOS", "nba") == "boston_celtics"


def test_resolve_nba_full_name():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("Los Angeles Lakers", "nba") == "los_angeles_lakers"


def test_resolve_nba_chinese_name():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("洛杉矶湖人", "nba") == "los_angeles_lakers"


def test_resolve_case_insensitive():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("lakers", "nba") == "los_angeles_lakers"
    assert resolve_team("LAKERS", "nba") == "los_angeles_lakers"


def test_resolve_mlb_team():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("NYY", "mlb") == "new_york_yankees"
    assert resolve_team("洛杉矶道奇", "mlb") == "los_angeles_dodgers"


def test_resolve_nhl_team():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("BOS", "nhl") == "boston_bruins"
    assert resolve_team("波士顿棕熊", "nhl") == "boston_bruins"


def test_resolve_epl_team():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("MCI", "epl") == "manchester_city"
    assert resolve_team("曼城", "epl") == "manchester_city"


def test_resolve_ucl_team():
    from app.sports._shared.team_aliases import resolve_team
    # UCL shares club aliases with domestic leagues
    assert resolve_team("Real Madrid", "ucl") == "real_madrid"
    assert resolve_team("皇家马德里", "ucl") == "real_madrid"


def test_resolve_world_cup_team():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("Brazil", "wc") == "brazil"
    assert resolve_team("巴西", "wc") == "brazil"


def test_resolve_unknown_competition_returns_none():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("Lakers", "nfl") is None


def test_resolve_unknown_team_returns_none():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("Nonexistent Team", "nba") is None


def test_all_10_competitions_present():
    from app.sports._shared.team_aliases import TEAM_ALIASES
    expected = {"wc", "ucl", "epl", "laliga", "bundesliga",
                "seriea", "ligue1", "nba", "mlb", "nhl"}
    assert expected.issubset(set(TEAM_ALIASES.keys()))
    # World Cup must have >= 32 entries
    assert len(TEAM_ALIASES["wc"]) >= 32
    # NBA must have >= 30 canonical teams (count distinct values)
    assert len(set(TEAM_ALIASES["nba"].values())) >= 30
