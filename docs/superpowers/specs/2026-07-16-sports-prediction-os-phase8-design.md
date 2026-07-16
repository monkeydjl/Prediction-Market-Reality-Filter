# Sports Prediction OS — Phase 8 Design

**Date:** 2026-07-16
**Status:** Design
**Author:** TRAE Agent
**Predecessor:** Phase 7 (Subprojects A/B/C/D — complete)

---

## 1. Goal

Complete the Phase 7 data pipeline by implementing two placeholder scheduler jobs
(`_job_capture_market_snapshots` and `_job_fetch_traditional_odds`), then fuse
Subproject D's market calibration signal into Subproject B's trust computation
to form a closed "predict → align → recommend → settle → calibrate → improve" loop.

**Success metric:** Phase 7's A→B→C→D chain runs end-to-end with real data; B's
`adjusted_edge` incorporates both Phase 3 match-outcome calibration and Phase 7
D market-settlement calibration, advancing the project toward 72–75% accuracy.

---

## 2. Background

### 2.1 Phase 7 left two placeholder scheduler jobs

| Job | State | Impact |
|-----|-------|--------|
| `_job_capture_market_snapshots` | **STUB** — instantiates `SportMarketBridgeService()` but calls no method | `kernel_market_snapshots` stays empty → Subproject D's settlement proxy has no data |
| `_job_fetch_traditional_odds` | **STUB** — only `init_kernel_db()`, no odds fetch | Multi-league traditional sportsbook path produces zero data |

Without these jobs, the entire Phase 7 investment produces no real calibration data.

### 2.2 Subproject D's calibration is isolated

D writes `kernel_market_calibrations` (per engine+competition, linear regression
fit) but B's `_compute_trust` only reads Phase 3's `KernelCalibration`. The
denser, more timely market-settlement signal is wasted.

### 2.3 Config redundancy

`PHASE7_SPORT_MARKET_SNAPSHOT_INTERVAL_SECONDS` (seconds) and
`MARKET_SNAPSHOT_INTERVAL_MIN` (minutes) configure the same concept.

---

## 3. Non-Goals

- **Do NOT** modify `PredictionKernel`, `LearningService`,
  `MarketSettlementService`, `SportRecommendationService`, or `domain.py`.
- **Do NOT** feed D's calibration back into D itself (D remains a parallel
  channel).
- **Do NOT** implement real-time WebSocket price push (deferred to future phase).
- **Do NOT** implement futures/championship markets (deferred to future phase).
- **Do NOT** modify existing `kernel_market_snapshots` table structure.
- **Do NOT** add new frontend pages (only 1 new component + 1 tab on existing page).

---

## 4. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         Phase 8 — Two Subprojects                            │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Subproject A: Pipeline Completion                                           │
│  ────────────────────────────────                                            │
│                                                                              │
│   Polymarket           _job_capture_market        kernel_market_snapshots    │
│   API ───────────────► _snapshots() ─────────────► (existing table)         │
│                                                                          │   │
│   The Odds API        _job_fetch_traditional       kernel_traditional_      │   │
│   (all sports) ──────► _odds() ──────────────────► odds_snapshots (NEW)    │   │
│                                                                          │   │
│                                      ↓                                    │   │
│           EdgeDetectorService._aggregate_market_prob                      │   │
│           (reads both snapshot tables)                                   │   │
│                                      ↓                                    │   │
│                          kernel_sport_edges                              │   │
│                                                                          │   │
│  Subproject B: Calibration Fusion                                           │
│  ────────────────────────────────                                            │
│                                                                          │   │
│   kernel_calibration      kernel_market_calibrations                    │   │
│   (Phase 3) ─────────┐   ─────────────────────────┐                    │   │
│                       ↓                            ↓                    │   │
│                  CalibrationFusionService.compute_trust()               │   │
│                  (weighted by sample_count)                              │   │
│                       ↓                                                   │   │
│                 composite_trust ──► EdgeDetectorService._compute_trust  │   │
│                                      ↓                                    │   │
│                          adjusted_edge (B)                               │   │
│                                                                          │   │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 4.1 Data Flow

