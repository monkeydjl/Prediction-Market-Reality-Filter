"""Backtest BTD vs old hardcoded draw heuristic on World Cup history.

Compares prediction quality (Brier / accuracy / log_loss / draw recall) of:
  - BTD model (gamma fitted from historical Elo, see fit_btd_model.py)
  - Old heuristic (base_draw=0.27 group / 0.20 knockout, elo_gap_factor hack)

Both use the SAME Elo ratings (computed from historical CSV) for fairness.
The backtest covers all FIFA World Cup final-stage matches in the CSV.

Usage:
    cd backend
    $env:PYTHONPATH = "."
    python scripts/backtest_btd.py
"""

from __future__ import annotations

import csv
import logging
import math
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.services.world_cup_engines import world_cup_btd_model as btd_model
from app.services.world_cup_engines.world_cup_btd_model import (
    calculate_btd_probabilities,
)


logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "international_results.csv"

# Elo computation constants (must match fit_btd_model.py)
ELO_INIT = 1500.0
ELO_K = 30.0


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


def _old_heuristic_probs(elo_home, elo_away, is_knockout):
    """Replicate the OLD hardcoded base_draw + elo_gap_factor logic."""
    elo_diff = elo_home - elo_away
    raw_home_expectancy = 1 / (1 + 10 ** (-elo_diff / 400))
    base_draw = 0.20 if is_knockout else 0.27
    elo_gap_factor = min(abs(elo_diff) / 400, 1.0)
    draw = base_draw * (1.0 - elo_gap_factor * 0.3)
    draw = max(0.10 if is_knockout else 0.15, min(0.35, draw))
    remaining_prob = 1.0 - draw
    home_win = remaining_prob * raw_home_expectancy
    away_win = remaining_prob * (1.0 - raw_home_expectancy)
    return {"home_win": home_win, "draw": draw, "away_win": away_win}


def _compute_elo_for_all_matches(matches):
    """Compute Elo ratings over time, return function(team, date) -> elo_before."""
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


def _load_all_matches():
    """Load ALL international matches (for Elo computation) and World Cup matches (for backtest)."""
    all_matches = []
    wc_matches = []
    with CSV_PATH.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                d = date.fromisoformat(str(row.get("date") or ""))
                home_score = int(row.get("home_score") or 0)
                away_score = int(row.get("away_score") or 0)
            except (TypeError, ValueError):
                continue
            tournament = str(row.get("tournament") or "")
            neutral = str(row.get("neutral") or "").upper() == "TRUE"
            m = {
                "date": d,
                "home_team": str(row.get("home_team") or ""),
                "away_team": str(row.get("away_team") or ""),
                "home_score": home_score,
                "away_score": away_score,
                "neutral": neutral,
                "tournament": tournament,
            }
            all_matches.append(m)
            if tournament == "FIFA World Cup":
                wc_matches.append(m)

    all_matches.sort(key=lambda m: m["date"])
    wc_matches.sort(key=lambda m: m["date"])
    return all_matches, wc_matches


def _is_knockout(world_cup_year, home_team, away_team):
    """Heuristic: World Cup knockout stage starts from Round of 16.
    We don't have stage info in the CSV, so we use match number within tournament
    as a proxy. For simplicity, assume the last 16 matches of each World Cup
    are knockout. This is approximate but sufficient for backtest comparison.
    """
    # This is a simplification; the actual knockout determination would need
    # stage data. For the backtest, we treat all World Cup matches as group
    # stage (is_knockout=False) to keep the comparison fair between old and new.
    return False


def backtest():
    print("\nLoading historical data...")
    all_matches, wc_matches = _load_all_matches()
    print(f"Total international matches: {len(all_matches)}")
    print(f"FIFA World Cup final-stage matches: {len(wc_matches)}")
    years = sorted({m["date"].year for m in wc_matches})
    print(f"World Cup years: {years}")

    # Compute Elo from ALL international matches (not just World Cup)
    print("Computing Elo ratings from all international matches...")
    elo_before = _compute_elo_for_all_matches(all_matches)

    # Backtest configs
    configs = [
        ("BTD (fitted gamma)", "btd"),
        ("Old heuristic (0.27/0.20)", "old"),
    ]

    results = {}
    for label, mode in configs:
        briers = []
        log_losses = []
        correct = 0
        draw_actual = 0
        draw_predicted = 0
        draw_correct = 0

        for m in wc_matches:
            eh = elo_before(m["home_team"], m["date"])
            ea = elo_before(m["away_team"], m["date"])
            is_knockout = _is_knockout(m["date"].year, m["home_team"], m["away_team"])
            actual = _outcome(m["home_score"], m["away_score"])

            if mode == "btd":
                probs = calculate_btd_probabilities(eh, ea, is_neutral=True, is_knockout=is_knockout)
            else:
                probs = _old_heuristic_probs(eh, ea, is_knockout)

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

        n = len(wc_matches)
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

    # Print comparison
    print("\n" + "=" * 100)
    print(f"{'Config':<28} {'avg_brier':>10} {'avg_logloss':>12} {'accuracy':>9} "
          f"{'draw_recall':>12} {'draw_prec':>10} {'draw_pred':>10}")
    print("-" * 100)
    for label, r in results.items():
        print(f"{label:<28} {r['avg_brier']:>10.4f} {r['avg_log_loss']:>12.4f} "
              f"{r['accuracy']:>8.1%} {r['draw_recall']:>11.1%} "
              f"{r['draw_precision']:>9.1%} {r['draw_predicted']:>5}/{r['draw_actual']}")
    print("=" * 100)
    print(f"n = {len(wc_matches)} World Cup matches  (lower Brier/log_loss better; higher accuracy/recall/precision better)")

    # Per-year breakdown for recent World Cups
    print("\n--- Per-year breakdown (2018+2022) ---")
    for year in [2018, 2022]:
        year_matches = [m for m in wc_matches if m["date"].year == year]
        if not year_matches:
            continue
        print(f"\n  {year} World Cup ({len(year_matches)} matches):")
        print(f"  {'Config':<28} {'avg_brier':>10} {'accuracy':>9} {'draw_recall':>12}")
        for label, mode in configs:
            briers = []
            correct = 0
            draw_actual = 0
            draw_correct = 0
            for m in year_matches:
                eh = elo_before(m["home_team"], m["date"])
                ea = elo_before(m["away_team"], m["date"])
                is_knockout = _is_knockout(m["date"].year, m["home_team"], m["away_team"])
                actual = _outcome(m["home_score"], m["away_score"])
                if mode == "btd":
                    probs = calculate_btd_probabilities(eh, ea, is_neutral=True, is_knockout=is_knockout)
                else:
                    probs = _old_heuristic_probs(eh, ea, is_knockout)
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
