from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EventAnalysisRequest(BaseModel):
    # Length caps bound LLM token cost / log size / memory on the analyze path,
    # which is reachable by any authenticated caller. Generous but not unbounded.
    event_question: str = Field(min_length=1, max_length=2000)
    # Percentage points, 0-100 -- the same scale `market_probability` uses
    # everywhere else in the pipeline, not a 0-1 fraction. State it because the
    # bound alone does not: `ge=0.0, le=100.0` accepts 0.2, which reads as a
    # legitimate 0.2% and is also what a caller who meant 20% sends by mistake.
    # One such call is in the store (a 0.2 against a 30.17 model estimate, a
    # 29.97pp phantom edge and a provisional_act). Nothing here can catch it:
    # this is the only probability in the request, with no market quote to
    # compare against, so 0.2 cannot be told from 0.2%. Documenting the scale is
    # the part that is actually fixable.
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
    fields beyond the declared ones below (via ``extra="allow"``)."""

    model_config = ConfigDict(extra="allow")

    type: str = "unknown"
    platform: str = ""
    event_type: str = ""
    source_id: str = ""
    baseline_probability: float | None = None
    volume: float | None = None
    liquidity: float | None = None
    url: str = ""
    category: str = ""
    tournament: str = ""
    entities: list[str] = []


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

    Brier score is 0 (perfect) / 0.25 (random) / 1 (fully wrong); skill_score
    rescales it so >0 beats random. The trajectory_* fields carry context so a
    reviewer can tell whether the score reflects a long, stable tracking
    history or a single shaky observation.

    Which estimate was scored depends on whether the event has a market, so
    `estimate_basis` names it: a market-derived event is scored on its latest
    trajectory point (the trajectory tracks a live price), while a market-less
    event is scored on its first-sight estimate, because nothing froze its
    verdict (`freeze_prediction` is market-gated). The number alone cannot say
    which, since the two can coincide.
    """

    brier_score: float                # 0=perfect, 0.25=random, 1=fully wrong
    skill_score: float                # 1 - brier/0.25; >0 beats random
    grade: str                        # EXCELLENT/GOOD/ACCEPTABLE/POOR/RANDOM_LEVEL
    estimated_probability: float      # the estimate that was scored (0-100)
    actual_outcome: float             # the outcome it was scored against (0-100)
    trajectory_observations: int      # how many probability snapshots the event had
    trajectory_span_hours: float | None = None  # how long the event was tracked
    # Which estimate the score was computed from: trajectory_latest (market
    # events), first_sight (market-less), or a baseline_* fallback naming why
    # no committed estimate was available. Defaulted so calibration rows
    # written before this field existed still validate.
    estimate_basis: str = "trajectory_latest"


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


class CrossValidation(BaseModel):
    """Cross-validation of the primary LLM probability by a second model.

    Written by ``event_intelligence_service.analyze_event_complete()`` when
    a second model is configured. None on EventRecord when cross-validation
    is disabled or the second model is unavailable.

    ``primary_probability`` is the original estimate; ``probability`` is the
    second model's estimate; ``divergence`` is their absolute difference;
    ``agreement`` is a derived label (high/medium/low).

    Closed contract: ``cross_validation_service.cross_validate`` writes exactly
    these five keys, so nothing here needs to survive an undeclared one.
    """

    model: str | None = None
    probability: float | None = None
    primary_probability: float | None = None
    divergence: float | None = None
    agreement: str | None = None


