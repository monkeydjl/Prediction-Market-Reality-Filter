# Sports Prediction OS — Phase 7 Subproject D: Market Settlement Feedback Loop

Date: 2026-07-16

## Goal

Build the market-settlement feedback channel that closes the Phase 7 learning loop. When a match finishes and its linked market settles, Subproject D captures the settlement price (last snapshot before match end), computes a Brier-style error against B's persisted `model_prob`, and aggregates the error into a new `kernel_market_calibrations` table (parallel to Phase 3's `KernelCalibration`). D is a **parallel channel**: match-outcome learning (Phase 3) continues unchanged; market-settlement learning (D) writes only to its own tables.

**Subproject D deliverable:** `MarketSettlementService` + `MarketSettlementStore` + 2 new tables (`kernel_market_settlements`, `kernel_market_calibrations`) + scheduler job + 4 API endpoints + CLI + frontend `/sports/settlements` page. Completes the Phase 7 "Full-Stack Fusion" loop: B detects edges → C recommends actions → D learns from market settlement.

**Consumed by:** Frontend settlements page, CLI tools, and (future) enhanced trust computation in B/C that reads market calibration alongside match-outcome calibration.

## Background: Current State

| Dimension | Phase 3 (match-outcome learning) | Subproject B (edge detector) | Subproject C (recommendation) | Gap D fills |
|-----------|----------------------------------|------------------------------|-------------------------------|-------------|
| Ground truth | `KernelMatchOutcome.outcome` (binary/ternary) | N/A | N/A | Market settlement price (continuous) |
| Error signal | `brier_score`, `score_mae`, `outcome_correct` | N/A | N/A | `brier_score` (model vs settlement), `signed_error`, `direction_correct` |
| Calibration | `KernelCalibration` (slope/intercept per engine/competition) | `EdgeResult.trust` (reads KernelCalibration) | Reads `KernelCalibration.sample_count` for `qualified` | `kernel_market_calibrations` (parallel regression on market settlement) |
| Trigger | `POST /api/predictions/outcomes/{match_id}/process` (manual) | Scheduler `_job_detect_sport_edges` | On-demand API request | Scheduler `_job_process_market_settlements` + manual API |
| Persistence | `kernel_match_outcomes`, `kernel_calibration`, `kernel_engine_scores` | `kernel_sport_edges` | Stateless (none) | `kernel_market_settlements`, `kernel_market_calibrations` |

