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
    from app.api.routes.predictions import normalize_competition_code

    assert normalize_competition_code("PL") == "epl"
    assert normalize_competition_code("serie-a") == "serie_a"
    assert normalize_competition_code("wc") == "world_cup"
    assert normalize_competition_code(None) is None
    assert normalize_competition_code("  ") is None
