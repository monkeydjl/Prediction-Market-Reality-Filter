"""Sport market bridge API routes.

When PHASE7_SPORT_MARKET_BRIDGE_ENABLED is false, all routes return 503.
/latest returns only verified links (fail-closed). /verify is the only write
operation in this sub-project.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core import config

router = APIRouter(prefix="/sport-markets", tags=["Sport Markets"])


def _ensure_enabled() -> None:
    if not config.settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Sport market bridge is disabled. Set PHASE7_SPORT_MARKET_BRIDGE_ENABLED=true to enable.",
        )


def _link_store():
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    return SportMarketLinkStore()


def _snap_store():
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    return MarketSnapshotStore()


@router.get("/links")
def list_links(
    match_id: str | None = Query(None),
    source: str | None = Query(None),
    verified: bool | None = Query(None),
) -> dict[str, Any]:
    _ensure_enabled()
    store = _link_store()
    if match_id:
        links = store.get_links(match_id=match_id)
        if source is not None:
            links = [l for l in links if l["source"] == source]
        if verified is not None:
            links = [l for l in links if l["verified"] == verified]
    else:
        links = store.list_links(source=source, verified=verified)
    return {"items": links, "total": len(links)}


@router.get("/links/{match_id}")
def get_links(match_id: str) -> dict[str, Any]:
    _ensure_enabled()
    store = _link_store()
    links = store.get_links(match_id=match_id)
    return {"match_id": match_id, "items": links, "total": len(links)}


@router.get("/links/{match_id}/latest")
def get_latest_links(match_id: str) -> dict[str, Any]:
    """Fail-closed: only verified=True links, joined with newest snapshot."""
    _ensure_enabled()
    store = _link_store()
    snaps = _snap_store()
    verified = store.get_verified_links(match_id=match_id)
    items = []
    for link in verified:
        latest = snaps.get_latest_snapshot(link_id=link["id"])
        items.append({**link, "latest_snapshot": latest})
    return {"match_id": match_id, "items": items, "total": len(items)}


@router.get("/pending")
def list_pending() -> dict[str, Any]:
    _ensure_enabled()
    store = _link_store()
    pending = store.get_pending_links()
    return {"items": pending, "total": len(pending)}


class VerifyBody(BaseModel):
    verified: bool
    note: str | None = None


@router.post("/links/{match_id}/{contract_id}/verify")
def verify_link(match_id: str, contract_id: str, body: VerifyBody) -> dict[str, Any]:
    _ensure_enabled()
    store = _link_store()
    links = store.get_links(match_id=match_id)
    target = next((l for l in links if l["contract_id"] == contract_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Link not found")
    ok = store.set_verified(link_id=target["id"], verified=body.verified)
    if not ok:
        raise HTTPException(status_code=500, detail="Verify failed")
    return {"ok": True, "link_id": target["id"], "verified": body.verified}


@router.get("/snapshots/{match_id}")
def get_snapshots(match_id: str) -> dict[str, Any]:
    _ensure_enabled()
    store = _link_store()
    snaps = _snap_store()
    links = store.get_links(match_id=match_id)
    series = []
    for link in links:
        rows = snaps.get_snapshots(link_id=link["id"])
        series.append({
            "contract_id": link["contract_id"],
            "outcome_label": link["outcome_label"],
            "mapped_outcome": link["mapped_outcome"],
            "snapshots": rows,
        })
    return {"match_id": match_id, "series": series}