class ConfidenceBreakdown(BaseModel):
    """Diagnostics explaining how source structure affected confidence.

    Emitted by ``ai_analysis_service.analyze_market()`` as a read-only audit
    block. It helps frontend/reporting distinguish raw source quantity from
    stronger evidence structures such as independent or official sources.

    Closed contract: ``probability_engine_service.calculate_confidence_breakdown``
    returns exactly the nine declared keys. (The same-named
    ``kernel.engines.confidence.confidence_breakdown`` is a *different* object --
    the sports engines' ``SportConfidencePanel`` diagnostics.)
    """

    source_count: int = 0
    independent_source_count: int = 0
    official_source_count: int = 0
    counterevidence_considered: bool = False
    news_quantity_score: float = 0.0
    source_structure_score: float = 0.0
    effective_source_score: float = 0.0
    source_structure_used: bool = False
    source_quality_reasons: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    """One piece of source evidence collected for an event (a news article or
    official/regulatory release that passed the news filter).

    The backend scores evidence direction only in aggregate (see
    EvidenceProfile), so an item carries quality / relevance rather than a
    per-item supports/contradicts stance. ``kind`` groups items in the UI:
    "official" (gov / regulator / economic feeds) vs "news" (general press).

    Closed contract: the only producer is
    ``event_intelligence_service.build_evidence_items``, which maps the raw
    article keys onto these declared ones (``description`` -> ``summary``,
    ``quality_score`` -> ``quality``, ``relevance_score`` -> ``relevance``).
    """

    kind: str = "news"          # official | news
    source: str = ""
    title: str = ""
    title_zh: str = ""
    summary: str = ""
    summary_zh: str = ""
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

    ``direction`` is typed as ``Literal["support", "oppose", "neutral"]`` so
    that invalid values (e.g., ``"YES"``, ``"LONG"``, ``"buy"``) raise
    ``ValidationError`` at model construction time, before they ever reach
    ``decision_quality_service``. This locks the two-vocabulary separation
    (article stance vs recommendation direction) at the model boundary.
    """

    source: str = ""
    title: str = ""
    direction: Literal["support", "oppose", "neutral"]
    strength: float = 0.0  # 0-1
    credibility: float = 0.0  # 0-1
    rationale_zh: str = ""


class DecisionEvidenceItem(BaseModel):
    """One piece of evidence selected by ``decision_quality_service`` as a
    top supporting or opposing driver for the current recommendation.

    Distinct from ``EvidenceBreakdownItem``: this is the *selected, ranked,
    capped* subset (top N by ``strength * credibility``) shown in the
    "why this direction" panel. ``EvidenceBreakdownItem`` is the full audit
    list. ``rationale_zh`` on this model is the same string the source
    ``EvidenceBreakdownItem`` carried.
    """

    source: str = ""
    title: str = ""
    strength: float = 0.0
    credibility: float = 0.0
    rationale_zh: str = ""


class DecisionQuality(BaseModel):
    """Phase 1 decision-quality overlay block.

    Pure audit/explanation layer produced by ``decision_quality_service``
    from ``actionable_recommendation`` + ``evidence_breakdown``. Reads its
    inputs one-way and produces overlay outputs (``displayed_direction``,
    ``downgrade_reason``, ``decision_rationale_zh``). MUST NOT feed back
    into ``ai_probability``, ``evidence_profile``,
    ``regression_to_market``, or ``actionable_recommendation``.

    ``raw_direction`` mirrors ``actionable_recommendation.direction`` at
    build time and is never mutated by the downgrade pipeline.
    ``displayed_direction`` starts equal to ``raw_direction`` and is the
    only field that may diverge (when a downgrade rule fires).
    ``downgraded`` is ``true`` iff ``displayed_direction != raw_direction``.

    When ``error`` is non-None, the build failed and the block is the
    fallback defined in the ``analyze_event`` integration contract.

    Closed contract: both the happy path (``build_decision_quality``) and the
    build-failure fallback block in ``event_intelligence_service`` write only
    the declared fields.
    """

    supporting_evidence: list[DecisionEvidenceItem] = []
    opposing_evidence: list[DecisionEvidenceItem] = []
    conflict_score: float = 0.0
    consensus_level: Literal["high", "medium", "low", "none"] = "none"
    decision_rationale_zh: str = ""
    reversal_triggers: list[str] = []
    downgrade_reason: str | None = None
    raw_direction: Literal["YES", "NO", "WAIT", "AVOID"] = "WAIT"
    displayed_direction: Literal["YES", "NO", "WAIT", "AVOID"] = "WAIT"
    downgraded: bool = False
    error: str | None = None


class MarketQuality(BaseModel):
    """Phase 2 market-quality overlay block.

    Produced by ``market_quality_service`` for events whose
    ``source.type == "prediction_market"`` (Polymarket, Kalshi). For other
    source types (``prediction_question``, ``open_web``, ``sports_event``,
    ``manual``), the block is omitted entirely — mirroring the
    ``freeze_prediction`` market-gated convention.

    ``raw_direction`` mirrors the direction passed in from the raw
    recommendation. ``suggested_direction`` starts equal to
    ``raw_direction``; it becomes ``WAIT`` when market quality fails a
    configured threshold. ``downgraded`` is ``true`` when
    ``suggested_direction != raw_direction``.

    ``applied_to_displayed_direction`` is set by the merge step when this
    block's ``suggested_direction`` is stricter than
    ``decision_quality.displayed_direction`` and therefore changes the
    final user-facing direction. Lets audits distinguish whether market
    quality changed the final direction or merely recorded a score.

    Closed contract: ``market_quality_service.build_market_quality`` and the
    build-failure fallback in ``event_intelligence_service`` write only the
    declared fields (``error`` is written by the fallback alone).
    """

    score: float = 0.0
    liquidity_score: float | None = None
    volume_score: float | None = None
    spread_penalty: float | None = None
    wide_spread_flag: bool = False
    thin_market_flag: bool = False
    stale_price_flag: bool | None = None  # None = unknown (no last_updated)
    downgrade_reason: str | None = None
    raw_direction: Literal["YES", "NO", "WAIT", "AVOID"] = "WAIT"
    suggested_direction: Literal["YES", "NO", "WAIT", "AVOID"] = "WAIT"
    downgraded: bool = False
    applied_to_displayed_direction: bool = False
    error: str | None = None


class SourceReliability(BaseModel):
    """Phase 4 source-reliability overlay block.

    Produced by ``source_reliability_service`` for events that have a
    non-empty ``evidence_breakdown`` (prediction_market, prediction_question,
    open_web). For events without evidence_breakdown (e.g., sports_event with
    match stats), the block is omitted entirely.

    Assesses the quality and diversity of news sources backing an event's
    evidence. Can downgrade YES/NO recommendations to WAIT when the source
    base is too thin or untrustworthy (low domain diversity, low trusted
    source ratio, too few sources, or overall score below threshold).

    ``raw_direction`` mirrors the direction passed in from
    ``actionable_recommendation.direction``. ``suggested_direction`` starts
    equal to ``raw_direction``; it becomes ``WAIT`` when source reliability
    fails a configured threshold. ``downgraded`` is ``true`` when
    ``suggested_direction != raw_direction``.

    ``applied_to_displayed_direction`` is set by the merge step when this
    block's ``suggested_direction`` is stricter than the current
    ``final_displayed_direction`` (after decision_quality and market_quality
    have already been merged) and therefore changes the final user-facing
    direction.

    Closed contract: ``source_reliability_service.build_source_reliability``
    and the build-failure fallback in ``event_intelligence_service`` write only
    the declared fields. (``source_breakdown`` items stay an open ``dict`` --
    that is a declared field, not an undeclared key.)
    """

    overall_score: float = 0.0
    source_count: int = 0
    domain_diversity: int = 0
    trusted_source_ratio: float = 0.0
    official_source_count: int = 0
    unknown_source_ratio: float = 0.0
    source_breakdown: list[dict[str, Any]] = []
    downgrade_reason: str | None = None
    raw_direction: Literal["YES", "NO", "WAIT", "AVOID"] = "WAIT"
    suggested_direction: Literal["YES", "NO", "WAIT", "AVOID"] = "WAIT"
    downgraded: bool = False
    applied_to_displayed_direction: bool = False
    error: str | None = None


class LLMTelemetry(BaseModel):
    """Phase 5 LLM telemetry overlay block.

    Produced by ``llm_telemetry_service`` for ALL events (every event makes
    at least one LLM call or falls back to deterministic). This is an
    observability layer — unlike Phases 1-4 (decision overlays), it does NOT
    participate in ``merge_quality_overlays`` and does NOT produce
    ``suggested_direction`` / ``downgrade_reason``. It only records what
    happened during the LLM call for audit/monitoring.

    Hybrid implementation: ``_ask_ai`` captures real ``response.usage`` token
    counts (attached as ``llm_usage`` on the analysis dict) plus the model the
    gateway actually served the call with (``llm_model``); this service reads
    that + ``analysis_quality`` + ``sentiment_profile.fallback`` to produce the
    structured block. ``model`` is that served model, falling back to
    ``settings.OPENAI_MODEL`` only when the gateway recorded none.

    ``degraded_mode`` is True when the analysis used the deterministic
    fallback (LLM call failed). ``degraded_reason`` is a structured enum
    value (``"llm_call_failed"``). ``analysis_quality`` mirrors the field
    already on the analysis dict (``"llm"`` / ``"deterministic_fallback"``).

    ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens`` are real
    counts from the API when available; None when the LLM call failed or
    usage data is unavailable. ``estimated_token_cost`` is computed from
    real tokens when available, or estimated from ``news_context`` length
    (chars/4 heuristic) when degraded.

    Closed contract: ``llm_telemetry_service.build_llm_telemetry`` and the
    build-failure fallback in ``event_intelligence_service`` write only the
    declared fields.
    """

    degraded_mode: bool = False
    degraded_reason: str | None = None
    analysis_quality: str = "unknown"
    sentiment_degraded: bool = False
    llm_call_count: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    estimated_token_cost: float = 0.0
    model: str = ""
    error: str | None = None


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

    ``schema_version`` tracks which set of overlay fields the record carries.
    Pre-Phase-4 records (created before source_reliability was added) carry
    ``schema_version="v1.0"``; records written after Phase 5 ship with
    ``schema_version="v2.1"``. ``event_store.normalize_event_record()`` uses
    this to upgrade old records in place instead of silently passing them
    through ``extra="allow"`` — see docs/superpowers/specs/2026-06-30-production-readiness-gaps.md §3.2.
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
    cross_validation: CrossValidation | None = None
    confidence_breakdown: ConfidenceBreakdown | None = None
    semantics: EventSemantics | None = None
    actionable_recommendation: ActionableRecommendation | None = None
    evidence_breakdown: list[EvidenceBreakdownItem] = Field(default_factory=list)
    # Phase 1-5 overlay fields. Explicitly declared (not relying on
    # extra="allow") so that Pydantic catches typos and downstream readers
    # can introspect the schema. All default to None for backward compat.
    decision_quality: DecisionQuality | None = None
    market_quality: MarketQuality | None = None
    source_reliability: SourceReliability | None = None
    llm_telemetry: LLMTelemetry | None = None
    final_displayed_direction: str | None = None
    final_downgrade_reason: str | None = None
    # Record schema version — see normalize_event_record() in event_store.
    schema_version: str = "v2.1"


class FlexibleResponse(BaseModel):
    """Permissive response base for endpoints whose nested payloads are dynamic."""

    model_config = ConfigDict(extra="allow")


class EventStoreEntry(FlexibleResponse):
    event_id: str = ""
    first_seen: str = ""
    last_updated: str = ""
    category: str = "general"
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


class CategoryCountsResponse(FlexibleResponse):
    counts: dict[str, int] = Field(default_factory=dict)


class DailyDigestHeadline(BaseModel):
    """The day's single most material move (mirrors the top entry of movers)."""

    event_id: str
    event_title: str | None = None
    movement: Literal["rising", "falling", "stable"]
    day_net: float
    close: float


