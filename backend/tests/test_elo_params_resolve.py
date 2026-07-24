"""Unit tests for resolve_elo_params / settings fallback."""
import json
from unittest.mock import MagicMock, patch

from app.kernel.elo_params_resolve import (
    has_applied_elo_params,
    resolve_elo_params,
    resolve_nba_hfa,
    settings_elo_params,
)


def test_settings_elo_params_nba_has_keys():
    p = settings_elo_params("nba")
    assert set(p) >= {"hfa", "k_regular", "k_playoff", "season_carry", "initial"}
    assert p["initial"] == 1500.0


def test_resolve_without_applied_equals_settings():
    with patch("app.kernel.optimized_params_store.OptimizedParamsStore") as Store:
        Store.return_value.get_applied.return_value = None
        assert resolve_elo_params("nba") == settings_elo_params("nba")


def test_resolve_overlays_applied_json():
    with patch("app.kernel.optimized_params_store.OptimizedParamsStore") as Store:
        Store.return_value.get_applied.return_value = {
            "elo_params": json.dumps({
                "hfa": 57.8,
                "k_regular": 30.5,
                "k_playoff": 44.2,
                "season_carry": 0.75,
                "initial": 1500,
            }),
        }
        p = resolve_elo_params("nba")
        assert p["hfa"] == 57.8
        assert p["k_regular"] == 30.5
        assert p["k_playoff"] == 44.2


def test_resolve_partial_keys_fill_from_settings():
    base = settings_elo_params("mlb")
    with patch("app.kernel.optimized_params_store.OptimizedParamsStore") as Store:
        Store.return_value.get_applied.return_value = {
            "elo_params": json.dumps({"hfa": 61.0}),
        }
        p = resolve_elo_params("mlb")
        assert p["hfa"] == 61.0
        assert p["k_regular"] == base["k_regular"]
        assert p["season_carry"] == base["season_carry"]


def test_resolve_bad_json_falls_back():
    with patch("app.kernel.optimized_params_store.OptimizedParamsStore") as Store:
        Store.return_value.get_applied.return_value = {"elo_params": "not-json{"}
        assert resolve_elo_params("nhl") == settings_elo_params("nhl")


def test_resolve_nba_hfa_playoff_without_applied():
    with patch("app.kernel.elo_params_resolve.has_applied_elo_params", return_value=False):
        with patch("app.core.config.settings") as s:
            s.NBA_ELO_HFA = 100
            s.NBA_ELO_HFA_PLAYOFF = 90
            assert resolve_nba_hfa(playoff=True) == 90.0
            assert resolve_nba_hfa(playoff=False) == 100.0


def test_resolve_nba_hfa_with_applied_ignores_playoff_split():
    with patch("app.kernel.elo_params_resolve.has_applied_elo_params", return_value=True):
        with patch(
            "app.kernel.elo_params_resolve.resolve_elo_params",
            return_value={"hfa": 57.0, "k_regular": 20, "k_playoff": 30, "season_carry": 0.75, "initial": 1500},
        ):
            assert resolve_nba_hfa(playoff=True) == 57.0
            assert resolve_nba_hfa(playoff=False) == 57.0
