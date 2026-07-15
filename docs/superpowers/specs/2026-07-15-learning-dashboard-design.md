# Learning Dashboard Design — Sports Prediction OS (Sub-project A)

**Date:** 2026-07-15
**Status:** Approved (pending user spec review)
**Phase:** Frontend Integration Iteration 2 — Sub-project A (Closed-Loop Learning Dashboard)

---

## 1. Goal & Scope

### Goal

Build a closed-loop learning dashboard at `/sports/learning` that visualizes the Phase 3 learning loop outcomes (outcome → error → calibration → weight), letting users answer three core questions in one place:

1. How is the model performing across sports/engines?
2. How did a specific match's prediction evolve over time?
3. Is the model well-calibrated?

### Scope (4 views, Hybrid Tab architecture — "Approach C")

- **Engine Performance Comparison** (Tab 1): engine/competition/sport free-filter, display accuracy / brier_score / confidence_calibration / sample_count
- **Prediction History List** (Tab 2): global prediction records, filterable by sport/competition, paginated
- **Single-Match Prediction Trajectory** (standalone route `/sports/learning/history/[matchId]`): probability/weight change trajectory across multiple predictions for one match
- **Calibration Diagnostics** (Tab 3): parameter table (slope/intercept/avg_confidence/avg_accuracy) + binned reliability chart (predicted prob vs actual frequency)

### Backend

5 new GET endpoints (read-only, reuse existing 3 learning tables + 1 new on-the-fly binning query).

### Frontend

1 new page `/sports/learning` (3-tab) + 1 sub-route `/sports/learning/history/[matchId]` + 6 generic components (reuse recharts + chart-lite).

### Out of Scope

- Weight update UI (weight adjustment is backend auto-EWMA, not exposed)
- Outcome entry UI (already exists via `POST /outcomes/{id}/process`, not duplicated)
- World Cup–specific analytics (remains exclusively in `/world-cup/`)
- Real-time refresh / polling (manual refresh button only — learning data is low-frequency)
- Data export (CSV/JSON download)
- Date range filter (pagination + sport/competition filter is sufficient)
- Weight visualization (weights live in FactorRegistry table, not learning tables — "engine internal state", not "learning outcome")
- Same-match cross-engine comparison (Kernel engines dispatch by sport, no same-match multi-engine)

---

## 2. Architecture Overview

### 2.1 Frontend Route Structure

```
frontend/src/app/sports/learning/
├── page.tsx                          # 3-tab dashboard ("use client")
├── loading.tsx                       # Skeleton (server component)
└── history/
    └── [matchId]/
        ├── page.tsx                  # Single-match trajectory ("use client")
        └── loading.tsx
```

`/sports/learning` switches 3 tabs via local state, synced with URL query `?tab=performance|history|calibration` (default `performance`) for shareability. Each tab lazy-loads its own data. Single-match trajectory is a standalone route (drilled down from history list Tab 2).

### 2.2 Frontend Component Structure

```
frontend/src/components/sports/learning/
├── learning-tabs.tsx                 # Tab container, manages active tab + refresh button
├── engine-performance-panel.tsx      # Tab 1: performance comparison (filters + metrics table)
├── prediction-history-list.tsx       # Tab 2: history list (filters + table + pagination)
├── prediction-trajectory.tsx         # Single-match trajectory (prob/weight charts) — standalone route
├── calibration-panel.tsx             # Tab 3: parameter table + binned reliability chart
└── reliability-chart.tsx             # Binned reliability chart (recharts scatter + diagonal ref line)
```

All new components live under `components/sports/learning/` subdirectory, isolated from MVP's `components/sports/`.

### 2.3 Backend Endpoint Structure

5 new GET endpoints, all added to existing `backend/app/api/routes/predictions.py`:

| Endpoint | Data Source | Purpose |
|----------|-------------|---------|
| `GET /api/predictions/engines/scores` | `KernelEngineScore` | Tab 1 performance comparison (with `?engine=&competition=&sport=` filters) |
| `GET /api/predictions/history` | `KernelPredictionHistory` + LEFT JOIN `KernelMatchOutcome` | Tab 2 history list (paginated + `?sport=&competition=&limit=&offset=`) |
| `GET /api/predictions/history/{match_id}` | `KernelPredictionHistory` (by match_id) | Single-match trajectory (multiple prediction records) |
| `GET /api/predictions/calibration` | `KernelCalibration` | Tab 3 parameter table (`?engine=&competition=`) |
| `GET /api/predictions/calibration/reliability` | on-the-fly aggregate `KernelPrediction` + `KernelMatchOutcome` | Tab 3 binned reliability chart (`?engine=&competition=&bins=10`) |

### 2.4 Data Flow

