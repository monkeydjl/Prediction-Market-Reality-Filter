"""Unit tests for as-of rest/form helpers."""
from datetime import datetime, timezone

from app.sports._shared.rest_form import (
    enrich_matches_rest_form,
    form_as_of,
    rest_days_as_of,
)

UTC = timezone.utc


def _m(
    mid: str,
    home: str,
    away: str,
    hs: int | None,
    aws: int | None,
    day: int,
) -> dict:
    return {
        "match_id": mid,
        "home_team": home,
        "away_team": away,
        "home_score": hs,
        "away_score": aws,
        "kickoff_utc": datetime(2024, 1, day, 19, 0, tzinfo=UTC),
    }


def test_form_empty_history_returns_default():
    kickoff = datetime(2024, 1, 10, tzinfo=UTC)
    assert form_as_of("A", kickoff, []) == 0.5


def test_rest_empty_history_returns_none():
    kickoff = datetime(2024, 1, 10, tzinfo=UTC)
    assert rest_days_as_of("A", kickoff, []) is None


def test_rest_days_calendar_gap():
    history = [_m("g1", "A", "B", 1, 0, 1)]
    kickoff = datetime(2024, 1, 4, 19, 0, tzinfo=UTC)
    assert rest_days_as_of("A", kickoff, history) == 3.0


def test_form_as_of_excludes_future_and_self():
    history = [
        _m("g1", "A", "B", 2, 1, 1),
        _m("g2", "B", "A", 3, 1, 5),
    ]
    kickoff = history[1]["kickoff_utc"]
    assert form_as_of("A", kickoff, history, exclude_match_id="g2") == 1.0
    assert form_as_of("B", kickoff, history, exclude_match_id="g2") == 0.0


def test_form_draw_is_not_win():
    history = [_m("g1", "A", "B", 1, 1, 1)]
    kickoff = datetime(2024, 1, 10, tzinfo=UTC)
    assert form_as_of("A", kickoff, history) == 0.0


def test_form_max_matches_uses_most_recent():
    history = []
    for i in range(1, 13):
        hs, aws = (0, 1) if i <= 2 else (1, 0)
        history.append(_m(f"g{i}", "A", "B", hs, aws, i))
    kickoff = datetime(2024, 1, 20, tzinfo=UTC)
    assert form_as_of("A", kickoff, history, max_matches=10) == 1.0


def test_rest_ignores_missing_kickoff_records():
    history = [
        {
            "match_id": "bad",
            "home_team": "A",
            "away_team": "B",
            "home_score": 1,
            "away_score": 0,
            "kickoff_utc": None,
        },
        _m("g1", "A", "B", 1, 0, 1),
    ]
    kickoff = datetime(2024, 1, 3, 19, 0, tzinfo=UTC)
    assert rest_days_as_of("A", kickoff, history) == 2.0


def test_rest_none_when_kickoff_missing():
    history = [_m("g1", "A", "B", 1, 0, 1)]
    assert rest_days_as_of("A", None, history) is None


def test_enrich_preserves_order_and_sets_fields():
    matches = [
        _m("g1", "A", "B", 1, 0, 1),
        _m("g2", "A", "C", 1, 0, 3),
    ]
    out = enrich_matches_rest_form(matches)
    assert [m["match_id"] for m in out] == ["g1", "g2"]
    assert out[0]["rest_days_home"] is None
    assert out[0]["form_home"] == 0.5
    assert out[1]["rest_days_home"] == 2.0
    assert out[1]["form_home"] == 1.0
    assert "rest_days_home" not in matches[0]


def test_naive_kickoff_treated_as_utc():
    history = [
        {
            "match_id": "g1",
            "home_team": "A",
            "away_team": "B",
            "home_score": 1,
            "away_score": 0,
            "kickoff_utc": datetime(2024, 1, 1, 12, 0),
        }
    ]
    kickoff = datetime(2024, 1, 3, 12, 0)
    assert rest_days_as_of("A", kickoff, history) == 2.0