1. **Subproject A (pipeline completion):**
   - `_job_capture_market_snapshots` (every `MARKET_SNAPSHOT_INTERVAL_MIN`):
     iterates all verified links in `kernel_sport_market_links`, fetches the
     current Polymarket price for each `contract_id`, appends to
     `kernel_market_snapshots`.
   - `_job_fetch_traditional_odds` (every `ODDS_FETCH_INTERVAL_MIN`): calls
     The Odds API `GET /sports` to dynamically discover all available sport
     keys, fetches odds for each, matches to `match_id` via team-name
     normalization, converts decimal odds to implied probabilities, appends to
     `kernel_traditional_odds_snapshots`.

2. **Subproject B (calibration fusion):**
   - `CalibrationFusionService.compute_trust(engine, competition)` reads both
     `KernelCalibration` (Phase 3) and `KernelMarketCalibration` (D), computes
     a weighted composite trust.
   - `EdgeDetectorService._compute_trust` delegates to the fusion service when
     `PHASE8_CALIBRATION_FUSION_ENABLED=true`; otherwise falls back to the
     Phase 7 Phase-3-only path (zero-invasion).

### 4.2 Zero-Invasion Boundaries

| Component | Phase 8 action |
|-----------|---------------|
| `PredictionKernel` | **No change** |
| `LearningService` (Phase 3) | **No change** |
| `MarketSettlementService` (D) | **No change** |
| `SportRecommendationService` (C) | **No change** |
| `domain.py` | **No change** |
| `scheduler.py` | **Modify** — fill 2 stub job bodies |
| `edge_detector_service.py` | **Modify** — `_compute_trust` delegates to fusion service |
| `config.py` | **Modify** — remove redundant config, add `PHASE8_*` flag |
| `kernel_db.py` | **Modify** — append 1 new table class |
| `.env.example` | **Modify** — document all `PHASE7_*` and `PHASE8_*` settings |

---

## 5. Data Model

### 5.1 New Table: `kernel_traditional_odds_snapshots`

Traditional sportsbook odds snapshots. Separate from
`kernel_market_snapshots` because the field semantics differ (no `link_id`,
`liquidity`, `volume`; has `decimal_odds`, `bookmaker`).

```python
class KernelTraditionalOddsSnapshot(KernelBase):
    __tablename__ = "kernel_traditional_odds_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "match_id", "mapped_outcome", "captured_at",
            name="uq_traditional_odds_match_outcome_time"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String, nullable=False, index=True)
    mapped_outcome = Column(String, nullable=False)     # home_win / draw / away_win
    competition = Column(String, nullable=False)        # wc/ucl/epl/nba/mlb/nhl...
    implied_prob = Column(Float, nullable=False)        # converted from decimal odds
    decimal_odds = Column(Float, nullable=False)        # original decimal odds
    bookmaker = Column(String, nullable=True)           # "pinnacle" / "average"
    bookmakers_count = Column(Integer, default=0)       # number of bookmakers aggregated
    captured_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
```

**Design decisions:**
- **Separate table** (not merging into `kernel_market_snapshots`): Polymarket
  snapshots have `link_id`, `liquidity`, `volume`; traditional odds have
  `decimal_odds`, `bookmaker`. Merging would create many nullable columns and
  confuse downstream consumers.
- **No `link_id`**: Traditional odds bypass the three-layer matching engine;
  they are matched directly by team name to `match_id`.
- **Unique constraint** `(match_id, mapped_outcome, captured_at)`: prevents
  duplicate snapshots from scheduler retries.

### 5.2 Existing Tables (unchanged)

| Table | Role in Phase 8 |
|-------|-----------------|
| `kernel_market_snapshots` | A's `_job_capture_market_snapshots` writes here |
| `kernel_sport_edges` | B reads two calibration tables to compute `trust` |
| `kernel_market_calibrations` | B's `CalibrationFusionService` reads |
| `kernel_calibration` | B's `CalibrationFusionService` reads |

---

## 6. Configuration Changes

### 6.1 Remove (config redundancy)

```python
# DELETE this line from config.py:
PHASE7_SPORT_MARKET_SNAPSHOT_INTERVAL_SECONDS: int = int(
    os.getenv("PHASE7_SPORT_MARKET_SNAPSHOT_INTERVAL_SECONDS", "300")
)
```

### 6.2 Modify (minute-level unification)