```
Tab 1 Performance:
  /sports/learning (Tab 1) → GET /engines/scores?engine=&competition=
    → query KernelEngineScore → return metrics list

Tab 2 History List:
  /sports/learning (Tab 2) → GET /history?sport=&limit=&offset=
    → KernelPredictionHistory LEFT JOIN KernelMatchOutcome → paginated list

Single-Match Trajectory (drill-down):
  /sports/learning/history/[matchId] → GET /history/{matchId}
    → KernelPredictionHistory WHERE match_id → time-sorted trajectory

Tab 3 Calibration:
  /sports/learning (Tab 3) → parallel GET /calibration + GET /calibration/reliability
    → KernelCalibration parameters + on-the-fly binning aggregate
```

### 2.5 Key Design Principles

1. **Read-only endpoints:** All 5 endpoints are GET, modify no data, trigger no learning loop.
2. **Reuse existing data:** No new backend tables — all based on Phase 3's existing 3 learning tables.
3. **On-the-fly binning:** Reliability chart does not pre-store binned data; aggregates on each request (data volume is bounded — learning data is low-frequency).
4. **Frontend component isolation:** New components in `learning/` subdirectory, do not touch MVP's `components/sports/`.
5. **Reuse chart infrastructure:** Use existing recharts + `@/components/ui/chart-lite` (`ChartFrame`/`DarkTooltip`), consistent with world-cup analytics-dashboard.

### 2.6 Relationship to MVP

- Navigation: `app-nav.tsx` gains 1 new entry `学习仪表盘 → /sports/learning` (after "体育预测", before "世界杯")
- Does not modify `/sports/` list page or `/sports/[matchId]/` detail page
- Creates new `learning-api.ts` (not extending `sports-api.ts` — separation of concerns)

---

## 3. Backend API Design

5 new GET endpoints in `backend/app/api/routes/predictions.py`; 5 new query functions in `backend/app/kernel/kernel_db.py`.

### 3.1 `GET /api/predictions/engines/scores` — Engine Performance Comparison

**Query params:** `engine?`, `competition?`, `sport?` (all optional, combinable)

**Response:**
```json
[
  {
    "engine": "basketball",
    "competition": "nba",
    "accuracy": 0.625,
    "avg_mae": 3.2,
    "brier_score": 0.21,
    "sample_count": 48,
    "confidence_calibration": 0.94,
    "last_updated": "2026-07-14T18:30:00Z"
  }
]
```

**Query logic:** Query `KernelEngineScore` table. `sport` filter implemented via `COMPETITION_SPORT` reverse-lookup (sport → competition list). `competition=NULL` global rows returned only when no `sport` filter.

**New DB function:** `get_engine_scores(engine=None, competition=None, sport=None) -> list[KernelEngineScore]`

### 3.2 `GET /api/predictions/history` — Prediction History List

**Query params:** `sport?`, `competition?`, `limit` (default 50, max 200), `offset` (default 0)

**Response:**
```json
{
  "items": [
    {
      "id": 123,
      "match_id": "nba-20250101-LAL-BOS",
      "sport": "basketball",
      "competition": "nba",
      "engine": "basketball",
      "predicted_scores": {"home": 112.5, "away": 108.3},
      "outcome_probabilities": {"home_win": 0.62, "away_win": 0.38},
      "confidence": 0.59,
      "feature_version": "nba-1.0",
      "trigger": "initial",
      "created_at": "2026-07-14T18:30:00Z",
      "outcome": {
        "home_score": 113,
        "away_score": 107,
        "outcome": "home_win",
        "outcome_correct": 1,
        "score_mae": 2.5,
        "brier_score": 0.19,
        "finished_at": "2026-07-15T02:00:00Z"
      }
    }
  ],
  "total": 156,
  "limit": 50,
  "offset": 0
}
```

`outcome` is `null` when match not finished. `outcome_correct` is `null` when outcome recorded but error not computed. `sport`/`competition` come from the JOIN to `KernelPrediction` (already required for filtering).

**Query logic:** Query `KernelPredictionHistory`, LEFT JOIN `KernelMatchOutcome` (by `match_id`), LEFT JOIN `KernelPrediction` (by `match_id`) for sport/competition. `sport`/`competition` filters apply on the `KernelPrediction` join. Order by `created_at` DESC.

**New DB function:** `get_prediction_history(sport=None, competition=None, limit=50, offset=0) -> tuple[list[dict], int]` (returns items + total)

### 3.3 `GET /api/predictions/history/{match_id}` — Single-Match Trajectory

**Path param:** `match_id`