class DailyDigestMover(BaseModel):
    """One event whose probability actually moved inside the day window."""

    event_id: str
    event_title: str | None = None
    movement: Literal["rising", "falling", "stable"]
    day_net: float
    open: float
    close: float
    open_source: Literal["previous_close", "first_in_day"]
    day_observations: int
    all_time_net_change: float
    close_ts: str


class DailyDigestUnchanged(BaseModel):
    """Events that did not make the mover cut, split by why they missed."""

    quiet_event_ids: list[str]
    new_event_ids: list[str]


class DailyDigestResponse(BaseModel):
    """Response for GET /api/events/digest.

    Declared as a strict model rather than a permissive ``FlexibleResponse``
    so every field reaches ``frontend/src/lib/generated-types.ts`` — the
    permissive base is deliberately excluded from that allowlist, which would
    leave the endpoint without a published contract.
    """

    date: str
    generated_at: str
    count: int
    headline: DailyDigestHeadline | None = None
    movers: list[DailyDigestMover]
    unchanged: DailyDigestUnchanged
    empty: bool


class EventAuditSnapshot(BaseModel):
    """One line of ``event_audit.jsonl`` as served back by ``/events/{id}/history``.

    Mirrors ``event_audit_service.record_event`` key for key (audit section
    53.53). ``record_event`` writes *all* eleven keys unconditionally, using
    ``None`` for a missing value rather than omitting the key, so every field
    here is present on every line and the nullable ones are spelled ``X | None``
    with no default -- pydantic reads that as required-but-nullable, which is the
    only spelling ``pydantic2ts`` renders as ``estimated: number | null`` (see
    ``MarketSnapshotPoint`` above for the same reasoning).

    ``int`` for the three scores is deliberate, not incidental: ``Credibility``
    and ``Impact`` declare ``score: int`` and ``EventRecord.value_score`` is
    ``int``. Declaring them ``float`` would re-serialize every line's ``26`` as
    ``26.0`` -- a wire change on 458 of 458 observed snapshots, for nothing.
    Conversely ``baseline``/``estimated``/``change`` are already Python floats in
    the log, so ``float`` leaves them byte-identical.

    ``event_id`` carries no default because reachability *derives* it:
    ``history_for_event`` looks the key up in ``histories_by_event()``, and
    ``_group_records`` drops any line whose ``event_id`` is falsy before grouping.
    A snapshot this route can return therefore has a non-empty string id.
    """

    timestamp: str
    event_id: str
    event_title: str | None
    baseline: float | None
    estimated: float | None
    change: float | None
    direction: str | None
    credibility_score: int | None
    impact_score: int | None
    value_score: int | None
    source_type: str | None


