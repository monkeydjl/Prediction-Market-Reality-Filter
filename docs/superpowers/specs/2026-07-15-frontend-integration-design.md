# Frontend Integration Design — Sports Prediction OS

**Date:** 2026-07-15
**Status:** Approved (pending user spec review)
**Phase:** Frontend Integration (post-Phase 5)

---

## 1. Goal

Build a unified multi-sport frontend on top of the existing Next.js application, exposing the Kernel's cross-sport prediction capabilities (football + basketball + baseball + hockey) through a new `/sports/` route tree. The existing `/world-cup/` pages and components remain untouched and coexist via shared navigation.

**Scope (MVP):**
- Today's matches list across all sports (with sport filter)
- Single match prediction detail (probabilities + factor breakdown + confidence)
- Trigger prediction button

**Out of scope (future iterations):**
- Engine comparison, prediction history, calibration view
- Batch prediction, LLM analysis, data sync UI
- World Cup–specific structures (group tables, bracket, tournament simulation) — these remain exclusively in `/world-cup/`

---

## 2. Architecture Overview

### Strategy

Add a new `/sports/` route tree in the existing Next.js app as the multi-sport entry point. The old `/world-cup/` routes and components are fully preserved — two page sets coexist through top navigation.

### Frontend New Structure

```
frontend/src/
├── app/
│   ├── sports/                       # NEW
│   │   ├── page.tsx                  # Today's matches list (cross-sport, with filter)
│   │   ├── loading.tsx
│   │   └── [matchId]/
│   │       ├── page.tsx              # Single match prediction detail
│   │       └── loading.tsx
├── components/
│   └── sports/                       # NEW (all-new generic components, do not reuse world-cup/)
│       ├── match-list-card.tsx       # Single match card in the list
│       ├── match-detail-panel.tsx    # Detail main panel
│       ├── probability-bar.tsx       # Probability bar (dynamic binary/ternary)
│       ├── factor-breakdown-table.tsx  # Factor breakdown table (dynamic factor names/count)
│       └── sport-filter.tsx          # Sport filter
└── lib/
    └── sports-api.ts                 # NEW: Kernel /api/predictions/* client + TypeScript types
```

### Backend API Extension