**Response:**
```json
{
  "match_id": "nba-20250101-LAL-BOS",
  "sport": "basketball",
  "competition": "nba",
  "items": [
    {
      "id": 123,
      "engine": "basketball",
      "predicted_scores": {"home": 112.5, "away": 108.3},
      "outcome_probabilities": {"home_win": 0.62, "away_win": 0.38},
      "confidence": 0.59,
      "feature_version": "nba-1.0",
      "trigger": "initial",
      "created_at": "2026-07-14T18:30:00Z"
    },
    {
      "id": 456,
      "engine": "basketball",
      "predicted_scores": {"home": 114.0, "away": 106.0},
      "outcome_probabilities": {"home_win": 0.68, "away_win": 0.32},
      "confidence": 0.64,
      "feature_version": "nba-1.0",
      "trigger": "weight_update",
      "created_at": "2026-07-14T20:00:00Z"
    }
  ],
  "count": 2
}
```

Top-level `sport`/`competition` come from `KernelPrediction` (LEFT JOIN by match_id). These are `null` if no `KernelPrediction` row exists for this match_id (match exists in fixtures but never predicted). Team names are NOT included — `KernelPrediction` table does not store them (team names live in `RawMatchData.match.home.name` which is not persisted to DB). The trajectory page header shows sport icon + match_id only. Fetching team names would require an additional adapter call (`get_match_identity`), out of scope for a read-only history endpoint.

**Query logic:** Query `KernelPredictionHistory` WHERE `match_id = ?`, order by `created_at` ASC (time trajectory). LEFT JOIN `KernelPrediction` (by `match_id`) for sport/competition. Returns empty list (NOT 404) when match_id has no history — match may exist but have zero predictions.

**New DB function:** `get_prediction_history_by_match(match_id: str) -> list[KernelPredictionHistory]`

### 3.4 `GET /api/predictions/calibration` — Calibration Parameter Table

**Query params:** `engine?`, `competition?`

**Response:**
```json
[
  {
    "engine": "basketball",
    "competition": "nba",
    "slope": 0.85,
    "intercept": 0.05,
    "sample_count": 48,
    "avg_confidence": 0.62,
    "avg_accuracy": 0.625,
    "last_updated": "2026-07-14T18:30:00Z"
  }
]
```

**Query logic:** Query `KernelCalibration` table, filter by `engine`/`competition`. Returns all records when no filter.

**New DB function:** `get_calibrations(engine=None, competition=None) -> list[KernelCalibration]`

### 3.5 `GET /api/predictions/calibration/reliability` — Binned Reliability Chart

**Query params:** `engine?`, `competition?`, `bins` (default 10, range 5-20)

**Response:**
```json
{
  "engine": "basketball",
  "competition": "nba",
  "bins": [
    {
      "lower": 0.0,
      "upper": 0.1,
      "center": 0.05,
      "avg_predicted": 0.07,
      "actual_frequency": 0.10,
      "count": 3
    },
    {
      "lower": 0.5,
      "upper": 0.6,
      "center": 0.55,
      "avg_predicted": 0.58,
      "actual_frequency": 0.55,
      "count": 12
    }
  ],
  "total_samples": 48
}
```

**Binning algorithm (core logic):**

1. Query `KernelPrediction` JOIN `KernelMatchOutcome` (WHERE `outcome_correct IS NOT NULL`), filter by `engine`/`competition`
2. For each record: take **max of outcome_probabilities** as `predicted_prob`, `outcome_correct` (0/1) as `actual`
3. Divide `[0, 1]` into N equal bins; each record falls into the bin matching its `predicted_prob`
4. Per bin: `avg_predicted` (mean of predicted_prob in bin), `actual_frequency` (mean of actual in bin), `count`
5. Empty bins (count=0) returned with `avg_predicted=null` and `actual_frequency=null`

**Why max probability:** The prediction's "confidence" is inherently the max probability — model says "home_win 62%", bin by 0.62, check if actual home_win frequency matches. Standard reliability chart approach.

**New DB function:** `compute_reliability_bins(engine=None, competition=None, bins=10) -> dict`

### 3.6 Shared Constant

`COMPETITION_SPORT` mapping at top of `predictions.py`, for 3.1 and 3.2 `sport` filter:

```python
COMPETITION_SPORT = {
    "wc": "football", "ucl": "football", "epl": "football",
    "laliga": "football", "bundesliga": "football",
    "seriea": "football", "ligue1": "football",
    "nba": "basketball", "mlb": "baseball", "nhl": "hockey",
}
```

### 3.7 No Changes Required

- `KernelPredictionHistory`, `KernelCalibration`, `KernelEngineScore` tables — zero modification
- `PredictionKernel`, `LearningService` — zero modification
- `KernelPrediction` table — zero modification (reliability query reads it but does not alter structure)
- Existing endpoints (`/matches`, `/matches/{id}`, `/predict`, `/engines/{name}/score`) — zero modification
- `/world-cup/` routes — zero modification

### 3.8 Error Handling

- `KERNEL_PREDICTION_ENABLED=false` → all 5 endpoints return 503 (consistent with existing endpoints)
- `bins` out of 5-20 range → 422 (FastAPI parameter validation)
- Other errors → 500 + log (consistent with existing pattern)

