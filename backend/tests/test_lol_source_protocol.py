from app.sports.lol.source import (
    NullLolScheduleSource,
    normalize_lol_schedule_vendor,
    resolve_lol_schedule_source,
)


def test_null_source_empty():
    src = NullLolScheduleSource()
    assert src.list_upcoming() == []
    assert src.get_result("x") is None


def test_normalize_lol_schedule_vendor():
    assert normalize_lol_schedule_vendor(None) == "null"
    assert normalize_lol_schedule_vendor("  GRID ") == "grid"
    assert normalize_lol_schedule_vendor("dry_run") == "dry_run"
    assert normalize_lol_schedule_vendor("not-a-vendor") == "null"


def test_resolve_null_and_dry_run_not_blocked():
    null_res = resolve_lol_schedule_source("null")
    assert null_res.requested_vendor == "null"
    assert null_res.effective_vendor == "null"
    assert null_res.blocked is False
    assert isinstance(null_res.source, NullLolScheduleSource)
    assert null_res.source.list_upcoming() == []

    dry = resolve_lol_schedule_source("dry_run")
    assert dry.requested_vendor == "dry_run"
    assert dry.effective_vendor == "dry_run"
    assert dry.blocked is False
    assert isinstance(dry.source, NullLolScheduleSource)
    assert dry.reason is not None


def test_resolve_grid_blocked_forces_null_source():
    res = resolve_lol_schedule_source("grid")
    assert res.requested_vendor == "grid"
    assert res.effective_vendor == "null"
    assert res.blocked is True
    assert isinstance(res.source, NullLolScheduleSource)
    assert res.source.list_upcoming() == []
    assert res.source.get_result("any") is None
    assert res.reason is not None
    assert "GATES" in res.reason or "HTTP" in res.reason


def test_resolve_pandascore_blocked():
    res = resolve_lol_schedule_source("pandascore")
    assert res.blocked is True
    assert res.effective_vendor == "null"


def test_resolve_unknown_vendor_blocked():
    res = resolve_lol_schedule_source("scraper-bot")
    assert res.blocked is True
    assert res.effective_vendor == "null"
    assert res.requested_vendor == "scraper-bot"


def test_lol_adapter_default_uses_resolver(monkeypatch):
    import app.core.config as config_module
    from app.sports.lol.lol_adapter import LolAdapter

    monkeypatch.setattr(config_module.settings, "LOL_SCHEDULE_VENDOR", "grid")
    monkeypatch.setattr(config_module.settings, "LOL_DRY_RUN_IMPORT", False)
    adapter = LolAdapter()
    assert adapter._source_resolution is not None
    assert adapter._source_resolution.blocked is True
    assert adapter._source_resolution.requested_vendor == "grid"
    assert isinstance(adapter._source, NullLolScheduleSource)
    assert adapter.sync_schedule() == 0