class TrendSummary(BaseModel):
    """Probability-trend summary: ``trend_analysis_service.analyze_trend``'s output.

    Named ``TrendSummary`` rather than ``Trend`` on purpose: the frontend already
    owns two things called ``Trend`` (the hand-written consumer interface in
    ``lib/types.ts`` and an unrelated ``"up" | "down" | "flat"`` union in
    ``lib/adapt.ts``), and ``types.ts`` re-exports this module's types wholesale.
    A generated ``Trend`` would be a duplicate export.

    Both branches of ``analyze_trend`` -- the computed summary and the
    ``_empty_trend`` fallback -- return all fourteen keys, so nothing here needs
    a default and no branch can fail validation.
    """

    observations: int
    direction: str
    first_probability: float | None
    latest_probability: float | None
    net_change: float
    min_probability: float | None
    max_probability: float | None
    range: float
    volatility: float
    recent_change: float
    pattern: str
    first_seen: str | None
    last_seen: str | None
    span_hours: float | None


class EdgeTrajectorySummary(BaseModel):
    """AI-vs-market edge trajectory: ``analyze_edge_trajectory``'s output.

    Named ``…Summary`` for the same collision reason as ``TrendSummary``: the
    frontend hand-wrote an ``EdgeTrajectory`` in ``lib/api.ts``. That copy was
    also *narrower* than the producer -- it omitted ``first_seen``, ``last_seen``
    and ``span_hours`` -- so reading the generated type is a gain, not a rename.

    ``first_seen``/``last_seen`` are strings, not datetimes: they carry the audit
    line's own ``timestamp`` value straight through. Declaring them ``datetime``
    would make pydantic re-serialize an ISO string it was just handed, for no
    gain. ``_empty_edge`` and the computed summary return the same twelve keys.
    """

    observations: int
    latest_edge: float | None
    first_edge: float | None
    peak_edge: float | None
    net_edge_change: float
    recent_edge_change: float
    first_seen: str | None
    last_seen: str | None
    span_hours: float | None
    age_hours: float | None
    freshness_band: str
    classification: str


