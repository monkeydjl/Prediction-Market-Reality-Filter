from app.services.conclusion_challenge_service import challenge_conclusion


def _base_payload(**overrides):
    payload = {
        "domain": "event_intelligence",
        "subject": {
            "id": "evt-1",
            "title": "Will X happen?",
            "type": "event_recommendation",
        },
        "conclusion": {
            "direction": "YES",
            "predicted_score": None,
            "probabilities": None,
            "confidence": 0.72,
            "recommended_action": "YES",
        },
        "calculation_trace": {
            "method": "event_intelligence",
            "engine_used": None,
            "weights": {},
            "key_scores": {
                "baseline": 40.0,
                "estimated": 58.0,
                "change": 18.0,
            },
            "calibration": {"is_reliable": True, "sample_count": 20},
        },
        "evidence": {
            "supporting": [
                {
                    "source": "Reuters",
                    "credibility": 0.9,
                    "direction": "supports",
                }
            ],
            "opposing": [],
            "neutral": [],
            "data_quality": {"quality": "real", "score": 0.9},
            "source_reliability": {
                "suggested_direction": "YES",
                "downgraded": False,
            },
        },
        "risk": {
            "level": "medium",
            "flags": [],
            "execution_constraints": {},
        },
        "options": {
            "max_recompute_attempts": 1,
            "strictness": "normal",
            "allow_llm_critic": False,
        },
        "attempt_count": 0,
    }
    payload.update(overrides)
    return payload


def test_all_checks_pass_returns_pass():
    result = challenge_conclusion(_base_payload())
    assert result["verdict"] == "pass"
    assert result["required_action"] == "allow_output"
    assert result["failed_checks"] == []


def test_strong_conclusion_without_support_is_insufficient_evidence():
    payload = _base_payload()
    payload["evidence"]["supporting"] = []
    payload["evidence"]["neutral"] = [{"source": "blog", "credibility": 0.4}]
    result = challenge_conclusion(payload)
    assert result["verdict"] == "insufficient_evidence"
    assert result["required_action"] == "downgrade_to_wait"
    assert result["failed_checks"][0]["check"] == "evidence_support"


def test_hard_counterevidence_rejects():
    payload = _base_payload()
    payload["evidence"]["opposing"] = [
        {"source": "official", "credibility": 0.95, "direction": "refutes"}
    ]
    result = challenge_conclusion(payload)
    assert result["verdict"] == "reject"
    assert result["required_action"] == "downgrade_to_wait"
    assert any(
        item["check"] == "counterevidence" for item in result["failed_checks"]
    )


def test_two_soft_failures_request_single_recalculation():
    payload = _base_payload()
    payload["conclusion"]["confidence"] = 0.86
    payload["calculation_trace"]["calibration"] = {
        "is_reliable": False,
        "sample_count": 2,
    }
    payload["risk"]["execution_constraints"] = {"liquidity_ok": False}
    result = challenge_conclusion(payload)
    assert result["verdict"] == "revise"
    assert result["required_action"] == "recalculate_once"


def test_revise_after_one_attempt_downgrades():
    payload = _base_payload(attempt_count=1)
    payload["conclusion"]["confidence"] = 0.86
    payload["calculation_trace"]["calibration"] = {
        "is_reliable": False,
        "sample_count": 2,
    }
    payload["risk"]["execution_constraints"] = {"liquidity_ok": False}
    result = challenge_conclusion(payload)
    assert result["verdict"] == "revise"
    assert result["required_action"] == "downgrade_to_wait"