---

## 4. Frontend Component Design

### 4.1 API Client Module

**New `frontend/src/lib/learning-api.ts`** (parallel to `sports-api.ts`, separation of concerns)

Rationale: learning dashboard endpoints differ in concern from MVP prediction endpoints (read-only analytics vs read/write prediction). Separate file avoids bloat. Reuses `getWorldCupApiBase()` for base URL.

```typescript
// Type definitions (single source of truth)
export interface EngineScoreItem {
  engine: string;
  competition: string | null;
  accuracy: number;
  avg_mae: number;
  brier_score: number;
  sample_count: number;
  confidence_calibration: number;
  last_updated: string | null;
}

export interface PredictionHistoryItem {
  id: number;
  match_id: string;
  sport: string | null;
  competition: string | null;
  engine: string;
  predicted_scores: Record<string, number>;
  outcome_probabilities: Record<string, number>;
  confidence: number;
  feature_version: string;
  trigger: string;
  created_at: string;
  outcome: {
    home_score: number;
    away_score: number;
    outcome: string;
    outcome_correct: number | null;
    score_mae: number | null;
    brier_score: number | null;
    finished_at: string | null;
  } | null;
}

export interface PredictionHistoryList {
  items: PredictionHistoryItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface PredictionTrajectory {
  match_id: string;
  sport: string | null;
  competition: string | null;
  items: PredictionHistoryItem[];  // ASC by created_at
  count: number;
}

export interface CalibrationItem {
  engine: string;
  competition: string;
  slope: number;
  intercept: number;
  sample_count: number;
  avg_confidence: number;
  avg_accuracy: number;
  last_updated: string | null;
}

export interface ReliabilityBin {
  lower: number;
  upper: number;
  center: number;
  avg_predicted: number | null;
  actual_frequency: number | null;
  count: number;
}

export interface ReliabilityData {
  engine: string | null;
  competition: string | null;
  bins: ReliabilityBin[];
  total_samples: number;
}

// fetch functions
export async function fetchEngineScores(params?: {
  engine?: string; competition?: string; sport?: string;
}): Promise<EngineScoreItem[]>
export async function fetchPredictionHistory(params?: {
  sport?: string; competition?: string; limit?: number; offset?: number;
}): Promise<PredictionHistoryList>
export async function fetchPredictionTrajectory(matchId: string): Promise<PredictionTrajectory>
export async function fetchCalibration(params?: {
  engine?: string; competition?: string;
}): Promise<CalibrationItem[]>
export async function fetchReliability(params?: {
  engine?: string; competition?: string; bins?: number;
}): Promise<ReliabilityData>
```

### 4.2 Component Inventory & Responsibilities

**`learning-tabs.tsx`** — Tab Container
- Manages 3 tabs' active state (local state, URL unchanged unless `?tab=` sync)
- Header: title "闭环学习仪表盘" + global refresh button (reloads current tab data)
- Lazy-loads corresponding panel on tab switch
- Each panel independently manages loading/error/data state

**`engine-performance-panel.tsx`** — Tab 1 Performance Comparison
- 3 filters: engine dropdown, competition dropdown, sport dropdown (all optional "全部")
- Data table: row=engine+competition, columns=accuracy/avg_mae/brier_score/confidence_calibration/sample_count/last_updated
- accuracy/brier color-coded (accuracy high=green, brier low=green)
- Empty state: "暂无性能数据，等待比赛结果录入"

**`prediction-history-list.tsx`** — Tab 2 History List
- 2 filters: sport dropdown, competition dropdown
- Pagination controls (limit=50, prev/next + current/total page)
- Table columns: time, match_id (truncated), engine, sport icon, predicted prob (max%), confidence, result (✓/✗/—), score_mae
- Click row → `Link` to `/sports/learning/history/[matchId]`
- `outcome=null` → result shows "—"; `outcome_correct=null` → shows "待算"

**`prediction-trajectory.tsx`** — Single-Match Trajectory (standalone route)
- Page header: sport icon + match_id (team names not available — see Section 3.3 note)
- Trajectory chart (recharts LineChart): X=created_at, Y=probability, one Line per outcome (e.g. home_win/away_win)
- Confidence chart (recharts LineChart): same X, Y=confidence
- Prediction detail table: per-prediction predicted_scores / feature_version / trigger
- Back link → `/sports/learning` (Tab 2)
- Empty state: "该比赛暂无历史预测记录"

**`calibration-panel.tsx`** — Tab 3 Calibration Diagnostics
- 2 filters: engine dropdown, competition dropdown
- Upper: parameter table (engine/competition/slope/intercept/sample_count/avg_confidence/avg_accuracy/last_updated)
- Lower: `ReliabilityChart` binned reliability chart
- Empty state: "暂无校准数据，需 ≥ MIN_SAMPLES_FOR_CALIBRATION 条记录"