class EventMoverItem(BaseModel):
    """One element of ``EventMoversResponse.movers`` (audit section 53.55).

    ``rank_movers`` writes the same three base keys -- ``event_id``,
    ``event_title`` and ``trend`` -- on every item it returns, and the route then
    adds ``event_title_zh`` when the store holds a Chinese title. A census of all
    five corpora over every reachable ``limit`` (332 items) found exactly one key
    set, so the four fields below are the whole element.

    ``trend`` is ``TrendSummary`` -- the model section 53.53 published for
    ``analyze_trend``'s output -- not a fresh shape. Both branches of that
    producer (the computed summary and ``_empty_trend``) write all fourteen keys;
    a probe put 1 334 of them through this field and rejected none, so it cannot
    turn a 200 into a 500. The frontend already aliases ``Trend = TrendSummary``.

    ``event_id`` and ``event_title`` carry no default because both are written
    unconditionally: ``rank_movers`` is keyed on ``histories_by_event()``, and
    ``_latest_title`` returns ``""`` -- never ``None`` -- when no snapshot
    carries a title. ``event_title_zh`` is optional because it is *conditionally
    written*, not because it is sometimes null.

    That default is load-bearing in a second way, and it is where this endpoint
    differed from ``GET /events/edges/fresh``: this route did **not** declare
    ``response_model_exclude_unset``, so naming the element would have turned
    today's *absent* key into an explicit ``null`` (probe
    ``probe_movers_exclude_unset.py``). Section 53.55 added that flag to the
    route in the same change, which keeps the serialized bytes identical.
    Declaring the field without a default would instead make a title-less event a
    500.

    Like the other registered models this is ``extra="ignore"`` at runtime (only
    schema generation forces ``extra="forbid"``), so a producer that invented a
    fifth key would lose it silently rather than fail. No such key exists today;
    that is a premise, not a property.
    """

    event_id: str
    event_title: str
    trend: TrendSummary
    event_title_zh: str | None = None


