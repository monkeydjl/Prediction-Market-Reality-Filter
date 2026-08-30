"""Feature engineering for the GBM (LightGBM) World Cup prediction engine.

This module is shared between:
- scripts/train_gbm_model.py (offline training)
- world_cup_gbm_engine.py (online prediction)

Keeping the feature derivation in one place prevents train/serve skew.

**The derivation was shared; the windows and the date cutoff were not.** Both
callers pass ``home_stats`` / ``away_stats`` / ``h2h`` that they built
themselves, so this module could only guarantee the *order* of the vector.
Training built h2h over the last 10 meetings before the fixture date; serving
called ``get_historical_h2h`` with no arguments, which defaults to **20** and to
"the most recent rows in the CSV" rather than "the rows before this fixture".
Hence :data:`RECENT_WINDOW`, :data:`H2H_WINDOW` and :func:`resolve_windows`
below: the windows are declared here, the training scripts import them, and the
serving path resolves them from the artifact's own metadata so a model trained
with a different window is still served on its own distribution.

Features (all derivable from pre-match data, no label leakage -- which requires
the caller to pass the fixture date; see :func:`resolve_windows`):
    0. elo_home
    1. elo_away
    2. elo_diff
    3. elo_diff_abs
    4. home_form_winrate      (last N matches win rate)
    5. away_form_winrate
    6. home_goals_scored_avg  (last N matches avg goals scored)
    7. away_goals_scored_avg
    8. home_goals_conceded_avg
    9. away_goals_conceded_avg
    10. h2h_home_winrate      (last N H2H matches)
    11. h2h_draw_rate
    12. h2h_avg_goal_diff     (home_goals - away_goals in H2H)
    13. is_neutral            (1.0 if neutral venue, 0.0 otherwise)
    14. is_world_cup          (1.0 if FIFA World Cup, 0.0 otherwise)
    15. days_since_last_match_home
    16. days_since_last_match_away
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Number of recent matches behind the form/goals features. Declared here rather
#: than in the training script so the serving path can reach it: it is part of
#: the feature definition, not of one caller's configuration.
RECENT_WINDOW = 10

#: Number of head-to-head meetings behind the h2h features. Deliberately *not*
#: ``get_historical_h2h``'s own default of 20 -- that default serves the other
#: consumers of that function, and the GBM vector must use whatever window the
#: model was fitted with.
H2H_WINDOW = 10

# Feature names in canonical order. MUST match the order used at training time.
FEATURE_NAMES: list[str] = [
    "elo_home",
    "elo_away",
    "elo_diff",
    "elo_diff_abs",
    "home_form_winrate",
    "away_form_winrate",
    "home_goals_scored_avg",
    "away_goals_scored_avg",
    "home_goals_conceded_avg",
    "away_goals_conceded_avg",
    "h2h_home_winrate",
    "h2h_draw_rate",
    "h2h_avg_goal_diff",
    "is_neutral",
    "is_world_cup",
    "days_since_last_match_home",
    "days_since_last_match_away",
]


def resolve_windows(meta: dict[str, Any] | None) -> tuple[int, int]:
    """Return ``(recent_window, h2h_window)`` for a loaded model artifact.

    The artifact wins. ``train_gbm_model.py`` records both windows under
    ``training_config``, and the model was fitted with those values, so serving
    has to honour them even if the constants above have since moved -- otherwise
    retraining with a different window silently puts the serving path
    off-distribution, which is the defect this function exists to prevent.

    A missing or unusable value falls back to the module constant, which is what
    the training script itself imports, so an artifact that declares nothing is
    served with the windows it was almost certainly built with.

    Args:
        meta: parsed ``gbm_features.json``, or ``None``/``{}`` when absent.

    Returns:
        The two window sizes, each a positive int.
    """
    config = (meta or {}).get("training_config")
    if not isinstance(config, dict):
        return RECENT_WINDOW, H2H_WINDOW
    return (
        _positive_int(config.get("recent_window"), RECENT_WINDOW, "recent_window"),
        _positive_int(config.get("h2h_window"), H2H_WINDOW, "h2h_window"),
    )


def _positive_int(value: Any, fallback: int, label: str) -> int:
    """Coerce a declared window, falling back loudly rather than silently."""
    if value is None:
        return fallback
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        logger.warning(
            "GBM artifact declares a non-numeric %s (%r); using %d",
            label, value, fallback,
        )
        return fallback
    if parsed <= 0:
        logger.warning(
            "GBM artifact declares %s=%d, which cannot select any match; using %d",
            label, parsed, fallback,
        )
        return fallback
    return parsed


def derive_gbm_features(
    *,
    elo_home: float,
    elo_away: float,
    home_stats: dict[str, Any] | None,
    away_stats: dict[str, Any] | None,
    h2h: dict[str, Any] | None,
    is_neutral: bool,
    is_world_cup: bool,
) -> list[float]:
    """Derive the canonical feature vector for the GBM engine.

    Args:
        elo_home, elo_away: Pre-match Elo ratings.
        home_stats, away_stats: Output of get_historical_team_stats() (last N
            matches before the match date). None if no history available.
        h2h: Output of get_historical_h2h() before the match date. None if no
            prior H2H.
        is_neutral: True if match is on neutral ground (World Cup = True).
        is_world_cup: True if tournament == "FIFA World Cup".

    Returns:
        Feature vector in the order specified by FEATURE_NAMES.
    """
    elo_diff = elo_home - elo_away

    # Team form stats (with safe defaults for new teams)
    if home_stats:
        home_winrate = _safe_winrate(home_stats)
        home_goals_scored = float(home_stats.get("goals_per_game", 1.4))
        home_goals_conceded = float(home_stats.get("goals_conceded_per_game", 1.3))
        home_days_since = _safe_days_since(home_stats)
    else:
        home_winrate = 0.5
        home_goals_scored = 1.4
        home_goals_conceded = 1.3
        home_days_since = 30.0

    if away_stats:
        away_winrate = _safe_winrate(away_stats)
        away_goals_scored = float(away_stats.get("goals_per_game", 1.3))
        away_goals_conceded = float(away_stats.get("goals_conceded_per_game", 1.4))
        away_days_since = _safe_days_since(away_stats)
    else:
        away_winrate = 0.5
        away_goals_scored = 1.3
        away_goals_conceded = 1.4
        away_days_since = 30.0

    # H2H stats (with safe defaults)
    if h2h and h2h.get("matches_played", 0) > 0:
        h2h_played = int(h2h["matches_played"])
        h2h_home_winrate = float(h2h.get("home_wins", 0)) / h2h_played
        h2h_draw_rate = float(h2h.get("draws", 0)) / h2h_played
        h2h_avg_goal_diff = (
            float(h2h.get("avg_goals_home", 0)) - float(h2h.get("avg_goals_away", 0))
        )
    else:
        h2h_home_winrate = 0.4
        h2h_draw_rate = 0.3
        h2h_avg_goal_diff = 0.0

    return [
        float(elo_home),
        float(elo_away),
        float(elo_diff),
        float(abs(elo_diff)),
        home_winrate,
        away_winrate,
        home_goals_scored,
        away_goals_scored,
        home_goals_conceded,
        away_goals_conceded,
        h2h_home_winrate,
        h2h_draw_rate,
        h2h_avg_goal_diff,
        1.0 if is_neutral else 0.0,
        1.0 if is_world_cup else 0.0,
        home_days_since,
        away_days_since,
    ]


def _safe_winrate(stats: dict[str, Any]) -> float:
    """Compute win rate from get_historical_team_stats output."""
    played = int(stats.get("played", 0))
    if played <= 0:
        return 0.5
    wins = int(stats.get("wins", 0))
    return wins / played


def _safe_days_since(stats: dict[str, Any]) -> float:
    """Extract days since last match from stats."""
    last_match = stats.get("last_match_date")
    if not last_match:
        return 30.0
    try:
        # last_match_date is ISO date string; we don't know the match date here
        # without it being passed in, so we return 30 (typical) as fallback.
        # Training scripts compute days_since directly from CSV dates.
        return 30.0
    except (TypeError, ValueError):
        return 30.0
