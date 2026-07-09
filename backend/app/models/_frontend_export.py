"""Allowlist of Pydantic models exported to the frontend TypeScript types.

This module is the single source of truth for which backend models get
generated as TypeScript types. ``generate_types.py`` runs ``pydantic2ts``
on this module; ``pydantic2ts`` auto-discovers all BaseModel subclasses
defined or imported here (via ``inspect.getmembers``), so only the models
imported below appear in ``generated-types.ts``.

Nested models (e.g., ``DecisionQuality`` referenced by ``EventRecord``) are
automatically included via field references — they do not need to be
imported here explicitly.

Do NOT import models that should not leak to the frontend:
- FlexibleResponse (base class, no fields)
- MarketModel / NewsModel (no frontend consumer)
- world_cup_prediction SQLAlchemy ORM (not Pydantic)
"""
from app.models.event import (
    AutoResolveResponse,
    CategoryCountsResponse,
    DecisionTimelineResponse,
    EventAnalysisRequest,
    EventDiscoveryResponse,
    EventHistoryResponse,
    EventListResponse,
    EventMoversResponse,
    EventRecord,
    EventStoreEntry,
    FreshEdgesResponse,
    OpenDecisionsResponse,
    PendingLinksResponse,
    RecentPredictionsResponse,
    SimilarEventsResponse,
)

__all__ = [
    "AutoResolveResponse",
    "CategoryCountsResponse",
    "DecisionTimelineResponse",
    "EventAnalysisRequest",
    "EventDiscoveryResponse",
    "EventHistoryResponse",
    "EventListResponse",
    "EventMoversResponse",
    "EventRecord",
    "EventStoreEntry",
    "FreshEdgesResponse",
    "OpenDecisionsResponse",
    "PendingLinksResponse",
    "RecentPredictionsResponse",
    "SimilarEventsResponse",
]
