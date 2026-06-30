"""Bradley-Terry-Davidson (BTD) win/draw/loss probability model.

Davidson (1970) extension of Bradley-Terry for three-way outcomes:

    P(home win) = alpha_h' / D
    P(draw)    = gamma * sqrt(alpha_h * alpha_a) / D
    P(away win) = alpha_a / D

where:
    alpha_h' = (1 + home_adv) * alpha_h    (for non-neutral matches)
    D = alpha_h' + alpha_a + gamma * sqrt(alpha_h * alpha_a)
    gamma > 0 is the global draw parameter (higher -> more draws)

The gamma and home_advantage are fitted from historical international results
by scripts/fit_btd_model.py and persisted to data/btd_params.json.

This module's job is to convert two Elo ratings into a three-way probability
distribution using the Davidson formula with the fitted gamma. We derive alpha
from Elo via the standard logit transform:

    alpha = 10 ** (elo / 400)

so a 400-Elo gap produces a 10:1 strength ratio (consistent with Elo's
underlying Bradley-Terry model).

Knockout-stage draw reduction is preserved as a multiplicative factor on gamma
(default 0.74, derived from World Cup knockout draw rate ~18% vs group ~27%).
The factor is clamped so gamma stays positive.
"""

from __future__ import annotations

import json
import logging
import math
import os
from functools import lru_cache
from pathlib import Path


logger = logging.getLogger(__name__)


_PARAMS_PATH = Path(os.getenv(
    "BTD_PARAMS_FILE",
    str(Path(__file__).resolve().parents[3] / "data" / "btd_params.json"),
))

# Fallbacks used when the fitted params file is missing (e.g. fresh checkout
# before scripts/fit_btd_model.py has been run). These produce a sane default
# draw probability (~27% for equal teams) without fitting.
_FALLBACK_GAMMA = 0.74  # 2*0.27/(1-0.27) ~= 0.74
_FALLBACK_HOME_ADV = 0.0  # World Cup is neutral; conservative default

# Knockout-stage draw rate adjustment. Knockout matches have extra time, so
# the 90-minute draw rate is roughly 0.74x the group-stage rate (18% vs 27%).
_KNOCKOUT_GAMMA_FACTOR = 0.74


@lru_cache(maxsize=1)
def _load_params() -> tuple[float, float]:
    """Load fitted BTD (gamma, home_advantage) from JSON.

    Returns:
        (gamma, home_advantage) tuple. Falls back to (0.74, 0.0) on missing
        or corrupt file and logs a warning so operators know to run the fitter.
    """
    try:
        if not _PARAMS_PATH.exists():
            logger.warning(
                "BTD params file not found at %s; using gamma=%.4f (no correction). "
                "Run `python scripts/fit_btd_model.py` to fit gamma from historical results.",
                _PARAMS_PATH,
                _FALLBACK_GAMMA,
            )
            return _FALLBACK_GAMMA, _FALLBACK_HOME_ADV
        data = json.loads(_PARAMS_PATH.read_text(encoding="utf-8"))
        gamma = float(data.get("gamma", _FALLBACK_GAMMA))
        home_adv = float(data.get("home_advantage", _FALLBACK_HOME_ADV))
        # Sanity bound: gamma should be positive and not absurd.
        if not (0.01 <= gamma <= 10.0):
            logger.warning(
                "Fitted BTD gamma=%.4f is outside expected [0.01, 10.0]; clamping. "
                "Re-run the fitter if this persists.",
                gamma,
            )
            gamma = max(0.01, min(10.0, gamma))
        if not (-1.0 <= home_adv <= 3.0):
            logger.warning(
                "Fitted BTD home_advantage=%.4f is outside expected [-1.0, 3.0]; clamping.",
                home_adv,
            )
            home_adv = max(-1.0, min(3.0, home_adv))
        return gamma, home_adv
    except Exception:
        logger.exception(
            "Failed to load BTD params from %s; falling back to gamma=%.4f",
            _PARAMS_PATH,
            _FALLBACK_GAMMA,
        )
        return _FALLBACK_GAMMA, _FALLBACK_HOME_ADV


def _alpha_from_elo(elo: float) -> float:
    """Convert Elo rating to BTD strength alpha.

    Uses the standard Bradley-Terry transform: alpha = 10^(elo/400).
    A 400-Elo gap corresponds to a 10:1 strength ratio, matching Elo's
    underlying BT model. The absolute scale cancels in the BTD formula,
    so only the *difference* matters.
    """
    return math.pow(10.0, elo / 400.0)


def calculate_btd_probabilities(
    elo_home: float,
    elo_away: float,
    *,
    is_neutral: bool = True,
    is_knockout: bool = False,
) -> dict[str, float]:
    """Calculate win/draw/loss probabilities from Elo ratings using BTD.

    Args:
        elo_home: Home team Elo rating (typically 1000-2200)
        elo_away: Away team Elo rating
        is_neutral: If True, no home advantage applied (World Cup default)
        is_knockout: If True, reduce draw probability (knockout matches have
                     extra time, so 90-min draw rate is lower)

    Returns:
        Dictionary with home_win, draw, away_win probabilities (sum to 1.0)
    """
    gamma, home_adv = _load_params()

    # Knockout adjustment: scale gamma down so draw probability is reduced
    if is_knockout:
        gamma = gamma * _KNOCKOUT_GAMMA_FACTOR

    alpha_h = _alpha_from_elo(elo_home)
    alpha_a = _alpha_from_elo(elo_away)

    # Apply home advantage: boost home alpha for non-neutral matches
    if not is_neutral and home_adv > 0:
        alpha_h_effective = alpha_h * (1.0 + home_adv)
    else:
        alpha_h_effective = alpha_h

    sqrt_ha = math.sqrt(alpha_h_effective * alpha_a)
    denom = alpha_h_effective + alpha_a + gamma * sqrt_ha

    p_home = alpha_h_effective / denom
    p_draw = gamma * sqrt_ha / denom
    p_away = alpha_a / denom

    # Numerical safety + normalization
    total = p_home + p_draw + p_away
    if total <= 0:
        # Degenerate fallback: pure BT without draw
        return {"home_win": 0.5, "draw": 0.0, "away_win": 0.5}

    p_home = p_home / total
    p_draw = p_draw / total
    p_away = p_away / total

    return {
        "home_win": round(p_home, 4),
        "draw": round(p_draw, 4),
        "away_win": round(p_away, 4),
    }
