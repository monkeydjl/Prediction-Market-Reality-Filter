# backend/app/kernel/backtest/runner.py
"""BacktestRunner — runs backtest with given parameters over historical matches.

Replicates the engine formulas (BasketballEngine, BaseballEngine, HockeyEngine)
to test candidate parameters without modifying engines. This is an acknowledged
DRY violation — the only way to honor zero-invasion.

If an engine's formula changes, the corresponding _compute_*_prediction method
must be updated to match.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.kernel.backtest.elo_time_machine import EloTimeMachine, EloParams
from app.sports._shared.elo_calculator import compute_expected_score


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class BacktestParams:
    """Parameters for a single backtest run."""
    factor_weights: dict[str, float]
    elo_params: dict[str, float]


@dataclass(frozen=True)
class BacktestResult:
    """Result of a backtest run."""
    accuracy: float
    brier_score: float
    mae: float
    sample_count: int
    score: float  # 0.5*accuracy + 0.3*(1-brier) + 0.2*(1-mae)
    predictions: list[dict] = field(default_factory=list)


# Sport-specific constants (match engine defaults)
_HOME_COURT_PROB = {
    "nba": 0.58,
    "mlb": 0.54,
    "nhl": 0.55,
}


class BacktestRunner:
    """Runs backtest with given parameters over historical matches."""

    def __init__(self) -> None:
        self._elo_machine = EloTimeMachine()

    def run(
        self,
        sport: str,
        *,
        train_matches: list[dict[str, Any]],
        test_matches: list[dict[str, Any]],
        params: BacktestParams,
    ) -> BacktestResult:
        """Run backtest with given params. Synchronous.

        Args:
            sport: "nba" / "mlb" / "nhl"
            train_matches: Training matches (for Elo accumulation only)
            test_matches: Test matches (for evaluation)
            params: BacktestParams with factor_weights + elo_params

        Returns:
            BacktestResult with accuracy, Brier, MAE, score.
        """
        if not test_matches:
            return BacktestResult(accuracy=0.0, brier_score=0.0, mae=0.0, sample_count=0, score=0.0)

        # Replay Elo over all matches (train + test) with candidate Elo params
        all_matches = train_matches + test_matches
        elo_params = EloParams(
            hfa=params.elo_params["hfa"],
            k_regular=params.elo_params["k_regular"],
            k_playoff=params.elo_params["k_playoff"],
            season_carry=params.elo_params.get("season_carry", 0.75),
            initial=params.elo_params.get("initial", 1500),
            league_avg_total=0,  # not used for Elo computation
        )
        snapshots = self._elo_machine.replay(sport, all_matches, elo_params)

        # Run predictions on test matches
        predictions: list[dict] = []
        correct = 0
        brier_sum = 0.0
        mae_sum = 0.0

        for match in test_matches:
            match_id = match["match_id"]
            snapshot = snapshots.get(match_id, {"home_elo": 1500.0, "away_elo": 1500.0})

            # Compute prediction using replicated engine formula
            p_home = self._compute_prediction(sport, match, snapshot, params.factor_weights, params.elo_params)
            p_away = 1.0 - p_home

            # Actual outcome
            home_won = match["home_score"] > match["away_score"]
            actual_home = 1.0 if home_won else 0.0

            # Metrics
            predicted_outcome = "home_win" if p_home >= 0.5 else "away_win"
            actual_outcome = "home_win" if home_won else "away_win"
            is_correct = predicted_outcome == actual_outcome
            if is_correct:
                correct += 1

            # Brier score: (predicted_prob - actual)^2 averaged over outcomes
            brier = ((p_home - actual_home) ** 2 + (p_away - (1 - actual_home)) ** 2) / 2
            brier_sum += brier

            # MAE: |predicted_prob - actual|
            mae = abs(p_home - actual_home)
            mae_sum += mae

            predictions.append({
                "match_id": match_id,
                "p_home": round(p_home, 4),
                "p_away": round(p_away, 4),
                "actual": actual_outcome,
                "predicted": predicted_outcome,
                "correct": is_correct,
            })

        n = len(test_matches)
        # Round components first, then compute score from rounded values so that
        # result.score is mathematically consistent with the formula recomputed
        # from result.accuracy / result.brier_score / result.mae.
        accuracy = round(correct / n, 4)
        brier_score = round(brier_sum / n, 4)
        mae = round(mae_sum / n, 4)
        score = 0.5 * accuracy + 0.3 * (1 - brier_score) + 0.2 * (1 - mae)

        return BacktestResult(
            accuracy=accuracy,
            brier_score=brier_score,
            mae=mae,
            sample_count=n,
            score=score,
            predictions=predictions,
        )

    def _compute_prediction(
        self,
        sport: str,
        match: dict[str, Any],
        elo_snapshot: dict[str, float],
        weights: dict[str, float],
        elo_params: dict[str, float],
    ) -> float:
        """Compute P(home_win) using replicated engine formula."""
        if sport == "nba":
            return self._compute_nba_prediction(match, elo_snapshot, weights, elo_params)
        elif sport == "mlb":
            return self._compute_mlb_prediction(match, elo_snapshot, weights, elo_params)
        elif sport == "nhl":
            return self._compute_nhl_prediction(match, elo_snapshot, weights, elo_params)
        else:
            raise ValueError(f"Unsupported sport: {sport}")

    def _compute_nba_prediction(
        self, match: dict, elo_snapshot: dict, weights: dict, elo_params: dict,
    ) -> float:
        """Replicate BasketballEngine formula."""
        hfa = elo_params["hfa"]
        factors: list[tuple[str, float, float, bool]] = []

        # 1. Elo factor
        elo_home = elo_snapshot["home_elo"]
        elo_away = elo_snapshot["away_elo"]
        p_elo = compute_expected_score(elo_home, elo_away, hfa)
        factors.append(("elo", p_elo, weights.get("elo", 0), True))

        # 2. Home court (constant)
        factors.append(("home_court", _HOME_COURT_PROB["nba"], weights.get("home_court", 0), True))

        # 3. Rest factor
        rest_home = match.get("rest_days_home")
        rest_away = match.get("rest_days_away")
        if rest_home is not None and rest_away is not None:
            rest_diff = _clamp(rest_home - rest_away, -3, 3)
            p_rest = 0.5 + rest_diff * 0.03
            factors.append(("rest", p_rest, weights.get("rest", 0), True))
        else:
            factors.append(("rest", 0.5, weights.get("rest", 0), False))

        # 4. Form factor
        form_home = match.get("form_home")
        form_away = match.get("form_away")
        if form_home is not None and form_away is not None:
            form_diff = _clamp(form_home - form_away, -0.3, 0.3)
            p_form = 0.5 + form_diff * 0.5
            factors.append(("form", p_form, weights.get("form", 0), True))
        else:
            factors.append(("form", 0.5, weights.get("form", 0), False))

        return self._weighted_fusion(factors)

    def _compute_mlb_prediction(
        self, match: dict, elo_snapshot: dict, weights: dict, elo_params: dict,
    ) -> float:
        """Replicate BaseballEngine formula."""
        hfa = elo_params["hfa"]
        factors: list[tuple[str, float, float, bool]] = []

        # 1. Elo
        p_elo = compute_expected_score(elo_snapshot["home_elo"], elo_snapshot["away_elo"], hfa)
        factors.append(("elo", p_elo, weights.get("elo", 0), True))

        # 2. Home court
        factors.append(("home_court", _HOME_COURT_PROB["mlb"], weights.get("home_court", 0), True))

        # 3. Rest
        rest_home = match.get("rest_days_home")
        rest_away = match.get("rest_days_away")
        if rest_home is not None and rest_away is not None:
            rest_diff = _clamp(rest_home - rest_away, -3, 3)
            factors.append(("rest", 0.5 + rest_diff * 0.03, weights.get("rest", 0), True))
        else:
            factors.append(("rest", 0.5, weights.get("rest", 0), False))

        # 4. Form
        form_home = match.get("form_home")
        form_away = match.get("form_away")
        if form_home is not None and form_away is not None:
            form_diff = _clamp(form_home - form_away, -0.3, 0.3)
            factors.append(("form", 0.5 + form_diff * 0.5, weights.get("form", 0), True))
        else:
            factors.append(("form", 0.5, weights.get("form", 0), False))

        # 5. Starting pitcher
        era_home = match.get("pitcher_era_home")
        era_away = match.get("pitcher_era_away")
        if era_home is not None and era_away is not None:
            era_diff = _clamp(era_away - era_home, -2.0, 2.0)
            factors.append(("starting_pitcher", 0.5 + era_diff * 0.1, weights.get("starting_pitcher", 0), True))
        else:
            factors.append(("starting_pitcher", 0.5, weights.get("starting_pitcher", 0), False))

        return self._weighted_fusion(factors)

    def _compute_nhl_prediction(
        self, match: dict, elo_snapshot: dict, weights: dict, elo_params: dict,
    ) -> float:
        """Replicate HockeyEngine formula."""
        hfa = elo_params["hfa"]
        factors: list[tuple[str, float, float, bool]] = []

        # 1. Elo
        p_elo = compute_expected_score(elo_snapshot["home_elo"], elo_snapshot["away_elo"], hfa)
        factors.append(("elo", p_elo, weights.get("elo", 0), True))

        # 2. Home court
        factors.append(("home_court", _HOME_COURT_PROB["nhl"], weights.get("home_court", 0), True))

        # 3. Rest
        rest_home = match.get("rest_days_home")
        rest_away = match.get("rest_days_away")
        if rest_home is not None and rest_away is not None:
            rest_diff = _clamp(rest_home - rest_away, -3, 3)
            factors.append(("rest", 0.5 + rest_diff * 0.03, weights.get("rest", 0), True))
        else:
            factors.append(("rest", 0.5, weights.get("rest", 0), False))

        # 4. Form
        form_home = match.get("form_home")
        form_away = match.get("form_away")
        if form_home is not None and form_away is not None:
            form_diff = _clamp(form_home - form_away, -0.3, 0.3)
            factors.append(("form", 0.5 + form_diff * 0.5, weights.get("form", 0), True))
        else:
            factors.append(("form", 0.5, weights.get("form", 0), False))

        # 5. Goalie
        sv_home = match.get("goalie_save_pct_home")
        sv_away = match.get("goalie_save_pct_away")
        if sv_home is not None and sv_away is not None:
            sv_diff = _clamp(sv_home - sv_away, -0.1, 0.1)
            factors.append(("goalie", 0.5 + sv_diff * 2.0, weights.get("goalie", 0), True))
        else:
            factors.append(("goalie", 0.5, weights.get("goalie", 0), False))

        return self._weighted_fusion(factors)

    @staticmethod
    def _weighted_fusion(factors: list[tuple[str, float, float, bool]]) -> float:
        """Weighted average with weight redistribution for unavailable factors."""
        available = [(f, p, w) for f, p, w, a in factors if a]
        total_w = sum(w for _, _, w in available)
        if total_w > 0:
            return sum(p * (w / total_w) for _, p, w in available)
        return 0.5