**`reliability-chart.tsx`** — Binned Reliability Chart
- Reuses `ChartFrame` container + `DarkTooltip`
- recharts `<Scatter>`: X=avg_predicted, Y=actual_frequency
- Diagonal reference line (perfect calibration y=x) via recharts `<ReferenceLine>` segment
- Empty bins (null values) skipped
- Hover shows bin range + count
- X/Y axis range fixed [0, 1]

### 4.3 Page Files

**`app/sports/learning/page.tsx`** ("use client")
- Renders `<LearningTabs />`
- Manages tab state (optional URL query `?tab=` sync, default `performance`)

**`app/sports/learning/loading.tsx`** (server component)
- Skeleton, reuses MVP loading.tsx style

**`app/sports/learning/history/[matchId]/page.tsx`** ("use client")
- `useParams()` gets matchId
- Renders `<PredictionTrajectory matchId={matchId} />`
- Handles loading/error/empty states + "返回列表" link

**`app/sports/learning/history/[matchId]/loading.tsx`** (server component)

### 4.4 Navigation Modification

**`components/app-nav.tsx`** — insert after `/sports` entry, before `/world-cup`:
```typescript
{ href: "/sports/learning", label: "学习仪表盘", icon: GraduationCap, match: ["/sports/learning"] },
```
`GraduationCap` imported from `lucide-react` — semantics match "learning".

### 4.5 Dynamic Rendering Principle (inherited from MVP)

- `PredictionTrajectory` trajectory chart uses `Object.keys(items[0].outcome_probabilities)` to dynamically render each Line — no hardcoded `home_win`/`away_win`
- `PredictionHistoryList` probability column shows max probability value (`Math.max(...Object.values(probs))`) — adapts to any sport
- `EnginePerformancePanel` table columns fixed (metric names fixed), rows data-driven

### 4.6 No Changes Required

- MVP's `components/sports/` 5 components — zero modification
- `sports-api.ts` — zero modification
- `app/sports/page.tsx`, `app/sports/[matchId]/page.tsx` — zero modification
- All world-cup components — zero modification

---

## 5. Data Flow

### 5.1 Tab 1 Engine Performance

```
User enters /sports/learning (default Tab 1)
  → LearningTabs renders EnginePerformancePanel
    → useEffect triggers fetchEngineScores() (no filter, returns all)
    → GET /api/predictions/engines/scores
      → get_engine_scores(engine=None, competition=None, sport=None)
        → SELECT * FROM kernel_engine_scores
        → COMPETITION_SPORT mapping enriches sport for each row

User selects filter "sport=basketball"
  → EnginePerformancePanel re-calls fetchEngineScores({ sport: "basketball" })
  → GET /api/predictions/engines/scores?sport=basketball
    → get_engine_scores(sport="basketball")
      → COMPETITION_SPORT reverse-lookup: sport=basketball → competitions=["nba"]
      → SELECT * FROM kernel_engine_scores WHERE competition IN ("nba")
```

Note: `KernelEngineScore` has no `sport` column; `sport` filter implemented via `COMPETITION_SPORT` reverse-lookup to competition list.

### 5.2 Tab 2 Prediction History List

```
User switches to Tab 2
  → PredictionHistoryList renders
    → fetchPredictionHistory({ limit: 50, offset: 0 })
    → GET /api/predictions/history?limit=50&offset=0
      → get_prediction_history(limit=50, offset=0)
        → SELECT h.*, o.home_score, o.away_score, o.outcome, o.outcome_correct,
                 o.score_mae, o.brier_score, o.finished_at
          FROM kernel_prediction_history h
          LEFT JOIN kernel_match_outcomes o ON h.match_id = o.match_id
          ORDER BY h.created_at DESC
          LIMIT 50 OFFSET 0
        → SELECT COUNT(*) for total
      → returns { items, total, limit, offset }

User clicks "next page"
  → fetchPredictionHistory({ limit: 50, offset: 50 })
  → same as above, OFFSET 50

User selects "sport=basketball" filter
  → fetchPredictionHistory({ sport: "basketball", limit: 50, offset: 0 })
  → GET /api/predictions/history?sport=basketball&limit=50&offset=0
    → sport="basketball" → COMPETITION_SPORT reverse-lookup → ["nba"]
    → WHERE p.competition IN ("nba")
      (additional JOIN kernel_predictions p ON h.match_id = p.match_id for competition)
```

Note: `KernelPredictionHistory` has no sport/competition column; sport/competition filter requires JOIN to `KernelPrediction`. If a match_id doesn't exist in `KernelPrediction` (theoretically shouldn't happen — record_prediction writes KernelPrediction before history), that history record is filtered out during sport/competition filter — acceptable edge case.

