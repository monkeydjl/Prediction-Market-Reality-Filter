"""Fit Dixon-Coles parameters from historical international results.

This is an offline fitting script (run manually, not in the request path). It
reads ``backend/data/international_results.csv`` and fits the Dixon-Coles
time-decayed Poisson model:

    log(lambda_home) = mu + attack_home - defense_away + home_adv * (1 - neutral)
    log(lambda_away) = mu + attack_away - defense_home
    P(home=x, away=y) = tau(x, y; rho) * Pois(x; lambda_home) * Pois(y; lambda_away)

where tau is the Dixon-Coles low-score correction:

    (0,0): 1 - lambda_h * lambda_a * rho
    (1,0): 1 + lambda_a * rho
    (0,1): 1 + lambda_h * rho
    (1,1): 1 - rho

Each match is weighted by an exponential time decay with half-life ``h``:

    w = exp(-ln(2) * days_ago / h)

Only the global parameters (rho, home_advantage, mu, half_life_days) are
persisted to ``data/dixon_coles_params.json``. Per-team attack/defense are
fit as intermediate variables but discarded — the online engines use Elo +
form for team strength, and only borrow Dixon-Coles' rho correction (the
piece that was previously hardcoded as ``rho = 0.96`` in
``world_cup_rule_engine.py``).

Standard Dixon-Coles uses a NEGATIVE rho (e.g. -0.1) to INCREASE the
probability of low-scoring draws (0-0, 1-1). The legacy hardcoded ``0.96``
in our code corresponded to a positive rho_dc = 1 - 0.96 = +0.04, which
DECREASED 1-1 probability — the opposite of the intended effect. Fitting
corrects this.

Usage:
    python scripts/fit_dixon_coles.py
    python scripts/fit_dixon_coles.py --half-life 730 --since 2018
    python scripts/fit_dixon_coles.py --output data/dixon_coles_params.json
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

from app.services.world_cup_historical_results import _load_results


DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "dixon_coles_params.json"


def _tau_correction(x: int, y: int, lam_h: np.ndarray, lam_a: np.ndarray, rho: float) -> np.ndarray:
    """Vectorized Dixon-Coles tau correction for (x, y) in {(0,0),(1,0),(0,1),(1,1)}."""
    if x == 0 and y == 0:
        return 1.0 - lam_h * lam_a * rho
    if x == 1 and y == 0:
        return 1.0 + lam_a * rho
    if x == 0 and y == 1:
        return 1.0 + lam_h * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return np.ones_like(lam_h)


def _neg_log_likelihood(
    params: np.ndarray,
    team_idx_h: np.ndarray,
    team_idx_a: np.ndarray,
    home_scores: np.ndarray,
    away_scores: np.ndarray,
    neutral: np.ndarray,
    weights: np.ndarray,
    n_teams: int,
) -> float:
    """Negative weighted log-likelihood of the Dixon-Coles model.

    params layout: [mu, home_adv, rho, attack_0..N-1, defense_0..N-1]
    Identifiability constraint: sum(attack) = 0, sum(defense) = 0 (enforced
    via a soft penalty rather than hard reparametrisation — simpler and the
    penalty barely binds with enough data).
    """
    mu = params[0]
    home_adv = params[1]
    rho = params[2]
    attack = params[3 : 3 + n_teams]
    defense = params[3 + n_teams : 3 + 2 * n_teams]

    log_lam_h = mu + attack[team_idx_h] - defense[team_idx_a] + home_adv * (1.0 - neutral)
    log_lam_a = mu + attack[team_idx_a] - defense[team_idx_h]

    # Clip to avoid log(0) / overflow. lambda in [0.01, 10] is a sane match range.
    lam_h = np.clip(np.exp(log_lam_h), 0.01, 10.0)
    lam_a = np.clip(np.exp(log_lam_a), 0.01, 10.0)

    # log Poisson(x; lambda) = x*log(lambda) - lambda - log(x!)
    log_fact = np.array([math.lgamma(int(s) + 1) for s in home_scores])
    log_pois_h = home_scores * np.log(lam_h) - lam_h - log_fact
    log_fact_a = np.array([math.lgamma(int(s) + 1) for s in away_scores])
    log_pois_a = away_scores * np.log(lam_a) - lam_a - log_fact_a

    # tau correction (only differs from 1 for (x,y) in the 4 low-score cells)
    tau = np.ones_like(lam_h)
    for x in range(2):
        for y in range(2):
            mask = (home_scores == x) & (away_scores == y)
            if mask.any():
                tau[mask] = _tau_correction(x, y, lam_h[mask], lam_a[mask], rho)
    # tau must stay positive; clip to a tiny floor to avoid log(0)
    tau = np.clip(tau, 1e-6, None)

    log_prob = log_pois_h + log_pois_a + np.log(tau)
    nll = -np.sum(weights * log_prob)

    # Soft identifiability penalty (keeps attack/defense centered near 0).
    penalty = 1e-6 * (np.sum(attack ** 2) + np.sum(defense ** 2))
    return nll + penalty


def fit_dixon_coles(
    *,
    half_life_days: float = 730.0,
    since_year: int | None = None,
    min_team_matches: int = 5,
) -> dict:
    """Fit Dixon-Coles global parameters from the historical results CSV.

    Returns a dict with keys: rho, home_advantage, mu, half_life_days,
    fitted_at, sample_count, team_count, since_year.
    """
    rows = _load_results()
    if not rows:
        raise RuntimeError("No historical results loaded (CSV missing or empty)")

    # Reference date for time decay = latest match in the dataset.
    ref_date = max(r["date"] for r in rows)
    cutoff_date = date(since_year, 1, 1) if since_year else None

    # Count matches per team (within cutoff window) to filter one-off teams.
    team_match_count: dict[str, int] = {}
    filtered: list[dict] = []
    for r in rows:
        if cutoff_date and r["date"] < cutoff_date:
            continue
        filtered.append(r)
        team_match_count[r["home_team"]] = team_match_count.get(r["home_team"], 0) + 1
        team_match_count[r["away_team"]] = team_match_count.get(r["away_team"], 0) + 1

    eligible_teams = {t for t, c in team_match_count.items() if c >= min_team_matches}
    team_list = sorted(eligible_teams)
    team_index = {t: i for i, t in enumerate(team_list)}
    n_teams = len(team_list)

    if n_teams < 10:
        raise RuntimeError(
            f"Too few eligible teams ({n_teams}) after filtering; "
            f"lower --min-team-matches or --since"
        )

    # Build arrays. Skip matches where either team is not in the eligible set.
    h_idx, a_idx, hs, as_, neu, wts = [], [], [], [], [], []
    ln2_over_h = math.log(2.0) / half_life_days
    for r in filtered:
        if r["home_team"] not in team_index or r["away_team"] not in team_index:
            continue
        days_ago = (ref_date - r["date"]).days
        w = math.exp(-ln2_over_h * days_ago)
        if w <= 0:
            continue
        h_idx.append(team_index[r["home_team"]])
        a_idx.append(team_index[r["away_team"]])
        hs.append(r["home_score"])
        as_.append(r["away_score"])
        neu.append(1 if r["neutral"] else 0)
        wts.append(w)

    if not h_idx:
        raise RuntimeError("No matches left after team eligibility filter")

    h_idx = np.array(h_idx, dtype=np.int64)
    a_idx = np.array(a_idx, dtype=np.int64)
    hs = np.array(hs, dtype=np.float64)
    as_ = np.array(as_, dtype=np.float64)
    neu = np.array(neu, dtype=np.float64)
    wts = np.array(wts, dtype=np.float64)
    # Normalize weights to sum=1 for stable optimisation.
    wts = wts / wts.sum()

    n_params = 3 + 2 * n_teams
    # Initial guess: mu = log(avg goals), home_adv=0.3, rho=0, attack/defense=0
    avg_goals = float(np.mean(np.concatenate([hs, as_]))) or 1.35
    x0 = np.zeros(n_params, dtype=np.float64)
    x0[0] = math.log(avg_goals)
    x0[1] = 0.3
    x0[2] = 0.0  # rho starts at 0 (no correction)

    bounds = [
        (math.log(0.3), math.log(5.0)),  # mu
        (0.0, 1.0),                       # home_adv
        (-0.5, 0.5),                       # rho (negative = draw boost, the standard DC direction)
    ] + [(None, None)] * (2 * n_teams)

    result = minimize(
        _neg_log_likelihood,
        x0,
        args=(h_idx, a_idx, hs, as_, neu, wts, n_teams),
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": 1000, "maxfun": 20000, "ftol": 1e-7},
    )

    if not result.success:
        # Don't hard-fail: even a non-converged fit is usually usable, just warn.
        print(f"[warn] optimizer did not fully converge: {result.message}")

    mu_fit, home_adv_fit, rho_fit = float(result.x[0]), float(result.x[1]), float(result.x[2])
    attack = result.x[3 : 3 + n_teams]
    defense = result.x[3 + n_teams : 3 + 2 * n_teams]

    # Sanity: per-team attack/defense should be centered (sum ~ 0).
    print(
        f"[fit] attack sum={attack.sum():.4f} defense sum={defense.sum():.4f} "
        f"(should be ~0)"
    )

    return {
        "rho": round(rho_fit, 4),
        "home_advantage": round(home_adv_fit, 4),
        "mu": round(math.exp(mu_fit), 4),  # store as base goals/game, not log
        "half_life_days": half_life_days,
        "since_year": since_year,
        "min_team_matches": min_team_matches,
        "sample_count": int(len(hs)),
        "team_count": int(n_teams),
        "ref_date": ref_date.isoformat(),
        "fitted_at": datetime.now(timezone.utc).isoformat(),
        "optimizer_success": bool(result.success),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fit Dixon-Coles rho from historical results.")
    parser.add_argument("--half-life", type=float, default=730.0, help="Time-decay half-life in days (default 730 ~ 2 years).")
    parser.add_argument("--since", type=int, default=None, help="Only use matches since this year (e.g. 2018).")
    parser.add_argument("--min-team-matches", type=int, default=5, help="Drop teams with fewer than this many matches (default 5).")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path.")
    args = parser.parse_args()

    print(f"[fit] loading historical results from CSV ...")
    params = fit_dixon_coles(
        half_life_days=args.half_life,
        since_year=args.since,
        min_team_matches=args.min_team_matches,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(params, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[fit] wrote {out_path}")
    print(f"[fit] rho={params['rho']} home_adv={params['home_advantage']} mu={params['mu']}")
    print(f"[fit] samples={params['sample_count']} teams={params['team_count']} half_life={params['half_life_days']}d")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