class EventMoversResponse(FlexibleResponse):
    """Response for GET /api/events/movers.

    ``count`` lost its default in audit section 53.55, tightening a previously
    all-optional published contract. Safe by construction: exactly one route
    serves this model and it returns a dict literal that sets both keys.

    ``movers`` is a list of ``EventMoverItem`` rather than ``list[dict[str, Any]]``.
    It used to render as ``{ [k: string]: unknown }[]`` -- an element type the
    frontend cannot read, which is why ``lib/types.ts`` hand-wrote ``Mover`` and
    ``lib/api.ts`` spelled the envelope inline at its call site.
    ``TrendSummary`` was already the faithful type of each item's ``trend`` field
    (section 53.53); this names the remaining four-field wrapper.
    """

    count: int
    movers: list[EventMoverItem]


class EventHistoryResponse(FlexibleResponse):
    """Response for GET /api/events/{event_id}/history.

    The five fields lost their defaults in audit section 53.53, tightening a
    previously all-optional published contract. That is safe by construction:
    the route raises 404 unless it has probability snapshots, then returns a
    dict literal with every one of these keys. Leaving the defaults on would have
    rendered ``trend?:`` in the generated TypeScript -- looser than what the
    endpoint actually promises.
    """

    event_id: str
    count: int
    trend: TrendSummary
    edge: EdgeTrajectorySummary
    history: list[EventAuditSnapshot]


class DecisionTimelineSnapshot(BaseModel):
    """One overlay-bearing snapshot of an event at a point in time."""
    snapshot_id: str
    event_id: str
    recorded_at: str
    final_displayed_direction: str | None = None
    final_downgrade_reason: str | None = None
    probability: dict[str, Any] | None = None
    decision_quality: dict[str, Any] | None = None
    market_quality: dict[str, Any] | None = None
    source_reliability: dict[str, Any] | None = None
    execution_quality: dict[str, Any] | None = None
    llm_degraded_mode: bool | None = None
    guardrail_fired: list[str] | None = None
    outcome: str | None = None


class DecisionTimelineDiff(BaseModel):
    """Diff between two consecutive snapshots."""
    direction_changed: bool
    prev_direction: str | None = None
    current_direction: str | None = None
    probability_delta: dict[str, Any] = {}
    overlay_deltas: list[dict[str, Any]] = []
    primary_change_driver: str
    prev_downgrade_reason: str | None = None
    current_downgrade_reason: str | None = None


class DecisionTimelineResponse(BaseModel):
    """Response for GET /api/events/{event_id}/decision-timeline."""
    event_id: str
    count: int
    snapshots: list[DecisionTimelineSnapshot]
    diffs: list[DecisionTimelineDiff]


class MarketSnapshotPoint(BaseModel):
    """One timestamped point on a market link's price path.

    Mirrors ``MarketSnapshotStore._row_to_dict`` key for key, so binding this as
    the route's ``response_model`` filters nothing out of the payload. The
    nullable columns are declared ``X | None`` with *no* default on purpose:
    pydantic reads that as required-but-nullable, which is the only spelling
    ``pydantic2ts`` renders as ``price: number | null``. A default would render
    it ``price?: number | null`` -- looser than the hand-written interface this
    replaces (the same reasoning as ``SimTrade`` below, audit section 53.49).
    """

    id: int
    link_id: int
    implied_prob: float
    price: float | None
    liquidity: float | None
    volume: float | None
    captured_at: datetime | None


class MarketSnapshotSeries(BaseModel):
    """One linked market drawn as a price path, plus what was left out.

    ``untimestamped_count`` is this door's half of the "declare what you
    dropped" rule (audit section 53.48): a row with no timestamp is not a point
    on the path, so the door omits it *and* says how many it omitted.
    """

    contract_id: str
    outcome_label: str
    mapped_outcome: str
    snapshots: list[MarketSnapshotPoint]
    untimestamped_count: int


class MarketSnapshotsResponse(BaseModel):
    """Response for GET /api/sport-markets/snapshots/{match_id}."""

    match_id: str
    series: list[MarketSnapshotSeries]


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
    # Platform -> unresolved event count for platforms no settlement source
    # covers. Declared rather than left to extra="allow" so the reading reaches
    # the generated TS types: by_source omits a never-asked platform entirely,
    # which is indistinguishable from asked-and-got-zero.
    unresolved_without_resolver: dict[str, int] = Field(default_factory=dict)