**Core gap:** Phase 3 learns from the **hard binary/ternary match outcome** (did home win? yes/no). D learns from the **continuous market settlement price** (the market's final implied probability). These are complementary signals: a market settling at 0.95 for home_win when the model predicted 0.70 is a different calibration signal than the binary "home won" — it tells you the market was confident, which the model should learn to align with. D provides this softer, denser calibration channel without touching Phase 3's infrastructure.

## Non-goals

- Do NOT modify Subproject A's code (`sport_market_link_store.py`, `market_snapshot_store.py`, `sport_markets.py`, `polymarket_sports_source`).
- Do NOT modify Subproject B's code (`edge_detector_service.py`, `edge_store.py`, `sport_edges.py`, `kernel_sport_edges` table).
- Do NOT modify Subproject C's code (`sport_recommendation_service.py`, `sport_recommendations.py`).
- Do NOT modify Phase 3 learning loop (`learning_service.py`, `prediction_kernel.py`, `diagnosis_service.py`, `decision_quality_service.py`).
- Do NOT structurally modify the 3 learning tables (`KernelPredictionHistory`, `KernelCalibration`, `KernelEngineScore`).
- Do NOT modify the learning dashboard components.
- Do NOT modify the event pipeline (`ActionableRecommendation`, `event_intelligence_service`, `polymarket_event_source`).
- Do NOT query Polymarket's resolution API — D infers settlement from `KernelMatchOutcome.finished_at` and uses the last snapshot before that timestamp as the settlement price proxy. (Future enhancement may add real resolution API support.)
- Do NOT feed D's calibration back into B's `trust` computation — that is a future integration. D writes to its own table only.
- Do NOT implement automated trading — D is informational/calibration only.

## Architecture

```
kernel_match_outcomes (Phase 3, finished_at IS NOT NULL)
        │
        ▼  trigger: scan for finished matches without settlements
┌──────────────────────────────────────────────────────────┐
│  MarketSettlementService (new, stateless compute + write) │
│  ├─ scan_finished_matches_without_settlements()           │
│  ├─ process_settlement(match_id):                         │
│  │   ├─ read KernelMatchOutcome (finished_at, outcome)    │
│  │   ├─ read KernelSportMarketLink (verified links)       │
│  │   ├─ read last KernelMarketSnapshot before finished_at │
│  │   ├─ read B's kernel_sport_edges (latest, via EdgeStore)│
│  │   ├─ compute brier_score, signed_error, direction_correct│
│  │   ├─ persist to kernel_market_settlements              │
│  │   └─ update kernel_market_calibrations (regression)    │
│  └─ read methods: get_settlement, get_calibrations, etc.  │
└──────────────────────────────────────────────────────────┘
        │
        ▼
   kernel_market_settlements (new, per match+outcome)
   kernel_market_calibrations (new, per engine+competition)
        │
        ▼
┌──────────────────────────────────────────────────────────┐
│  Scheduler: _job_process_market_settlements (every 10min) │
│  API: /api/sport-settlements/*                             │
│  ├─ GET /{match_id}      single match settlement          │
│  ├─ GET /calibrations    market calibration list           │
│  ├─ GET /history         settlement history (paginated)   │
│  └─ POST /process/{mid}  manual trigger (write-key gated) │
└──────────────────────────────────────────────────────────┘
        │
        ▼
   Frontend: /sports/settlements
   (settlement history table + market calibration panel)
```

### Data flow

1. **Scan**: Scheduler job (or manual API) calls `scan_and_process(limit)`. Service queries `KernelMatchOutcome` for rows where `finished_at IS NOT NULL` AND `match_id NOT IN (SELECT match_id FROM kernel_market_settlements)`.
2. **Detect settlement**: For each finished match, read `KernelMatchOutcome.finished_at` and `outcome`. The market is considered settled at this timestamp.
3. **Read market data**: Query `KernelSportMarketLink` for `verified=1` links for this `match_id`. For each link, query `KernelMarketSnapshot` for the last row where `captured_at <= finished_at`. That snapshot's `implied_prob` is the **settlement implied probability** for the link's `mapped_outcome`.
4. **Read model data**: Query B's `kernel_sport_edges` via `EdgeStore.get_latest_edges(match_id)` for the latest `model_prob` and `market_prob` per `mapped_outcome`.
5. **Match outcomes to edges**: For each `mapped_outcome` that has both a settlement snapshot and a B edge, compute the error signals.
6. **Compute error**: `brier_score = (model_prob - settlement_implied_prob)^2`, `signed_error = model_prob - settlement_implied_prob`, `direction_correct = 1 if sign(raw_edge) == sign(settlement_implied_prob - market_prob) else 0`.
7. **Persist settlement**: Insert one row into `kernel_market_settlements` per `(match_id, mapped_outcome)`. Idempotent via unique constraint.
8. **Update calibration**: Query the last `MARKET_CALIBRATION_WINDOW_SIZE` (default 30) settlements for `(engine, competition)`, fit a linear regression (`settlement_implied_prob ~ slope * model_prob + intercept`), upsert into `kernel_market_calibrations`.

### Settlement price proxy

D does NOT query Polymarket's resolution API. Instead, it uses the **last `kernel_market_snapshots` row before `KernelMatchOutcome.finished_at`** as the settlement price proxy. Rationale:
- When a match finishes, the market has effectively settled — the last price before the match ends is very close to the final settlement price (for a YES/NO market on a finished event, the price converges to 0 or 1).
- This avoids modifying Subproject A's `polymarket_sports_source` (zero-invasion constraint).
- This avoids adding a Polymarket API dependency in D.
- Future enhancement: if a `fetch_market_resolution()` method is added to `polymarket_sports_source` (additive, in a future phase), D can be upgraded to use real settlement prices.

### Edge cases

- **No verified links for a match**: `process_settlement` returns a `SettlementResult` with `status="no_links"` and writes nothing. The match is NOT re-processed on the next scan (it's added to a skip list to avoid infinite retries — implemented by inserting a settlement row with `settlement_implied_prob=NULL` and a `status` column, or by a separate processed-matches tracker). Design choice: insert a "skipped" settlement row with `brier_score=NULL` to mark it processed.
- **No snapshots before `finished_at`**: Same — insert a "skipped" row. The market may have been linked after the match finished, or snapshots were never captured.
- **No B edges for the match**: Same — insert a "skipped" row. B may not have run for this match.
- **Already processed**: The unique constraint `(match_id, mapped_outcome)` prevents duplicate inserts. `process_settlement` checks existence first and returns `status="already_processed"`.
- **Multiple links for same `mapped_outcome`**: Use the link with the highest `link_confidence` among verified links. If tie, use the most recent snapshot.

## Data Model

### `kernel_market_settlements` (new table, append-only)

```python
class KernelMarketSettlement(KernelBase):
    """One settlement record per (match_id, mapped_outcome).

    Records the market's settlement price (last snapshot before match finished)
    and the error against B's persisted model_prob. Idempotent via unique
    constraint on (match_id, mapped_outcome).
    """
    __tablename__ = "kernel_market_settlements"
    __table_args__ = (
        UniqueConstraint("match_id", "mapped_outcome", name="uq_market_settlement_match_outcome"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String, nullable=False, index=True)
    mapped_outcome = Column(String, nullable=False)       # "home_win" | "draw" | "away_win"
    engine = Column(String, nullable=False)
    competition = Column(String, nullable=False)

    # Settlement data (from last snapshot before finished_at)
    settlement_implied_prob = Column(Float)               # NULL when skipped (no snapshot)
    settlement_captured_at = Column(DateTime)             # when the settlement snapshot was taken
    link_id = Column(Integer)                             # which market link was used

    # Model data (from B's kernel_sport_edges, latest before finished_at)
    model_prob = Column(Float)                            # NULL when skipped (no edges)
    market_prob_at_detection = Column(Float)              # B's market_prob
    raw_edge = Column(Float)                              # B's raw_edge
    adjusted_edge = Column(Float)                         # B's adjusted_edge

    # Error signals (NULL when skipped)
    brier_score = Column(Float)                           # (model_prob - settlement_implied_prob)^2
    signed_error = Column(Float)                          # model_prob - settlement_implied_prob
    direction_correct = Column(Integer)                   # 1/0: did the edge direction match market resolution?

    # Status
    status = Column(String, nullable=False, default="processed")  # "processed" | "skipped_no_links" | "skipped_no_snapshot" | "skipped_no_edges"
    skip_reason = Column(String)                          # human-readable skip reason

    # Metadata
    match_finished_at = Column(DateTime, nullable=False)
    processed_at = Column(DateTime, nullable=False)
```

### `kernel_market_calibrations` (new table, parallel to `KernelCalibration`)

```python
class KernelMarketCalibration(KernelBase):
    """Market-settlement-based calibration per (engine, competition).

    Parallel to KernelCalibration (which uses match-outcome-based learning).
    Fitted by linear regression: settlement_implied_prob ~ slope * model_prob + intercept.
    """
    __tablename__ = "kernel_market_calibrations"
    __table_args__ = (
        UniqueConstraint("engine", "competition", name="uq_market_calibration_engine_competition"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    engine = Column(String(50), nullable=False)
    competition = Column(String(50), nullable=False)

    # Linear regression: settlement_implied_prob ~ slope * model_prob + intercept
    slope = Column(Float, nullable=False, default=1.0)
    intercept = Column(Float, nullable=False, default=0.0)

    # Aggregated metrics over the calibration window
    sample_count = Column(Integer, nullable=False, default=0)
    avg_brier = Column(Float, nullable=False, default=0.0)
    avg_signed_error = Column(Float, nullable=False, default=0.0)
    direction_accuracy = Column(Float, nullable=False, default=0.0)  # % of direction_correct=1

    last_updated = Column(DateTime, nullable=False)
```

### Vocabulary: D vs Phase 3 vs B/C

| Concept | Phase 3 | Subproject B | Subproject C | Subproject D |
|---------|---------|--------------|--------------|--------------|
| Ground truth | `KernelMatchOutcome.outcome` (binary) | N/A | N/A | `settlement_implied_prob` (continuous 0-1) |
| Error | `brier_score` (binary label), `score_mae` | `raw_edge`, `adjusted_edge` | N/A | `brier_score` (continuous label), `signed_error` |
| Calibration | `KernelCalibration.slope/intercept` | `trust` (reads KernelCalibration) | `qualified` (reads KernelCalibration.sample_count) | `kernel_market_calibrations.slope/intercept` (new) |
| Direction | N/A | `raw_edge` sign | `direction` (YES/NO/WAIT/AVOID) | `direction_correct` (did edge sign match market resolution?) |

## Service Interface

### `MarketSettlementService` (new)

**File**: `backend/app/kernel/market_settlement_service.py`

```python
@dataclass(frozen=True)
class SettlementResult:
    """Result of processing a single match's settlement."""
    match_id: str
    status: str                # "processed" | "already_processed" | "skipped_no_links" | "skipped_no_snapshot" | "skipped_no_edges" | "skipped_not_finished"
    settlements_count: int     # number of settlement rows written (0 if skipped)
    skip_reason: str | None


@dataclass(frozen=True)
class ScanResult:
    """Result of a batch scan."""
    scanned: int               # matches checked
    processed: int             # matches with new settlement rows
    skipped: int               # matches skipped (no links/snapshot/edges)
    already_processed: int     # matches already had settlements
    errors: int                # matches that raised exceptions
    error_details: list[str]   # first N error messages


class MarketSettlementService:
    """Market settlement feedback service.

    Reads B's persisted edges + A's market snapshots + Phase 3's match outcomes,
    computes market-settlement-based error signals, and writes to
    kernel_market_settlements + kernel_market_calibrations.

    Stateless computation, but DOES write to its own tables (unlike C which is
    fully stateless). This is necessary because settlement processing is a
    one-time event per match — the result must be persisted for querying.
    """

    def __init__(self) -> None:
        from app.kernel.edge_store import EdgeStore
        self._edge_store = EdgeStore()
        self._store = MarketSettlementStore()

    def process_settlement(self, match_id: str) -> SettlementResult:
        """Process a single match's market settlement.

        Idempotent: if already processed, returns status="already_processed".
        """

    def scan_and_process(self, limit: int = 50) -> ScanResult:
        """Scan for finished matches without settlements, process them in batch."""

    def get_settlement(self, match_id: str) -> list[dict[str, Any]]:
        """Get all settlement records for a match. Empty list if none."""

    def get_calibrations(
        self, engine: str | None = None, competition: str | None = None
    ) -> list[dict[str, Any]]:
        """Get market calibrations, optionally filtered."""

    def get_history(
        self, limit: int = 20, engine: str | None = None
    ) -> list[dict[str, Any]]:
        """Get recent settlements (most recent first)."""
```

### `MarketSettlementStore` (new)

**File**: `backend/app/kernel/market_settlement_store.py`

```python
class MarketSettlementStore:
    """Persistence for kernel_market_settlements + kernel_market_calibrations."""

    def append_settlement(self, *, match_id, mapped_outcome, engine, competition,
                          settlement_implied_prob, settlement_captured_at, link_id,
                          model_prob, market_prob_at_detection, raw_edge, adjusted_edge,
                          brier_score, signed_error, direction_correct,
                          status, skip_reason, match_finished_at, processed_at) -> dict:
        """Insert a settlement row. Idempotent via unique constraint."""

    def get_settlement(self, match_id: str) -> list[dict]:
        """All settlement rows for a match."""

    def get_settlements_for_calibration(
        self, engine: str, competition: str, limit: int
    ) -> list[dict]:
        """Recent processed settlements for (engine, competition), most recent first.
        Only returns rows where status='processed' and brier_score IS NOT NULL."""

    def upsert_calibration(self, *, engine, competition, slope, intercept,
                           sample_count, avg_brier, avg_signed_error,
                           direction_accuracy, last_updated) -> dict:
        """Upsert a market calibration row."""

    def get_calibrations(self, engine: str | None, competition: str | None) -> list[dict]:
        """List calibrations, optionally filtered."""

    def get_history(self, limit: int, engine: str | None) -> list[dict]:
        """Recent settlements, most recent first."""

    def get_processed_match_ids(self) -> set[str]:
        """Set of match_ids that already have settlement rows (for scan dedup)."""
```

### Error computation (pure functions)

```python
def _compute_brier(model_prob: float, settlement_implied_prob: float) -> float:
    """Brier-style score: (model_prob - settlement_implied_prob)^2."""
    return round((model_prob - settlement_implied_prob) ** 2, 6)


def _compute_signed_error(model_prob: float, settlement_implied_prob: float) -> float:
    """Signed error: model_prob - settlement_implied_prob."""
    return round(model_prob - settlement_implied_prob, 6)


def _compute_direction_correct(raw_edge: float, market_prob: float,
                                settlement_implied_prob: float) -> int:
    """Did the edge direction match the market resolution?

    Edge direction: sign(raw_edge) — model thinks outcome is more (>0) or less (<0)
    likely than the market.
    Market resolution direction: sign(settlement_implied_prob - market_prob) —
    the market moved up (>0) or down (<0) from detection to settlement.
    Correct if both signs match.
    """
    edge_sign = 1 if raw_edge > 0 else (-1 if raw_edge < 0 else 0)
    market_sign = 1 if (settlement_implied_prob - market_prob) > 0 else (
        -1 if (settlement_implied_prob - market_prob) < 0 else 0
    )
    return 1 if edge_sign == market_sign and edge_sign != 0 else 0
```

### Calibration update (linear regression, parallel to Phase 3's `update_calibration`)

```python
def _update_market_calibration(
    store: MarketSettlementStore, engine: str, competition: str
) -> None:
    """Fit linear regression on recent settlements and upsert calibration.

    x = model_prob, y = settlement_implied_prob
    slope clamped to [0.0, 2.0], intercept clamped to [-0.5, 0.5]
    (same bounds as Phase 3's update_calibration for consistency).
    """
    settlements = store.get_settlements_for_calibration(
        engine, competition, limit=config.settings.MARKET_CALIBRATION_WINDOW_SIZE
    )
    if len(settlements) < config.settings.MIN_SAMPLES_FOR_MARKET_CALIBRATION:
        return  # not enough samples

    xs = [s["model_prob"] for s in settlements]
    ys = [s["settlement_implied_prob"] for s in settlements]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    slope = num / den if den != 0 else 1.0
    intercept = mean_y - slope * mean_x

    slope = max(_CALIBRATION_SLOPE_MIN, min(_CALIBRATION_SLOPE_MAX, slope))
    intercept = max(_CALIBRATION_INTERCEPT_MIN, min(_CALIBRATION_INTERCEPT_MAX, intercept))

    avg_brier = sum(s["brier_score"] for s in settlements) / n
    avg_signed_error = sum(s["signed_error"] for s in settlements) / n
    direction_accuracy = sum(s["direction_correct"] for s in settlements) / n

    store.upsert_calibration(
        engine=engine, competition=competition, slope=round(slope, 4),
        intercept=round(intercept, 4), sample_count=n,
        avg_brier=round(avg_brier, 6), avg_signed_error=round(avg_signed_error, 6),
        direction_accuracy=round(direction_accuracy, 4), last_updated=datetime.now(timezone.utc),
    )
```

### Settlement detection logic

```python
def _find_settlement_snapshot(link_id: int, finished_at: datetime) -> dict | None:
    """Find the last market snapshot before the match finished.

    Queries kernel_market_snapshots directly (read-only, does not modify
    Subproject A's MarketSnapshotStore).
    """
    session = get_kernel_session()
    try:
        row = (
            session.query(KernelMarketSnapshot)
            .filter(
                KernelMarketSnapshot.link_id == link_id,
                KernelMarketSnapshot.captured_at <= finished_at,
            )
            .order_by(KernelMarketSnapshot.captured_at.desc())
            .first()
        )
        return _snapshot_to_dict(row) if row is not None else None
    except Exception:
        return None
    finally:
        session.close()


def _find_verified_link_for_outcome(match_id: str, mapped_outcome: str) -> dict | None:
    """Find the best verified market link for a match's outcome.

    Picks the link with highest link_confidence among verified links.
    """
    session = get_kernel_session()
    try:
        row = (
            session.query(KernelSportMarketLink)
            .filter(
                KernelSportMarketLink.match_id == match_id,
                KernelSportMarketLink.mapped_outcome == mapped_outcome,
                KernelSportMarketLink.verified == 1,
            )
            .order_by(KernelSportMarketLink.link_confidence.desc())
            .first()
        )
        return _link_to_dict(row) if row is not None else None
    except Exception:
        return None
    finally:
        session.close()
```

## Scheduler Job

**File**: `backend/app/core/scheduler.py` (additive — add new job function + registration)

```python
async def _job_process_market_settlements():
    """Scan for finished matches without settlements, process them."""
    if not settings.PHASE7_MARKET_SETTLEMENT_SCHEDULER_ENABLED:
        return
    from app.kernel.market_settlement_service import MarketSettlementService
    try:
        svc = MarketSettlementService()
        result = svc.scan_and_process(limit=settings.MARKET_SETTLEMENT_BATCH_LIMIT)
        logger.info(
            f"[Scheduler] Market settlements: scanned={result.scanned} "
            f"processed={result.processed} skipped={result.skipped} "
            f"already={result.already_processed} errors={result.errors}"
        )
    except Exception as exc:
        logger.error(f"[Scheduler] Market settlement job failed: {exc}")
```

Registration (additive, after B's job registration):

```python
if settings.PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED:
    scheduler.add_job(
        _job_process_market_settlements,
        IntervalTrigger(minutes=settings.MARKET_SETTLEMENT_INTERVAL_MIN),
        id="market_settlement_feedback",
        replace_existing=True,
        max_instances=1,
    )
```

## API Endpoints

**New route file**: `backend/app/api/routes/sport_settlements.py`

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/sport-settlements/{match_id}` | GET | — | Single match settlement records |
| `/api/sport-settlements/calibrations` | GET | — | Market calibration list (filter by engine/competition) |
| `/api/sport-settlements/history` | GET | — | Settlement history (paginated, filter by engine) |
| `/api/sport-settlements/process/{match_id}` | POST | `require_write_key` | Manual trigger for single match |

### Endpoint specifications

```python
router = APIRouter(prefix="/sport-settlements", tags=["Sport Settlements"])

def _ensure_enabled() -> None:
    """Raise 503 when PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED is false."""
    if not config.settings.PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED:
        raise HTTPException(status_code=503, detail="Market settlement feedback is disabled.")

@router.get("/calibrations")
def get_calibrations(
    engine: str | None = Query(None),
    competition: str | None = Query(None),
) -> dict:
    """Market calibration list. Static path before /{match_id} to avoid route conflict."""
    _ensure_enabled()
    svc = _service()
    items = svc.get_calibrations(engine=engine, competition=competition)
    return {"items": items, "total": len(items)}

@router.get("/history")
def get_history(
    limit: int = Query(20, ge=1, le=100),
    engine: str | None = Query(None),
) -> dict:
    """Settlement history. Static path before /{match_id}."""
    _ensure_enabled()
    svc = _service()
    items = svc.get_history(limit=limit, engine=engine)
    return {"items": items, "total": len(items)}

@router.get("/{match_id}")
def get_settlement(match_id: str) -> dict:
    """Single match settlement. Returns 404 when no settlements exist."""
    _ensure_enabled()
    svc = _service()
    items = svc.get_settlement(match_id)
    if not items:
        raise HTTPException(status_code=404, detail="No settlements found for match.")
    return {"match_id": match_id, "items": items, "total": len(items)}

@router.post("/process/{match_id}")
def process_settlement(
    match_id: str, _auth: None = Depends(require_write_key)
) -> dict:
    """Manually trigger settlement processing for a match."""
    _ensure_enabled()
    svc = _service()
    result = svc.process_settlement(match_id)
    return {"match_id": match_id, "status": result.status, "settlements_count": result.settlements_count}
```

**Constraints**:
- All 4 endpoints gated by `PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED`; return 503 when false.
- 3 GET endpoints are read-only (no `require_write_key`).
- 1 POST endpoint (`/process/{match_id}`) requires `require_write_key` (consistent with `POST /api/predictions/outcomes/{match_id}/process`).
- Route order: static paths (`/calibrations`, `/history`) before dynamic `/{match_id}` (lesson from Subproject C's route-ordering bug).
- `/{match_id}` returns 404 when no settlements exist for the match.

## Frontend

**New route**: `/sports/settlements`

**New components** (in `frontend/src/components/sports/settlements/`):
1. `SettlementHistoryTable.tsx` — table of recent settlements (match_id, outcome, model_prob, settlement_prob, brier, direction_correct)
2. `MarketCalibrationPanel.tsx` — market calibration cards (engine, competition, slope, intercept, sample_count, avg_brier, direction_accuracy)

**Navigation entry**: `app-nav.tsx` inserts `体育结算 → /sports/settlements` after `/sports/recommendations`, before `/sports/learning`.

**API client**: `frontend/src/lib/sport-settlements-api.ts` (independent file for职责分离).

### Component specifications

**`SettlementHistoryTable.tsx`**:
- Columns: match_id, engine, competition, outcome, model_prob, settlement_prob, brier_score, direction_correct (✓/✗), processed_at
- Filter by engine (dropdown)
- Empty state ("暂无结算记录")
- Error state
- Loading state

**`MarketCalibrationPanel.tsx`**:
- Cards grouped by engine, showing: competition, slope, intercept, sample_count, avg_brier, direction_accuracy
- Visual indicator: slope near 1.0 = well-calibrated; slope far from 1.0 = miscalibrated
- Empty state ("暂无市场校准数据")

## Configuration

**New items in `config.py`** (added before `settings = Settings()`):

```python
# Phase 7 Subproject D — Market Settlement Feedback (default OFF).
# When false, all /api/sport-settlements/* endpoints return 503 and the
# scheduler job is not registered.
PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED: bool = _env_bool(
    "PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED", "false"
)
PHASE7_MARKET_SETTLEMENT_SCHEDULER_ENABLED: bool = _env_bool(
    "PHASE7_MARKET_SETTLEMENT_SCHEDULER_ENABLED", "false"
)
MARKET_SETTLEMENT_INTERVAL_MIN: int = int(
    os.getenv("MARKET_SETTLEMENT_INTERVAL_MIN", "10")
)
MARKET_SETTLEMENT_BATCH_LIMIT: int = int(
    os.getenv("MARKET_SETTLEMENT_BATCH_LIMIT", "50")
)
MIN_SAMPLES_FOR_MARKET_CALIBRATION: int = int(
    os.getenv("MIN_SAMPLES_FOR_MARKET_CALIBRATION", "10")
)
MARKET_CALIBRATION_WINDOW_SIZE: int = int(
    os.getenv("MARKET_CALIBRATION_WINDOW_SIZE", "30")
)
```

All defaults to OFF / conservative values to maintain backward compatibility.

## CLI

**New file**: `backend/scripts/sport_settlement_cli.py`

Follows `sport_edge_cli.py` / `sport_recommendation_cli.py` pattern:

```bash
python -m scripts.sport_settlement_cli process --match-id ID
python -m scripts.sport_settlement_cli scan [--limit N]
python -m scripts.sport_settlement_cli calibrations [--engine E] [--competition C]
python -m scripts.sport_settlement_cli history [--limit N] [--engine E]
```

## Integration Points

### Zero-invasion modules (NOT modified in Subproject D)

- `backend/app/kernel/edge_detector_service.py` (Subproject B)
- `backend/app/kernel/edge_store.py` (Subproject B)
- `backend/app/api/routes/sport_edges.py` (Subproject B)
- `backend/app/kernel/sport_recommendation_service.py` (Subproject C)
- `backend/app/api/routes/sport_recommendations.py` (Subproject C)
- `backend/app/kernel/sport_market_link_store.py` (Subproject A)
- `backend/app/kernel/market_snapshot_store.py` (Subproject A)
- `backend/app/api/routes/sport_markets.py` (Subproject A)
- `backend/app/kernel/learning_service.py` (Phase 3)
- `backend/app/kernel/prediction_kernel.py` (Phase 3)
- `backend/app/services/diagnosis_service.py`
- `backend/app/services/decision_quality_service.py`
- `backend/app/models/event.py` (`ActionableRecommendation`)
- 3 learning tables (`KernelPredictionHistory`, `KernelCalibration`, `KernelEngineScore`) — zero structural modifications
- Learning dashboard components
- `polymarket_event_source` / `kalshi_event_source`

### Modules D reads from (read-only)

- `EdgeStore.get_latest_edges(match_id)` — B's persisted edges per match (public API)
- `KernelMatchOutcome` table (via `get_kernel_session`) — for `finished_at` and `outcome`
- `KernelSportMarketLink` table (via `get_kernel_session`) — for verified links
- `KernelMarketSnapshot` table (via `get_kernel_session`) — for settlement price proxy
- `KernelPrediction` table (via `get_latest_prediction`) — for engine/competition metadata

### New module manifest

```
backend/app/
├── api/routes/sport_settlements.py              # 4 endpoints (3 GET + 1 POST)
└── kernel/
    ├── market_settlement_service.py              # settlement computation + calibration
    └── market_settlement_store.py                # persistence for 2 new tables

backend/scripts/
└── sport_settlement_cli.py                       # CLI tool

frontend/src/
├── app/sports/settlements/
│   └── page.tsx                                   # settlements page
├── components/sports/settlements/
│   ├── SettlementHistoryTable.tsx
│   └── MarketCalibrationPanel.tsx
└── lib/sport-settlements-api.ts

backend/tests/
├── test_market_settlement_service.py              # ~25 tests
├── test_market_settlement_routes.py               # ~10 tests
└── test_market_settlement_cli.py                  # ~4 tests

frontend/src/components/sports/settlements/
├── SettlementHistoryTable.test.tsx                # ~4 tests
└── MarketCalibrationPanel.test.tsx                # ~4 tests
```

### Modified files (additive only)

- `backend/app/core/config.py` — add 6 config flags
- `backend/app/kernel/kernel_db.py` — append 2 new table class definitions (`KernelMarketSettlement`, `KernelMarketCalibration`)
- `backend/app/api/router.py` — add `sport_settlements` router registration
- `backend/app/core/scheduler.py` — add `_job_process_market_settlements` function + registration
- `frontend/src/components/app-nav.tsx` — add nav entry

## Testing Strategy

### Backend TDD (strict RED → GREEN)

| Test file | Coverage | Key scenarios |
|-----------|----------|---------------|
| `test_market_settlement_service.py` | `MarketSettlementService` + pure helpers | `_compute_brier`, `_compute_signed_error`, `_compute_direction_correct`, `_update_market_calibration` (regression fitting), `process_settlement` (happy path: finished match + verified link + snapshot + edge → settlement row + calibration update), idempotency (already processed), skip cases (no links, no snapshot, no edges, not finished), `scan_and_process` (batch), `get_settlement`, `get_calibrations`, `get_history` |
| `test_market_settlement_routes.py` | API endpoints | 503 when disabled, 404 when no settlements, GET /{match_id}, GET /calibrations, GET /history, POST /process/{match_id} (write-key gated), route ordering (static before dynamic) |
| `test_market_settlement_cli.py` | CLI tool | `process`, `scan`, `calibrations`, `history` subcommands |

**Test constraints**:
- Backend DB tests use `tmp_path` real SQLite, no mocks (inherit B/C test pattern)
- Edge data seeded via `EdgeStore.append_edge` (B's public API)
- Market link data seeded via `sport_market_link_store` (A's public API)
- Market snapshot data seeded via `market_snapshot_store` (A's public API)
- Match outcome data seeded via direct `KernelMatchOutcome` insert
- Prediction data seeded via `KernelPrediction` insert
- Calibration regression tested with known datasets (verify slope/intercept within tolerance)

### Frontend tests

- `SettlementHistoryTable.test.tsx`: table rendering, empty state, error state, engine filter
- `MarketCalibrationPanel.test.tsx`: card rendering, calibration metrics display, empty state
- `next/link` mock (inherit `trades/page.test.tsx` pattern)

**Estimate**: ~39 backend tests + ~8 frontend tests = ~47 new tests

## Phase Boundaries

### In scope (Subproject D)

- `kernel_market_settlements` table + `kernel_market_calibrations` table
- `MarketSettlementService` + `MarketSettlementStore`
- 4 API endpoints (3 GET + 1 POST)
- Scheduler job `_job_process_market_settlements`
- Frontend settlements page + 2 components
- CLI tool
- Config flags (5 new)

### Out of scope (deferred)

- Feeding D's calibration back into B's `trust` computation → future integration
- Querying Polymarket's real resolution API → future enhancement
- LLM-enhanced settlement analysis → future option
- Cross-engine calibration comparison dashboard → future enhancement
- Real-time settlement push (WebSocket) → current design uses scheduler + on-demand API

## Success Criteria

1. **Functional completeness**:
   - `process_settlement(match_id)` generates settlement records for finished matches with verified links + snapshots + edges
   - `scan_and_process(limit)` batches processes multiple matches
   - Calibration regression correctly computes slope/intercept/avg_brier/direction_accuracy
   - Idempotency: re-processing a match returns `status="already_processed"` without writing duplicates

2. **Zero regression**:
   - Subproject A tests pass with zero modifications
   - Subproject B tests pass with zero modifications
   - Subproject C tests pass with zero modifications
   - Phase 3 learning loop tests pass with zero modifications
   - `diagnosis_service` / `decision_quality_service` tests pass with zero modifications

3. **Safety gating**:
   - `PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED=false` → all 4 endpoints return 503
   - Scheduler job not registered when `PHASE7_MARKET_SETTLEMENT_SCHEDULER_ENABLED=false`
   - D writes only to its own 2 tables; never modifies A/B/C/Phase 3 data

4. **Operability**:
   - CLI can process single match, scan batch, query calibrations, query history
   - Frontend page renders settlement history + market calibration panel
   - Scheduler runs at configured interval, processes new finished matches

## Estimate

- **New files**: 5 backend + 4 frontend + 5 tests + 1 CLI = 15 files
- **Modified files**: 5 (config / kernel_db / router / scheduler / app-nav)
- **Code volume**: +~2,200 lines
- **Tests**: ~47 new tests
