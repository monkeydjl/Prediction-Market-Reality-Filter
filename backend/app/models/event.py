from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventAnalysisRequest(BaseModel):
    # Length caps bound LLM token cost / log size / memory on the analyze path,
    # which is reachable by any authenticated caller. Generous but not unbounded.
    event_question: str = Field(min_length=1, max_length=2000)
    baseline_probability: float = Field(default=50.0, ge=0.0, le=100.0)
    news_context: str | None = Field(default=None, max_length=20000)
    volume: float | None = Field(default=None, ge=0.0)
    liquidity: float | None = Field(default=None, ge=0.0)


class Probability(BaseModel):
    baseline: float
    estimated: float
    change: float
    direction: str


class Credibility(BaseModel):
    score: int
    level: str
    confidence: float
    news_quality: float
    evidence_strength: float
    source_count: int


class Impact(BaseModel):
    score: int
    level: str
    drivers: list[str]


class Risk(BaseModel):
    level: str
    flags: list[Any] = []


class EvidenceProfile(BaseModel):
    direction: str
    strength: float
    conflict: float
    freshness: float
    resolution_relevance: float


class IntelligenceReport(BaseModel):
    headline: str
    why_it_matters: str
    probability_assessment: str
    recommended_action: str


class ActionableRecommendation(BaseModel):
    """Structured actionable conclusion for an event (Stage 3).

    Surfaces the already-computed legacy signal as an event-vocabulary
    recommendation: direction (YES/NO/AVOID/WAIT) + confidence + suggested
    allocation. None when evidence quality is insufficient or the feature is
    disabled. calibration_status distinguishes calibrated (segment has enough
    resolved samples) from uncalibrated_provisional (dormant but edge is large).
    """

    direction: str  # YES | NO | AVOID | WAIT
    confidence: str  # high | medium | low
    suggested_allocation_pct: float  # 0-25, from legacy position_size * 100
    edge: float  # expected_edge in percentage points
    risk_level: str  # low | medium | high
    rationale: str
    calibration_status: str  # calibrated | uncalibrated_provisional


class EventSource(BaseModel):
    """Where an event came from. Permissive: source adapters add their own
    fields (platform, source_id, liquidity, volume, ...)."""

    model_config = ConfigDict(extra="allow")

    type: str = "unknown"


class Outcome(BaseModel):
    """The settled result of an event. None on EventRecord until the event is
    resolved.

    Probability-shaped (0-100 actual_outcome) so partial / probabilistic
    resolutions are representable, not just binary yes/no. `confidence` records
    how certain the resolution itself is (a court ruling is more certain than a
    noisy source); `source` records who/what resolved it.
    """

    status: str            # "resolved" (single state today; room to grow)
    actual_outcome: float  # 0-100: 0=NO, 100=YES, middle=partial/probabilistic
    confidence: float      # 0-1: how certain this resolution is
    resolved_at: str       # ISO 8601 timestamp
    source: str            # "manual", "auto_market", ...
    notes: str = ""        # optional human-readable explanation


class Calibration(BaseModel):
    """How accurate the event's probability estimate was vs the settled
    outcome. Computed once at resolve time (a snapshot); None on EventRecord
    until the event is resolved.

    Scored on the latest probability estimate against outcome.actual_outcome.
    Brier score is 0 (perfect) / 0.25 (random) / 1 (fully wrong); skill_score
    rescales it so >0 beats random. The trajectory_* fields carry context so a
    reviewer can tell whether the score reflects a long, stable tracking
    history or a single shaky observation.
    """

    brier_score: float                # 0=perfect, 0.25=random, 1=fully wrong
    skill_score: float                # 1 - brier/0.25; >0 beats random
    grade: str                        # EXCELLENT/GOOD/ACCEPTABLE/POOR/RANDOM_LEVEL
    estimated_probability: float      # the latest estimate that was scored (0-100)
    actual_outcome: float             # the outcome it was scored against (0-100)
    trajectory_observations: int      # how many probability snapshots the event had
    trajectory_span_hours: float | None = None  # how long the event was tracked


