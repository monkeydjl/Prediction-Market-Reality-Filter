"""Review queue HTTP routes.

Exposes the ``review_queue_store`` (items + append-only audit log) that the
detectors and orchestrators already write to. Without these routes the queue
was write-only: candidates accumulated in SQLite with no way for a reviewer to
see or resolve them outside the CLI.

Authorization: reads are open (same posture as ``/api/quality-metrics``, which
also exposes operator-grade aggregates). The action endpoint writes to the
audit log and therefore requires a write key.

Store calls are synchronous SQLite, so every one is offloaded with
``asyncio.to_thread`` rather than run on the event loop.
"""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.security import require_write_key
from app.memory import review_queue_store

router = APIRouter(prefix="/review-queue", tags=["Review Queue"])


class ReviewQueueActionRequest(BaseModel):
    """One reviewer action. ``action`` mirrors the store's locked vocabulary.

    Declaring the literals here makes an invalid action a 422 from FastAPI
    instead of a 400 raised out of the store, and puts the allowed set in the
    OpenAPI schema.
    """

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
    """List queue items by status, newest first."""
    if status == "pending":
        items = await asyncio.to_thread(
            review_queue_store.list_pending, trigger=trigger
        )
        items = items[:limit]
    else:
        items = await asyncio.to_thread(
            review_queue_store.list_resolved, limit=limit
        )
        if trigger is not None:
            items = [item for item in items if item.get("trigger") == trigger]
    return {"items": items, "count": len(items), "status": status}


@router.get("/{item_id}")
async def get_review_queue_item(item_id: str) -> dict[str, Any]:
    item = await asyncio.to_thread(review_queue_store.get_item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Review item not found")
    return {"item": item}


@router.get("/{item_id}/audit")
async def get_review_queue_audit(item_id: str) -> dict[str, Any]:
    item = await asyncio.to_thread(review_queue_store.get_item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Review item not found")
    audit = await asyncio.to_thread(
        review_queue_store.get_audit_log, item_id=item_id
    )
    return {"audit": audit, "count": len(audit), "item_id": item_id}


@router.post("/{item_id}/action")
async def take_review_queue_action(
    item_id: str,
    body: ReviewQueueActionRequest,
    _auth: None = Depends(require_write_key),
) -> dict[str, Any]:
    """Resolve an item and append the action to the audit log."""
    try:
        await asyncio.to_thread(
            review_queue_store.take_action,
            item_id=item_id,
            reviewer=body.reviewer,
            action=body.action,
            note=body.note,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Review item not found") from None
    except ValueError as exc:
        # Banned vocabulary in the note — the store is the authority on this.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    item = await asyncio.to_thread(review_queue_store.get_item, item_id)
    return {"item": item}