Rename `ODDS_API_FETCH_INTERVAL_HOURS` to `ODDS_FETCH_INTERVAL_MIN` and change
unit from hours to minutes. This requires updating **3 references**:
- `config.py:1064-1065` — definition (rename + change default from `"6"` to `"10"`)
- `scheduler.py:877` — `IntervalTrigger(hours=settings.ODDS_API_FETCH_INTERVAL_HOURS)`
  → `IntervalTrigger(minutes=settings.ODDS_FETCH_INTERVAL_MIN)`

```python
# In config.py (replace ODDS_API_FETCH_INTERVAL_HOURS):
ODDS_FETCH_INTERVAL_MIN: int = int(os.getenv("ODDS_FETCH_INTERVAL_MIN", "10"))

# In scheduler.py (change hours= to minutes=):
IntervalTrigger(minutes=settings.ODDS_FETCH_INTERVAL_MIN),

# Existing, unchanged:
MARKET_SNAPSHOT_INTERVAL_MIN: int = int(os.getenv("MARKET_SNAPSHOT_INTERVAL_MIN", "1"))
```

### 6.3 Add (Phase 8 feature flag)

```python
PHASE8_CALIBRATION_FUSION_ENABLED: bool = _env_bool(
    "PHASE8_CALIBRATION_FUSION_ENABLED", "false"
)
```

### 6.4 `.env.example` Documentation

All `PHASE7_*` and `PHASE8_*` settings must be documented in `.env.example`
with comments explaining each flag's purpose and default.

---

## 7. Service Interfaces

### 7.1 `TraditionalOddsStore` (new file: `backend/app/kernel/traditional_odds_store.py`)

```python
class TraditionalOddsStore:
    """CRUD for kernel_traditional_odds_snapshots table."""

    def append_snapshot(
        self,
        match_id: str,
        mapped_outcome: str,
        competition: str,
        implied_prob: float,
        decimal_odds: float,
        bookmaker: str | None = None,
        bookmakers_count: int = 0,
        captured_at: datetime,
    ) -> dict:
        """Insert a snapshot. Returns the inserted row as dict.
        Idempotent via unique constraint (match_id, mapped_outcome, captured_at)."""

    def get_latest_snapshot(
        self, match_id: str, mapped_outcome: str | None = None
    ) -> dict | None:
        """Get the most recent snapshot for a match (optionally filtered by outcome)."""

    def get_snapshots(
        self, match_id: str, mapped_outcome: str | None = None
    ) -> list[dict]:
        """Get all snapshots for a match (optionally filtered by outcome), oldest first."""
```

### 7.2 `odds_api_service.py` Extension

New function for dynamic all-sports fetch:

```python
async def fetch_all_sports_odds() -> dict[str, list[dict]]:
    """Fetch odds for all available sports from The Odds API.

    1. GET /sports to discover all active sport keys.
    2. For each sport key, GET /sports/{key}/odds.
    3. Respect quota: stop early if x-requests-remaining <= 0.
    4. Skip sports with no upcoming fixtures.

    Returns:
        {sport_key: [fixture_dict, ...], ...}
        Each fixture_dict has: home_team, away_team, commence_time, bookmakers.
    """
```

### 7.3 `SportMarketBridgeService` Extension

New method to fetch current Polymarket price:

```python
async def fetch_current_price(self, contract_id: str) -> dict | None:
    """Fetch the current price and implied prob for a Polymarket contract.

    Returns:
        {"price": float, "implied_prob": float, "liquidity": float | None, "volume": float | None}
        None if the contract is unavailable or API error.
    """
```

### 7.4 `CalibrationFusionService` (new file: `backend/app/kernel/calibration_fusion_service.py`)