### 5.3 Single-Match Trajectory (drill-down)

```
User clicks a row in Tab 2 (match_id="nba-xxx")
  → Link to /sports/learning/history/nba-xxx
    → app/sports/learning/history/[matchId]/page.tsx renders
      → useParams() gets matchId
      → fetchPredictionTrajectory("nba-xxx")
      → GET /api/predictions/history/nba-xxx
        → get_prediction_history_by_match("nba-xxx")
          → SELECT * FROM kernel_prediction_history
            WHERE match_id = "nba-xxx"
            ORDER BY created_at ASC  -- ascending, time trajectory
      → returns { match_id, items, count }

    → PredictionTrajectory renders:
      - Trajectory chart: Object.keys(items[0].outcome_probabilities) → ["home_win","away_win"]
        → one Line per key (recharts LineChart, X=created_at, Y=probability)
      - Confidence chart: single Line (X=created_at, Y=confidence)
      - Detail table: iterate items showing each prediction's full info

User clicks "返回列表"
  → Link back to /sports/learning (URL can carry ?tab=history to return to Tab 2)
```

### 5.4 Tab 3 Calibration

```
User switches to Tab 3
  → CalibrationPanel renders
    → parallel triggers two requests:
      [1] fetchCalibration() → GET /api/predictions/calibration
        → get_calibrations()
          → SELECT * FROM kernel_calibration
      [2] fetchReliability({ bins: 10 }) → GET /api/predictions/calibration/reliability?bins=10
        → compute_reliability_bins(bins=10)
          → SELECT p.outcome_probabilities, o.outcome_correct
            FROM kernel_predictions p
            JOIN kernel_match_outcomes o ON p.match_id = o.match_id
            WHERE o.outcome_correct IS NOT NULL
          → per record: predicted_prob = max(outcome_probabilities.values())
                        actual = outcome_correct (0/1)
          → bin into 10 buckets, compute avg_predicted / actual_frequency / count per bin
    → both complete → render parameter table + ReliabilityChart

User selects filter "engine=basketball"
  → both requests re-triggered with engine=basketball parameter
  → [1] WHERE engine = "basketball"
  → [2] WHERE p.engine = "basketball"
```

Parallel requests: `Promise.allSettled([fetchCalibration(...), fetchReliability(...)])`. Independent — one failure doesn't affect the other (failed one shows error, successful one renders normally).

### 5.5 Global Refresh

```
User clicks top "刷新" button
  → LearningTabs calls active tab panel's reload method
  → implementation: key prop or useReducer to trigger panel re-mount
  → panel's useEffect re-triggers data fetch
```

### 5.6 Error State Handling

| Scenario | Behavior |
|----------|----------|
| `KERNEL_PREDICTION_ENABLED=false` | 503 → panel shows "Kernel 预测未启用" |
| Network error | panel shows error + retry button |
| Empty data (no predictions/outcomes) | panel shows empty state (see 4.2 per-component empty state text) |
| `bins` out of range | 422 → frontend never sends out-of-range value (dropdown fixed options 5/10/15/20) |

---

## 6. Testing Strategy

Follows MVP pattern: backend pytest (TDD RED→GREEN), frontend Vitest 4 (colocated test files, jsdom environment).

### 6.1 Backend Tests

**File:** `backend/tests/test_learning_endpoints.py` (new)

**5 DB function tests (direct kernel_db calls, tmp_path SQLite fixture):**

| Test Class | Test Cases | Covers |
|------------|-----------|--------|
| `TestGetEngineScores` | return all / filter by engine / filter by competition / filter by sport (reverse-lookup) / empty table returns empty list | `get_engine_scores` |
| `TestGetPredictionHistory` | pagination / total count / sport filter (JOIN KernelPrediction) / outcome=null (unfinished) / outcome_correct=null (uncomputed) | `get_prediction_history` |
| `TestGetPredictionHistoryByMatch` | returns ASC list / match_id nonexistent returns empty list (NOT 404) / multiple predictions time-sorted | `get_prediction_history_by_match` |
| `TestGetCalibrations` | return all / filter by engine / filter by competition / empty table returns empty list | `get_calibrations` |
| `TestComputeReliabilityBins` | 10 bins default / bins=5 / empty bins return null values / total_samples correct / filter by engine / no samples returns empty bins | `compute_reliability_bins` |

**5 endpoint tests (FastAPI TestClient, mock kernel_db functions):**

| Test Class | Test Cases | Covers |
|------------|-----------|--------|
| `TestEngineScoresEndpoint` | 200 returns list / sport filter param passthrough / 503 when disabled | `GET /engines/scores` |
| `TestHistoryListEndpoint` | 200 returns paginated structure / limit/offset passthrough / sport filter passthrough / 503 when disabled | `GET /history` |
| `TestHistoryByMatchEndpoint` | 200 returns trajectory / match_id nonexistent returns empty items (NOT 404) / 503 when disabled | `GET /history/{match_id}` |
| `TestCalibrationEndpoint` | 200 returns parameter list / engine filter / 503 when disabled | `GET /calibration` |
| `TestReliabilityEndpoint` | 200 returns binned data / bins param passthrough / bins out of range 422 / 503 when disabled | `GET /calibration/reliability` |

