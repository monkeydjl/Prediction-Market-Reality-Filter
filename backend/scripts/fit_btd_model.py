"""Fit Bradley-Terry-Davidson (BTD) gamma parameter from historical results.

Davidson (1970) extension of Bradley-Terry for three-way outcomes (win/draw/loss):

    P(home win) = alpha_h / D
    P(draw)    = gamma * sqrt(alpha_h * alpha_a) / D
    P(away win) = alpha_a / D

where D = alpha_h + alpha_a + gamma * sqrt(alpha_h * alpha_a).

Unlike the team-level alpha fitter (which fits per-team strength freely),
this fitter computes Elo ratings from the historical CSV and derives alpha
via the standard Bradley-Terry transform alpha = 10^(elo/400). Only gamma and
home_advantage are fit. This ensures the fitted gamma is properly calibrated
for the Elo scale used at prediction time, avoiding scale mismatch.

Time-decay weighting: w = exp(-ln(2) * days_ago / half_life)

Output: backend/data/btd_params.json with structure:
    {
      "gamma": float,
      "home_advantage": float,
      "half_life_days": float,
      "since_year": int,
      "sample_count": int,
      "team_count": int,
      "ref_date": "YYYY-MM-DD",
      "fitted_at": "ISO8601",
      "optimizer_success": bool,
      "diagnostics": {...}
    }

Usage:
    cd backend
    $env:PYTHONPATH = "."
    python scripts/fit_btd_model.py
"""

from __future__ import annotations

import csv
import json
import logging
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import minimize


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "international_results.csv"
OUTPUT_PATH = Path(__file__).resolve().parents[1] / "data" / "btd_params.json"

HALF_LIFE_DAYS = 730.0
SINCE_YEAR = 2018
MIN_TEAM_MATCHES = 5

# Elo computation constants (standard FIFA-like settings)
ELO_INIT = 1500.0
ELO_K = 30.0  # Match importance factor (friendlies lower, but we use uniform)


def _load_matches() -> list[dict]:
    """Load international matches from martj42 CSV."""
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"CSV not found at {CSV_PATH}")

    matches: list[dict] = []
    with CSV_PATH.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                d = date.fromisoformat(str(row.get("date") or ""))
                home_score = int(row.get("home_score") or 0)
                away_score = int(row.get("away_score") or 0)
            except (TypeError, ValueError):
                continue
            if d.year < SINCE_YEAR:
                continue
            matches.append({
                "date": d,
                "home_team": str(row.get("home_team") or ""),
                "away_team": str(row.get("away_team") or ""),
                "home_score": home_score,
                "away_score": away_score,
                "neutral": str(row.get("neutral") or "").upper() == "TRUE",
            })
    matches.sort(key=lambda m: m["date"])
    return matches


def _compute_elo_history(matches: list[dict]) -> dict[str, list[tuple[date, float]]]:
    """Compute Elo ratings over time for each team.

    Returns a dict mapping team name -> list of (date, elo) snapshots,
    one per match the team played (post-update). Snapshots are appended
    after each match so callers can look up the Elo *before* a given date
    by finding the most recent snapshot strictly before that date.
    """
    ratings: dict[str, float] = {}
    history: dict[str, list[tuple[date, float]]] = {}

    for m in matches:
        home = m["home_team"]
        away = m["away_team"]
        r_h = ratings.get(home, ELO_INIT)
        r_a = ratings.get(away, ELO_INIT)

        # Expected scores
        e_h = 1.0 / (1.0 + 10.0 ** ((r_a - r_h) / 400.0))
        e_a = 1.0 - e_h

        # Actual scores (2=win, 1=draw, 0=loss -> normalize to [0, 1])
        if m["home_score"] > m["away_score"]:
            s_h, s_a = 1.0, 0.0
        elif m["home_score"] < m["away_score"]:
            s_h, s_a = 0.0, 1.0
        else:
            s_h, s_a = 0.5, 0.5

        # Home advantage: boost home Elo by 65 points (typical) for non-neutral
        # This only affects the Elo computation, not the BTD fitting.
        if not m["neutral"]:
            r_h_effective = r_h + 65.0
            e_h = 1.0 / (1.0 + 10.0 ** ((r_a - r_h_effective) / 400.0))
            e_a = 1.0 - e_h

        # Update ratings
        ratings[home] = r_h + ELO_K * (s_h - e_h)
        ratings[away] = r_a + ELO_K * (s_a - e_a)

        # Record post-match snapshot
        history.setdefault(home, []).append((m["date"], ratings[home]))
        history.setdefault(away, []).append((m["date"], ratings[away]))

    return history


