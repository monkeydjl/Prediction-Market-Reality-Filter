# Sports Prediction OS — Phase 7 Subproject B: Edge Detector Design

> **Date:** 2026-07-16
> **Status:** Design (pending implementation)
> **Depends on:** Phase 7 Subproject A (complete at `71ebedc`) — Sport Market Bridge Layer
> **Consumed by:** Phase 7 Subproject C (ActionableRecommendation extension), Subproject D (market calibration feedback)

## Goal

Build the Edge Detector that computes model-vs-market divergence for sports matches. The detector reads verified market-implied probabilities (produced by Subproject A) and model probabilities (recorded by the Prediction Kernel), aligns them by `mapped_outcome`, and produces per-outcome `raw_edge` + trust-weighted `adjusted_edge` snapshots persisted as a time-series.

This is the sports analogue of the existing event-pipeline edge detection (`diagnosis_service.py`), adapted for multi-outcome sports markets and the Sports Prediction Kernel's calibration source.

**Subproject B deliverable:** `EdgeDetectorService` + `kernel_sport_edges` persistence + 3 read-only API endpoints + scheduler job + CLI. Produces per-outcome edge data that Subproject C will consume to build sports `ActionableRecommendation`, and Subproject D will consume for market-calibration feedback.

## Background: Current State After Subproject A

Subproject A (complete) produces:

1. **Verified market links** — `SportMarketLinkStore.get_verified_links(match_id)` returns links with `mapped_outcome` (home_win/draw/away_win), `implied_prob` (0-1), `source` (polymarket/the_odds_api), `link_confidence`.
2. **Market price snapshots** — `MarketSnapshotStore.get_latest_snapshot(link_id)` returns the freshest `implied_prob`, `price`, `liquidity`, `volume`, `captured_at`.
3. **Fail-closed guarantee** — `get_verified_links` filters `verified=1` at SQL layer; `/links/{match_id}/latest` endpoint delegates to it.

The Prediction Kernel (Phase 1-5) produces:

1. **Model predictions** — `KernelPrediction` table records `outcome_probabilities` (JSON dict, keys `home_win`/`draw`/`away_win`, values 0-1) per match.
2. **Read-only accessor** — `get_latest_prediction(match_id)` returns the persisted prediction without triggering a new one.
3. **Sports calibration** — `KernelCalibration` table records per-engine-per-competition `slope`, `intercept`, `avg_confidence`, `avg_accuracy`, `sample_count`.

**Core gap:** No component computes the divergence between `KernelPrediction.outcome_probabilities` and verified market `implied_prob`. The two systems cannot compare model accuracy against market consensus. Subproject B closes this gap.

## Scope

### In scope

- `EdgeDetectorService` — domain service that computes per-outcome edge for a match
- `kernel_sport_edges` table — time-series persistence of edge snapshots
- `EdgeStore` — data access for edge snapshots
- `EdgeResult` / `EdgeSource` — frozen dataclass value objects
- 3 read-only API endpoints (`/api/sport-edges/*`), 503-gated by `PHASE7_EDGE_DETECTOR_ENABLED`
- 1 scheduler job (`sport_edge_detect`) — periodic edge computation for matches with verified links
- 1 CLI script (`sport_edge_cli.py`) — manual edge computation and inspection
- Trust-weighting via `KernelCalibration` (sports calibration, not event segment_skill)
- Liquidity-weighted multi-source market probability aggregation
- Freshness/staleness marking (`EDGE_STALE_HOURS`)

### Non-goals (deferred to C/D)

- Do NOT produce act/watch/skip decisions — that is Subproject C's job (extends `ActionableRecommendation` for sports).
- Do NOT extend `ActionableRecommendation` — that is Subproject C.
- Do NOT feed market settlement prices back into the learning loop — that is Subproject D.
- Do NOT build frontend UI — edge display will be part of Subproject C's recommendation UI or a separate frontend task.
- Do NOT infer missing Polymarket NO-side outcomes — if Subproject A did not create a link for an outcome, B skips it. Inferring complements is Subproject A's bridge-service responsibility.
- Do NOT adjust Polymarket spread — use raw `implied_prob`; record spread as metadata for Subproject D.
- Do NOT call `PredictionKernel.predict()` — B is strictly read-only on the kernel. Only `get_latest_prediction(match_id)` is used.
- Do NOT modify `PredictionKernel`, `LearningService`, `domain.py`, the 3 learning tables, learning dashboard components, `event_market_link_store`, `polymarket_event_source`, or any Subproject A file.

## Architecture

### Data flow (single-direction, read-only)

