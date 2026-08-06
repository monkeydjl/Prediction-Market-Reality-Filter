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


def test_resolve_nhl_utah_mammoth_variants():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("Utah Mammoth", "nhl") == "utah_mammoth"
    assert resolve_team("Utah Hockey Club", "nhl") == "utah_mammoth"
    assert resolve_team("Utah Utah Hockey Club", "nhl") == "utah_mammoth"
    assert resolve_team("UTA", "nhl") == "utah_mammoth"
    assert resolve_team("犹他猛犸", "nhl") == "utah_mammoth"
    assert resolve_team("Arizona Coyotes", "nhl") == "utah_mammoth"
    assert resolve_team("ARI", "nhl") == "utah_mammoth"


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


class TestComparisonKey:
    """Shared comparison key used by club_form and the schedule-density path.

    Both need the same notion of "same team", so the key lives here rather than
    being duplicated per call site.
    """

    def test_alias_and_full_name_agree(self):
        from app.sports._shared.team_aliases import comparison_key
        assert comparison_key("Man City", "epl") == comparison_key(
            "Manchester City", "epl",
        )

    def test_canonical_is_stable_across_competitions(self):
        """Cross-league merging depends on this: the same club resolved under
        epl and under ucl must produce one key.
        """
        from app.sports._shared.team_aliases import comparison_key
        assert comparison_key("Man City", "ucl") == comparison_key(
            "Manchester City", "epl",
        )

    def test_colliding_abbreviation_stays_separate(self):
        """CEL is Celta Vigo in laliga and Celtic in ucl."""
        from app.sports._shared.team_aliases import comparison_key
        assert comparison_key("CEL", "laliga") != comparison_key("CEL", "ucl")

    def test_no_competition_falls_back_to_normalize(self):
        from app.sports._shared.team_aliases import comparison_key
        assert comparison_key("Man City", None) == "man city"
        assert comparison_key("Man City", None) != comparison_key(
            "Manchester City", "epl",
        )

    def test_unknown_name_folds_case_and_whitespace(self):
        from app.sports._shared.team_aliases import comparison_key
        assert comparison_key("  Obscure   Town FC ", "epl") == comparison_key(
            "obscure town fc", "epl",
        )

    def test_empty_name(self):
        from app.sports._shared.team_aliases import comparison_key
        assert comparison_key("", "epl") == ""
        assert comparison_key(None, "epl") == ""