class EventSemantics(BaseModel):
    """Structured meaning of an event: how it resolves, when, and who/what it
    is about. Populated by the analysis engine; None on EventRecord until then.

    resolution_criteria is the specific condition that determines YES vs NO
    (as the LLM understands it from the question + evidence). time_horizon is
    the deadline / time window in free text. entities are the key subjects
    (people, orgs, assets, etc.) in raw, cleaned form - no canonicalization
    (alias merging) is applied. Used by historical matching as an additional
    similarity signal alongside title tokens.
    """

    resolution_criteria: str = ""     # how YES/NO is determined
    time_horizon: str = ""            # e.g. "by end of 2026", "Q3 2026"
    entities: list[str] = []          # key subjects, raw cleaned form


class EvidenceItem(BaseModel):
    """One piece of source evidence collected for an event (a news article or
    official/regulatory release that passed the news filter).

    The backend scores evidence direction only in aggregate (see
    EvidenceProfile), so an item carries quality / relevance rather than a
    per-item supports/contradicts stance. ``kind`` groups items in the UI:
    "official" (gov / regulator / economic feeds) vs "news" (general press).
    """

    model_config = ConfigDict(extra="allow")

    kind: str = "news"          # official | news
    source: str = ""
    title: str = ""
    summary: str = ""
    url: str = ""
    published: str = ""
    quality: float = 0.0
    relevance: float = 0.0


class EvidenceBreakdownItem(BaseModel):
    """One article's contribution to the event-level YES/NO evidence (Stage:
    evidence decomposition).

    Produced by aggregating the per-article fields emitted by the
    ``analyze_sentiment`` LLM call. Unlike ``EvidenceItem`` (which carries
    quality/relevance for the UI), this model carries the LLM's directional
    judgment (support/oppose) and is purely an audit/explanation layer: it
    MUST NOT feed back into ``evidence_profile`` or ``ai_probability``.
    """

    source: str = ""
    title: str = ""
    direction: str  # support | oppose
    strength: float = 0.0  # 0-1
    credibility: float = 0.0  # 0-1
    rationale_zh: str = ""


class Tracking(BaseModel):
    """Human tracking decision for an event. Defaults are seeded at analysis
    time; a user's explicit choice is preserved across re-scans by the store.

    status: tracking (持续跟踪) | watching (观察中) | archived (已归档).
    priority: high | medium | low.
    """

    status: str = "watching"
    priority: str = "medium"


class MarketLink(BaseModel):
    """Binds an event to a specific prediction-market contract - the join the
    feedback loop depends on (see docs/user/DATABASE_DESIGN.md).

    Persisted in the SQLite loop store. A link is only eligible to be scored
    when ``verified`` is True; an unverified link (e.g. a fuzzy question match)
    is recorded but fail-closed, so an event is never scored against the wrong
    market's outcome. ``market_question`` and ``resolution_criteria`` are stored
    so a later resolution can be checked to mean the same thing we predicted.
    """

    event_id: str
    market_name: str = ""              # source platform / feed name
    contract_id: str = ""              # market/contract id on that platform
    market_question: str = ""          # the market's own question text
    resolution_criteria: str = ""      # how the market resolves YES/NO
    link_method: str = "auto"          # auto | manual
    link_confidence: float = 0.0       # 0..1 (auto = match score; manual = 1.0)
    verified: bool = False             # only verified links are scored
    linked_at: str = ""                # ISO 8601 timestamp


class Prediction(BaseModel):
    """A point-in-time committed prediction: the AI probability vs the market
    price for an event, frozen at decision time, with the raw edge between them.

    One event, one prediction (commitment, not trajectory): exactly one row per
    event, frozen at first sight and never overwritten or re-versioned - a
    re-scan is a no-op (UNIQUE(event_id) + ON CONFLICT DO NOTHING). Probability
    and edge trajectories live in the audit log, not here. Terminal statuses:
    `scored` (an act row resolved -> in calibration), `observed` (a watch/skip row
    resolved -> outcome+Brier kept but excluded from calibration), and `voided`
    (a non-genuine resolution: identity conflict / void market - closed without
    scoring). `open` is the live committed prediction before resolution.

    raw_edge = ai_probability - market_probability (both 0-100). adjusted_edge is
    raw_edge trust-weighted by conditional calibration and liquidity (M2). The
    diagnosis fields (liquidity_factor, qualified, segment_n, segment_skill) are
    the inputs behind the verdict, frozen at decision time so a decision report
    can explain WHY without recomputing. ``decision`` is act / watch / skip
    (legacy default "tracked" predates the M2 Decision Gate).
    """

    event_id: str
    contract_id: str = ""
    platform: str = ""
    base_rate_category: str = "unknown"   # segment key for conditional calibration
    ai_probability: float        # 0-100, frozen estimated
    market_probability: float    # 0-100, frozen market-implied price
    raw_edge: float              # ai_probability - market_probability
    trust: float | None = None       # M2: 0-1 calibration trust in this divergence
    adjusted_edge: float | None = None  # M2: raw_edge * trust * liquidity_factor
    liquidity: float = 0.0
    volume: float = 0.0
    decision: str = "tracked"    # M2: act | watch | skip (tracked = pre-M2 default)
    # Diagnosis explanation, frozen at decision time (why this verdict, not
    # recomputed at read): the liquidity weight, whether the segment was
    # qualified (>= min_samples), its sample count, and its skill (None=dormant).
    liquidity_factor: float | None = None
    qualified: bool | None = None
    segment_n: int | None = None
    segment_min_samples: int | None = None
    segment_skill: float | None = None
    created_at: str = ""         # ISO 8601
    status: str = "open"         # open | scored
    actual_outcome: float | None = None   # 0-100, filled at resolve
    brier_score: float | None = None       # filled at resolve
    resolved_at: str | None = None