```
[KernelPrediction table]              [kernel_sport_market_links + kernel_market_snapshots]
  outcome_probabilities                       verified links + latest_snapshot
  (home_win/draw/away_win, 0-1)              (implied_prob, liquidity, volume, 0-1)
         │                                            │
         │  get_latest_prediction(match_id)           │  get_verified_links + get_latest_snapshot
         ▼                                            ▼
  ┌──────────────────────────────────────────────────────────────┐
  │              EdgeDetectorService (Subproject B)                │
  │                                                                │
  │  1. Align by mapped_outcome (home_win/draw/away_win)           │
  │  2. Aggregate multi-source market prob per outcome             │
  │     (liquidity-weighted average)                               │
  │  3. raw_edge = model_prob - market_prob  (0-1 scale)           │
  │  4. trust = KernelCalibration-based trust (0-1)                │
  │  5. liquidity_factor = ramp(latest_snapshot.liquidity)         │
  │  6. adjusted_edge = raw_edge * trust * liquidity_factor        │
  │  7. Persist edge snapshot to kernel_sport_edges                │
  └──────────────────────────────────────────────────────────────┘
         │
         ▼
  [kernel_sport_edges table]  ──→  /api/sport-edges/* endpoints  ──→  Subproject C (future)
  (per match_id, per outcome,                                         Subproject D (future)
   time-series snapshots)
```

### Key architectural decisions

1. **Read-only on Kernel.** B uses `get_latest_prediction(match_id)` (existing read accessor in `kernel_db.py:310`). Never calls `PredictionKernel.predict()` (which has a write side-effect to `KernelPredictionHistory`). If no prediction exists for a match, B skips that match and returns an empty list.

2. **Unit convention: 0-1.** Both model probabilities and market implied probabilities are 0-1. `raw_edge = model_prob - market_prob` ranges from -1.0 to +1.0 (positive = model favors this outcome more than market; negative = market favors it more). `adjusted_edge = raw_edge * trust * liquidity_factor` inherits the same range. This differs from the event pipeline's 0-100 convention (where `DECISION_ACT_EDGE=6.0` means 6 percentage points). Subproject C will multiply by 100 when converting to `ActionableRecommendation.edge`. Rationale: 0-1 is the natural unit for probability arithmetic, and B does not produce decisions so the 0-100 threshold constants are irrelevant here.

3. **Polymarket spread: not adjusted.** Use the raw `implied_prob` from Subproject A (where `polymarket_to_implied` returns `yes_price`/`no_price` without normalization). YES+NO may sum >1.0. The Odds API side is already normalized (vigorish removed by `odds_api_to_implied`). **Known limitation:** `EdgeResult.spread` is currently always `None` because computing the spread requires both YES and NO prices, which are stored on separate links in Subproject A. See Known Limitations #1.

4. **Multi-source aggregation: liquidity-weighted average.** A single `mapped_outcome` may have multiple verified links (e.g., Polymarket YES→home_win AND traditional sportsbook home→home_win). Market probability for the outcome:
   ```
   market_prob = Σ(link_implied_prob × link_weight) / Σ(link_weight)
   where link_weight = max(latest_snapshot.liquidity, 1) if liquidity is not None else 1
   ```
   Traditional sportsbook links have `liquidity=None` → weight=1. If only one link exists, `market_prob = link_implied_prob` directly.

5. **Trust from sports calibration.** The event pipeline uses `diagnosis_service.calibration_trust(segment_stats)` reading event-segment statistics. B mirrors this pattern but reads from `KernelCalibration` (per engine + competition), which is the sports kernel's own calibration table. Trust formula:
   - If `KernelCalibration` row exists and `sample_count >= CALIBRATION_FEEDBACK_MIN_SAMPLES` (default 8): `trust = clamp(avg_accuracy, DIAGNOSIS_TRUST_FLOOR, 1.0)` where `DIAGNOSIS_TRUST_FLOOR=0.1`.
   - If `KernelCalibration` row exists but `sample_count < min_samples` (dormant): `trust = DIAGNOSIS_DORMANT_TRUST` (default 0.5).
   - If no `KernelCalibration` row exists (cold start): `trust = DIAGNOSIS_DORMANT_TRUST` (0.5).

