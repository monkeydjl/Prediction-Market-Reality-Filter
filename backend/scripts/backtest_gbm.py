"""Backtest GBM engine against historical World Cup matches.

Compares prediction quality (Brier / accuracy / log_loss / draw recall) of:
  - GBM engine (LightGBM, predicts home_xg/away_xg, then DC probabilities)
  - elo_odds engine (Elo + BTD, baseline)
  - Old heuristic (pre-BTD, hardcoded 0.27/0.20 base_draw)

All three engines use the SAME pre-match Elo ratings (computed from historical
CSV) for fair comparison.

Usage:
    cd backend
    $env:PYTHONPATH = "."
    python scripts/backtest_gbm.py
"""

from __future__ import annotations

import csv
import logging
import math
from datetime import date
from pathlib import Path

import numpy as np


logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "international_results.csv"

ELO_INIT = 1500.0
ELO_K = 30.0
RECENT_WINDOW = 10
H2H_WINDOW = 10


def _brier(home_p, draw_p, away_p, actual):
    actual_vec = (
        [1.0, 0.0, 0.0] if actual == "home_win"
        else [0.0, 1.0, 0.0] if actual == "draw"
        else [0.0, 0.0, 1.0]
    )
    pred = [home_p, draw_p, away_p]
    return sum((p - a) ** 2 for p, a in zip(pred, actual_vec))


def _log_loss(home_p, draw_p, away_p, actual):
    p = home_p if actual == "home_win" else draw_p if actual == "draw" else away_p
    return -math.log(max(p, 1e-12))


def _predicted_outcome(home_p, draw_p, away_p):
    return max(
        [("home_win", home_p), ("draw", draw_p), ("away_win", away_p)],
        key=lambda kv: kv[1],
    )[0]


def _outcome(home_score, away_score):
    if home_score > away_score:
        return "home_win"
    if home_score < away_score:
        return "away_win"
    return "draw"


def _load_all_matches():
    matches = []
    with CSV_PATH.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                d = date.fromisoformat(str(row.get("date") or ""))
                home_score = int(row.get("home_score") or 0)
                away_score = int(row.get("away_score") or 0)
            except (TypeError, ValueError):
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


def _compute_elo(matches):
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

    def elo_before(team, before_date):
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

    return elo_before


def _team_stats_before(matches, team, before_date, window=RECENT_WINDOW):
    team_matches = [m for m in matches if m["date"] < before_date
                    and (m["home_team"] == team or m["away_team"] == team)]
    if not team_matches:
        return None
    team_matches = team_matches[-window:]
    goals_for = goals_against = wins = draws = losses = 0
    last_date = None
    for m in team_matches:
        if m["home_team"] == team:
            gf, ga = m["home_score"], m["away_score"]
        else:
            gf, ga = m["away_score"], m["home_score"]
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
        "wins": wins, "draws": draws, "losses": losses, "played": played,
        "last_match_date": last_date.isoformat() if last_date else None,
    }


def _h2h_before(matches, home_team, away_team, before_date, window=H2H_WINDOW):
    h2h_matches = [m for m in matches if m["date"] < before_date
                   and {m["home_team"], m["away_team"]} == {home_team, away_team}]
    if not h2h_matches:
        return None
    h2h_matches = h2h_matches[-window:]
    home_wins = away_wins = draws = home_goals = away_goals = 0
    for m in h2h_matches:
        if m["home_team"] == home_team:
            h_g, a_g = m["home_score"], m["away_score"]
        else:
            h_g, a_g = m["away_score"], m["home_score"]
        home_goals += h_g
        away_goals += a_g
        if h_g > a_g:
            home_wins += 1
        elif h_g < a_g:
            away_wins += 1
        else:
            draws += 1
    played = len(h2h_matches)
    return {
        "matches_played": played,
        "home_wins": home_wins, "draws": draws, "away_wins": away_wins,
        "avg_goals_home": home_goals / played,
        "avg_goals_away": away_goals / played,
    }


