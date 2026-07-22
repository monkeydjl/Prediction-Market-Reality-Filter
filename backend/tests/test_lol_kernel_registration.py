"""Task 7: LolAdapter / LolMarketOnlyEngine registration when PHASE_LOL_ENABLED."""


def _clear_kernel():
    from app.api.routes import predictions

    if hasattr(predictions._get_kernel, "_instance"):
        delattr(predictions._get_kernel, "_instance")


def test_lol_not_registered_when_phase_lol_disabled(tmp_path, monkeypatch):
    """With KERNEL on and PHASE_LOL_ENABLED off, multi has no lol- prefix."""
    import app.core.config as config_module
    from app.kernel.kernel_db import close_kernel_session, init_kernel_db

    db_path = str(tmp_path / "kernel_api_test_lol_off.db")
    init_kernel_db(db_path)
    try:
        monkeypatch.setattr(config_module.settings, "KERNEL_PREDICTION_ENABLED", True)
        monkeypatch.setattr(config_module.settings, "PHASE_LOL_ENABLED", False)
        monkeypatch.setattr(config_module.settings, "PHASE4_NBA_ENABLED", False)
        monkeypatch.setattr(config_module.settings, "PHASE5_MLB_ENABLED", False)
        monkeypatch.setattr(config_module.settings, "PHASE5_NHL_ENABLED", False)

        from app.api.routes import predictions

        _clear_kernel()
        kernel = predictions._get_kernel()
        prefixes = kernel._adapter.registered_prefixes()
        engines = kernel._engine_registry.list_engines()

        assert "lol-" not in prefixes
        assert "lol_market_only" not in engines
    finally:
        close_kernel_session()
        _clear_kernel()


def test_lol_registered_when_phase_lol_enabled(tmp_path, monkeypatch):
    """With PHASE_LOL_ENABLED on, lol- prefix and lol_market_only engine are wired."""
    import app.core.config as config_module
    from app.kernel.kernel_db import close_kernel_session, init_kernel_db

    db_path = str(tmp_path / "kernel_api_test_lol_on.db")
    init_kernel_db(db_path)
    try:
        monkeypatch.setattr(config_module.settings, "KERNEL_PREDICTION_ENABLED", True)
        monkeypatch.setattr(config_module.settings, "PHASE_LOL_ENABLED", True)

        from app.api.routes import predictions

        _clear_kernel()
        kernel = predictions._get_kernel()
        prefixes = kernel._adapter.registered_prefixes()
        engines = kernel._engine_registry.list_engines()

        assert "lol-" in prefixes
        assert "lol_market_only" in engines
    finally:
        close_kernel_session()
        _clear_kernel()