**Estimated:** ~30 backend test cases.

### 6.2 Frontend Tests

**`learning-api.test.ts`** (colocated in `lib/`)
- 5 fetch functions' URL construction and param concatenation
- Response parsing and error throwing (including 422, 503)
- Reuses MVP `sports-api.test.ts` mock fetch pattern

**`learning-tabs.test.tsx`**
- Renders 3 tab buttons
- Default active Tab 1
- Tab switch renders corresponding panel (mock panel components)

**`engine-performance-panel.test.tsx`**
- Renders filters (3 dropdowns)
- Renders data table (mock fetchEngineScores)
- Empty data shows hint
- Color coding verification (accuracy high=green class)

**`prediction-history-list.test.tsx`**
- Renders table rows (mock fetchPredictionHistory)
- Pagination controls (prev/next disabled state)
- outcome=null shows "—"
- outcome_correct=null shows "待算"
- Row click navigation (mock `next/link`, verify href contains match_id)
- Sport filter re-requests

**`prediction-trajectory.test.tsx`**
- Renders trajectory chart (mock recharts, verify Line count = outcome count)
- Renders confidence chart
- Renders detail table
- Empty data shows hint
- "返回列表" link present
- **mock `next/link`** (jsdom required, inherited Task 6 pattern)

**`calibration-panel.test.tsx`**
- Renders parameter table (mock fetchCalibration)
- Renders reliability chart (mock fetchReliability)
- Parallel request fault tolerance (fetchCalibration fails but fetchReliability succeeds → table shows error, chart renders normally)

**`reliability-chart.test.tsx`**
- Renders scatter (mock recharts Scatter, verify data point count = non-empty bin count)
- Empty bins skipped
- Diagonal reference line present

**Estimated:** ~35 frontend test cases.

### 6.3 Test Constraints

1. **TDD strict:** Backend DB functions write tests first (RED → ImportError), then implement (GREEN)
2. **Vitest jsdom mock pattern:** All component tests using `next/link` must mock `next/link` (inherited `trades/page.test.tsx:18-24` pattern)
3. **recharts mock:** recharts components don't render properly in jsdom (world-cup analytics-dashboard test has `width(0) height(0)` warning precedent); tests verify props passed to recharts components, not actual rendering
4. **No existing test modifications:** MVP's 23 frontend tests + 16 backend API tests zero modifications, regression passes
5. **tmp_path isolation:** Backend DB tests use independent `tmp_path` SQLite per case, teardown calls `close_kernel_session()` (inherited `test_kernel_db_fixtures.py` pattern)

### 6.4 Verification Checklist

- [ ] 30 new backend tests all pass
- [ ] 35 new frontend tests all pass
- [ ] MVP 16 backend API tests no regression
- [ ] MVP 23 frontend tests no regression
- [ ] `npx tsc --noEmit` typecheck passes
- [ ] Total ~173 + 65 = ~238 frontend tests pass

---

## 7. Constraints, Deliverables & Risks

### 7.1 Hard Constraints

1. **All 5 new endpoints must be GET** — read-only, trigger no learning loop, write no data
2. **3 learning tables zero modification** — `KernelPredictionHistory`, `KernelCalibration`, `KernelEngineScore` table structure and existing data zero modification
3. **`PredictionKernel`, `LearningService` zero modification** — frontend only reads data they write
4. **`KernelPrediction` table zero modification** — reliability chart aggregates it on-the-fly but does not alter structure
5. **`bins` parameter range 5-20, default 10** — out of range returns 422
6. **`COMPETITION_SPORT` mapping constant** defined at top of `predictions.py`, covers all 10 competitions (wc/ucl/epl/laliga/bundesliga/seriea/ligue1/nba/mlb/nhl)
7. **`KernelPredictionHistory` has no sport/competition column** — history list's sport/competition filter must JOIN `KernelPrediction` table
8. **`KernelEngineScore` has no sport column** — performance comparison's sport filter must reverse-lookup via `COMPETITION_SPORT` to competition list
9. **`get_prediction_history_by_match` returns empty list, NOT 404** — match_id nonexistent returns `{ items: [], count: 0 }`
10. **Frontend new components all under `components/sports/learning/` subdirectory** — isolated from MVP `components/sports/`
11. **New `learning-api.ts`, not extending `sports-api.ts`** — separation of concerns
12. **`getWorldCupApiBase()` returns without `/api` suffix** — fetch paths include `/api/` prefix (inherited MVP constraint)
13. **recharts + `@/components/ui/chart-lite` reuse** — no new chart library
14. **MVP 5 components + 2 pages zero modification** — `ProbabilityBar`/`FactorBreakdownTable`/`MatchListCard`/`SportFilter`/`MatchDetailPanel` + `app/sports/page.tsx` + `app/sports/[matchId]/page.tsx`
15. **`app-nav.tsx` only adds 1 entry** — insert `学习仪表盘 → /sports/learning` after `/sports`, before `/world-cup`; no other entries modified
16. **Dynamic rendering principle** — trajectory chart uses `Object.keys(probs)` for dynamic line count, no hardcoded sport-specific outcome names
17. **Vitest jsdom must mock `next/link`** — all component tests using `Link` inherit `trades/page.test.tsx:18-24` pattern
18. **TDD strict execution** — backend DB functions RED (ImportError) before GREEN
19. **`KERNEL_PREDICTION_ENABLED=false` returns 503 on all 5 endpoints** — consistent with existing endpoints
20. **`outcome_correct=null` (uncomputed)** frontend shows "待算"; `outcome=null` (unfinished) shows "—"
21. **Parallel request fault tolerance uses `Promise.allSettled`** — calibration tab's two requests independent, neither blocks the other
22. **`loading.tsx` is server component** (no `"use client"`); `page.tsx` with hooks is `"use client"`

