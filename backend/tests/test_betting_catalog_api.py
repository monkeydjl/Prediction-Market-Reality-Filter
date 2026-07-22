"""Tests for GET /api/betting/catalog (竞猜 catalog)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_betting_catalog_shape():
    from app.main import app

    client = TestClient(app)
    resp = client.get("/api/betting/catalog")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == 1
    assert "football" in data["sections"]
    ids = {c["id"] for c in data["competitions"]}
    assert "world-cup" in ids
    assert "epl" in ids
    assert "nba" in ids
    assert "esports" in ids
    assert "lol" in ids
    tool_ids = {t["id"] for t in data["tools"]}
    assert "edges" in tool_ids
    es = next(c for c in data["competitions"] if c["id"] == "esports")
    assert es["status"] == "coming_soon"
    assert es["track"] == "placeholder"
    lol = next(c for c in data["competitions"] if c["id"] == "lol")
    assert lol["status"] == "coming_soon"
    assert lol["track"] == "placeholder"
    assert lol["sport"] == "lol"
    assert lol["competition_code"] == "lol"
    assert lol["kernel_sport"] == "lol"
    assert lol["section"] == "esports"
    assert data["flags"]["phase_lol_enabled"] is False


def test_betting_catalog_item_found():
    from app.main import app

    client = TestClient(app)
    resp = client.get("/api/betting/catalog/epl")
    assert resp.status_code == 200
    assert resp.json()["competition_code"] == "epl"
    assert resp.json()["kernel_sport"] == "football"


def test_betting_catalog_item_404():
    from app.main import app

    client = TestClient(app)
    resp = client.get("/api/betting/catalog/does-not-exist")
    assert resp.status_code == 404


def test_normalize_competition_code_aliases():
    from app.kernel.competition_codes import (
        competitions_equivalent,
        normalize_competition_code,
    )

    assert normalize_competition_code("PL") == "epl"
    assert normalize_competition_code("serie-a") == "serie_a"
    assert normalize_competition_code("wc") == "world_cup"
    assert normalize_competition_code(None) is None
    assert normalize_competition_code("  ") is None
    assert competitions_equivalent("seriea", "serie_a")
    assert competitions_equivalent("ligue1", "ligue-1")


def test_betting_status_shape():
    from app.main import app

    client = TestClient(app)
    resp = client.get("/api/betting/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == 1
    assert "flags" in data
    assert data["flags"]["phase_lol_enabled"] is False
    assert data["flags"]["lol_dry_run_import"] is False
    assert data["flags"]["lol_dry_run_path_configured"] is False
    assert "kernel_ready" in data
    assert isinstance(data["registered_prefixes"], list)
    assert "hint" in data


def test_build_status_payload_kernel_off():
    from unittest.mock import patch

    from app.kernel.betting_catalog import build_status_payload

    with patch(
        "app.kernel.betting_catalog._kernel_flags",
        return_value={
            "kernel_prediction_enabled": False,
            "phase2_leagues_enabled": False,
            "epl_data_enabled": False,
            "ucl_data_enabled": False,
            "phase4_nba_enabled": False,
            "phase5_mlb_enabled": False,
            "phase5_nhl_enabled": False,
            "phase_lol_enabled": False,
            "lol_dry_run_import": False,
            "lol_dry_run_path_configured": False,
        },
    ):
        body = build_status_payload()
    assert body["kernel_ready"] is False
    assert body["registered_prefixes"] == []
    assert body["kernel_error"] is None


def test_build_status_hint_mentions_lol_when_phase_on():
    from unittest.mock import MagicMock, patch

    from app.kernel.betting_catalog import build_status_payload

    flags_on = {
        "kernel_prediction_enabled": True,
        "phase2_leagues_enabled": False,
        "epl_data_enabled": False,
        "ucl_data_enabled": False,
        "phase4_nba_enabled": False,
        "phase5_mlb_enabled": False,
        "phase5_nhl_enabled": False,
        "phase_lol_enabled": True,
        "lol_dry_run_import": True,
        "lol_dry_run_path_configured": True,
    }
    mock_kernel = MagicMock()
    mock_adapter = MagicMock()
    mock_adapter.registered_prefixes.return_value = ["lol-"]
    mock_kernel._adapter = mock_adapter

    with (
        patch("app.kernel.betting_catalog._kernel_flags", return_value=flags_on),
        patch("app.api.routes.predictions._get_kernel", return_value=mock_kernel),
    ):
        body = build_status_payload()
    assert body["kernel_ready"] is True
    assert "lol-" in body["registered_prefixes"]
    assert "lol-" in body["hint"]
    assert "LOL_DRY_RUN_IMPORT" in body["hint"]


def test_lol_adapter_likely_when_phase_lol_enabled():
    from unittest.mock import patch

    from app.kernel.betting_catalog import build_catalog_payload

    base_flags = {
        "kernel_prediction_enabled": False,
        "phase2_leagues_enabled": False,
        "epl_data_enabled": False,
        "ucl_data_enabled": False,
        "phase4_nba_enabled": False,
        "phase5_mlb_enabled": False,
        "phase5_nhl_enabled": False,
        "phase_lol_enabled": False,
        "lol_dry_run_import": False,
        "lol_dry_run_path_configured": False,
    }
    with patch(
        "app.kernel.betting_catalog._kernel_flags",
        return_value=base_flags,
    ):
        off = build_catalog_payload()
    lol_off = next(c for c in off["competitions"] if c["id"] == "lol")
    assert lol_off["adapter_likely"] is False

    with patch(
        "app.kernel.betting_catalog._kernel_flags",
        return_value={**base_flags, "phase_lol_enabled": True},
    ):
        on = build_catalog_payload()
    lol_on = next(c for c in on["competitions"] if c["id"] == "lol")
    assert lol_on["adapter_likely"] is True
