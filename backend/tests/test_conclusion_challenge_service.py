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


def test_strong_no_conclusion_without_support_is_insufficient_evidence():
    """The NO side of the same strong-conclusion evidence gate.

    ``..._without_support_is_insufficient_evidence`` pins the YES side only, and
    a YES-only guard stays green when ``_STRONG_EVENT_DIRECTIONS`` loses NO: the
    conclusion simply stops counting as strong, so every downstream check --
    including the one that produced that verdict -- goes quiet. This is the
    guard registered as mutation W28.
    """
    payload = _base_payload()
    payload["conclusion"]["direction"] = "NO"
    payload["conclusion"]["recommended_action"] = "NO"
    payload["evidence"]["supporting"] = []
    payload["evidence"]["source_reliability"] = {
        "suggested_direction": "NO",
        "downgraded": False,
    }
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


def test_small_probability_change_counts_for_a_no_conclusion():
    """A NO conclusion whose probability barely moved still soft-fails.

    ``_check_calculation`` reads the same strong-direction membership as the
    evidence checks, and until this test nothing exercised its
    ``event_intelligence`` branch at all -- a 2.0pt move is well under the 3.0pt
    bar, so the check must fire. Paired with the liquidity soft-fail from
    ``risk`` that is two soft failures, i.e. a single recalculation. Reverting
    ``_STRONG_EVENT_DIRECTIONS`` to YES-only drops the calculation failure and
    the verdict falls back to ``pass_with_warnings`` -- the second call site
    covered by mutation W28.
    """
    payload = _base_payload()
    payload["conclusion"]["direction"] = "NO"
    payload["conclusion"]["recommended_action"] = "NO"
    payload["calculation_trace"]["key_scores"]["change"] = 2.0
    payload["evidence"]["source_reliability"] = {
        "suggested_direction": "NO",
        "downgraded": False,
    }
    payload["risk"]["execution_constraints"] = {"liquidity_ok": False}
    result = challenge_conclusion(payload)
    assert result["verdict"] == "revise"
    assert result["required_action"] == "recalculate_once"
