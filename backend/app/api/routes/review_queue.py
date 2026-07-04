from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.security import require_write_key
from app.memory import review_queue_store


router = APIRouter(prefix="/review-queue", tags=["Review Queue"])


class ReviewQueueActionRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=120)
    action: Literal[
        "confirm",
        "override",
        "request_more_evidence",
        "mark_bad_source",
        "mark_bad_resolution",
    ]
    note: str = ""


@router.get("")
async def list_review_queue(
    status: Literal["pending", "resolved"] = Query(default="pending"),
    trigger: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    if status == "pending":
        items = review_queue_store.list_pending(trigger=trigger)[:limit]
    else:
        items = review_queue_store.list_resolved(limit=limit)
        if trigger is not None:
            items = [item for item in items if item.get("trigger") == trigger]
    return {"items": items, "count": len(items), "status": status}


@router.get("/{item_id}")
async def get_review_queue_item(item_id: str) -> dict[str, Any]:
    item = review_queue_store.get_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Review item not found")
    return {"item": item}


@router.get("/{item_id}/audit")
async def get_review_queue_audit(item_id: str) -> dict[str, Any]:
    item = review_queue_store.get_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Review item not found")
    audit = review_queue_store.get_audit_log(item_id=item_id)
    return {"audit": audit, "count": len(audit), "item_id": item_id}


@router.post("/{item_id}/action")
async def take_review_queue_action(
    item_id: str,
    body: ReviewQueueActionRequest,
    _auth: None = Depends(require_write_key),
) -> dict[str, Any]:
    try:
        review_queue_store.take_action(
            item_id=item_id,
            reviewer=body.reviewer,
            action=body.action,
            note=body.note,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Review item not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    item = review_queue_store.get_item(item_id)
    return {"item": item}
