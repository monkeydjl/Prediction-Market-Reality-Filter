"""Allowlist of Pydantic models exported to the frontend TypeScript types.

This module is the single source of truth for which backend models get
generated as TypeScript types. ``generate_types.py`` runs ``pydantic2ts``
on this module; ``pydantic2ts`` auto-discovers all BaseModel subclasses
defined or imported here (via ``inspect.getmembers``), so only the models
imported below appear in ``generated-types.ts``.

Nested models (e.g., ``DecisionQuality`` referenced by ``EventRecord``) are
automatically included via field references — they do not need to be
imported here explicitly.

One exception: a nested model that must render as a *closed* interface has to
be listed anyway. ``MarketSnapshotPoint`` / ``MarketSnapshotSeries`` are here
for exactly that reason. A model reached only by field reference gets no
``additionalProperties: false`` in the JSON schema, so ``json2ts`` appends a
``[k: string]: unknown`` tail and the generated interface ends up looser than
the hand-written one it replaced. Listing the model makes it a root model,
which does emit ``additionalProperties: false`` (audit section 53.49).

Do NOT import models that should not leak to the frontend:
- FlexibleResponse (base class, no fields)
- MarketModel / NewsModel (no frontend consumer)
- world_cup_prediction SQLAlchemy ORM (not Pydantic)
"""
from app.models.event import (
    AutoResolveResponse,
    CategoryCountsResponse,
    DailyDigestResponse,
    DecisionTimelineResponse,
    EventAnalysisRequest,
    EventDiscoveryResponse,
    EventHistoryResponse,
    EventListResponse,
    EventMoversResponse,
    EventRecord,
    EventStoreEntry,
    FreshEdgesResponse,
    MarketSnapshotPoint,
    MarketSnapshotSeries,
    MarketSnapshotsResponse,
    OpenDecisionsResponse,
    PendingLinksResponse,
    RecentPredictionsResponse,
    SimilarEventsResponse,
    SimTrade,
    SimTradeListResponse,
    TradeStatBucket,
    TradeStats,
)

__all__ = [
    "AutoResolveResponse",
    "CategoryCountsResponse",
    "DailyDigestResponse",
    "DecisionTimelineResponse",
    "EventAnalysisRequest",
    "EventDiscoveryResponse",
    "EventHistoryResponse",
    "EventListResponse",
    "EventMoversResponse",
    "EventRecord",
    "EventStoreEntry",
    "FreshEdgesResponse",
    "MarketSnapshotPoint",
    "MarketSnapshotSeries",
    "MarketSnapshotsResponse",
    "OpenDecisionsResponse",
    "PendingLinksResponse",
    "RecentPredictionsResponse",
    "SimilarEventsResponse",
    "SimTrade",
    "SimTradeListResponse",
    "TradeStatBucket",
    "TradeStats",
]