### 7.2 Deliverables

**Backend (2 files modified + 1 file new):**
- `backend/app/kernel/kernel_db.py` — 5 new query functions (+~120 lines)
- `backend/app/api/routes/predictions.py` — `COMPETITION_SPORT` constant + 5 endpoints + helpers (+~180 lines)
- `backend/tests/test_learning_endpoints.py` — new (~300 lines, ~30 tests)

**Frontend (18 files new + 1 file modified):**
- `frontend/src/lib/learning-api.ts` — new (types + 5 fetch functions)
- `frontend/src/lib/learning-api.test.ts` — new
- `frontend/src/components/sports/learning/learning-tabs.tsx` — new
- `frontend/src/components/sports/learning/engine-performance-panel.tsx` — new
- `frontend/src/components/sports/learning/prediction-history-list.tsx` — new
- `frontend/src/components/sports/learning/prediction-trajectory.tsx` — new
- `frontend/src/components/sports/learning/calibration-panel.tsx` — new
- `frontend/src/components/sports/learning/reliability-chart.tsx` — new
- 6 `.test.tsx` files for the above 6 components — new
- `frontend/src/app/sports/learning/page.tsx` — new
- `frontend/src/app/sports/learning/loading.tsx` — new
- `frontend/src/app/sports/learning/history/[matchId]/page.tsx` — new
- `frontend/src/app/sports/learning/history/[matchId]/loading.tsx` — new
- `frontend/src/components/app-nav.tsx` — modified (1 new NAV entry + GraduationCap import)

**Documentation:**
- `docs/superpowers/specs/2026-07-15-learning-dashboard-design.md` — this design doc
- `docs/superpowers/plans/2026-07-15-learning-dashboard.md` — implementation plan (writing-plans phase)

**Total:** ~22 files changed (3 backend + 19 frontend), +~1600 lines, ~65 new tests

### 7.3 Risks & Mitigation

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| History list JOIN `KernelPrediction` for filter drops some history records (KernelPrediction overwritten on re-predict) | Low | Medium (few records unfilterable) | Constraint 7 documents this as acceptable edge — record_prediction writes KernelPrediction before history, theoretically no loss |
| Reliability on-the-fly aggregate slow on large data | Low | Low | Learning data is low-frequency (one prediction per match); volume bounded. Add LIMIT or cache if needed (YAGNI for now) |
| recharts jsdom test rendering warnings | Medium | Low (warnings only) | Test constraint 3: verify props not actual rendering, inherited analytics-dashboard precedent |
| `COMPETITION_SPORT` mapping misses future new competition | Low | Low | Mapping in prominent location; new competitions update simultaneously; sport filter with no match returns empty list (no error) |
| Calibration data sparse (< MIN_SAMPLES_FOR_CALIBRATION) → Tab 3 often empty | Medium | Low | Empty state text explicitly hints "需 ≥ N 条记录"; this is expected behavior, not a bug |

### 7.4 YAGNI — Things Not Done

- No data export (CSV/JSON download) — not MVP need
- No date range filter — pagination + sport/competition filter sufficient
- No auto-refresh/polling — manual refresh button suffices
- No weight visualization — weights in FactorRegistry table not learning tables, "engine internal state" not "learning outcome"
- No prediction comparison (same-match multiple predictions side-by-side) — trajectory chart already shows time evolution, comparison is over-design
- No cross-engine same-match comparison — Kernel engines dispatch by sport, no same-match multi-engine