class PendingLinksResponse(FlexibleResponse):
    pending: list[dict[str, Any]] = Field(default_factory=list)


class RecentPredictionsResponse(FlexibleResponse):
    count: int = 0
    total: int = 0
    limit: int = 10
    offset: int = 0
    predictions: list[dict[str, Any]] = Field(default_factory=list)


class OpenDecisionsResponse(FlexibleResponse):
    count: int = 0
    total: int = 0
    limit: int = 10
    offset: int = 0
    decision_totals: dict[str, int] = Field(default_factory=dict)
    decisions: list[dict[str, Any]] = Field(default_factory=list)


class EdgeSeriesPoint(BaseModel):
    """One point of an edge time series: ``trend_analysis_service.edge_series``.

    Named ``EdgeSeriesPoint`` rather than ``EdgePoint`` on purpose: the frontend
    hand-wrote an ``EdgePoint`` in ``lib/api.ts``, and ``lib/types.ts`` re-exports
    this module's types wholesale (``export type * from "./generated-types"``),
    so a generated ``EdgePoint`` would be a duplicate export reachable from two
    modules. That is the collision that produced ``TrendSummary`` and
    ``EdgeTrajectorySummary`` (sections 53.53, 53.54); the shape of the name
    follows ``MarketSnapshotPoint`` above.

    ``edge_series`` writes all four keys unconditionally and skips any snapshot
    whose ``estimated``/``baseline`` is non-numeric, so the three numeric fields
    are always present and always Python floats -- ``float`` leaves the wire
    bytes identical (``EventAuditSnapshot`` records the opposite case).

    ``timestamp`` is ``str | None`` with no default. The key is always written
    (it is ``snap.get("timestamp")`` straight through), but the value is whatever
    the audit line carried, and ``edge_series`` never requires it to be present.
    Across the 4 666 points observed in the five corpora it was a string every
    time; that is an observation about those corpora, not a guarantee, so the
    type keeps the nullable branch the code actually has.
    """

    timestamp: str | None
    estimated: float
    baseline: float
    edge: float


class FreshEdgeItem(BaseModel):
    """One element of ``FreshEdgesResponse.edges`` (audit section 53.54).

    Both producers write the same three base keys -- ``rank_fresh_edges`` and
    ``_edge_items`` each build ``{"event_id", "event_title", "edge"}`` -- after
    which the route adds ``event_title_zh`` when the store holds a Chinese title
    and ``_edge_items`` adds ``series`` when ``include_series=True``. A census of
    all five corpora over every reachable parameter combination (2 324 items)
    found exactly four key sets and no others, so the five fields below are the
    whole element.

    ``event_id`` carries no default because reachability derives it: both
    producers are keyed on ``histories_by_event()``, and ``_group_records`` drops
    every line whose ``event_id`` is falsy before grouping, so an item this route
    can return has a non-empty string id. ``event_title`` is always a string
    because ``_latest_title`` returns ``""``, never ``None``, when no snapshot
    carries one.

    The two optional fields are optional because they are *conditionally written*,
    not because they are sometimes null. Both keep a default so that an item
    which never set them omits the key entirely: the route is declared
    ``response_model_exclude_unset=True`` and pydantic applies that to nested
    models too, which is what preserves the current "absent key, not null"
    behaviour (probe ``probe_exclude_unset_nested.py``). Declaring either without
    a default would turn an event without a Chinese title -- the norm in three of
    the five corpora -- into a 500.

    Like the other registered models this is ``extra="ignore"`` at runtime (only
    schema generation forces ``extra="forbid"``), so a producer that invented a
    sixth key would lose it silently rather than fail. No such key exists today;
    that is a premise, not a property.
    """

    event_id: str
    event_title: str
    event_title_zh: str | None = None
    edge: EdgeTrajectorySummary
    series: list[EdgeSeriesPoint] | None = None


