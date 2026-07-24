"""Resolve Elo HFA/K params: applied Optuna row first, settings fallback."""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_ELO_KEYS = ("hfa", "k_regular", "k_playoff", "season_carry", "initial")


def settings_elo_params(sport: str) -> dict[str, float]:
    """Baseline Elo params from config.settings (safe defaults)."""
    from app.core import config

    s = config.settings
    code = (sport or "").lower()
    if code == "mlb":
        return {
            "hfa": float(getattr(s, "MLB_ELO_HFA", 50)),
            "k_regular": float(getattr(s, "MLB_ELO_K_REGULAR", 20)),
            "k_playoff": float(getattr(s, "MLB_ELO_K_PLAYOFF", 30)),
            "season_carry": float(getattr(s, "MLB_ELO_SEASON_CARRY", 0.7)),
            "initial": 1500.0,
        }
    if code == "nhl":
        return {
            "hfa": float(getattr(s, "NHL_ELO_HFA", 55)),
            "k_regular": float(getattr(s, "NHL_ELO_K_REGULAR", 20)),
            "k_playoff": float(getattr(s, "NHL_ELO_K_PLAYOFF", 30)),
            "season_carry": float(getattr(s, "NHL_ELO_SEASON_CARRY", 0.75)),
            "initial": 1500.0,
        }
    return {
        "hfa": float(getattr(s, "NBA_ELO_HFA", 100)),
        "k_regular": float(getattr(s, "NBA_ELO_K_REGULAR", 20)),
        "k_playoff": float(getattr(s, "NBA_ELO_K_PLAYOFF", 30)),
        "season_carry": 0.75,
        "initial": 1500.0,
    }


def has_applied_elo_params(sport: str) -> bool:
    """True if an applied optimized-params row exists for sport/competition."""
    try:
        from app.kernel.optimized_params_store import OptimizedParamsStore

        code = (sport or "").lower()
        row = OptimizedParamsStore().get_applied(code, code)
        return row is not None
    except Exception:  # noqa: BLE001
        return False


def resolve_elo_params(sport: str) -> dict[str, float]:
    """Return {hfa, k_regular, k_playoff, season_carry, initial}.

    Start from settings; overlay numeric keys from applied elo_params JSON.
    On any store/parse error, return settings only.
    """
    base = settings_elo_params(sport)
    code = (sport or "").lower()
    try:
        from app.kernel.optimized_params_store import OptimizedParamsStore

        row = OptimizedParamsStore().get_applied(code, code)
        if not row:
            return base
        raw = row.get("elo_params")
        if raw is None:
            return base
        if isinstance(raw, str):
            data = json.loads(raw)
        elif isinstance(raw, dict):
            data = raw
        else:
            return base
        if not isinstance(data, dict):
            return base
        out = dict(base)
        for key in _ELO_KEYS:
            if key not in data:
                continue
            try:
                out[key] = float(data[key])
            except (TypeError, ValueError):
                continue
        return out
    except Exception:  # noqa: BLE001
        logger.debug("resolve_elo_params fallback to settings for %s", sport, exc_info=True)
        return base


def resolve_nba_hfa(*, playoff: bool) -> float:
    """NBA HFA: applied single hfa when present; else settings regular/playoff."""
    if has_applied_elo_params("nba"):
        return float(resolve_elo_params("nba")["hfa"])
    from app.core import config

    if playoff:
        return float(getattr(config.settings, "NBA_ELO_HFA_PLAYOFF", 90))
    return float(getattr(config.settings, "NBA_ELO_HFA", 100))
