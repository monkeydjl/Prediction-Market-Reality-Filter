from app.core import config
from app.kernel.competition_codes import (
    COMPETITION_SPORT,
    PREFIX_TO_COMPETITION,
    PREFIX_TO_SPORT,
    normalize_competition_code,
)

def test_phase_lol_defaults_off():
    assert config.settings.PHASE_LOL_ENABLED is False
    assert config.settings.LOL_DRY_RUN_IMPORT is False

def test_lol_prefix_and_aliases():
    assert PREFIX_TO_SPORT["lol-"] == "lol"
    assert PREFIX_TO_COMPETITION["lol-"] == "lol"
    assert COMPETITION_SPORT["lol"] == "lol"
    assert COMPETITION_SPORT["lol_lck"] == "lol"
    assert normalize_competition_code("LOL") == "lol"
    assert normalize_competition_code("lol-lck") == "lol_lck"
