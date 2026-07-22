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
    tool_ids = {t["id"] for t in data["tools"]}
    assert "edges" in tool_ids
    es = next(c for c in data["competitions"] if c["id"] == "esports")
    assert es["status"] == "coming_soon"
    assert es["track"] == "placeholder"


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
        },
    ):
        body = build_status_payload()
    assert body["kernel_ready"] is False
    assert body["registered_prefixes"] == []
    assert body["kernel_error"] is None