class EventRecord(BaseModel):
    """Native typed shape of the event intelligence record produced by
    ``event_intelligence_service.build_event_record()``.

    Used as a validation gate in the event store and as the schema for event
    read endpoints. ``extra='allow'`` keeps forward-compatible fields (such as
    ``news_filter``) without breaking when build_event_record adds output.
    """

    model_config = ConfigDict(extra="allow")

    event_id: str
    event_title: str
    event_title_zh: str = ""
    event_summary: str
    probability: Probability
    credibility: Credibility
    impact: Impact
    risk: Risk
    evidence: EvidenceProfile
    source: EventSource
    value_score: int
    intelligence_report: IntelligenceReport
    evidence_items: list[EvidenceItem] = []
    tracking: Tracking | None = None
    legacy_analysis: dict[str, Any] = {}
    outcome: Outcome | None = None
    calibration: Calibration | None = None
    semantics: EventSemantics | None = None
    actionable_recommendation: ActionableRecommendation | None = None
    evidence_breakdown: list[EvidenceBreakdownItem] = Field(default_factory=list)


class FlexibleResponse(BaseModel):
    """Permissive response base for endpoints whose nested payloads are dynamic."""

    model_config = ConfigDict(extra="allow")


class EventStoreEntry(FlexibleResponse):
    event_id: str = ""
    first_seen: str = ""
    last_updated: str = ""
    record: dict[str, Any] = Field(default_factory=dict)


class EventDiscoveryResponse(FlexibleResponse):
    platform: str = ""
    source: str = ""
    count: int = 0
    events: list[dict[str, Any]] = Field(default_factory=list)


class EventListResponse(FlexibleResponse):
    count: int = 0
    total: int = 0
    limit: int = 0
    offset: int = 0
    events: list[EventStoreEntry] = Field(default_factory=list)


class EventMoversResponse(FlexibleResponse):
    count: int = 0
    movers: list[dict[str, Any]] = Field(default_factory=list)


class EventHistoryResponse(FlexibleResponse):
    event_id: str = ""
    count: int = 0
    trend: dict[str, Any] = Field(default_factory=dict)
    edge: dict[str, Any] = Field(default_factory=dict)
    history: list[dict[str, Any]] = Field(default_factory=list)


class AutoResolveResponse(FlexibleResponse):
    status: str = ""
    dry_run: bool = False
    resolved_count: int = 0
    pending_count: int = 0
    invalid_count: int = 0
    checked_count: int = 0
    unresolved_events: int = 0
    matches: list[dict[str, Any]] = Field(default_factory=list)
    by_source: dict[str, int] = Field(default_factory=dict)


class PendingLinksResponse(FlexibleResponse):
    pending: list[dict[str, Any]] = Field(default_factory=list)


class RecentPredictionsResponse(FlexibleResponse):
    predictions: list[dict[str, Any]] = Field(default_factory=list)


class OpenDecisionsResponse(FlexibleResponse):
    count: int = 0
    decisions: list[dict[str, Any]] = Field(default_factory=list)


class FreshEdgesResponse(FlexibleResponse):
    count: int = 0
    classification: str | None = None
    edges: list[dict[str, Any]] = Field(default_factory=list)


class SimilarEventsResponse(FlexibleResponse):
    event_id: str = ""
    count: int = 0
    similar: list[dict[str, Any]] = Field(default_factory=list)
