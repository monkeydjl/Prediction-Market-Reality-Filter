"""Train LightGBM models to predict match goal counts (home_xg, away_xg).

Trains two LightGBM regressors on historical international results:
- home_model: predicts home team goals
- away_model: predicts away team goals

Features are derived via world_cup_gbm_features.derive_gbm_features (shared
with the prediction engine to prevent train/serve skew).

Labels are the actual goal counts from international_results.csv. The
predicted xG values feed into rule_engine.calculate_outcome_probabilities
(with Dixon-Coles rho correction) to produce win/draw/loss probabilities.

Usage:
    cd backend
    $env:PYTHONPATH = "."
    python scripts/train_gbm_model.py
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
import lightgbm as lgb


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "international_results.csv"
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data"
HOME_MODEL_PATH = OUTPUT_DIR / "gbm_home_model.txt"
AWAY_MODEL_PATH = OUTPUT_DIR / "gbm_away_model.txt"
META_PATH = OUTPUT_DIR / "gbm_features.json"

# Training config
SINCE_YEAR = 2010  # Train on more history than BTD/DC (more samples for GBM)
MIN_TEAM_MATCHES = 3
RECENT_WINDOW = 10  # Number of recent matches for form/goals stats
H2H_WINDOW = 10

# Elo computation (must match fit_btd_model.py)
ELO_INIT = 1500.0
ELO_K = 30.0


def _load_matches() -> list[dict]:
    """Load all international matches from CSV."""
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
                "tournament": str(row.get("tournament") or ""),
                "neutral": str(row.get("neutral") or "").upper() == "TRUE",
            })
    matches.sort(key=lambda m: m["date"])
    return matches


def _compute_elo_history(matches: list[dict]) -> dict[str, list[tuple[date, float]]]:
    """Compute Elo ratings over time. Same as fit_btd_model.py."""
    ratings: dict[str, float] = {}
    history: dict[str, list[tuple[date, float]]] = {}

    for m in matches:
        home = m["home_team"]
        away = m["away_team"]
        r_h = ratings.get(home, ELO_INIT)
        r_a = ratings.get(away, ELO_INIT)

        e_h = 1.0 / (1.0 + 10.0 ** ((r_a - r_h) / 400.0))
        e_a = 1.0 - e_h

        if m["home_score"] > m["away_score"]:
            s_h, s_a = 1.0, 0.0
        elif m["home_score"] < m["away_score"]:
            s_h, s_a = 0.0, 1.0
        else:
            s_h, s_a = 0.5, 0.5

        if not m["neutral"]:
            r_h_eff = r_h + 65.0
            e_h = 1.0 / (1.0 + 10.0 ** ((r_a - r_h_eff) / 400.0))
            e_a = 1.0 - e_h

        ratings[home] = r_h + ELO_K * (s_h - e_h)
        ratings[away] = r_a + ELO_K * (s_a - e_a)

        history.setdefault(home, []).append((m["date"], ratings[home]))
        history.setdefault(away, []).append((m["date"], ratings[away]))

    return history


def _elo_before(history: dict[str, list[tuple[date, float]]], team: str, before_date: date) -> float:
    snaps = history.get(team)
    if not snaps:
        return ELO_INIT
    elo = ELO_INIT
    for d, e in snaps:
        if d < before_date:
            elo = e
        else:
            break
    return elo


def _team_stats_before(matches: list[dict], team: str, before_date: date, window: int = RECENT_WINDOW) -> dict | None:
    """Compute team stats (form, goals) from the last N matches before a date."""
    team_matches = []
    for m in matches:
        if m["date"] >= before_date:
            break
        if m["home_team"] == team or m["away_team"] == team:
            team_matches.append(m)

    if not team_matches:
        return None

    team_matches = team_matches[-window:]
    goals_for = 0
    goals_against = 0
    wins = draws = losses = 0
    last_date = None

    for m in team_matches:
        if m["home_team"] == team:
            gf = m["home_score"]
            ga = m["away_score"]
        else:
            gf = m["away_score"]
            ga = m["home_score"]
        goals_for += gf
        goals_against += ga
        if gf > ga:
            wins += 1
        elif gf < ga:
            losses += 1
        else:
            draws += 1
        last_date = m["date"]

    played = len(team_matches)
    return {
        "goals_per_game": goals_for / played,
        "goals_conceded_per_game": goals_against / played,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "played": played,
        "last_match_date": last_date.isoformat() if last_date else None,
    }


def _h2h_before(matches: list[dict], home_team: str, away_team: str, before_date: date, window: int = H2H_WINDOW) -> dict | None:
    """Compute H2H stats from the last N matches between two teams before a date."""
    h2h_matches = []
    for m in matches:
        if m["date"] >= before_date:
            break
        if {m["home_team"], m["away_team"]} != {home_team, away_team}:
            continue
        h2h_matches.append(m)

    if not h2h_matches:
        return None

    h2h_matches = h2h_matches[-window:]
    home_wins = away_wins = draws = 0
    home_goals = away_goals = 0

    for m in h2h_matches:
        if m["home_team"] == home_team:
            h_goals = m["home_score"]
            a_goals = m["away_score"]
        else:
            h_goals = m["away_score"]
            a_goals = m["home_score"]
        home_goals += h_goals
        away_goals += a_goals
        if h_goals > a_goals:
            home_wins += 1
        elif h_goals < a_goals:
            away_wins += 1
        else:
            draws += 1

    played = len(h2h_matches)
    return {
        "matches_played": played,
        "home_wins": home_wins,
        "draws": draws,
        "away_wins": away_wins,
        "avg_goals_home": home_goals / played,
        "avg_goals_away": away_goals / played,
    }


def _build_dataset(matches: list[dict]):
    """Build (X, y_home, y_away) tensors for training."""
    logger.info("Computing Elo history for %d matches...", len(matches))
    elo_history = _compute_elo_history(matches)

    # Count matches per team (filter teams with too few matches)
    team_counts: dict[str, int] = {}
    for m in matches:
        team_counts[m["home_team"]] = team_counts.get(m["home_team"], 0) + 1
        team_counts[m["away_team"]] = team_counts.get(m["away_team"], 0) + 1

    # We need to import the feature derivation function lazily to avoid
    # import-time errors when the app package isn't on PYTHONPATH during
    # standalone script execution.
    import sys
    backend_root = Path(__file__).resolve().parents[1]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    from app.services.world_cup_engines.world_cup_gbm_features import derive_gbm_features

    X_list = []
    y_home_list = []
    y_away_list = []

    skipped = 0
    for m in matches:
        if team_counts[m["home_team"]] < MIN_TEAM_MATCHES:
            skipped += 1
            continue
        if team_counts[m["away_team"]] < MIN_TEAM_MATCHES:
            skipped += 1
            continue

        eh = _elo_before(elo_history, m["home_team"], m["date"])
        ea = _elo_before(elo_history, m["away_team"], m["date"])

        home_stats = _team_stats_before(matches, m["home_team"], m["date"])
        away_stats = _team_stats_before(matches, m["away_team"], m["date"])
        h2h = _h2h_before(matches, m["home_team"], m["away_team"], m["date"])

        is_world_cup = m["tournament"] == "FIFA World Cup"
        features = derive_gbm_features(
            elo_home=eh,
            elo_away=ea,
            home_stats=home_stats,
            away_stats=away_stats,
            h2h=h2h,
            is_neutral=m["neutral"],
            is_world_cup=is_world_cup,
        )

        X_list.append(features)
        y_home_list.append(float(m["home_score"]))
        y_away_list.append(float(m["away_score"]))

    X = np.array(X_list, dtype=np.float64)
    y_home = np.array(y_home_list, dtype=np.float64)
    y_away = np.array(y_away_list, dtype=np.float64)

    return X, y_home, y_away, skipped


def train_gbm() -> dict:
    """Train LightGBM models and save artifacts."""
    logger.info("Loading matches from %s", CSV_PATH)
    matches = _load_matches()
    logger.info("Loaded %d matches since %d", len(matches), SINCE_YEAR)

    X, y_home, y_away, skipped = _build_dataset(matches)
    n = len(X)
    logger.info("Dataset: %d samples, %d features, %d skipped (too few team matches)",
                n, X.shape[1], skipped)

    if n < 100:
        raise RuntimeError(f"Not enough training data: {n} samples")

    # Log label distribution
    logger.info("Label stats: home_goals mean=%.3f std=%.3f, away_goals mean=%.3f std=%.3f",
                y_home.mean(), y_home.std(), y_away.mean(), y_away.std())

    # Time-based train/validation split: last 20% chronologically as validation
    split_idx = int(n * 0.8)
    X_train, X_val = X[:split_idx], X[split_idx:]
    yh_train, yh_val = y_home[:split_idx], y_home[split_idx:]
    ya_train, ya_val = y_away[:split_idx], y_away[split_idx:]

    logger.info("Train: %d samples, Validation: %d samples", len(X_train), len(X_val))

    # LightGBM parameters
    params = {
        "objective": "regression",
        "metric": "rmse",
        "boosting_type": "gbdt",
        "num_leaves": 31,
        "learning_rate": 0.05,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "verbose": -1,
        "min_data_in_leaf": 20,
        "lambda_l2": 1.0,
    }

    # Train home goals model
    logger.info("Training home goals model...")
    train_data_h = lgb.Dataset(X_train, label=yh_train, feature_name=[
        "elo_home", "elo_away", "elo_diff", "elo_diff_abs",
        "home_form_winrate", "away_form_winrate",
        "home_goals_scored_avg", "away_goals_scored_avg",
        "home_goals_conceded_avg", "away_goals_conceded_avg",
        "h2h_home_winrate", "h2h_draw_rate", "h2h_avg_goal_diff",
        "is_neutral", "is_world_cup",
        "days_since_last_match_home", "days_since_last_match_away",
    ])
    val_data_h = lgb.Dataset(X_val, label=yh_val, reference=train_data_h)
    home_model = lgb.train(
        params, train_data_h,
        num_boost_round=500,
        valid_sets=[val_data_h],
        callbacks=[lgb.early_stopping(stopping_rounds=50), lgb.log_evaluation(period=100)],
    )
    home_pred = home_model.predict(X_val)
    home_rmse = math.sqrt(np.mean((home_pred - yh_val) ** 2))
    logger.info("Home model validation RMSE: %.4f", home_rmse)

    # Train away goals model
    logger.info("Training away goals model...")
    train_data_a = lgb.Dataset(X_train, label=ya_train)
    val_data_a = lgb.Dataset(X_val, label=ya_val, reference=train_data_a)
    away_model = lgb.train(
        params, train_data_a,
        num_boost_round=500,
        valid_sets=[val_data_a],
        callbacks=[lgb.early_stopping(stopping_rounds=50), lgb.log_evaluation(period=100)],
    )
    away_pred = away_model.predict(X_val)
    away_rmse = math.sqrt(np.mean((away_pred - ya_val) ** 2))
    logger.info("Away model validation RMSE: %.4f", away_rmse)

    # Feature importance (top 5)
    logger.info("Home model top features: %s",
                sorted(zip(train_data_h.feature_name, home_model.feature_importance("gain")),
                       key=lambda x: -x[1])[:5])
    logger.info("Away model top features: %s",
                sorted(zip(train_data_h.feature_name, away_model.feature_importance("gain")),
                       key=lambda x: -x[1])[:5])

    # Save models
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    home_model.save_model(str(HOME_MODEL_PATH))
    away_model.save_model(str(AWAY_MODEL_PATH))
    logger.info("Saved home model to %s", HOME_MODEL_PATH)
    logger.info("Saved away model to %s", AWAY_MODEL_PATH)

    # Save metadata
    meta = {
        "feature_names": [
            "elo_home", "elo_away", "elo_diff", "elo_diff_abs",
            "home_form_winrate", "away_form_winrate",
            "home_goals_scored_avg", "away_goals_scored_avg",
            "home_goals_conceded_avg", "away_goals_conceded_avg",
            "h2h_home_winrate", "h2h_draw_rate", "h2h_avg_goal_diff",
            "is_neutral", "is_world_cup",
            "days_since_last_match_home", "days_since_last_match_away",
        ],
        "lightgbm_version": lgb.__version__,
        "training_config": {
            "since_year": SINCE_YEAR,
            "min_team_matches": MIN_TEAM_MATCHES,
            "recent_window": RECENT_WINDOW,
            "h2h_window": H2H_WINDOW,
            "elo_init": ELO_INIT,
            "elo_k": ELO_K,
            "train_split": 0.8,
            "num_boost_round": 500,
            "early_stopping_rounds": 50,
            "params": params,
        },
        "dataset_stats": {
            "total_samples": n,
            "skipped": skipped,
            "train_samples": len(X_train),
            "val_samples": len(X_val),
            "home_goals_mean": float(y_home.mean()),
            "away_goals_mean": float(y_away.mean()),
            "home_goals_std": float(y_home.std()),
            "away_goals_std": float(y_away.std()),
        },
        "validation_metrics": {
            "home_rmse": float(home_rmse),
            "away_rmse": float(away_rmse),
        },
        "fitted_at": datetime.now(timezone.utc).isoformat(),
    }
    META_PATH.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Saved metadata to %s", META_PATH)

    return meta


if __name__ == "__main__":
    train_gbm()
