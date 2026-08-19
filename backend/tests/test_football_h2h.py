from datetime import date

from app.sports.football.h2h import H2HMeeting, aggregate_h2h_meetings, merge_h2h_meetings


def test_merge_h2h_meetings_deduplicates_matching_source_rows():
    historical = [H2HMeeting(date(2025, 9, 1), 2, 0, True)]
    kernel = [H2HMeeting(date(2025, 9, 1), 2, 0, True)]

    merged = merge_h2h_meetings(historical, kernel)

    assert merged == historical


def test_merge_h2h_meetings_keeps_same_day_different_results():
    meetings = merge_h2h_meetings(
        [H2HMeeting(date(2025, 9, 1), 2, 0, True)],
        [H2HMeeting(date(2025, 9, 1), 1, 1, True)],
    )

    assert len(meetings) == 2


def test_aggregate_applies_cap_after_merge_and_sort():
    meetings = merge_h2h_meetings(
        [H2HMeeting(date(2025, 9, 1), 2, 0, True)],
        [
            H2HMeeting(date(2025, 10, 1), 0, 1, False),
            H2HMeeting(date(2025, 8, 1), 1, 1, False),
        ],
    )

    result = aggregate_h2h_meetings(meetings, max_matches=2)

    assert result is not None
    assert result["matches_played"] == 2
    assert result["home_wins"] == 1
    assert result["away_wins"] == 1
    assert result["draws"] == 0
