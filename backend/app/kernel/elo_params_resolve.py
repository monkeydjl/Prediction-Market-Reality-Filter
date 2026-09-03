"""Resolve Elo HFA/K params: applied Optuna row first, settings fallback."""
from __future__ import annotations

import json
import logging

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
    """True if an applied optimized-params row exists for sport/competition.

    A store read failure escapes rather than becoming ``False``. Measured with
    the params table dropped: ``False`` is also the answer before anything has
    been applied, so ``resolve_nba_hfa`` silently resurrected the settings
    playoff/regular HFA split (90.0 / 100.0) in place of the applied single
    hfa 57.875.
    """
    from app.kernel.optimized_params_store import OptimizedParamsStore

    code = (sport or "").lower()
    row = OptimizedParamsStore().get_applied(code, code)
    return row is not None


def resolve_elo_params(sport: str) -> dict[str, float]:
    """Return {hfa, k_regular, k_playoff, season_carry, initial}.

    Start from settings; overlay numeric keys from applied elo_params JSON.
    Malformed stored JSON still falls back to settings -- that is a statement
    about one row's contents. A store read failure escapes instead: measured
    with ``elo_params`` renamed and with the table dropped, the fallback
    published un-tuned parameters (nba hfa 100 vs the fitted 57.875, mlb 50 vs
    61.293, nhl 55 vs 83.230), which moved HockeyEngine's home_win from 0.5714
    to 0.559 and BaseballEngine's from 0.583 to 0.5777 with no diagnostic.
    """
    base = settings_elo_params(sport)
    code = (sport or "").lower()
    from app.kernel.optimized_params_store import OptimizedParamsStore

    row = OptimizedParamsStore().get_applied(code, code)
    if not row:
        return base
    raw = row.get("elo_params")
    if raw is None:
        return base
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("resolve_elo_params: malformed elo_params for %s", sport)
            return base
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


def resolve_nba_hfa(*, playoff: bool) -> float:
    """NBA HFA: applied single hfa when present; else settings regular/playoff."""
    if has_applied_elo_params("nba"):
        return float(resolve_elo_params("nba")["hfa"])
    from app.core import config

    if playoff:
        return float(getattr(config.settings, "NBA_ELO_HFA_PLAYOFF", 90))
    return float(getattr(config.settings, "NBA_ELO_HFA", 100))
