"""P1-O5: review_priority soft demotion of sport recommendations."""
from app.kernel.sport_recommendation_service import (
    _compute_risk_level,
    _review_priority_from_edge,
    _soften_decision_for_priority,
)


def test_soften_critical_demotes_act():
    assert _soften_decision_for_priority("act", "critical") == "provisional_act"
    assert _soften_decision_for_priority("provisional_act", "critical") == "watch"
    assert _soften_decision_for_priority("watch", "critical") == "watch"
    assert _soften_decision_for_priority("skip", "critical") == "skip"


def test_soften_high_only_demotes_act():
    assert _soften_decision_for_priority("act", "high") == "provisional_act"
    assert _soften_decision_for_priority("provisional_act", "high") == "provisional_act"
    assert _soften_decision_for_priority("act", "normal") == "act"


def test_risk_raised_by_priority():
    assert _compute_risk_level(1.0, 0.9, False, "critical") == "high"
    assert _compute_risk_level(1.0, 0.9, False, "high") == "medium"
    assert _compute_risk_level(1.0, 0.9, False, "normal") == "low"


def test_review_priority_from_edge_stale_large():
    p = _review_priority_from_edge(
        {
            "adjusted_edge": 0.15,
            "stale": True,
            "liquidity_factor": 1.0,
            "sources_count": 2,
            "trust": 0.8,
        }
    )
    assert p == "critical"