```python
@dataclass(frozen=True)
class CompositeTrust:
    trust: float                     # fused trust value
    phase3_trust: float              # Phase 3 calibration trust
    market_trust: float              # D market calibration trust
    phase3_weight: float             # w1
    market_weight: float             # w2
    phase3_sample_count: int
    market_sample_count: int
    source: str                      # "dormant" / "phase3_only" / "market_only" / "fusion"


class CalibrationFusionService:
    """Fuses Phase 3 and Phase 7 D calibration signals into a composite trust."""

    def compute_trust(self, engine: str, competition: str) -> CompositeTrust:
        """Compute composite trust by sample-count-weighted fusion.

        Rules:
        1. Both tables have no data → DIAGNOSIS_DORMANT_TRUST (0.5), source="dormant"
        2. Only Phase 3 has data (sample_count >= MIN) → phase3_trust, source="phase3_only"
        3. Only market has data (sample_count >= MIN) → market_trust, source="market_only"
        4. Both have data → weighted fusion, source="fusion"
           w1 = phase3_count / (phase3_count + market_count)
           w2 = market_count / (phase3_count + market_count)
           composite = w1 * phase3_trust + w2 * market_trust

        Trust computation per source:
        - phase3_trust = clamp(KernelCalibration.avg_accuracy, DIAGNOSIS_TRUST_FLOOR, 1.0)
          (dormant = 0.5 if sample_count < CALIBRATION_FEEDBACK_MIN_SAMPLES)
        - market_trust = clamp(KernelMarketCalibration.direction_accuracy,
          DIAGNOSIS_TRUST_FLOOR, 1.0)
          (dormant = 0.5 if sample_count < MIN_SAMPLES_FOR_MARKET_CALIBRATION)
        """
```

### 7.5 `EdgeDetectorService._compute_trust` Modification

```python
def _compute_trust(self, engine_name: str, competition: str) -> float:
    """Phase 8: delegates to CalibrationFusionService when enabled."""
    if not config.settings.PHASE8_CALIBRATION_FUSION_ENABLED:
        # Zero-invasion fallback: Phase 7 behavior unchanged
        return self._compute_trust_phase3(engine_name, competition)

    fusion = CalibrationFusionService()
    composite = fusion.compute_trust(engine_name, competition)
    return composite.trust

def _compute_trust_phase3(self, engine_name: str, competition: str) -> float:
    """Phase 7 behavior (extracted, unchanged)."""
    # ... original _compute_trust body ...
```

---

## 8. Scheduler Job Implementation

### 8.1 `_job_capture_market_snapshots` (complete implementation)

```python
async def _job_capture_market_snapshots():
    """Every MARKET_SNAPSHOT_INTERVAL_MIN: capture Polymarket price snapshots."""
    if not settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED:
        return
    run_id = _start_run("sport_market_snapshots")
    try:
        from app.kernel.kernel_db import init_kernel_db
        from app.kernel.sport_market_bridge_service import SportMarketBridgeService
        from app.kernel.sport_market_link_store import SportMarketLinkStore
        from app.kernel.market_snapshot_store import MarketSnapshotStore

        init_kernel_db()
        bridge = SportMarketBridgeService()
        link_store = SportMarketLinkStore()
        snap_store = MarketSnapshotStore()

        links = link_store.get_verified_links()
        captured = 0
        errors = 0
        for link in links:
            try:
                price = await bridge.fetch_current_price(link["contract_id"])
                if price is not None:
                    snap_store.append_snapshot(
                        link_id=link["id"],
                        implied_prob=price["implied_prob"],
                        price=price["price"],
                        liquidity=price.get("liquidity"),
                        volume=price.get("volume"),
                        captured_at=_utcnow(),
                    )
                    captured += 1
            except Exception as exc:
                errors += 1
                logger.warning(
                    f"Snapshot capture failed for link {link['id']}: {exc}"
                )

        _finish_run(run_id, "success", result={
            "links_total": len(links),
            "captured": captured,
            "errors": errors,
        })
    except Exception as exc:
        logger.exception("[Scheduler] Market snapshot capture failed")
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
```

### 8.2 `_job_fetch_traditional_odds` (complete implementation)

