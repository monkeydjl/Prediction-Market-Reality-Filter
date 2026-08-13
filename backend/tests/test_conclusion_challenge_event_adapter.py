from app.services.conclusion_challenge_event_adapter import (
    apply_event_challenge_result,
    build_event_challenge_input,
)


def _record():
    return {
        "event_id": "evt-1",
        "event_title": "Will X happen?",
        "probability": {"baseline": 40.0, "estimated": 61.0, "change": 21.0},
        "actionable_recommendation": {
            "direction": "YES",
            "confidence": "high",
            "suggested_allocation_pct": 2.5,
            "edge": 21.0,
            "risk_level": "medium",
        },
        "final_displayed_direction": "YES",
        "final_downgrade_reason": None,
        "evidence_breakdown": [
            {
                "source": "Reuters",
                "direction": "supports",
                "credibility": 0.9,
                "url": "https://reuters.com/a",
            },
            {
                "source": "Blog",
                "direction": "neutral",
                "credibility": 0.4,
                "url": "https://example.com/b",
            },
        ],
        "decision_quality": {"conflict_score": 0.1, "downgraded": False},
        "market_quality": {
            "score": 0.8,
            "downgraded": False,
            "liquidity_score": 0.9,
        },
        "source_reliability": {"suggested_direction": "YES", "downgraded": False},
        "risk": {"level": "medium", "flags": []},
    }


def test_build_event_challenge_input_maps_core_fields():
    payload = build_event_challenge_input(_record(), attempt_count=0)
    assert payload["domain"] == "event_intelligence"
    assert payload["subject"]["id"] == "evt-1"
    assert payload["conclusion"]["direction"] == "YES"
    assert payload["calculation_trace"]["key_scores"]["change"] == 21.0
    assert len(payload["evidence"]["supporting"]) == 1
    assert len(payload["evidence"]["neutral"]) == 1


def test_apply_pass_writes_challenge_without_downgrade():
    record = _record()
    result = {
        "verdict": "pass",
        "required_action": "allow_output",
        "failed_checks": [],
        "warnings": [],
        "challenge_summary": "结论通过否定门检查。",
    }
    apply_event_challenge_result(record, result)
    assert record["final_displayed_direction"] == "YES"
    assert record["conclusion_challenge"]["verdict"] == "pass"


def test_apply_reject_downgrades_to_wait():
    record = _record()
    result = {
        "verdict": "reject",
        "required_action": "downgrade_to_wait",
        "failed_checks": [{"check": "counterevidence", "reason": "存在高可信反证"}],
        "warnings": [],
        "challenge_summary": "结论否定门结果：reject。主要原因：存在高可信反证",
    }
    apply_event_challenge_result(record, result)
    assert record["final_displayed_direction"] == "WAIT"
    assert record["final_downgrade_reason"].startswith(
        "conclusion_challenge_rejected"
    )


def test_challenge_survives_the_read_path_serialization():
    """conclusion_challenge is an extra field, so pin that it reaches clients.

    EventRecord is the event store's validation gate and EventStoreEntry is the
    response_model of GET /api/events/{event_id}. Either one dropping extras
    would leave the UI panel silently empty. (_record() is a partial fixture, so
    the gate is pinned via its extra policy rather than a full validation pass.)
    """
    from app.models.event import EventRecord, EventStoreEntry

    record = _record()
    apply_event_challenge_result(record, {
        "verdict": "reject",
        "required_action": "downgrade_to_wait",
        "failed_checks": [{"check": "counterevidence", "reason": "存在高可信反证"}],
        "warnings": [],
        "challenge_summary": "结论否定门结果：reject。",
    })

    assert EventRecord.model_config["extra"] == "allow"

    entry = EventStoreEntry(event_id="evt-1", record=record).model_dump()
    assert entry["record"]["conclusion_challenge"]["verdict"] == "reject"
    assert entry["record"]["conclusion_challenge"]["required_action"] == (
        "downgrade_to_wait"
    )
