from app.core import config
from app.kernel.competition_codes import (
    COMPETITION_ALIASES,
    COMPETITION_SPORT,
    PREFIX_TO_COMPETITION,
    PREFIX_TO_SPORT,
    competitions_equivalent,
    normalize_competition_code,
)

def test_phase_lol_defaults_off():
    assert config.settings.PHASE_LOL_ENABLED is False
    assert config.settings.LOL_DRY_RUN_IMPORT is False
    assert config.settings.LOL_SCHEDULE_VENDOR == "null"
    assert config.settings.LOL_VENDOR_API_BASE == ""
    assert config.settings.LOL_VENDOR_API_KEY == ""
    assert config.settings.LOL_SETTLE_GRACE_HOURS == 6

def test_lol_prefix_and_aliases():
    assert PREFIX_TO_SPORT["lol-"] == "lol"
    assert PREFIX_TO_COMPETITION["lol-"] == "lol"
    assert COMPETITION_SPORT["lol"] == "lol"
    assert COMPETITION_SPORT["lol_lck"] == "lol"
    assert COMPETITION_ALIASES["lol"] == "lol"
    assert COMPETITION_ALIASES["lol_lck"] == "lol_lck"
    assert COMPETITION_ALIASES["lol_lpl"] == "lol_lpl"
    assert COMPETITION_ALIASES["lol_lec"] == "lol_lec"
    assert COMPETITION_ALIASES["lol_worlds"] == "lol_worlds"
    assert COMPETITION_ALIASES["lol_msi"] == "lol_msi"
    assert COMPETITION_SPORT["lol_msi"] == "lol"
    assert normalize_competition_code("LOL") == "lol"
    assert normalize_competition_code("lol-lck") == "lol_lck"
    assert normalize_competition_code("lol-msi") == "lol_msi"


def test_lol_league_codes_equivalent_to_umbrella_lol():
    assert competitions_equivalent("lol_lck", "lol") is True
    assert competitions_equivalent("lol", "lol_lpl") is True
    assert competitions_equivalent("lol_lec", "lol") is True
    assert competitions_equivalent("lol_worlds", "lol") is True
    assert competitions_equivalent("lol_msi", "lol") is True
    assert competitions_equivalent("lol_lck", "lol_lpl") is False
    assert competitions_equivalent("epl", "ucl") is False