```python
async def _job_fetch_traditional_odds():
    """Every ODDS_FETCH_INTERVAL_MIN: fetch traditional sportsbook odds."""
    if not settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED:
        return
    if not settings.ODDS_API_ENABLED:
        return
    run_id = _start_run("sport_market_odds_fetch")
    try:
        from app.kernel.kernel_db import init_kernel_db
        from app.kernel.traditional_odds_store import TraditionalOddsStore
        from app.kernel.sport_market_link_store import SportMarketLinkStore
        from app.services.odds_api_service import fetch_all_sports_odds

        init_kernel_db()
        odds_store = TraditionalOddsStore()
        link_store = SportMarketLinkStore()

        # Get all matches with verified links (need odds)
        matches = link_store.get_matches_with_verified_links()
        if not matches:
            _finish_run(run_id, "success", result={"matches_total": 0, "captured": 0})
            return

        # Fetch all available sports odds from The Odds API
        all_odds = await fetch_all_sports_odds()

        # Match odds to matches and store
        captured = 0
        errors = 0
        for match_id in matches:
            try:
                odds_list = _match_odds_to_match(match_id, all_odds)
                if odds_list:
                    now = _utcnow()
                    competition = match_id.split("-")[0]
                    for outcome, implied_prob, decimal_odds, bookmaker, book_count in odds_list:
                        odds_store.append_snapshot(
                            match_id=match_id,
                            mapped_outcome=outcome,
                            competition=competition,
                            implied_prob=implied_prob,
                            decimal_odds=decimal_odds,
                            bookmaker=bookmaker,
                            bookmakers_count=book_count,
                            captured_at=now,
                        )
                        captured += 1
            except Exception as exc:
                errors += 1
                logger.warning(f"Odds fetch failed for {match_id}: {exc}")

        _finish_run(run_id, "success", result={
            "matches_total": len(matches),
            "captured": captured,
            "errors": errors,
        })
    except Exception as exc:
        logger.exception("[Scheduler] Traditional odds fetch failed")
        _finish_run(run_id, "failed", error=str(exc), exc=exc)


def _match_odds_to_match(
    match_id: str, all_odds: dict[str, list[dict]]
) -> list[tuple[str, float, float, str, int]] | None:
    """Match The Odds API fixtures to a match_id.

    Parses match_id to extract competition, date, and team tokens, then
    finds the matching fixture in all_odds by team-name normalization.

    Returns:
        [(mapped_outcome, implied_prob, decimal_odds, bookmaker, bookmakers_count), ...]
        None if no match found.
    """
```

---

## 9. API Endpoints

Two new GET endpoints (read-only) in a new route file
`backend/app/api/routes/sport_odds.py`:

| Endpoint | Description | Gating |
|----------|-------------|--------|
| `GET /api/sport-odds/{match_id}/latest` | Latest traditional odds snapshot for the match | `PHASE7_SPORT_MARKET_BRIDGE_ENABLED` (503 if false) |
| `GET /api/sport-odds/{match_id}/history` | Historical traditional odds time-series | Same |

**Route order**: static paths before dynamic (lesson from Phase 7 Subproject C).
No POST endpoints — all data is written only by the scheduler.

**Response shape (`/latest`):**
```json
{
  "match_id": "nba-2026-g1",
  "outcomes": [
    {
      "mapped_outcome": "home_win",
      "implied_prob": 0.65,
      "decimal_odds": 1.538,
      "bookmaker": "pinnacle",
      "bookmakers_count": 12,
      "captured_at": "2026-07-16T12:00:00+00:00"
    }
  ],
  "skipped": false,
  "skip_reason": null
}
```

---

## 10. Frontend

### 10.1 New Component: `TraditionalOddsChart.tsx`

**Location:** `frontend/src/components/sports/markets/TraditionalOddsChart.tsx`

**Purpose:** Display a line chart comparing traditional sportsbook implied
probabilities vs Polymarket implied probabilities over time for a given match.

**Props:**
```typescript
interface TraditionalOddsChartProps {
  matchId: string;
}
```

**Data sources:**
- `GET /api/sport-odds/{match_id}/history` (traditional odds)
- `GET /api/sport-markets/{match_id}/snapshots` (Polymarket, existing)

**Integration:** Added as a new tab on the existing match detail page
(`frontend/src/app/sports/markets/[match_id]/page.tsx`).

### 10.2 New API Client: `sport-odds-api.ts`

**Location:** `frontend/src/lib/sport-odds-api.ts`

Following the Phase 6 pattern (`learning-api.ts`) and Phase 7 pattern
(`sport-settlements-api.ts`), separate from existing `sports-api.ts` for
职责分离.

---

## 11. Testing Strategy

| Layer | Tests | Coverage |
|-------|-------|----------|
| **Pure functions** | ~8 | `fetch_all_sports_odds` (mocked httpx), `_match_odds_to_match` (team normalization), `CompositeTrust` computation (4 source cases: dormant/phase3_only/market_only/fusion), weight calculation |
| **DB-integrated** | ~6 | `TraditionalOddsStore` (append, get_latest, get_snapshots, idempotency), `CalibrationFusionService` (two-table read, sample_count thresholds) |
| **Scheduler** | ~4 | 2 jobs × (success path + gating/disable path) |
| **API routes** | ~4 | 2 endpoints × (200 with data + 503 when disabled) |
| **E2E integration** | ~2 | Full A→B(fusion)→D flow with both calibration tables populated |
| **Total** | **~24** | |

