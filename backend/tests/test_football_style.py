"""Tests for football_style.stats_for_team (P1-F6)."""
import pytest

from app.sports.football.football_style import (
    _AFFIX_TOKENS,
    _TEAM_STYLE,
    _fold_accents,
    _lookup_key,
    _normalize,
    _strip_affixes,
    stats_for_team,
)


class TestStatsForTeam:
    def test_known_club_has_all_keys_in_band(self):
        s = stats_for_team("Arsenal")
        assert s is not None
        assert set(s.keys()) >= {"possession_pct", "shots_per90", "ppda"}
        assert 30.0 <= float(s["possession_pct"]) <= 75.0
        assert 5.0 <= float(s["shots_per90"]) <= 25.0
        assert 5.0 <= float(s["ppda"]) <= 20.0

    def test_top_possession_above_mid_table(self):
        top = stats_for_team("Manchester City")
        mid = stats_for_team("Everton")
        assert top is not None and mid is not None
        assert float(top["possession_pct"]) > float(mid["possession_pct"])

    def test_low_ppda_press_below_passive(self):
        # Lower PPDA = stronger press
        press = stats_for_team("Liverpool")
        passive = stats_for_team("Everton")
        assert press is not None and passive is not None
        assert float(press["ppda"]) < float(passive["ppda"])

    def test_unknown_returns_none(self):
        assert stats_for_team("NotAFootballClubXYZ") is None

    def test_empty_returns_none(self):
        assert stats_for_team("") is None
        assert stats_for_team("   ") is None

    def test_normalize_case_and_spaces(self):
        a = stats_for_team("Arsenal")
        b = stats_for_team("  arsenal  ")
        c = stats_for_team("ARSENAL")
        assert a is not None
        assert a == b == c

    def test_common_alias_man_city(self):
        primary = stats_for_team("Manchester City")
        alias = stats_for_team("Man City")
        assert primary is not None
        assert primary == alias

    def test_fixture_style_real_madrid_cf(self):
        s = stats_for_team("Real Madrid CF")
        assert s is not None
        assert 30.0 <= float(s["possession_pct"]) <= 75.0

    def test_fixture_style_bayern(self):
        s = stats_for_team("FC Bayern München")
        assert s is not None
        assert 5.0 <= float(s["shots_per90"]) <= 25.0


# The fixture spellings the adapters actually pass. Each pair is
# (Football-Data.org name, the short name this table is keyed on). Every entry
# was read off the league / UCL / EPL alias tables and eyeballed one by one, so
# a wrong-club match here would be a factual error in the pair, not a fuzzy
# score to tune.
_FIXTURE_SPELLINGS = [
    ("Arsenal FC", "Arsenal"),
    ("Chelsea FC", "Chelsea"),
    ("Liverpool FC", "Liverpool"),
    ("Manchester City FC", "Manchester City"),
    ("Manchester United FC", "Manchester United"),
    ("Tottenham Hotspur FC", "Tottenham Hotspur"),
    ("West Ham United FC", "West Ham United"),
    ("Wolverhampton Wanderers FC", "Wolverhampton Wanderers"),
    ("AFC Bournemouth", "Bournemouth"),
    ("Brighton & Hove Albion FC", "Brighton and Hove Albion"),
    ("SS Lazio", "Lazio"),
    ("SSC Napoli", "Napoli"),
    ("ACF Fiorentina", "Fiorentina"),
    ("Atalanta BC", "Atalanta"),
    ("Juventus FC", "Juventus"),
    ("Villarreal CF", "Villarreal"),
    ("Sevilla FC", "Sevilla"),
    ("Girona FC", "Girona"),
    ("Borussia Mönchengladbach", "Borussia Monchengladbach"),
    ("VfL Wolfsburg", "Wolfsburg"),
    ("RC Lens", "Lens"),
    ("OGC Nice", "Nice"),
    ("Lille OSC", "Lille"),
    ("SL Benfica", "Benfica"),
    ("Club Brugge KV", "Club Brugge"),
    ("Celtic FC", "Celtic"),
    ("Rangers FC", "Rangers"),
    ("FC Salzburg", "Salzburg"),
    ("Atlético de Madrid", "Atletico de Madrid"),
]


