from types import SimpleNamespace

from app.services.conclusion_challenge_world_cup_adapter import (
    apply_world_cup_challenge_result,
    build_world_cup_challenge_input,
)


def _match():
    return SimpleNamespace(
        match_id="wc-1",
        home_team="A",
        away_team="B",
        stage="group_stage",
    )


def _prediction():
    return {
        "predicted_score": {"home": 2.0, "away": 1.0},
        "outcome_probabilities": {
            "home_win": 0.52,
            "draw": 0.24,
            "away_win": 0.24,
        },
        "confidence": 0.78,
        "prediction_method": "integrated",
        "elo_ratings": {"home": 1700, "away": 1600},
        "has_betting_odds": True,
        "high_confidence_selection": {"selected_engine": "integrated"},
    }


def test_build_world_cup_input_maps_prediction_fields():
    factors = {
        "data_quality": "real",
        "confidence_calibration": {"is_reliable": True, "sample_count": 12},
        "explanation_contributions": {
            "items": [{"key": "elo", "home_impact": 0.2}]
        },
    }
    payload = build_world_cup_challenge_input(_match(), _prediction(), factors)
    assert payload["domain"] == "world_cup"
    assert payload["subject"]["id"] == "wc-1"
    assert payload["conclusion"]["predicted_score"]["home"] == 2.0
    assert payload["conclusion"]["probabilities"]["home_win"] == 0.52
    assert payload["evidence"]["data_quality"]["quality"] == "real"


def test_apply_pass_writes_challenge_result_only():
    prediction = _prediction()
    result = {
        "verdict": "pass",
        "required_action": "allow_output",
        "failed_checks": [],
        "warnings": [],
        "challenge_summary": "结论通过否定门检查。",
    }
    updated = apply_world_cup_challenge_result(prediction, result)
    assert updated["confidence"] == 0.78
    assert updated["factors"]["challenge_result"]["verdict"] == "pass"


def test_apply_reject_caps_confidence_and_removes_high_confidence_semantics():
    prediction = _prediction()
    result = {
        "verdict": "reject",
        "required_action": "downgrade_to_wait",
        "failed_checks": [{"check": "counterevidence", "reason": "赔率反向"}],
        "warnings": [],
        "confidence_adjustment": {"cap": 0.60, "reason": "否定门未通过"},
        "challenge_summary": "结论否定门结果：reject。主要原因：赔率反向",
    }
    updated = apply_world_cup_challenge_result(prediction, result)
    assert updated["confidence"] == 0.60
    assert updated["high_confidence_selection"] is None
    assert updated["factors"]["requires_review"] is True