6. **Liquidity factor.** Mirrors `diagnosis_service.liquidity_factor(liquidity, floor)`:
   - `liquidity_factor = clamp(liquidity / DIAGNOSIS_LIQUIDITY_FLOOR, 0.0, 1.0)` where `DIAGNOSIS_LIQUIDITY_FLOOR=5000.0`.
   - If `liquidity` is None or <= 0: `liquidity_factor = 1.0` (no penalty — traditional sportsbook links are not penalized for missing liquidity).
   - Uses the **latest snapshot's** liquidity, not the link's (the link doesn't carry liquidity per Section B of the research report).

7. **Persistence: edge snapshots.** Every `detect_edges(match_id)` call appends one row per outcome to `kernel_sport_edges`. This creates a time-series for Subproject D (market calibration feedback) and the future frontend edge chart. The scheduler job calls `detect_edges` for all matches with verified links every `EDGE_DETECTION_INTERVAL_MIN` minutes.

8. **Freshness/staleness.** An edge is marked `stale=True` if the latest market snapshot's `captured_at` is older than `EDGE_STALE_HOURS` (72.0) relative to now, OR if the kernel prediction's `prediction_timestamp` is older than `EDGE_STALE_HOURS`. Stale edges are still computed and persisted (they carry useful historical signal), but the API response includes the `stale` flag for consumers to filter.

### Architecture style: DDD + Hexagonal

Consistent with the Prediction Kernel (Phase 1-5) and Subproject A. `EdgeDetectorService` is a domain service that consumes Protocol-based interfaces:

- `SportMarketLinkStore` (Subproject A — unchanged) — provides `get_verified_links(match_id)`
- `MarketSnapshotStore` (Subproject A — unchanged) — provides `get_latest_snapshot(link_id)`
- `KernelPrediction` read layer (existing `kernel_db.get_latest_prediction`) — provides model probabilities
- `KernelCalibration` read layer (existing `kernel_db` — new read accessor `get_calibration(engine, competition)`) — provides trust source
- `EdgeStore` (new) — persists and reads edge snapshots

`EdgeDetectorService` produces `EdgeResult` value objects (frozen dataclass) and delegates persistence to `EdgeStore`.

## Data Models

### Table: `kernel_sport_edges`

New table in `kernel_predictions.db`, subclassing `KernelBase` (consistent with Subproject A tables).

```python
class KernelSportEdge(KernelBase):
    __tablename__ = "kernel_sport_edges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String, nullable=False, index=True)
    mapped_outcome = Column(String, nullable=False)  # "home_win" | "draw" | "away_win"
    model_prob = Column(Float, nullable=False)        # 0-1, from KernelPrediction.outcome_probabilities
    market_prob = Column(Float, nullable=False)       # 0-1, liquidity-weighted aggregated
    raw_edge = Column(Float, nullable=False)          # model_prob - market_prob, range -1.0 to +1.0
    trust = Column(Float, nullable=False)             # 0-1, from KernelCalibration
    liquidity_factor = Column(Float, nullable=False)  # 0-1, from latest snapshot liquidity
    adjusted_edge = Column(Float, nullable=False)     # raw_edge * trust * liquidity_factor
    spread = Column(Float, nullable=True)             # Polymarket YES+NO-1; None for traditional odds
    sources_count = Column(Integer, nullable=False)   # number of verified links aggregated
    stale = Column(Boolean, nullable=False, default=False)  # True if snapshot/prediction > EDGE_STALE_HOURS old
    captured_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_kernel_sport_edges_match_outcome_captured", "match_id", "mapped_outcome", "captured_at"),
    )
```

**Unique key:** None — this is a time-series (append-only). The index `(match_id, mapped_outcome, captured_at)` supports the "latest edge per outcome" and "history time-series" queries.

**`raw_edge` range:** Can be negative (model predicts lower than market). Range: -1.0 to +1.0. Positive = model favors this outcome more than market; negative = market favors it more.

**`spread` semantics:** Only meaningful for Polymarket sources. If all aggregated links are from `the_odds_api`, `spread = None`. If mixed, `spread` = the Polymarket spread (YES+NO-1) if any Polymarket link is present, else None.

### Value object: `EdgeResult`

```python
@dataclass(frozen=True)
class EdgeSource:
    """Contribution from one verified link to an aggregated edge."""
    link_id: int
    source: str           # "polymarket" | "the_odds_api"
    contract_id: str
    implied_prob: float   # 0-1, from latest_snapshot or link fallback
    liquidity: float | None
    volume: float | None
    weight: float          # max(liquidity, 1) or 1.0
    link_confidence: float # 0-1, from Subproject A matcher


@dataclass(frozen=True)
class EdgeResult:
    """Per-outcome edge computation result."""
    match_id: str
    mapped_outcome: str          # "home_win" | "draw" | "away_win"
    model_prob: float            # 0-1
    market_prob: float           # 0-1, liquidity-weighted aggregate
    raw_edge: float              # model_prob - market_prob
    trust: float                 # 0-1, from KernelCalibration
    liquidity_factor: float      # 0-1
    adjusted_edge: float         # raw_edge * trust * liquidity_factor
    spread: float | None         # Polymarket spread or None
    sources: list[EdgeSource]    # all verified links that contributed
    sources_count: int
    stale: bool
    captured_at: datetime
```

### Value object: `EdgeDetectionSummary`

```python
@dataclass(frozen=True)
class EdgeDetectionSummary:
    """Result of detect_edges(match_id) — all outcomes for one match."""
    match_id: str
    outcomes: list[EdgeResult]
    engine_name: str | None      # from KernelPrediction.engine_name
    competition: str | None      # from KernelPrediction.competition
    prediction_timestamp: datetime | None
    skipped: bool                # True if no prediction or no verified links
    skip_reason: str | None      # "no_prediction" | "no_verified_links" | None
```

## Components

### `EdgeDetectorService` (domain service)

Location: `backend/app/kernel/edge_detector_service.py`

```python
class EdgeDetectorService:
    """Computes model-vs-market edge for sports matches.

    Read-only on the Prediction Kernel. Consumes verified market links
    (Subproject A) and persisted kernel predictions. Produces per-outcome
    edge snapshots persisted to kernel_sport_edges.
    """

    def __init__(self) -> None:
        self._link_store = SportMarketLinkStore()
        self._snap_store = MarketSnapshotStore()
        self._edge_store = EdgeStore()

    def detect_edges(self, match_id: str) -> EdgeDetectionSummary:
        """Compute and persist edge snapshots for all outcomes of a match.

        Steps:
        1. Fetch KernelPrediction via get_latest_prediction(match_id).
           If None → return skipped summary (reason="no_prediction").
        2. Fetch verified links via get_verified_links(match_id).
           If empty → return skipped summary (reason="no_verified_links").
        3. Fetch latest snapshot for each link.
        4. Group links by mapped_outcome.
        5. For each outcome in prediction.outcome_probabilities:
           a. Aggregate market_prob (liquidity-weighted average).
           b. raw_edge = model_prob - market_prob.
           c. trust = _compute_trust(engine_name, competition).
           d. liquidity_factor = _compute_liquidity_factor(links).
           e. adjusted_edge = raw_edge * trust * liquidity_factor.
           f. stale = _is_stale(prediction_timestamp, snapshot timestamps).
           g. Build EdgeResult, persist to kernel_sport_edges.
        6. Return EdgeDetectionSummary.
        """
        ...

    def get_latest_edges(self, match_id: str) -> list[EdgeResult]:
        """Read the most recent edge snapshot per outcome for a match."""
        ...

    def get_edge_history(
        self, match_id: str, mapped_outcome: str | None = None
    ) -> list[EdgeResult]:
        """Read full edge time-series for a match, optionally filtered by outcome."""
        ...

    def get_top_discrepancies(
        self, limit: int = 20, min_abs_edge: float = 0.0
    ) -> list[EdgeResult]:
        """Read the matches with the largest |adjusted_edge| across all matches.

        Uses the latest edge snapshot per (match_id, mapped_outcome).
        Ordered by |adjusted_edge| DESC.
        """
        ...

    def _compute_trust(self, engine_name: str, competition: str) -> float:
        """Trust from KernelCalibration. Mirrors diagnosis_service.calibration_trust
        but reads sports kernel calibration (per engine+competition, not event segment)."""
        ...

    def _compute_liquidity_factor(self, links: list[dict]) -> float:
        """Liquidity factor from the max liquidity among all links for an outcome.

        Uses the latest snapshot's liquidity. If all links have None liquidity
        (traditional sportsbook only), returns 1.0 (no penalty).
        """
        ...

    def _is_stale(
        self, prediction_ts: datetime, snapshot_timestamps: list[datetime | None]
    ) -> bool:
        """True if prediction or any snapshot is older than EDGE_STALE_HOURS."""
        ...
```

### `EdgeStore` (data access)

Location: `backend/app/kernel/edge_store.py`

```python
class EdgeStore:
    """Persistence for kernel_sport_edges table.

    Append-only writes (time-series). Read methods support latest-per-outcome
    and history queries.
    """

    def append_edge(self, *, match_id: str, mapped_outcome: str, model_prob: float,
                    market_prob: float, raw_edge: float, trust: float,
                    liquidity_factor: float, adjusted_edge: float,
                    spread: float | None, sources_count: int, stale: bool) -> dict:
        """Append one edge snapshot row. Returns the inserted row as dict."""
        ...

    def get_latest_edges(self, match_id: str) -> list[dict]:
        """Latest edge per mapped_outcome for a match (subquery: max(captured_at) per outcome)."""
        ...

    def get_edge_history(
        self, match_id: str, mapped_outcome: str | None = None
    ) -> list[dict]:
        """Full time-series, optionally filtered by outcome. Ordered by captured_at ASC."""
        ...

    def get_top_discrepancies(
        self, limit: int = 20, min_abs_edge: float = 0.0
    ) -> list[dict]:
        """Top matches by |adjusted_edge| (latest snapshot per match+outcome)."""
        ...
```

### `get_calibration` read accessor

Location: `backend/app/kernel/kernel_db.py` (new function, does NOT modify existing code)

```python
def get_calibration(engine_name: str, competition: str) -> KernelCalibration | None:
    """Read sports calibration for trust computation. Returns None if no row exists."""
    ...
```

This is a read-only accessor added to the existing `kernel_db.py`. It does NOT modify `KernelCalibration` table structure or any existing function.

## Edge computation details

### Alignment by `mapped_outcome`

The kernel's `outcome_probabilities` dict and the market links' `mapped_outcome` field use the same outcome namespace: `home_win` / `draw` / `away_win`. B iterates `outcome_probabilities.keys()` dynamically (same pattern as `learning_service.py:149-153`):

```python
for outcome, model_prob in prediction.outcome_probabilities.items():
    links_for_outcome = [l for l in verified_links if l["mapped_outcome"] == outcome]
    if not links_for_outcome:
        continue  # no market data for this outcome — skip
    # ... compute edge for this outcome
```

**Binary sports (NBA/MLB/NHL):** `outcome_probabilities` has `home_win` + `away_win` (no draw). If market links only cover `home_win` (e.g., Polymarket "Will home win?" YES→home_win, NO→away_win but NO link not created), B computes edge for `home_win` only and skips `away_win`. B does NOT infer the complement.

**Football (3-way):** `outcome_probabilities` has `home_win` + `draw` + `away_win`. B computes edge for each outcome that has at least one verified link.

### Multi-source market probability aggregation

For each outcome, aggregate all verified links' implied probabilities:

```python
def _aggregate_market_prob(
    links_with_snapshots: list[tuple[dict, dict | None]]
) -> tuple[float, float | None]:
    """Returns (market_prob, spread).

    market_prob = Σ(implied_prob × weight) / Σ(weight)
    where weight = max(latest_snapshot.liquidity, 1) if liquidity is not None else 1

    spread = YES+NO-1 from Polymarket if any Polymarket link present, else None.
    """
    total_weight = 0.0
    weighted_sum = 0.0
    spread = None

    for link, snap in links_with_snapshots:
        # Use snapshot's implied_prob if available (freshest), else link's (stale fallback)
        implied = snap["implied_prob"] if snap else link["implied_prob"]
        liquidity = snap["liquidity"] if snap else None
        weight = max(liquidity, 1.0) if liquidity is not None and liquidity > 0 else 1.0

        weighted_sum += implied * weight
        total_weight += weight

        # Capture spread from Polymarket links
        if link["source"] == "polymarket" and snap and snap.get("price") is not None:
            # spread requires both YES and NO prices — but we only have one side per link.
            # Spread is computed at the link level in Subproject A's implied_prob.py
            # and stored implicitly. For B, we record the spread if the link's
            # implied_prob + complement > 1.0. Simplification: spread = None at edge level
            # unless we can determine it. For now, spread is recorded per-link in
            # EdgeSource and aggregated as max(spreads) if any Polymarket source.
            pass

    market_prob = weighted_sum / total_weight if total_weight > 0 else 0.0
    return market_prob, spread
```

**Spread simplification:** The Polymarket spread (`YES+NO-1`) is computed by `polymarket_to_implied` in Subproject A, but only the `implied_prob` (not the spread) is stored on the link/snapshot. B cannot recompute the spread from a single link (it needs both YES and NO prices). Therefore, `EdgeResult.spread` is set to `None` for now. A future enhancement to Subproject A could store `spread` on the snapshot, and B would then surface it. This is documented as a known limitation.

### Trust computation

```python
def _compute_trust(self, engine_name: str, competition: str) -> float:
    """Trust from KernelCalibration (sports), mirroring diagnosis_service.calibration_trust."""
    calibration = get_calibration(engine_name, competition)
    if calibration is None:
        return config.settings.DIAGNOSIS_DORMANT_TRUST  # 0.5, cold start

    if calibration.sample_count < config.settings.CALIBRATION_FEEDBACK_MIN_SAMPLES:
        return config.settings.DIAGNOSIS_DORMANT_TRUST  # 0.5, dormant

    # Qualified: use avg_accuracy as trust, clamped to [floor, 1.0]
    trust = max(config.settings.DIAGNOSIS_TRUST_FLOOR, min(calibration.avg_accuracy, 1.0))
    return trust
```

**Rationale for `avg_accuracy` over `avg_confidence`:** `KernelCalibration.avg_accuracy` measures how often the model's predicted winner matches the actual outcome — a direct measure of predictive skill. `avg_confidence` measures the model's self-reported confidence (which may be systematically over/under-calibrated). Trust should reflect actual skill, so `avg_accuracy` is the right source. This mirrors `diagnosis_service.calibration_trust` which uses `segment_skill` (actual accuracy).

### Liquidity factor

```python
def _compute_liquidity_factor(self, links_with_snapshots: list[tuple[dict, dict | None]]) -> float:
    """Liquidity factor from the max liquidity among all links.

    Mirrors diagnosis_service.liquidity_factor but uses max(liquidities)
    across all sources for an outcome (the most liquid source dominates).
    """
    liquidities = [
        snap["liquidity"]
        for _, snap in links_with_snapshots
        if snap and snap.get("liquidity") is not None and snap["liquidity"] > 0
    ]
    if not liquidities:
        return 1.0  # no liquidity info (traditional sportsbook) — no penalty

    max_liq = max(liquidities)
    floor = config.settings.DIAGNOSIS_LIQUIDITY_FLOOR  # 5000.0
    return min(max_liq / floor, 1.0)
```

**Rationale for `max` over `sum`:** The event pipeline uses a single liquidity value per event. For sports with multiple sources, the most liquid source is the best signal — summing liquidities would over-reward having many illiquid sources. `max` captures "the best available market depth for this outcome."

### Staleness check

```python
def _is_stale(
    self,
    prediction_ts: datetime | None,
    snapshot_timestamps: list[datetime | None],
) -> bool:
    """True if prediction is stale OR ALL market snapshots are stale.

    A fresh snapshot (one with captured_at within EDGE_STALE_HOURS) is enough
    to mark the edge as not stale — even if other snapshots are old.
    """
    threshold = config.settings.EDGE_STALE_HOURS  # 72.0 hours
    now = datetime.utcnow()

    if prediction_ts is not None:
        pred_age = (now - prediction_ts).total_seconds() / 3600
        if pred_age > threshold:
            return True

    valid_snaps = [ts for ts in snapshot_timestamps if ts is not None]
    if not valid_snaps:
        return True  # no snapshots at all — definitely stale

    # Use the NEWEST snapshot (max timestamp). If the newest is still old,
    # then ALL snapshots are old → stale. One fresh snapshot is enough.
    newest_snap = max(valid_snaps)
    snap_age = (now - newest_snap).total_seconds() / 3600
    return snap_age > threshold
```

## API Endpoints

All endpoints are 503-gated by `PHASE7_EDGE_DETECTOR_ENABLED` (following the Subproject A pattern).

Location: `backend/app/api/routes/sport_edges.py`

### `GET /api/sport-edges/{match_id}/latest`

Returns the latest edge snapshot per outcome for a match.

```json
{
  "match_id": "nba-20250101-LAL-BOS",
  "outcomes": [
    {
      "mapped_outcome": "home_win",
      "model_prob": 0.65,
      "market_prob": 0.58,
      "raw_edge": 0.07,
      "trust": 0.72,
      "liquidity_factor": 0.85,
      "adjusted_edge": 0.0428,
      "spread": null,
      "sources_count": 2,
      "stale": false,
      "captured_at": "2026-07-16T12:00:00Z",
      "sources": [
        {
          "link_id": 1, "source": "polymarket", "contract_id": "c1",
          "implied_prob": 0.58, "liquidity": 4250.0, "volume": 15000.0,
          "weight": 4250.0, "link_confidence": 0.95
        }
      ]
    }
  ],
  "engine_name": "BasketballEngine",
  "competition": "nba",
  "prediction_timestamp": "2026-07-16T11:30:00Z",
  "skipped": false,
  "skip_reason": null
}
```

If `skipped=true`, `outcomes` is empty and `skip_reason` is `"no_prediction"` or `"no_verified_links"`.

### `GET /api/sport-edges/{match_id}/history`

Returns the full edge time-series for a match, optionally filtered by outcome.

Query params:
- `mapped_outcome` (optional): filter to one outcome (e.g., `?mapped_outcome=home_win`)

```json
{
  "match_id": "nba-20250101-LAL-BOS",
  "series": [
    {
      "mapped_outcome": "home_win",
      "snapshots": [
        {
          "captured_at": "2026-07-16T10:00:00Z",
          "model_prob": 0.65, "market_prob": 0.55, "raw_edge": 0.10,
          "adjusted_edge": 0.061, "stale": false
        },
        {
          "captured_at": "2026-07-16T12:00:00Z",
          "model_prob": 0.65, "market_prob": 0.58, "raw_edge": 0.07,
          "adjusted_edge": 0.0428, "stale": false
        }
      ]
    }
  ]
}
```

### `GET /api/sport-edges/discrepancies`

Returns the matches with the largest `|adjusted_edge|` across all matches with edge data.

Query params:
- `limit` (optional, default 20, range 1-100): number of results
- `min_abs_edge` (optional, default 0.0, range 0.0-1.0): minimum `|adjusted_edge|` threshold

```json
{
  "items": [
    {
      "match_id": "nba-20250101-LAL-BOS",
      "mapped_outcome": "home_win",
      "model_prob": 0.70,
      "market_prob": 0.50,
      "raw_edge": 0.20,
      "adjusted_edge": 0.144,
      "stale": false,
      "captured_at": "2026-07-16T12:00:00Z"
    }
  ],
  "total": 1
}
```

### Security

All 3 endpoints are GET (read-only). No `require_write_key` needed (consistent with Subproject A's GET endpoints, which don't use auth). The 503 gate (`_ensure_enabled()`) is the only access control.

## Configuration

New config flags added to `backend/app/core/config.py` (following the `PHASE7_*` pattern):

```python
PHASE7_EDGE_DETECTOR_ENABLED: bool = _env_bool("PHASE7_EDGE_DETECTOR_ENABLED", "false")
EDGE_DETECTION_INTERVAL_MIN: int = int(os.getenv("EDGE_DETECTION_INTERVAL_MIN", "5"))
```

**Reused existing config (no new flags):**
- `DIAGNOSIS_DORMANT_TRUST` (0.5) — cold-start trust
- `DIAGNOSIS_TRUST_FLOOR` (0.1) — minimum trust for qualified calibration
- `DIAGNOSIS_LIQUIDITY_FLOOR` (5000.0) — liquidity ramp ceiling
- `CALIBRATION_FEEDBACK_MIN_SAMPLES` (8) — dormant threshold
- `EDGE_STALE_HOURS` (72.0) — staleness threshold

## Scheduler

One new job added to `backend/app/core/scheduler.py`:

```python
async def _job_detect_sport_edges():
    """Every EDGE_DETECTION_INTERVAL_MIN: compute edges for matches with verified links."""
    if not settings.PHASE7_EDGE_DETECTOR_ENABLED:
        return
    run_id = _start_run("sport_edge_detect")
    try:
        from app.kernel.kernel_db import init_kernel_db
        from app.kernel.edge_detector_service import EdgeDetectorService
        from app.kernel.sport_market_link_store import SportMarketLinkStore
        init_kernel_db()
        # Get all matches with verified links
        store = SportMarketLinkStore()
        matches = store.get_matches_with_verified_links()  # new method
        service = EdgeDetectorService()
        for match_id in matches:
            try:
                service.detect_edges(match_id)
            except Exception as exc:
                logger.warning(f"[Scheduler] Edge detection failed for {match_id}: {exc}")
        _finish_run(run_id, "success", result={"matches_processed": len(matches)})
    except Exception as exc:
        logger.exception("[Scheduler] Sport edge detection failed")
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
```

Registered in `start_scheduler`:
```python
if settings.PHASE7_EDGE_DETECTOR_ENABLED:
    scheduler.add_job(
        _job_detect_sport_edges,
        IntervalTrigger(minutes=settings.EDGE_DETECTION_INTERVAL_MIN),
        id="sport_edge_detect",
        replace_existing=True,
        max_instances=1,
    )
```

### New store method: `get_matches_with_verified_links`

Added to `SportMarketLinkStore` (Subproject A file — minimal append, does NOT modify existing methods):

```python
def get_matches_with_verified_links(self) -> list[str]:
    """Return distinct match_ids that have at least one verified=True link."""
    session = get_kernel_session()
    try:
        rows = session.query(KernelSportMarketLink.match_id).filter(
            KernelSportMarketLink.verified == 1
        ).distinct().all()
        return [r[0] for r in rows]
    except Exception:
        return []
    finally:
        session.close()
```

## CLI

Location: `backend/scripts/sport_edge_cli.py`

```python
"""Sport edge detector CLI.

Usage:
    python -m scripts.sport_edge_cli detect --match-id ID
    python -m scripts.sport_edge_cli latest --match-id ID
    python -m scripts.sport_edge_cli discrepancies [--limit N] [--min-abs-edge F]
"""
```

Subcommands:
- `detect --match-id ID` — manually trigger edge computation for one match
- `latest --match-id ID` — show latest edge snapshot per outcome
- `discrepancies [--limit N] [--min-abs-edge F]` — show top discrepancies

## Testing Strategy

### TDD approach

Strict TDD (RED → GREEN) for all backend code. Tests written before implementation.

### Unit tests

**`test_edge_detector_service.py`:**
- `test_detect_edges_no_prediction_returns_skipped` — match with no KernelPrediction → `skipped=True, skip_reason="no_prediction"`
- `test_detect_edges_no_verified_links_returns_skipped` — match with prediction but no verified links → `skipped=True, skip_reason="no_verified_links"`
- `test_detect_edges_single_outcome_single_source` — one outcome, one link → correct `raw_edge`, `market_prob`, `adjusted_edge`
- `test_detect_edges_multi_source_liquidity_weighted` — two links for same outcome with different liquidities → weighted average correct
- `test_detect_edges_traditional_odds_no_liquidity_uses_weight_1` — `liquidity=None` → `weight=1`, `liquidity_factor=1.0`
- `test_detect_edges_trust_cold_start` — no KernelCalibration → `trust=0.5`
- `test_detect_edges_trust_dormant` — `sample_count < 8` → `trust=0.5`
- `test_detect_edges_trust_qualified` — `sample_count >= 8` → `trust=clamp(avg_accuracy, 0.1, 1.0)`
- `test_detect_edges_liquidity_factor_ramp` — `liquidity=2500` → `factor=0.5`; `liquidity=5000` → `factor=1.0`; `liquidity=10000` → `factor=1.0`
- `test_detect_edges_stale_when_prediction_old` — prediction_timestamp 100h old → `stale=True`
- `test_detect_edges_stale_when_all_snapshots_old` — all snapshots 100h old → `stale=True`
- `test_detect_edges_not_stale_when_one_snapshot_fresh` — one snapshot 1h old, another 100h old → `stale=False`
- `test_detect_edges_binary_sport_skips_missing_outcome` — `outcome_probabilities` has `away_win` but no verified link for it → skipped (no edge row for `away_win`)
- `test_detect_edges_persists_to_edge_store` — after `detect_edges`, `get_latest_edges` returns the computed edges
- `test_get_top_discrepancies_orders_by_abs_adjusted_edge_desc` — multiple matches, correct ordering

**`test_edge_store.py`:**
- `test_append_edge_and_get_latest` — append 2 edges for same outcome at different times → `get_latest_edges` returns only the newest
- `test_get_latest_edges_multiple_outcomes` — 3 outcomes → returns 3 latest edges
- `test_get_edge_history_filtered_by_outcome` — history with `mapped_outcome` filter
- `test_get_top_discrepancies_min_abs_edge_filter` — `min_abs_edge=0.05` filters out small edges

**`test_sport_edge_routes.py`:**
- `test_latest_returns_503_when_disabled` — `PHASE7_EDGE_DETECTOR_ENABLED=false` → 503
- `test_latest_returns_edges` — seeded edges → correct response
- `test_latest_returns_skipped_summary` — match with no prediction → `skipped=true`
- `test_history_returns_timeseries` — multiple snapshots → correct series
- `test_history_filtered_by_outcome` — `?mapped_outcome=home_win` → only one series
- `test_discrepancies_returns_top_edges` — multiple matches → ordered by `|adjusted_edge|` DESC
- `test_discrepancies_respects_limit` — `?limit=5` → max 5 results
- `test_discrepancies_respects_min_abs_edge` — `?min_abs_edge=0.1` → filters

**`test_sport_edge_cli.py`:**
- `test_cli_detect` — manually trigger → exit code 0, edge persisted
- `test_cli_latest` — show latest edges → exit code 0, output contains edge data
- `test_cli_discrepancies` — show top discrepancies → exit code 0

### Regression tests

- Run `test_sport_market_routes.py` (Subproject A) — must still pass (new `get_matches_with_verified_links` method is additive).
- Run `test_odds_cache_service.py` — must still pass (no changes to odds API).

## Global Constraints

1. `PHASE7_EDGE_DETECTOR_ENABLED` feature flag must default to OFF — when false, all 3 endpoints return 503 and the scheduler job is not registered.
2. Zero-invasion: `PredictionKernel`, `PredictionEngine`, `FeatureSet`, `domain.py`, `LearningService`, the 3 learning tables (`KernelPredictionHistory`, `KernelCalibration`, `KernelEngineScore`), learning dashboard components, `event_market_link_store`, `polymarket_event_source`, and all Subproject A files (except minimal additive appends to `sport_market_link_store.py` and `kernel_db.py`) must NOT be modified.
3. `KernelCalibration` table must have zero structural modifications — B only reads via a new `get_calibration()` accessor function.
4. New table `kernel_sport_edges` must subclass `KernelBase` and use the `kernel_` prefix.
5. New `get_matches_with_verified_links` method on `SportMarketLinkStore` must be additive — must NOT modify existing `upsert_link`, `get_links`, `get_verified_links`, `get_pending_links`, `set_verified`, or `list_links` methods.
6. `EdgeDetectorService` must NOT call `PredictionKernel.predict()` — only `get_latest_prediction(match_id)` (read-only).
7. Edge values are 0-1 scale (NOT 0-100). `raw_edge` can be negative (model predicts lower than market).
8. Polymarket spread is NOT adjusted — use raw `implied_prob`. `spread` field on `EdgeResult` is `None` for now (known limitation: spread requires both YES and NO prices, which are stored on separate links).
9. All 3 API endpoints are GET (read-only) — no `require_write_key` auth (consistent with Subproject A's GET endpoints).
10. Standing instruction "不推送" — commits must not be pushed to origin.
11. B must NOT produce act/watch/skip decisions — that is Subproject C.
12. B must NOT extend `ActionableRecommendation` — that is Subproject C.
13. B must NOT feed market settlement prices back into the learning loop — that is Subproject D.
14. Subagent-driven task execution must be used for implementation, with independent sub-agents per task and inter-task reviews.

## File Structure

### New files

| File | Responsibility |
|------|----------------|
| `backend/app/kernel/edge_detector_service.py` | `EdgeDetectorService` — domain service computing edges |
| `backend/app/kernel/edge_store.py` | `EdgeStore` — persistence for `kernel_sport_edges` |
| `backend/app/api/routes/sport_edges.py` | 3 GET endpoints, 503-gated |
| `backend/scripts/sport_edge_cli.py` | CLI for manual edge computation and inspection |
| `backend/tests/test_edge_detector_service.py` | Unit tests for `EdgeDetectorService` (15 tests) |
| `backend/tests/test_edge_store.py` | Unit tests for `EdgeStore` (4 tests) |
| `backend/tests/test_sport_edge_routes.py` | API integration tests (8 tests) |
| `backend/tests/test_sport_edge_cli.py` | CLI tests (3 tests) |

### Modified files (minimal additive changes only)

| File | Change |
|------|--------|
| `backend/app/core/config.py` | Add `PHASE7_EDGE_DETECTOR_ENABLED` + `EDGE_DETECTION_INTERVAL_MIN` before `settings = Settings()` |
| `backend/app/kernel/kernel_db.py` | Add `KernelSportEdge` table class + `get_calibration()` function (additive, no existing code modified) |
| `backend/app/kernel/sport_market_link_store.py` | Add `get_matches_with_verified_links()` method (additive, no existing methods modified) |
| `backend/app/api/router.py` | Register `sport_edges` router (additive) |
| `backend/app/core/scheduler.py` | Add `_job_detect_sport_edges` + register in `start_scheduler` (additive) |

### Value objects (in `edge_detector_service.py`)

- `EdgeSource` (frozen dataclass) — one link's contribution to an aggregated edge
- `EdgeResult` (frozen dataclass) — per-outcome edge computation result
- `EdgeDetectionSummary` (frozen dataclass) — all outcomes for one match

## Known Limitations

1. **Polymarket spread not surfaced.** `EdgeResult.spread` is always `None` because the spread requires both YES and NO prices, which are stored on separate links in Subproject A. A future enhancement to Subproject A could store `spread` on the snapshot, and B would then surface it. Subproject D can recompute spread when it has access to both sides.

2. **No NO-side complement inference.** If Subproject A only created a link for Polymarket YES→home_win and not for NO→away_win, B computes edge for `home_win` only. B does not infer that NO implies `away_win`. This is by design — inferring complements is Subproject A's bridge-service responsibility.

3. **No frontend UI.** B produces API endpoints only. Edge display will be part of Subproject C's recommendation UI or a separate frontend task.

4. **Edge computed only for matches with existing predictions.** B reads `get_latest_prediction(match_id)` and skips matches without a prediction. B does not trigger new predictions. If a match has verified market links but no kernel prediction, its edges are not computed until the kernel runs.

5. **Trust uses `avg_accuracy` not calibrated probabilities.** The trust factor is based on `KernelCalibration.avg_accuracy` (raw accuracy), not the calibration slope/intercept (which would correct systematic over/under-confidence). Applying the calibration curve is a Subproject D responsibility (market calibration feedback).