class TestFixtureSpellingsResolve:
    """The rows were already here; the lookup could not reach them.

    ``_normalize`` only lowercased and collapsed whitespace, so the adapters'
    Football-Data.org names missed this table's short keys and both sides of a
    possession share resolved on as few as 1.0% of Serie A fixtures. That is a
    name-normalization bug, not a data gap -- the table *has* Arsenal, the
    lookup could not find ``arsenal fc``.
    """

    @pytest.mark.parametrize(("fixture_name", "short_name"), _FIXTURE_SPELLINGS)
    def test_fixture_name_resolves_to_the_same_club(self, fixture_name, short_name):
        short = stats_for_team(short_name)
        assert short is not None, f"table lost its row for {short_name!r}"
        assert stats_for_team(fixture_name) == short

    def test_both_sides_of_a_real_epl_fixture_resolve(self):
        """A pair, because the engine's possession factor needs a share.

        One side alone produces nothing (``enrich_style_features`` writes only on
        a full pair), so a per-team hit rate overstates coverage: 41.7% per team
        is 17.4% per fixture.
        """
        home = stats_for_team("Arsenal FC")
        away = stats_for_team("Manchester City FC")
        assert home is not None and away is not None
        assert home["possession_pct"] != away["possession_pct"]


class TestResolutionCannotInvent:
    """What the loosened lookup is *not* allowed to do.

    Loosening a lookup can turn a miss into a *wrong* hit, which is worse than
    the miss: the engine cannot tell a wrong club's possession from the right
    club's. These are the guards that make the loosening safe, and they run
    against the whole table rather than a hand-picked sample.
    """

    def test_no_two_keys_collapse_to_one_form_with_different_stats(self):
        """The collision audit. A wrong-club match would have to show up here."""
        groups: dict[str, list[str]] = {}
        for key in _TEAM_STYLE:
            groups.setdefault(_strip_affixes(_fold_accents(key)), []).append(key)
        collisions = {
            form: sorted(keys)
            for form, keys in groups.items()
            if len({_TEAM_STYLE[k] for k in keys}) > 1
        }
        assert collisions == {}

    @pytest.mark.parametrize("fixture_name", [n for n, _ in _FIXTURE_SPELLINGS])
    def test_resolved_key_invents_no_token(self, fixture_name):
        """Every token of the matched key must be present in the input.

        This is what separates stripping from guessing: the lookup may *drop* a
        legal-form token, never add an identifying one. ``chelsea fc`` may match
        ``chelsea``; nothing may match ``manchester city`` unless the input said
        both words.
        """
        key = _lookup_key(fixture_name)
        assert key is not None
        supplied = set(
            _fold_accents(_normalize(fixture_name).replace("&", "and")).split(),
        )
        assert set(_fold_accents(key).split()) <= supplied

    def test_affix_set_holds_no_identifying_token(self):
        """Stripping any of these would merge two different clubs."""
        forbidden = {
            "city", "united", "real", "sporting", "athletic", "sg", "inter",
            "milan", "madrid", "borussia", "olympique", "stade", "club",
            "town", "wanderers", "albion", "forest", "villa", "ham",
        }
        assert _AFFIX_TOKENS & forbidden == set()

    def test_a_bare_legal_form_resolves_to_nothing(self):
        for name in ("FC", "AFC", "SS", "fc fc"):
            assert stats_for_team(name) is None

    def test_a_partial_club_name_does_not_resolve(self):
        """Half a name is a different club, or no club."""
        for name in ("Manchester", "Manchester FC", "Real", "Borussia"):
            assert stats_for_team(name) is None

    def test_unknown_club_with_a_legal_form_still_returns_none(self):
        assert stats_for_team("Cheltenham Town FC") is None
        assert stats_for_team("FC Nowhere United") is None

    def test_exact_key_wins_over_any_weakened_candidate(self):
        """Every name the table spells itself resolves to *itself*.

        Asserting identity rather than equal stats is deliberate: today's table
        happens to give ``ac milan`` and ``milan`` the same row, so a values
        check would pass even if stripping ran first. Identity fails the moment
        the exact pass stops going first.
        """
        for key in _TEAM_STYLE:
            assert _lookup_key(key) == key