def backtest():
    from app.services.world_cup_engines.world_cup_gbm_features import derive_gbm_features
    from app.services.world_cup_engines.world_cup_gbm_engine import _load_models
    from app.services.world_cup_engines.world_cup_rule_engine import calculate_outcome_probabilities
    from app.services.world_cup_engines.world_cup_elo_odds_engine import calculate_elo_win_probability

    print("Loading all international matches...")
    all_matches = _load_all_matches()
    print(f"Total: {len(all_matches)} matches")

    # Filter World Cup matches
    wc_matches = [m for m in all_matches if m["tournament"] == "FIFA World Cup"]
    print(f"FIFA World Cup matches: {len(wc_matches)}")
    years = sorted({m["date"].year for m in wc_matches})
    print(f"Years: {years}")

    # Use only 2018+ for backtest (training data starts 2010, so 2018+ has
    # enough Elo history)
    wc_recent = [m for m in wc_matches if m["date"].year >= 2018]
    print(f"Recent (2018+): {len(wc_recent)} matches")

    print("\nComputing Elo ratings from all matches...")
    elo_before = _compute_elo(all_matches)

    # Load GBM models
    print("Loading GBM models...")
    home_model, away_model, meta = _load_models()
    if home_model is None:
        print("ERROR: GBM models not loaded. Run scripts/train_gbm_model.py first.")
        return

    # Run backtest
    configs = [
        ("GBM (LightGBM)", "gbm"),
        ("Elo + BTD", "elo_btd"),
        ("Elo + old heuristic", "elo_old"),
    ]

    results = {}
    for label, mode in configs:
        briers, log_losses = [], []
        correct = 0
        draw_actual = draw_predicted = draw_correct = 0
        n_processed = 0

        for m in wc_recent:
            eh = elo_before(m["home_team"], m["date"])
            ea = elo_before(m["away_team"], m["date"])
            actual = _outcome(m["home_score"], m["away_score"])
            is_wc = True
            is_neutral = m["neutral"]

            if mode == "gbm":
                home_stats = _team_stats_before(all_matches, m["home_team"], m["date"])
                away_stats = _team_stats_before(all_matches, m["away_team"], m["date"])
                h2h = _h2h_before(all_matches, m["home_team"], m["away_team"], m["date"])
                features = derive_gbm_features(
                    elo_home=eh, elo_away=ea,
                    home_stats=home_stats, away_stats=away_stats,
                    h2h=h2h, is_neutral=is_neutral, is_world_cup=is_wc,
                )
                home_xg = float(home_model.predict([features])[0])
                away_xg = float(away_model.predict([features])[0])
                home_xg = max(0.1, min(5.0, home_xg))
                away_xg = max(0.1, min(5.0, away_xg))
                probs = calculate_outcome_probabilities(home_xg, away_xg)
            elif mode == "elo_btd":
                probs = calculate_elo_win_probability(eh, ea, is_knockout=False)
            else:  # elo_old
                elo_diff = eh - ea
                raw_home = 1 / (1 + 10 ** (-elo_diff / 400))
                base_draw = 0.27
                elo_gap_factor = min(abs(elo_diff) / 400, 1.0)
                draw = base_draw * (1.0 - elo_gap_factor * 0.3)
                draw = max(0.15, min(0.35, draw))
                rem = 1.0 - draw
                probs = {
                    "home_win": rem * raw_home,
                    "draw": draw,
                    "away_win": rem * (1.0 - raw_home),
                }

            hp, dp, ap = probs["home_win"], probs["draw"], probs["away_win"]
            briers.append(_brier(hp, dp, ap, actual))
            log_losses.append(_log_loss(hp, dp, ap, actual))
            pred = _predicted_outcome(hp, dp, ap)
            if pred == actual:
                correct += 1
            if actual == "draw":
                draw_actual += 1
            if pred == "draw":
                draw_predicted += 1
                if actual == "draw":
                    draw_correct += 1
            n_processed += 1

        n = n_processed
        results[label] = {
            "n": n,
            "avg_brier": sum(briers) / n,
            "avg_log_loss": sum(log_losses) / n,
            "accuracy": correct / n,
            "draw_actual": draw_actual,
            "draw_predicted": draw_predicted,
            "draw_recall": draw_correct / draw_actual if draw_actual else 0.0,
            "draw_precision": draw_correct / draw_predicted if draw_predicted else 0.0,
        }

    # Print results
    print("\n" + "=" * 105)
    print(f"{'Config':<28} {'avg_brier':>10} {'avg_logloss':>12} {'accuracy':>9} "
          f"{'draw_recall':>12} {'draw_prec':>10} {'draw_pred':>10}")
    print("-" * 105)
    for label, r in results.items():
        print(f"{label:<28} {r['avg_brier']:>10.4f} {r['avg_log_loss']:>12.4f} "
              f"{r['accuracy']:>8.1%} {r['draw_recall']:>11.1%} "
              f"{r['draw_precision']:>9.1%} {r['draw_predicted']:>5}/{r['draw_actual']}")
    print("=" * 105)
    print(f"n = {len(wc_recent)} World Cup matches (2018+)")
    print("(lower Brier/log_loss better; higher accuracy/recall/precision better)")

    # Per-year breakdown
    for year in [2018, 2022]:
        year_matches = [m for m in wc_recent if m["date"].year == year]
        if not year_matches:
            continue
        print(f"\n--- {year} World Cup ({len(year_matches)} matches) ---")
        print(f"  {'Config':<28} {'avg_brier':>10} {'accuracy':>9} {'draw_recall':>12}")
        for label, mode in configs:
            briers = []
            correct = 0
            draw_actual = draw_correct = 0
            for m in year_matches:
                eh = elo_before(m["home_team"], m["date"])
                ea = elo_before(m["away_team"], m["date"])
                actual = _outcome(m["home_score"], m["away_score"])
                if mode == "gbm":
                    home_stats = _team_stats_before(all_matches, m["home_team"], m["date"])
                    away_stats = _team_stats_before(all_matches, m["away_team"], m["date"])
                    h2h = _h2h_before(all_matches, m["home_team"], m["away_team"], m["date"])
                    features = derive_gbm_features(
                        elo_home=eh, elo_away=ea,
                        home_stats=home_stats, away_stats=away_stats,
                        h2h=h2h, is_neutral=m["neutral"], is_world_cup=True,
                    )
                    home_xg = max(0.1, min(5.0, float(home_model.predict([features])[0])))
                    away_xg = max(0.1, min(5.0, float(away_model.predict([features])[0])))
                    probs = calculate_outcome_probabilities(home_xg, away_xg)
                elif mode == "elo_btd":
                    probs = calculate_elo_win_probability(eh, ea, is_knockout=False)
                else:
                    elo_diff = eh - ea
                    raw_home = 1 / (1 + 10 ** (-elo_diff / 400))
                    draw = max(0.15, min(0.35, 0.27 * (1.0 - min(abs(elo_diff) / 400, 1.0) * 0.3)))
                    rem = 1.0 - draw
                    probs = {"home_win": rem * raw_home, "draw": draw, "away_win": rem * (1.0 - raw_home)}
                hp, dp, ap = probs["home_win"], probs["draw"], probs["away_win"]
                briers.append(_brier(hp, dp, ap, actual))
                pred = _predicted_outcome(hp, dp, ap)
                if pred == actual:
                    correct += 1
                if actual == "draw":
                    draw_actual += 1
                    if pred == "draw":
                        draw_correct += 1
            n = len(year_matches)
            print(f"  {label:<28} {sum(briers)/n:>10.4f} {correct/n:>8.1%} "
                  f"{(draw_correct/draw_actual if draw_actual else 0):>11.1%}")


if __name__ == "__main__":
    backtest()