Extend the existing `/api/predictions/*` router with 2 new endpoints (`predict` already exists):

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/predictions/matches` | GET | Today's matches list (cross-sport, optional `?sport=` filter) |
| `/api/predictions/matches/{match_id}` | GET | Single match detail + latest prediction (if any) |
| `/api/predictions/matches/{match_id}/predict` | POST | Trigger prediction (existing, reused with return-value fix) |

### Key Design Principles

1. **Generic components + dynamic rendering.** Components hardcode no sport assumptions. `ProbabilityBar` renders based on `Object.keys(outcome_probabilities)`. `FactorBreakdownTable` iterates the `explanation` array. Factor count and names are data-driven.
2. **Kernel-first.** All calls go to `/api/predictions/*` (Kernel routes), never `/api/world-cup/predictions/*`.
3. **Zero modification to old code.** `/world-cup/` pages, `world-cup-predictions.ts` client, `world-cup/` components — all untouched.
4. **Navigation coexistence.** `app-nav.tsx` gains one new entry `体育预测 → /sports`; existing entries preserved.

### Data Flow

```
User visits /sports/
  → sports-api.ts calls GET /api/predictions/matches
    → predictions.py list_matches endpoint
      → kernel._adapter.fetch_schedule(ScheduleFilter())   # no status filter
        → MultiAdapter iterates all registered adapters, merges RawMatchData list
      → API layer filters to today by kickoff_utc date
      → API layer optionally filters by ?sport=
  → Frontend renders MatchListCard list

User clicks a match → /sports/[matchId]/
  → sports-api.ts calls GET /api/predictions/matches/{matchId}
    → returns MatchIdentity + latest PredictionResult (if any)
  → User clicks "Predict" button → POST /api/predictions/matches/{matchId}/predict
    → returns full PredictionResult (with feature_version + prediction_timestamp)
  → Frontend renders ProbabilityBar + FactorBreakdownTable
```

---

## 3. Backend API Design

### 3.1 New Endpoint: `GET /api/predictions/matches`

Fetch today's matches across all sports via the Kernel Adapter layer.

```python
@router.get("/matches")
def list_matches(sport: str | None = None):
    """List today's matches across all sports.

    Args:
        sport: Optional sport filter (football, basketball, baseball, hockey).
               When None, returns all sports.
    """
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(status_code=503, detail="Kernel prediction is disabled.")
    kernel = _get_kernel()
    from app.kernel.protocols import ScheduleFilter
    raw_matches = kernel._adapter.fetch_schedule(ScheduleFilter())

    # Filter to today by kickoff date (API-layer uniform definition)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()
    today_matches = []
    for m in raw_matches:
        kickoff = m.match.kickoff_utc
        if kickoff is not None and kickoff.date() == today:
            today_matches.append(m)

    # Optional sport filter
    if sport:
        today_matches = [m for m in today_matches
                         if m.match.season.competition.sport.code == sport]

    # Batch query for has_prediction (avoid N+1)
    from app.kernel.kernel_db import get_match_ids_with_predictions
    predicted_ids = get_match_ids_with_predictions([m.match.match_id for m in today_matches])

    return [_match_summary(m, predicted_ids) for m in today_matches]
```

**Response format (per match summary):**

```json
{
  "match_id": "nba-12345",
  "sport": "basketball",
  "competition": "nba",
  "home_team": "Los Angeles Lakers",
  "away_team": "Boston Celtics",
  "home_code": "LAL",
  "away_code": "BOS",
  "kickoff_utc": "2026-07-15T02:00:00Z",
  "stage": "regular_season",
  "has_prediction": false
}
```

`has_prediction` is determined by a batch query: collect all `match_id` values from `raw_matches`, call `get_match_ids_with_predictions(match_ids)` (new DB function, see Section 3.4), and check membership. This avoids N+1 queries.

### 3.2 New Endpoint: `GET /api/predictions/matches/{match_id}`

Fetch single match detail + latest prediction (if any).

```python
@router.get("/matches/{match_id}")
def get_match(match_id: str):
    """Get match detail and latest prediction (if any)."""
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(status_code=503, detail="Kernel prediction is disabled.")
    kernel = _get_kernel()
    match = kernel._adapter.get_match_identity(match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    from app.kernel.kernel_db import get_latest_prediction
    latest = get_latest_prediction(match_id)

    return {
        "match": _match_detail(match),
        "prediction": _prediction_to_dict(latest) if latest else None,
    }
```

**Response format:**

```json
{
  "match": {
    "match_id": "nba-12345",
    "sport": "basketball",
    "competition": "nba",
    "season_key": "20252026",
    "home_team": "Los Angeles Lakers",
    "away_team": "Boston Celtics",
    "home_code": "LAL",
    "away_code": "BOS",
    "kickoff_utc": "2026-07-15T02:00:00Z",
    "stage": "regular_season",
    "round": null
  },
  "prediction": {
    "engine": "basketball",
    "predicted_scores": {"home": 112.5, "away": 108.3},
    "outcome_probabilities": {"home_win": 0.62, "away_win": 0.38},
    "confidence": 0.59,
    "explanation": [
      {"factor": "elo", "direction": "support", "weight": 0.45,
       "available": true, "detail": "P(home_win)=0.65", "predicted_outcome": "home_win"}
    ],
    "feature_version": "nba-1.0",
    "prediction_timestamp": "2026-07-14T18:30:00Z"
  }
}
```

When no prediction exists, `prediction` is `null`.

### 3.3 Fix: `POST /api/predictions/matches/{match_id}/predict`

The existing `predict_match` endpoint returns a dict missing `feature_version` and `prediction_timestamp`. This fix adds both fields to align with the `PredictionResult` dataclass.

**Before (current):**

```python
return {
    "match_id": match_id,
    "engine": result.engine_name,
    "predicted_scores": result.predicted_scores,
    "outcome_probabilities": result.outcome_probabilities,
    "confidence": result.confidence,
    "explanation": [c.__dict__ for c in result.explanation],
}
```

**After (fixed):**

```python
return {
    "match_id": match_id,
    "engine": result.engine_name,
    "predicted_scores": result.predicted_scores,
    "outcome_probabilities": result.outcome_probabilities,
    "confidence": result.confidence,
    "explanation": [c.__dict__ for c in result.explanation],
    "feature_version": result.feature_version,
    "prediction_timestamp": result.prediction_timestamp.isoformat(),
}
```

### 3.4 New DB Functions

Add to `backend/app/kernel/kernel_db.py`:

```python
def get_latest_prediction(match_id: str) -> KernelPrediction | None:
    """Get the latest prediction for a match from the kernel_predictions table.

    The table uses match_id as primary key, so each match has at most one row
    (updated on each prediction).
    """
    session = get_kernel_session()
    try:
        return session.query(KernelPrediction).filter_by(match_id=match_id).one_or_none()
    except Exception:
        return None


def get_match_ids_with_predictions(match_ids: list[str]) -> set[str]:
    """Batch query: return the subset of match_ids that have a prediction row.

    Used by the list endpoint to populate has_prediction without N+1 queries.
    """
    if not match_ids:
        return set()
    session = get_kernel_session()
    try:
        rows = session.query(KernelPrediction.match_id).filter(
            KernelPrediction.match_id.in_(match_ids)
        ).all()
        return {row[0] for row in rows}
    except Exception:
        return set()
```

`get_latest_prediction()` is used by the detail endpoint; `get_match_ids_with_predictions()` is used by the list endpoint for batch `has_prediction` lookup.

### 3.5 Helper Functions

Add 3 private helper functions in `predictions.py`:

```python
def _match_summary(raw: RawMatchData, predicted_ids: set[str]) -> dict:
    """Compact match summary for list endpoint.

    Args:
        raw: RawMatchData from adapter.
        predicted_ids: Set of match_ids that have a prediction row (batch-queried
                       by the caller via get_match_ids_with_predictions).
    """
    m = raw.match
    return {
        "match_id": m.match_id,
        "sport": m.season.competition.sport.code,
        "competition": m.season.competition.code,
        "home_team": m.home.name,
        "away_team": m.away.name,
        "home_code": m.home.code,
        "away_code": m.away.code,
        "kickoff_utc": m.kickoff_utc.isoformat() if m.kickoff_utc else None,
        "stage": m.stage,
        "has_prediction": m.match_id in predicted_ids,
    }


def _match_detail(match: MatchIdentity) -> dict:
    """Full match detail for detail endpoint."""
    return {
        "match_id": match.match_id,
        "sport": match.season.competition.sport.code,
        "competition": match.season.competition.code,
        "season_key": match.season.season_key,
        "home_team": match.home.name,
        "away_team": match.away.name,
        "home_code": match.home.code,
        "away_code": match.away.code,
        "kickoff_utc": match.kickoff_utc.isoformat() if match.kickoff_utc else None,
        "stage": match.stage,
        "round": match.round,
    }


def _prediction_to_dict(pred) -> dict:
    """Convert PredictionResult (dataclass) or KernelPrediction (ORM) to dict.

    Works for both types because both expose the same attribute names for the
    common fields (engine, predicted_scores, outcome_probabilities, confidence,
    explanation, feature_version).

    Timestamp handling:
    - PredictionResult (dataclass) has `prediction_timestamp: datetime`
    - KernelPrediction (ORM) has `created_at: DateTime` (no prediction_timestamp)
    - The `hasattr` check routes to the correct attribute for each type.
    """
    import json
    from datetime import datetime

    explanation = pred.explanation
    if isinstance(explanation, str):
        explanation = json.loads(explanation)

    timestamp = pred.prediction_timestamp if hasattr(pred, 'prediction_timestamp') else pred.created_at
    if isinstance(timestamp, datetime):
        timestamp = timestamp.isoformat()

    return {
        "engine": pred.engine,
        "predicted_scores": pred.predicted_scores,
        "outcome_probabilities": pred.outcome_probabilities,
        "confidence": pred.confidence,
        "explanation": explanation,
        "feature_version": pred.feature_version,
        "prediction_timestamp": timestamp,
    }
```

The `list_matches` endpoint calls `get_match_ids_with_predictions([m.match.match_id for m in today_matches])` once, then passes the resulting set to `_match_summary()` for each match — no N+1 queries.

### 3.6 No Changes Required

- `PredictionKernel` class — zero modification
- `MultiAdapter` — zero modification
- All sport Adapters (WorldCup, UCL, EPL, League, NBA, MLB, NHL) — zero modification
- `domain.py` — zero modification
- `/api/world-cup/predictions/*` routes — zero modification

### 3.7 Risk Mitigation: `fetch_schedule` status filter inconsistency

Each Adapter's `fetch_schedule` handles the `status` filter differently (WorldCupAdapter queries the old `MatchFixture` table; others query `KernelMatchFixture`). The status field semantics may differ across tables.

**MVP decision:** `GET /api/predictions/matches` calls `fetch_schedule(ScheduleFilter())` (no filters). The API layer filters to "today" by comparing `kickoff_utc.date()` to `datetime.now(timezone.utc).date()`. This:
- Avoids dependence on Adapter status-filter implementation consistency
- Keeps "today" definition uniform at the API layer
- Can be optimized later to pass status filter

---

## 4. Frontend Component Design

### 4.1 `SportFilter` — Sport Filter

Button group: `[全部] [Football] [Basketball] [Baseball] [Hockey]`

```typescript
interface SportFilterProps {
  value: string | null;
  onChange: (sport: string | null) => void;
}
```

- Sport list is hardcoded (4 sports + "all") — sport set is stable, no dynamic fetch needed
- Active button highlighted

### 4.2 `MatchListCard` — List Card

```typescript
interface MatchSummary {
  match_id: string;
  sport: string;
  competition: string;
  home_team: string;
  away_team: string;
  home_code: string;
  away_code: string;
  kickoff_utc: string;
  stage: string;
  has_prediction: boolean;
}

interface MatchListCardProps {
  match: MatchSummary;
}
```

Layout:
- Left: sport icon (⚽🏀⚾🏒 via sport code mapping) + competition badge
- Center: "Home vs Away", below: kickoff time in local timezone
- Right: status badge (`has_prediction` → "已预测", else → "未预测")
- Entire card clickable → navigates to `/sports/[matchId]/`

Sport icon mapping:
```typescript
const SPORT_ICONS: Record<string, string> = {
  football: "⚽",
  basketball: "🏀",
  baseball: "⚾",
  hockey: "🏒",
};
```

### 4.3 `ProbabilityBar` — Probability Bar (Dynamic Binary/Ternary)

Core generic component. Renders based on `outcome_probabilities` keys.

```typescript
interface ProbabilityBarProps {
  probabilities: Record<string, number>;
  homeTeam: string;
  awayTeam: string;
}
```

Rendering logic:
- Iterate `Object.entries(probabilities)`, render one row per outcome
- `home_win` / `home` → home team color (blue), label shows home team name
- `away_win` / `away` → away team color (red), label shows away team name
- `draw` → neutral gray, label "平局"
- Bar width = `probability * 100%`
- Percentage displayed as `(probability * 100).toFixed(1)%`
- **No hardcoded outcome count** — football renders 3 rows, NBA/MLB/NHL render 2 rows, all data-driven

### 4.4 `FactorBreakdownTable` — Factor Breakdown Table (Dynamic Factors)

```typescript
interface ContributionItem {
  factor: string;
  direction: string;       // "support" | "oppose" | "neutral"
  weight: number;
  available: boolean;
  detail: string | null;
  predicted_outcome: string | null;
}

interface FactorBreakdownTableProps {
  items: ContributionItem[];
}
```

Renders as a table:

| 因子 | 方向 | 权重 | 详情 |
|------|------|------|------|
| Elo 等级分 | 支持 (主胜) | 30% | P(home_win)=0.65 |
| 主场优势 | 支持 (主胜) | 10% | HFA=100 |

- Direction: `support` → "支持", `oppose` → "反对", `neutral` → "中立"
- Direction followed by `(predicted_outcome)` in brackets: `home_win` → "(主胜)", `away_win` → "(客胜)", `draw` → "(平局)"
- Weight: `(weight * 100).toFixed(0)%`
- `available=false` rows are greyed out, detail shows "不可用"
- Factor name Chinese mapping (lookup table, unmapped factors displayed as-is):

```typescript
const FACTOR_NAME_ZH: Record<string, string> = {
  elo: "Elo 等级分",
  home_court: "主场优势",
  rest: "休息天数",
  form: "近期状态",
  starting_pitcher: "先发投手",
  goalie: "门将",
  odds: "赔率",
};
```

### 4.5 `MatchDetailPanel` — Detail Panel

Composes the above components to show full prediction detail.

```typescript
interface MatchDetailPanelProps {
  match: MatchDetail;
  prediction: PredictionResult | null;
  onPredict: () => void;
  isPredicting: boolean;
}
```

Layout (top to bottom):
1. **Header:** sport icon + competition + "Home vs Away" + kickoff time
2. **Action area:**
   - `prediction === null` → "预测" button (triggers `onPredict`)
   - `prediction !== null` → "重新预测" button + prediction timestamp
   - `isPredicting === true` → button shows loading state
3. **Prediction result area** (when `prediction !== null`):
   - `ProbabilityBar` (probability bar)
   - Predicted scores (`predicted_scores`, if present) + confidence
   - `FactorBreakdownTable` (factor breakdown)
   - `feature_version` badge (e.g. "nba-1.0")

### 4.6 Page: `/sports/page.tsx` — List Page

```typescript
"use client";
// 1. useState: sport filter (null = all)
// 2. useEffect: fetch GET /api/predictions/matches?sport=...
// 3. Render: SportFilter + MatchListCard list
//    - Empty list: "今日无比赛"
//    - Loading: skeleton
//    - Error: error message + retry button
```

### 4.7 Page: `/sports/[matchId]/page.tsx` — Detail Page

```typescript
"use client";
// 1. Get matchId from useParams()
// 2. useEffect: fetch GET /api/predictions/matches/{matchId}
// 3. Render: MatchDetailPanel
//    - "Predict" button: POST /api/predictions/matches/{matchId}/predict
//    - On prediction complete: update prediction state with return value
//    - 404: "比赛不存在"
```

### 4.8 API Client: `lib/sports-api.ts`

```typescript
import { getApiBaseUrl } from "./env";  // reuse existing

const API_BASE = getApiBaseUrl();

// Type definitions (single source of truth)

export interface MatchSummary {
  match_id: string;
  sport: string;
  competition: string;
  home_team: string;
  away_team: string;
  home_code: string;
  away_code: string;
  kickoff_utc: string | null;
  stage: string;
  has_prediction: boolean;
}

export interface MatchDetail {
  match_id: string;
  sport: string;
  competition: string;
  season_key: string;
  home_team: string;
  away_team: string;
  home_code: string;
  away_code: string;
  kickoff_utc: string | null;
  stage: string;
  round: string | null;
}

export interface ContributionItem {
  factor: string;
  direction: string;       // "support" | "oppose" | "neutral"
  weight: number;
  available: boolean;
  detail: string | null;
  predicted_outcome: string | null;
}

export interface PredictionResult {
  engine: string;
  predicted_scores: Record<string, number>;
  outcome_probabilities: Record<string, number>;
  confidence: number;
  explanation: ContributionItem[];
  feature_version: string;
  prediction_timestamp: string | null;
}

export class NotFoundError extends Error {}

export async function fetchMatches(sport?: string): Promise<MatchSummary[]> {
  const params = sport ? `?sport=${sport}` : "";
  const res = await fetch(`${API_BASE}/api/predictions/matches${params}`);
  if (!res.ok) throw new Error("Failed to fetch matches");
  return res.json();
}

export async function fetchMatchDetail(matchId: string): Promise<{ match: MatchDetail; prediction: PredictionResult | null }> {
  const res = await fetch(`${API_BASE}/api/predictions/matches/${matchId}`);
  if (res.status === 404) throw new NotFoundError("Match not found");
  if (!res.ok) throw new Error("Failed to fetch match");
  return res.json();
}

export async function triggerPrediction(matchId: string): Promise<PredictionResult> {
  const res = await fetch(`${API_BASE}/api/predictions/matches/${matchId}/predict`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) throw new Error("Prediction failed");
  return res.json();
}
```

### 4.9 Navigation Integration

In `app-nav.tsx`, add one entry to the `NAV` array:

```typescript
{ href: "/sports", label: "体育预测" },  // NEW
{ href: "/world-cup", label: "世界杯" },  // preserved unchanged
```

### 4.10 Styling

- Use existing project's Tailwind CSS (consistent with World Cup pages)
- Sport icons use emoji (⚽🏀⚾🏒) — no icon library dependency
- Colors: home team blue, away team red, draw gray — visually consistent with World Cup components

---

## 5. Testing Strategy

### 5.1 Backend Tests

**New file:** `backend/tests/test_api_predictions.py`

Uses FastAPI TestClient + temporary Kernel DB + FakeAdapter.

```
TestListMatches:
  - test_list_matches_returns_today_matches          # returns today's matches
  - test_list_matches_sport_filter                   # sport filter works
  - test_list_matches_empty_when_no_fixtures         # empty array when no matches
  - test_list_matches_summary_format                 # summary fields complete
  - test_list_matches_503_when_kernel_disabled       # KERNEL_PREDICTION_ENABLED=false → 503

TestGetMatch:
  - test_get_match_returns_detail_and_prediction     # returns detail + existing prediction
  - test_get_match_returns_null_prediction_when_none # prediction=null when no prediction
  - test_get_match_404_when_not_found                # match_id not found → 404
  - test_get_match_detail_format                     # detail fields complete

TestPredictEndpointFix:
  - test_predict_returns_feature_version             # fix: returns feature_version
  - test_predict_returns_prediction_timestamp        # fix: returns prediction_timestamp

TestGetLatestPrediction:
  - test_get_latest_prediction_returns_row           # table has data → returns row
  - test_get_latest_prediction_returns_none          # table empty → returns None

TestGetMatchIdsWithPredictions:
  - test_returns_subset_with_predictions             # 3 matches, 2 have predictions → returns 2
  - test_empty_input_returns_empty_set               # empty list → empty set
  - test_no_predictions_returns_empty_set            # matches exist but no predictions → empty set
```

**Total: 15 backend tests.**

#### FakeAdapter Design

Extends the pattern from `test_kernel_prediction_kernel.py`'s `FakeAdapter`:
- `fetch_schedule` returns multiple sport fake matches (football + basketball + baseball + hockey, 1 each)
- `get_match_identity` returns the appropriate sport's MatchIdentity based on matchId prefix
- Other methods return empty data

### 5.2 Frontend Tests

**New files (4 test files):**

#### `frontend/src/components/sports/__tests__/probability-bar.test.tsx`

```
- test_renders_two_outcomes_for_binary            # NBA data → 2 rows
- test_renders_three_outcomes_for_ternary         # football data → 3 rows
- test_renders_correct_percentages                # 0.62 → "62.0%"
- test_applies_home_color_to_home_win             # home_win row blue
- test_applies_away_color_to_away_win             # away_win row red
- test_applies_neutral_color_to_draw              # draw row gray
```

#### `frontend/src/components/sports/__tests__/factor-breakdown-table.test.tsx`

```
- test_renders_all_items                          # 5 factors → 5 rows
- test_displays_factor_name_zh_mapping            # elo → "Elo 等级分"
- test_displays_unmapped_factor_as_is             # unknown_factor → "unknown_factor"
- test_unavailable_factor_row_greyed              # available=false → greyed
- test_direction_translated                       # support → "支持"
- test_predicted_outcome_in_brackets              # predicted_outcome="home_win" → "(主胜)"
```

#### `frontend/src/components/sports/__tests__/match-list-card.test.tsx`

```
- test_renders_team_names                         # shows home/away team names
- test_renders_kickoff_local_time                 # UTC → local time
- test_renders_sport_icon                         # basketball → 🏀
- test_renders_predicted_badge                    # has_prediction=true → "已预测"
- test_renders_not_predicted_badge                # has_prediction=false → "未预测"
- test_click_navigates_to_detail                  # click → /sports/{matchId}/
```

#### `frontend/src/lib/__tests__/sports-api.test.ts`

```
- test_fetch_matches_calls_correct_url            # URL construction correct
- test_fetch_matches_with_sport_param             # ?sport=basketball
- test_fetch_match_detail_404_throws_not_found    # 404 → NotFoundError
- test_trigger_prediction_uses_post               # POST method
- test_fetch_matches_throws_on_non_ok             # non-200 throws
```

**Total: 21 frontend tests.**

**Frontend test framework:** Check `frontend/package.json` for existing Vitest configuration before implementation. If Vitest is not configured, the implementation plan must include a configuration step.

### 5.3 Test Coverage Matrix

| Component/Endpoint | Unit Tests | Integration Tests |
|--------------------|------------|-------------------|
| `GET /matches` | 5 | 0 |
| `GET /matches/{id}` | 4 | 0 |
| `POST /matches/{id}/predict` (fix) | 2 | 0 |
| `get_latest_prediction` | 2 | 0 |
| `get_match_ids_with_predictions` | 3 | 0 |
| `ProbabilityBar` | 6 | 0 |
| `FactorBreakdownTable` | 6 | 0 |
| `MatchListCard` | 6 | 0 |
| `sports-api.ts` | 5 | 0 |
| **Total** | **39** | **0** |

### 5.4 Not Tested

- `MatchDetailPanel` — composition component, simple logic, verified via manual page-level testing
- `SportFilter` — pure UI interaction, minimal logic
- Page routes — Next.js routing guaranteed by framework
- Styles — visual verification, not automated

### 5.5 Regression Protection

After backend tests run, all existing tests must pass:
- `test_kernel_prediction_kernel.py` (Kernel unit tests)
- `test_kernel_factor_registry.py` (FactorRegistry)
- `test_multi_feature_builder.py` (MultiFeatureBuilder)
- `test_api_main.py` (existing API integration)

After frontend tests run, existing `world-cup/` related tests must be unaffected (zero-modification guarantee).

If the `predict` endpoint fix (adding `feature_version` / `prediction_timestamp` to return) breaks existing test assertions in `test_api_main.py`, update those assertions to include the new fields.

---

## 6. Hard Constraints

### Backend Constraints

1. `KERNEL_PREDICTION_ENABLED` defaults to OFF — new `/matches` endpoints return 503 when disabled, consistent with existing `/predict` behavior
2. `GET /api/predictions/matches` must obtain data via `kernel._adapter.fetch_schedule()` — never query DB tables directly, preserving Adapter abstraction
3. `GET /api/predictions/matches/{match_id}` 404 response must return `{"detail": "Match not found"}` — consistent with FastAPI default HTTPException format
4. `get_latest_prediction()` function must be placed in `kernel_db.py` — consistent with other DB query function locations
5. `POST /predict` endpoint fix must add `feature_version` and `prediction_timestamp` fields — aligned with `PredictionResult` dataclass fields
6. `_prediction_to_dict()` helper must handle both `PredictionResult` (dataclass) and `KernelPrediction` (ORM) types — reused by list/detail/predict endpoints
7. `MultiAdapter`, `PredictionKernel`, `domain.py`, all Adapters — zero modification
8. Existing `/api/world-cup/predictions/*` routes — zero modification
9. `_match_summary()` and `_match_detail()` must extract fields from `RawMatchData` / `MatchIdentity` — no additional DB queries (except `has_prediction` which requires a `kernel_predictions` lookup)

### Frontend Constraints

10. `frontend/src/app/world-cup/` directory — zero modification
11. `frontend/src/components/world-cup/` directory — zero modification
12. `frontend/src/lib/world-cup-predictions.ts` — zero modification
13. New components must be placed under `frontend/src/components/sports/` — physically isolated from `world-cup/` components
14. `ProbabilityBar` must not hardcode outcome count — must render dynamically via `Object.keys(probabilities).length`
15. `FactorBreakdownTable` must not hardcode factor names or count — must iterate `items` array dynamically
16. `sports-api.ts` must reuse existing `getApiBaseUrl()` function — do not re-implement API base URL logic
17. Navigation `app-nav.tsx` only adds `/sports` entry, does not modify or delete existing entries
18. Sport icons use emoji (⚽🏀⚾🏒) — no icon library dependency introduced
19. TypeScript type definitions placed at top of `lib/sports-api.ts`, components import from this file — single type source

### Testing Constraints

20. Backend tests use FakeAdapter, do not mock real Adapters — preserve Protocol abstraction
21. Frontend tests do not test Next.js routing or styles — focus on component logic
22. All existing tests must remain passing — backend `test_kernel_*.py`, `test_api_main.py`, frontend `world-cup/` related tests

---

## 7. Deliverables

### Backend (2 files modified, 1 test file new)

| File | Operation | Content |
|------|-----------|---------|
| `backend/app/api/routes/predictions.py` | Modify | Add 2 endpoints + 3 helpers + fix predict return value |
| `backend/app/kernel/kernel_db.py` | Modify | Add `get_latest_prediction()` function |
| `backend/tests/test_api_predictions.py` | New | 12 backend tests |

No new backend source files — all extensions within existing files.

### Frontend (10 files new, 1 file modified, 4 test files new)

| File | Operation | Content |
|------|-----------|---------|
| `frontend/src/app/sports/page.tsx` | New | List page |
| `frontend/src/app/sports/loading.tsx` | New | List page skeleton |
| `frontend/src/app/sports/[matchId]/page.tsx` | New | Detail page |
| `frontend/src/app/sports/[matchId]/loading.tsx` | New | Detail page skeleton |
| `frontend/src/components/sports/match-list-card.tsx` | New | List card component |
| `frontend/src/components/sports/match-detail-panel.tsx` | New | Detail panel component |
| `frontend/src/components/sports/probability-bar.tsx` | New | Probability bar component |
| `frontend/src/components/sports/factor-breakdown-table.tsx` | New | Factor breakdown table component |
| `frontend/src/components/sports/sport-filter.tsx` | New | Sport filter component |
| `frontend/src/lib/sports-api.ts` | New | API client + type definitions |
| `frontend/src/components/app-nav.tsx` | Modify | Add `/sports` nav entry |
| `frontend/src/components/sports/__tests__/probability-bar.test.tsx` | New | 6 tests |
| `frontend/src/components/sports/__tests__/factor-breakdown-table.test.tsx` | New | 6 tests |
| `frontend/src/components/sports/__tests__/match-list-card.test.tsx` | New | 6 tests |
| `frontend/src/lib/__tests__/sports-api.test.ts` | New | 5 tests |

**Total:** Backend 2 modified + 1 new; Frontend 10 new + 1 modified + 4 test files new.

---

## 8. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `fetch_schedule` status filter inconsistency across Adapters | List may return empty or include non-today matches | MVP: do not pass status filter; filter to today by `kickoff_utc.date()` at API layer. Documented in Section 3.7. |
| `get_match_identity` may require network requests in some Adapters | Detail page slow response | MVP: accept this latency. Future: add cache layer. |
| Frontend project may not have Vitest configured | Frontend tests cannot run | Check `frontend/package.json` before implementation; add configuration step to plan if missing. |
| `feature_version` fix may break existing `test_api_main.py` assertions | Existing tests fail | Check existing test assertions on predict return format during implementation; update assertions to include new fields. |