def _elo_before(history: dict[str, list[tuple[date, float]]], team: str, before_date: date) -> float:
    """Get team's Elo rating as of just before a given date."""
    snapshots = history.get(team)
    if not snapshots:
        return ELO_INIT
    # Find the most recent snapshot strictly before before_date
    elo = ELO_INIT
    for snap_date, snap_elo in snapshots:
        if snap_date < before_date:
            elo = snap_elo
        else:
            break
    return elo


def _neg_log_likelihood(
    params: np.ndarray,
    elo_home: np.ndarray,
    elo_away: np.ndarray,
    outcomes: np.ndarray,
    weights: np.ndarray,
    neutral: np.ndarray,
) -> float:
    """Negative log-likelihood for BTD with alpha=10^(elo/400).

    params[0] = log(gamma)
    params[1] = log(home_adv_logit)  # home advantage scaling for non-neutral
    """
    log_gamma = params[0]
    home_adv_logit = params[1]

    gamma = math.exp(log_gamma)
    home_adv = 1.0 / (1.0 + math.exp(-home_adv_logit))

    # alpha = 10^(elo/400)
    alpha_h = np.power(10.0, elo_home / 400.0)
    alpha_a = np.power(10.0, elo_away / 400.0)

    # Apply home advantage for non-neutral matches
    home_boost = np.where(neutral, 1.0, 1.0 + home_adv)
    alpha_h_eff = alpha_h * home_boost

    sqrt_ha = np.sqrt(alpha_h_eff * alpha_a)
    denom = alpha_h_eff + alpha_a + gamma * sqrt_ha

    p_home = alpha_h_eff / denom
    p_draw = gamma * sqrt_ha / denom
    p_away = alpha_a / denom

    eps = 1e-12
    p_home = np.clip(p_home, eps, 1.0 - eps)
    p_draw = np.clip(p_draw, eps, 1.0 - eps)
    p_away = np.clip(p_away, eps, 1.0 - eps)

    ll = np.where(outcomes == 0, np.log(p_home),
         np.where(outcomes == 1, np.log(p_draw), np.log(p_away)))

    weighted_ll = np.sum(weights * ll)
    return -weighted_ll