class FreshEdgesResponse(FlexibleResponse):
    """Response for GET /api/events/edges/fresh.

    The envelope fields lost their defaults in audit section 53.54, tightening a
    previously all-optional published contract. Safe by construction for the same
    reason as ``EventHistoryResponse``: exactly one route serves this model and it
    returns a dict literal that sets every one of these keys unconditionally.
    ``classification`` was declared ``str | None`` while it is in fact always the
    validated query parameter -- a string -- so it is tightened to ``str`` along
    with the rest.

    ``edges`` is a list of ``FreshEdgeItem`` rather than ``list[dict[str, Any]]``.
    It used to render as ``{ [k: string]: unknown }[]`` -- an element type the
    frontend cannot read, which is why ``lib/api.ts`` hand-wrote ``FreshEdge`` and
    spelled the envelope inline at both of its call sites.
    ``EdgeTrajectorySummary`` was already the faithful type of each item's ``edge``
    field (section 53.53); this names the remaining five-field wrapper.
    """

    count: int
    total: int
    limit: int
    offset: int
    classification: str
    classification_totals: dict[str, int]
    edges: list[FreshEdgeItem]


class SimilarEventsResponse(FlexibleResponse):
    event_id: str = ""
    count: int = 0
    similar: list[dict[str, Any]] = Field(default_factory=list)


# ── Simulated trades (M6 paper trading) ──────────────────────────────
#
# /trades/* were the last endpoints answering with the bare FlexibleResponse
# base class. Declared here for the reason that class is excluded from the
# frontend export allowlist: with no fields in the model there is no field
# contract in the OpenAPI schema, so `frontend/src/lib/api.ts` had to hand-write
# `SimTrade` and the CI type-sync job could not see these endpoints at all.
#
# Every field has a default and the base class sets extra="allow", so a store
# row that grows or drops a key does not turn a 200 into a 500. `direction` and
# `status` are Literals because they are the two values with a CHECK behind them
# (simulated_trade_store), and a client union that cannot represent a fourth
# value should fail here rather than in a browser.


class SimTrade(FlexibleResponse):
    """One paper trade row, as returned by the /trades/* list endpoints.

    The fields without a default mirror the store's NOT NULL columns and are
    deliberately required. Giving every field a default would render them all
    optional in ``generated-types.ts``, which is *looser* than the hand-written
    interface this replaces -- ``t.market_prob.toFixed(1)`` would stop
    type-checking. A row missing one of these is a store bug, and a loud 422 is
    the right signal; ``_row_to_dict`` always emits them.
    """

    #: The sqlite rowid. Optional: present because the store selects ``*``, but
    #: not part of the API contract.
    id: int | None = None
    trade_id: str
    event_id: str
    event_title: str
    #: Matches the store's CHECK (direction IN ('YES','NO')).
    direction: Literal["YES", "NO"]
    entry_prob: float
    market_prob: float
    entry_edge: float
    entry_time: str
    position_pct: float
    confidence: float | None = None
    trust_weight: float | None = None
    decision: str
    exit_prob: float | None = None
    exit_market: float | None = None
    exit_time: str | None = None
    exit_reason: str | None = None
    actual_outcome: float | None = None
    pnl_pct: float | None = None
    is_win: int | None = None
    #: Matches the CHECK widened to ('open','closed','voided') in schema v2.
    status: Literal["open", "closed", "voided"]
    created_at: str
    updated_at: str
    #: Derived by the store, not stored columns.
    raw_edge: float | None = None
    directional_edge: float | None = None
    edge_definition: str = ""


class SimTradeListResponse(FlexibleResponse):
    """Envelope shared by /trades/open, /trades/closed and /trades/voided."""

    count: int
    total: int
    limit: int
    offset: int
    trades: list[SimTrade]


class TradeStatBucket(FlexibleResponse):
    """One row of the by_direction / by_decision tables.

    ``total``/``wins``/``win_rate`` are always present on a bucket the store
    emits (it only adds a bucket when ``total > 0``). ``total_pnl`` is present
    only in ``by_direction``, so it keeps a default rather than a second,
    near-identical bucket type.
    """

    total: int
    wins: int
    win_rate: float
    avg_pnl: float | None = None
    total_pnl: float | None = None


class TradeStats(FlexibleResponse):
    """Aggregate statistics for closed simulated trades."""

    total_closed: int = 0
    win_rate: float | None = None
    total_pnl_pct: float = 0.0
    avg_pnl_pct: float | None = None
    avg_edge_at_entry: float | None = None
    avg_directional_edge_at_entry: float | None = None
    edge_definition: dict[str, str] = Field(default_factory=dict)
    by_direction: dict[str, TradeStatBucket] = Field(default_factory=dict)
    by_decision: dict[str, TradeStatBucket] = Field(default_factory=dict)
