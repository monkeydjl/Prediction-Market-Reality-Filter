from app.sports.lol.source import NullLolScheduleSource, LolSeriesRecord


def test_null_source_empty():
    src = NullLolScheduleSource()
    assert src.list_upcoming() == []
    assert src.get_result("x") is None
