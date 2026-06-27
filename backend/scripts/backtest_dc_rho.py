"""Backtest Dixon-Coles rho values on historical World Cup matches.

Compares prediction quality (Brier / accuracy / log_loss / draw recall) of:
  - rho = -0.0763  (new, fitted from international_results.csv)
  - rho = +0.04    (old equivalent: legacy hardcoded rho=0.96 with (1-rho) formula)
  - rho = 0.0      (pure independent Poisson, the current fallback)

Usage:
    cd backend
    $env:PYTHONPATH = "."
    python scripts/backtest_dc_rho.py
"""

from __future__ import annotations

import csv
import logging
import math
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.services.world_cup_engines import world_cup_rule_engine as rule_engine
from app.services.world_cup_historical_results import (
    _load_results,
    get_historical_team_stats,
)


logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "international_results.csv"

RHO_CONFIGS = [
    ("rho=-0.0763 (new, fitted)", -0.0763),
    ("rho=+0.04   (old, hardcoded)", 0.04),
    ("rho=0.0     (pure Poisson)", 0.0),
]


def _brier(home_p: float, draw_p: float, away_p: float, actual: str) -> float:
    actual_vec = (
        [1.0, 0.0, 0.0] if actual == "home_win"
        else [0.0, 1.0, 0.0] if actual == "draw"
        else [0.0, 0.0, 1.0]
    )
    pred = [home_p, draw_p, away_p]
    return sum((p - a) ** 2 for p, a in zip(pred, actual_vec))


def _log_loss(home_p: float, draw_p: float, away_p: float, actual: str) -> float:
    p = home_p if actual == "home_win" else draw_p if actual == "draw" else away_p
    return -math.log(max(p, 1e-12))


def _predicted_outcome(home_p: float, draw_p: float, away_p: float) -> str:
    return max(
        [("home_win", home_p), ("draw", draw_p), ("away_win", away_p)],
        key=lambda kv: kv[1],
    )[0]


def _outcome(home_score: int, away_score: int) -> str:
    if home_score > away_score:
        return "home_win"
    if home_score < away_score:
        return "away_win"
    return "draw"


def _load_world_cup_matches() -> list[dict]:
    """Load all FIFA World Cup (finals) matches sorted by date."""
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Historical CSV not found at {CSV_PATH}")

    matches: list[dict] = []
    with CSV_PATH.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tournament = str(row.get("tournament") or "")
            if tournament != "FIFA World Cup":
                continue
            try:
                d = date.fromisoformat(str(row.get("date") or ""))
                home_score = int(row.get("home_score") or 0)
                away_score = int(row.get("away_score") or 0)
            except (TypeError, ValueError):
                continue
            # Skip matches that ended in penalties (caught by extra-time notation
            # in the CSV — martj42 records 90-min score; finals like 2022 final
            # Argentina-France is recorded as 3-3 in regular time). Keep them
            # since 90-min draw is a legitimate prediction target.
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


def _compute_xg(home_stats: dict, away_stats: dict) -> tuple[float, float]:
    """Baseline xG using rule_engine's formula with all modifiers at neutral."""
    home_attack = home_stats["goals_per_game"]
    home_defense = home_stats["goals_conceded_per_game"]
    away_attack = away_stats["goals_per_game"]
    away_defense = away_stats["goals_conceded_per_game"]
    # rule_engine.calculate_expected_goals with is_home=False, all factors=1.0:
    #   base_xg = (team_attack + opponent_defense) / 2
    home_xg = (home_attack + away_defense) / 2.0
    away_xg = (away_attack + home_defense) / 2.0
    return home_xg, away_xg


def backtest() -> None:
    # Warm the lru_cache once so per-match team-stats queries are fast.
    _load_results()

    matches = _load_world_cup_matches()
    print(f"\nLoaded {len(matches)} FIFA World Cup final-stage matches from CSV")
    years = sorted({m["date"].year for m in matches})
    print(f"Tournament years covered: {years}")

    # Skip matches where we cannot build team stats (no prior history).
    usable: list[dict] = []
    skipped = 0
    for m in matches:
        home_stats = get_historical_team_stats(
            m["home_team"], before_date=m["date"], max_matches=10
        )
        away_stats = get_historical_team_stats(
            m["away_team"], before_date=m["date"], max_matches=10
        )
        if not home_stats or not away_stats:
            skipped += 1
            continue
        m["home_stats"] = home_stats
        m["away_stats"] = away_stats
        m["actual"] = _outcome(m["home_score"], m["away_score"])
        usable.append(m)
    print(f"Usable (both teams have >=1 prior match): {len(usable)}, skipped: {skipped}")

    # Three rho configs
    results: dict[str, dict] = {}
    for label, rho_val in RHO_CONFIGS:
        briers = []
        log_losses = []
        correct = 0
        draw_actual = 0
        draw_predicted = 0
        draw_correct = 0
        for m in usable:
            home_xg, away_xg = _compute_xg(m["home_stats"], m["away_stats"])
            with patch.object(rule_engine, "_load_rho", return_value=rho_val):
                probs = rule_engine.calculate_outcome_probabilities(home_xg, away_xg)
            actual = m["actual"]
            briers.append(_brier(probs["home_win"], probs["draw"], probs["away_win"], actual))
            log_losses.append(_log_loss(probs["home_win"], probs["draw"], probs["away_win"], actual))
            pred = _predicted_outcome(probs["home_win"], probs["draw"], probs["away_win"])
            if pred == actual:
                correct += 1
            if actual == "draw":
                draw_actual += 1
            if pred == "draw":
                draw_predicted += 1
                if actual == "draw":
                    draw_correct += 1

        n = len(usable)
        results[label] = {
            "n": n,
            "avg_brier": sum(briers) / n,
            "avg_log_loss": sum(log_losses) / n,
            "accuracy": correct / n,
            "draw_actual": draw_actual,
            "draw_predicted": draw_predicted,
            "draw_recall": draw_correct / draw_actual if draw_actual else 0.0,
        }

    # Print comparison table
    print("\n" + "=" * 92)
    print(f"{'Config':<32} {'avg_brier':>10} {'avg_logloss':>12} {'accuracy':>9} "
          f"{'draw_recall':>12} {'draw_pred':>10}")
    print("-" * 92)
    for label, r in results.items():
        print(f"{label:<32} {r['avg_brier']:>10.4f} {r['avg_log_loss']:>12.4f} "
              f"{r['accuracy']:>8.1%} {r['draw_recall']:>11.1%} "
              f"{r['draw_predicted']:>5}/{r['draw_actual']}")
    print("=" * 92)
    print(f"n = {len(usable)} matches  (lower Brier / log_loss is better; higher accuracy / draw_recall is better)")


if __name__ == "__main__":
    backtest()
