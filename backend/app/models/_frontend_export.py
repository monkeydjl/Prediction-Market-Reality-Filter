"""Allowlist of Pydantic models exported to the frontend TypeScript types.

This module is the single source of truth for which backend models get
generated as TypeScript types. ``generate_types.py`` runs ``pydantic2ts``
on this module; ``pydantic2ts`` auto-discovers all BaseModel subclasses
defined or imported here (via ``inspect.getmembers``), so only the models
imported below appear in ``generated-types.ts``.

Roots vs. nested, and why the distinction matters
-------------------------------------------------
A model can reach the generated file in two ways:

* **Root** -- its name is imported into *this* module's namespace. ``pydantic2ts``
  collects exactly those (``inspect.getmembers``; ``__all__`` is documentation
  only) and temporarily sets ``extra='forbid'`` on each, which emits
  ``additionalProperties: false``. ``json2ts`` then writes a *closed* interface.
* **Nested** -- reached only by a ``$ref`` from some root's field. No
  ``additionalProperties`` key is emitted, so ``json2ts`` appends a
  ``[k: string]: unknown`` tail and the interface comes out **looser than the
  hand-written one it replaces** (audit sections 53.49, 53.50).

**Rule: list a model here whenever its frontend interface must be closed.**
Those are the models whose own config does *not* opt into ``extra="allow"`` --
i.e. every plain ``BaseModel`` that the frontend can reach. List them even when
they are also referenced as a field of a root; being referenced is not enough.

Models that *do* set ``extra="allow"`` render open **on purpose** and must NOT
be relied on for closure:

* ``FlexibleResponse`` subclasses (``EventListResponse`` and friends) -- the
  base class ships its own payloads with undeclared keys; the tail is the point.
* The overlay models that declare ``ConfigDict(extra="allow")`` themselves
  (``EventRecord``, ``ConfidenceBreakdown``, ``CrossValidation``,
  ``DecisionQuality``, ``EventSource``, ``EvidenceItem``, ``LLMTelemetry``,
  ``MarketQuality``, ``SourceReliability``). Closing them would misrepresent
  their contract, so the generator deliberately skips the override for them.

Do NOT import models that should not leak to the frontend:
- FlexibleResponse (base class, no fields)
- MarketModel / NewsModel (no frontend consumer)
- world_cup_prediction SQLAlchemy ORM (not Pydantic)
"""
from app.models.event import (
    ActionableRecommendation,
    AutoResolveResponse,
    Calibration,
    CategoryCountsResponse,
    Credibility,
    DailyDigestHeadline,
    DailyDigestMover,
    DailyDigestResponse,
    DailyDigestUnchanged,
    DecisionEvidenceItem,
    DecisionTimelineDiff,
    DecisionTimelineResponse,
    DecisionTimelineSnapshot,
    EventAnalysisRequest,
    EventDiscoveryResponse,
    EventHistoryResponse,
    EventListResponse,
    EventMoversResponse,
    EventRecord,
    EventSemantics,
    EventStoreEntry,
    EvidenceBreakdownItem,
    EvidenceProfile,
    FreshEdgesResponse,
    Impact,
    IntelligenceReport,
    MarketSnapshotPoint,
    MarketSnapshotSeries,
    MarketSnapshotsResponse,
    OpenDecisionsResponse,
    Outcome,
    PendingLinksResponse,
    Probability,
    RecentPredictionsResponse,
    Risk,
    SimilarEventsResponse,
    SimTrade,
    SimTradeListResponse,
    Tracking,
    TradeStatBucket,
    TradeStats,
)

__all__ = [
    "ActionableRecommendation",
    "AutoResolveResponse",
    "Calibration",
    "CategoryCountsResponse",
    "Credibility",
    "DailyDigestHeadline",
    "DailyDigestMover",
    "DailyDigestResponse",
    "DailyDigestUnchanged",
    "DecisionEvidenceItem",
    "DecisionTimelineDiff",
    "DecisionTimelineResponse",
    "DecisionTimelineSnapshot",
    "EventAnalysisRequest",
    "EventDiscoveryResponse",
    "EventHistoryResponse",
    "EventListResponse",
    "EventMoversResponse",
    "EventRecord",
    "EventSemantics",
    "EventStoreEntry",
    "EvidenceBreakdownItem",
    "EvidenceProfile",
    "FreshEdgesResponse",
    "Impact",
    "IntelligenceReport",
    "MarketSnapshotPoint",
    "MarketSnapshotSeries",
    "MarketSnapshotsResponse",
    "OpenDecisionsResponse",
    "Outcome",
    "PendingLinksResponse",
    "Probability",
    "RecentPredictionsResponse",
    "Risk",
    "SimilarEventsResponse",
    "SimTrade",
    "SimTradeListResponse",
    "Tracking",
    "TradeStatBucket",
    "TradeStats",
]