### 11.1 TDD Approach

Strict TDD for all backend DB functions (RED → GREEN):
1. Write failing test
2. Verify failure
3. Implement minimal code
4. Verify pass
5. Commit

### 11.2 Regression

All existing tests (183+ after Phase 7 + Minor cleanup + E2E) must pass
with zero modifications. The only exception: tests that directly assert
the Phase 7 `_compute_trust` behavior may need a flag-set check — but
since `PHASE8_CALIBRATION_FUSION_ENABLED` defaults to OFF, even those
should pass unchanged.

---

## 12. Phase Boundaries

### 12.1 Subproject A: Pipeline Completion (independent, no B dependency)

**Deliverables:**
- `TraditionalOddsStore` + `KernelTraditionalOddsSnapshot` table
- `fetch_all_sports_odds()` in `odds_api_service.py`
- `fetch_current_price()` in `SportMarketBridgeService`
- 2 scheduler job implementations
- 2 API endpoints
- `TraditionalOddsChart` frontend component
- `.env.example` documentation
- Config cleanup (remove redundancy)

### 12.2 Subproject B: Calibration Fusion (independent, no A dependency)

**Deliverables:**
- `CalibrationFusionService`
- `EdgeDetectorService._compute_trust` modification (with fallback)
- `PHASE8_CALIBRATION_FUSION_ENABLED` feature flag

**Note:** B does not depend on A's data. B reads the existing
`kernel_market_calibrations` table (populated by D), which is already
functional. The two subprojects can be developed in parallel.

---

## 13. Integration Points

| From | To | Interface |
|------|----|-----------|
| A `_job_capture_market_snapshots` | `kernel_market_snapshots` | `MarketSnapshotStore.append_snapshot()` |
| A `_job_fetch_traditional_odds` | `kernel_traditional_odds_snapshots` | `TraditionalOddsStore.append_snapshot()` |
| B `CalibrationFusionService` | `kernel_calibration` | `get_calibration(engine, competition)` |
| B `CalibrationFusionService` | `kernel_market_calibrations` | `MarketSettlementStore.get_calibrations(engine, competition)` |
| B `EdgeDetectorService._compute_trust` | `CalibrationFusionService` | `compute_trust(engine, competition)` |
| A/B `EdgeDetectorService._aggregate_market_prob` | Both snapshot tables | Read from both (existing for Polymarket, new for traditional) |

---

## 14. Success Criteria

1. `_job_capture_market_snapshots` writes to `kernel_market_snapshots` when
   Polymarket data is available (verified by scheduler test with mocked API).
2. `_job_fetch_traditional_odds` writes to `kernel_traditional_odds_snapshots`
   when The Odds API is enabled (verified by scheduler test with mocked API).
3. `PHASE8_CALIBRATION_FUSION_ENABLED=true` produces a composite trust that
   differs from Phase 3-only trust when both calibration tables have data.
4. `PHASE8_CALIBRATION_FUSION_ENABLED=false` (default) keeps all existing
   behavior byte-identical (all 183+ tests pass with zero modifications).
5. All `PHASE7_*` and `PHASE8_*` settings are documented in `.env.example`.
6. Config redundancy (`PHASE7_SPORT_MARKET_SNAPSHOT_INTERVAL_SECONDS`) is removed.

---

## 15. Estimate

| Subproject | Tasks | Files | Tests | Effort |
|------------|-------|-------|-------|--------|
| A: Pipeline Completion | 4 | ~12 | ~16 | Medium |
| B: Calibration Fusion | 2 | ~4 | ~8 | Small-Medium |
| **Total** | **6** | **~16** | **~24** | **Medium** |

---

## 16. Out of Scope (Future Phase)

- WebSocket real-time price push
- Futures/championship markets (season-level)
- Kalshi sports markets
- Real Polymarket resolution API (D continues to use snapshot proxy)
- D → D self-feedback (D's calibration feeding back into D's own computation)
- Guardrail P1 state-based rules (`daily_llm_cost_cap`, etc.)
- WorldCupAdapter `fetch_team_data`/`fetch_player_data` stubs
