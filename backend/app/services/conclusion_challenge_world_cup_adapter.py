from __future__ import annotations

from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _getattr(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


def build_world_cup_challenge_input(
    match: Any,
    prediction_result: dict[str, Any],
    factors: dict[str, Any] | None,
    *,
    attempt_count: int = 0,
    allow_llm_critic: bool = False,
    strictness: str = "normal",
) -> dict[str, Any]:
    prediction = prediction_result if isinstance(prediction_result, dict) else {}
    factor_payload = factors if isinstance(factors, dict) else {}
    data_quality = (
        prediction.get("data_quality")
        or factor_payload.get("data_quality")
        or "unknown"
    )
    calibration = (
        prediction.get("calibration_info")
        or factor_payload.get("confidence_calibration")
        or {}
    )
    explanation = (
        prediction.get("explanation_contributions")
        or factor_payload.get("explanation_contributions")
        or {}
    )
    supporting: list[dict[str, Any]] = []
    explanation_items = explanation.get("items", []) if isinstance(explanation, dict) else []
    for item in explanation_items:
        if isinstance(item, dict):
            supporting.append(
                {
                    "source": item.get("label") or item.get("key"),
                    "credibility": 0.7 if item.get("available", True) else 0.3,
                    "direction": "supports",
                    "details": item,
                }
            )

    return {
        "domain": "world_cup",
        "subject": {
            "id": str(_getattr(match, "match_id", "")),
            "title": (
                f"{_getattr(match, 'home_team', '')} "
                f"vs {_getattr(match, 'away_team', '')}"
            ).strip(),
            "type": "match_prediction",
        },
        "conclusion": {
            "direction": None,
            "predicted_score": prediction.get("predicted_score"),
            "probabilities": prediction.get("outcome_probabilities"),
            "confidence": prediction.get("confidence"),
            "recommended_action": None,
        },
        "calculation_trace": {
            "method": prediction.get("prediction_method") or "unknown",
            "engine_used": prediction.get("engine_used"),
            "weights": factor_payload.get("integrated_weights") or {},
            "key_scores": {
                "elo_ratings": prediction.get("elo_ratings"),
                "has_betting_odds": prediction.get("has_betting_odds"),
            },
            "calibration": calibration,
        },
        "evidence": {
            "supporting": supporting,
            "opposing": [],
            "neutral": [],
            "data_quality": {
                "quality": data_quality,
                "score": _dict(factor_payload.get("data_quality_metrics")).get(
                    "quality_score"
                ),
                "metrics": factor_payload.get("data_quality_metrics") or {},
            },
            "source_reliability": None,
        },
        "risk": {
            "level": (
                "high"
                if _getattr(match, "stage", "")
                in {"round_of_16", "quarter_final", "semi_final", "final"}
                else "medium"
            ),
            "flags": [],
            "execution_constraints": {},
        },
        "options": {
            "max_recompute_attempts": 1,
            "strictness": strictness,
            "allow_llm_critic": allow_llm_critic,
        },
        "attempt_count": attempt_count,
    }


def apply_world_cup_challenge_result(
    prediction_result: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    prediction = dict(prediction_result)
    res = result if isinstance(result, dict) else {}
    factors = dict(prediction.get("factors") or {})
    factors["challenge_result"] = {
        "verdict": res.get("verdict"),
        "required_action": res.get("required_action"),
        "failed_checks": res.get("failed_checks", []),
        "warnings": res.get("warnings", []),
        "challenge_summary": res.get("challenge_summary", ""),
        "critic_notes": res.get("critic_notes", {}),
        "attempt_count": res.get("attempt_count", 0),
    }
    if res.get("required_action") in {"downgrade_to_wait", "enqueue_review"}:
        factors["requires_review"] = True
        prediction["high_confidence_selection"] = None
    cap = _dict(res.get("confidence_adjustment")).get("cap")
    if isinstance(cap, (int, float)) and not isinstance(cap, bool):
        current = prediction.get("confidence")
        if isinstance(current, (int, float)) and not isinstance(current, bool):
            prediction["confidence"] = min(float(current), float(cap))
    prediction["factors"] = factors
    return prediction