def fit_btd() -> dict:
    """Fit BTD gamma with alpha derived from Elo, return parameters dict."""
    logger.info("Loading matches from %s", CSV_PATH)
    matches = _load_matches()
    logger.info("Loaded %d matches since %d", len(matches), SINCE_YEAR)

    # Compute Elo history (only uses matches since SINCE_YEAR; earlier Elo
    # starts at ELO_INIT=1500 for all teams)
    logger.info("Computing Elo ratings for %d matches...", len(matches))
    elo_history = _compute_elo_history(matches)

    # Build dataset: for each match, look up Elo BEFORE the match date
    ref_date = matches[-1]["date"]
    ln2 = math.log(2.0)

    # Filter: both teams must have enough history
    team_counts: dict[str, int] = {}
    for m in matches:
        team_counts[m["home_team"]] = team_counts.get(m["home_team"], 0) + 1
        team_counts[m["away_team"]] = team_counts.get(m["away_team"], 0) + 1

    elo_home_list = []
    elo_away_list = []
    outcomes_list = []
    weights_list = []
    neutral_list = []

    for m in matches:
        if team_counts[m["home_team"]] < MIN_TEAM_MATCHES:
            continue
        if team_counts[m["away_team"]] < MIN_TEAM_MATCHES:
            continue

        r_h = _elo_before(elo_history, m["home_team"], m["date"])
        r_a = _elo_before(elo_history, m["away_team"], m["date"])

        elo_home_list.append(r_h)
        elo_away_list.append(r_a)
        neutral_list.append(m["neutral"])

        if m["home_score"] > m["away_score"]:
            outcomes_list.append(0)
        elif m["home_score"] < m["away_score"]:
            outcomes_list.append(2)
        else:
            outcomes_list.append(1)

        days_ago = (ref_date - m["date"]).days
        weights_list.append(math.exp(-ln2 * days_ago / HALF_LIFE_DAYS))

    elo_home = np.array(elo_home_list, dtype=np.float64)
    elo_away = np.array(elo_away_list, dtype=np.float64)
    outcomes = np.array(outcomes_list, dtype=np.int64)
    weights = np.array(weights_list, dtype=np.float64)
    neutral = np.array(neutral_list, dtype=bool)

    n = len(elo_home)
    n_teams = len(team_counts)
    logger.info("Dataset: %d matches, %d teams (min %d matches per team)", n, n_teams, MIN_TEAM_MATCHES)

    if n == 0:
        raise RuntimeError("Empty dataset after filtering")

    n_home = int(np.sum(outcomes == 0))
    n_draw = int(np.sum(outcomes == 1))
    n_away = int(np.sum(outcomes == 2))
    empirical_draw_rate = n_draw / n
    logger.info("Outcomes: %d home wins, %d draws, %d away wins", n_home, n_draw, n_away)
    logger.info("Empirical draw rate: %.4f", empirical_draw_rate)

    # Elo stats
    logger.info("Elo range: home [%.1f, %.1f], away [%.1f, %.1f]",
                elo_home.min(), elo_home.max(), elo_away.min(), elo_away.max())
    logger.info("Elo diff range: [%.1f, %.1f], mean abs diff: %.1f",
                (elo_home - elo_away).min(), (elo_home - elo_away).max(),
                np.mean(np.abs(elo_home - elo_away)))

    # Initial guess: gamma that gives empirical draw rate for equal teams
    init_gamma = 2.0 * empirical_draw_rate / (1.0 - empirical_draw_rate)
    x0 = np.array([math.log(init_gamma), 0.0])  # log_gamma, home_adv_logit=0

    logger.info("Initial gamma=%.4f (equal_team_draw=%.4f)",
                init_gamma, init_gamma / (2.0 + init_gamma))

    result = minimize(
        _neg_log_likelihood,
        x0,
        args=(elo_home, elo_away, outcomes, weights, neutral),
        method="L-BFGS-B",
        bounds=[(-3.0, 3.0), (-3.0, 3.0)],
        options={"maxiter": 500, "maxfun": 5000, "ftol": 1e-9},
    )

    if not result.success:
        logger.warning("Optimizer did not fully converge: %s", result.message)

    gamma = math.exp(result.x[0])
    home_adv = 1.0 / (1.0 + math.exp(-result.x[1]))

    # Diagnostics
    equal_draw = gamma / (2.0 + gamma)
    boosted_draw = gamma / (2.0 + home_adv + gamma)

    params = {
        "gamma": round(gamma, 6),
        "home_advantage": round(home_adv, 6),
        "half_life_days": HALF_LIFE_DAYS,
        "since_year": SINCE_YEAR,
        "min_team_matches": MIN_TEAM_MATCHES,
        "sample_count": n,
        "team_count": n_teams,
        "ref_date": matches[-1]["date"].isoformat(),
        "fitted_at": datetime.now(timezone.utc).isoformat(),
        "optimizer_success": bool(result.success),
        "diagnostics": {
            "empirical_draw_rate": round(empirical_draw_rate, 6),
            "equal_team_draw_prob": round(equal_draw, 6),
            "boosted_draw_prob_neutral_off": round(boosted_draw, 6),
            "n_home_wins": n_home,
            "n_draws": n_draw,
            "n_away_wins": n_away,
            "elo_mean_abs_diff": round(float(np.mean(np.abs(elo_home - elo_away))), 2),
            "final_neg_log_likelihood": round(float(result.fun), 4),
            "alpha_mapping": "10^(elo/400)",
        },
    }

    OUTPUT_PATH.write_text(json.dumps(params, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote params to %s", OUTPUT_PATH)
    logger.info("gamma=%.6f  home_adv=%.6f  equal_draw_prob=%.4f",
                gamma, home_adv, equal_draw)
    logger.info("Final negative log-likelihood: %.4f", result.fun)

    return params


if __name__ == "__main__":
    fit_btd()
