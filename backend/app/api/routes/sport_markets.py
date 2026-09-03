"""Sport market bridge API routes.

When PHASE7_SPORT_MARKET_BRIDGE_ENABLED is false, all routes return 503.
/latest returns only verified links (fail-closed). /verify is the only write
operation in this sub-project.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.security import require_write_key
from app.core import config

if TYPE_CHECKING:
    # Kernel imports stay inside the helpers at runtime (lazy); these are here
    # only so the annotations resolve.
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    from app.kernel.sport_market_link_store import SportMarketLinkStore

router = APIRouter(prefix="/sport-markets", tags=["Sport Markets"])


def _ensure_enabled() -> None:
    if not config.settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Sport market bridge is disabled. Set PHASE7_SPORT_MARKET_BRIDGE_ENABLED=true to enable.",
        )


def _link_store() -> SportMarketLinkStore:
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    return SportMarketLinkStore()


def _snap_store() -> MarketSnapshotStore:
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
            links = [link for link in links if link["source"] == source]
        if verified is not None:
            links = [link for link in links if link["verified"] == verified]
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
def verify_link(
    match_id: str,
    contract_id: str,
    body: VerifyBody,
    _auth: None = Depends(require_write_key),
) -> dict[str, Any]:
    _ensure_enabled()
    store = _link_store()
    links = store.get_links(match_id=match_id)
    target = next((link for link in links if link["contract_id"] == contract_id), None)
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


@router.get("/links/{link_id}/audit")
def link_price_audit(link_id: int) -> dict[str, Any]:
    """Price-path audit for a linked market (P1-V1)."""
    _ensure_enabled()
    store = _snap_store()
    summary = store.audit_summary(link_id=link_id)
    # attach link meta when the row exists. A failed lookup is not "no meta":
    # this door answered 200 with match_id/source/verified silently absent while
    # /matches/{match_id}/audit reported link_count 0 for the same links, so the
    # two audit doors disagreed about one fact.
    link = _link_store().get_link(link_id=link_id)
    if link:
        summary["match_id"] = link.get("match_id")
        summary["source"] = link.get("source")
        summary["market_id"] = link.get("market_id")
        summary["verified"] = link.get("verified")
    return summary


@router.get("/matches/{match_id}/audit")
def match_price_audit(match_id: str) -> dict[str, Any]:
    """Aggregate price-path audits for all links of a match (P1-V1)."""
    _ensure_enabled()
    links = _link_store().get_links(match_id=match_id)
    snap = _snap_store()
    audits = []
    for link in links:
        lid = link.get("id") or link.get("link_id")
        if lid is None:
            continue
        a = snap.audit_summary(link_id=int(lid))
        a["source"] = link.get("source")
        a["market_id"] = link.get("market_id")
        a["verified"] = link.get("verified")
        a["mapped_outcome"] = link.get("mapped_outcome")
        audits.append(a)
    return {
        "match_id": match_id,
        "link_count": len(links),
        "audits": audits,
    }


@router.post("/pending/auto-verify")
def auto_verify_pending(
    dry_run: bool = Query(False, description="If true, only report candidates"),
    min_confidence: float | None = Query(
        None, description="Override auto-verify confidence threshold",
    ),
    _auth: None = Depends(require_write_key),
) -> dict[str, Any]:
    """Promote high-confidence pending links to verified (P1-V2).

    Gated by PHASE7_SPORT_MARKET_LINK_AUTO_VERIFY_ENABLED unless dry_run=true.
    """
    _ensure_enabled()
    from app.core import config

    thr = (
        float(min_confidence)
        if min_confidence is not None
        else float(config.settings.PHASE7_SPORT_MARKET_LINK_AUTO_VERIFY_THRESHOLD)
    )
    if not dry_run and not config.settings.PHASE7_SPORT_MARKET_LINK_AUTO_VERIFY_ENABLED:
        # still allow dry_run to inspect queue
        result = _link_store().auto_verify_high_confidence(
            min_confidence=thr, dry_run=True,
        )
        result["enabled"] = False
        result["message"] = (
            "AUTO_VERIFY disabled; set PHASE7_SPORT_MARKET_LINK_AUTO_VERIFY_ENABLED=true"
        )
        return result

    result = _link_store().auto_verify_high_confidence(
        min_confidence=thr, dry_run=dry_run,
    )
    result["enabled"] = True
    return result

