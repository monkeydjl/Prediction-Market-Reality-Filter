# Sports Prediction OS — Phase 7: Sport Market Bridge Design

Date: 2026-07-15

## Goal

Build the bridge layer that connects the Sports Prediction Kernel (Phase 1-6) with prediction market reality (Polymarket) and traditional sportsbook odds (The Odds API). This is Sub-project A of the Phase 7 "Full-Stack Fusion" effort, which unifies the project's two isolated pipelines — the prediction-market event pipeline (LLM + edge) and the sports prediction kernel (Elo/BTD + learning loop) — back to the project's original "Reality Filter" intent.

**Sub-project A deliverable:** `match_id ↔ contract_id` mapping service, sports market collection, implied-probability extraction, and a bridge management UI. Produces verified market-implied probabilities per match outcome that Sub-project B (Edge Detector) will consume to compute model-vs-market divergence.

## Background: Current State

The project has two isolated pipelines:

| Dimension | Prediction-Market Pipeline | Sports Prediction Kernel |
|-----------|---------------------------|--------------------------|
| Event-contract link | `event_market_link_store` (event_id ↔ contract_id) | None |
| Market collection | Polymarket/Kalshi (politics/crypto focus) | None (sports_event is manually curated) |
| Odds source | Prediction-market price (baseline_probability) | Traditional sportsbook (World Cup football only) |
| Edge calculation | `raw_edge` / `adjusted_edge` | None |
| Actionable recommendation | `ActionableRecommendation` (YES/NO) | None |
| ID mapping | event_id ↔ contract_id | match_id (prefix routing) |

**Core gap:** No `match_id ↔ contract_id` bridge exists. The two systems cannot compare model probabilities with market-implied probabilities. Sub-project A closes this gap.

## Non-goals

- Do NOT compute model-vs-market edge/divergence — that is Sub-project B.
- Do NOT extend `ActionableRecommendation` for sports — that is Sub-project C.
- Do NOT feed market settlement prices back into the learning loop — that is Sub-project D.
- Do NOT handle futures/championship markets (e.g., "Who wins NBA 2025?") — single-match markets only in Phase 7.
- Do NOT integrate Kalshi sports markets — CFTC regulatory complexity; defer to a later phase.
- Do NOT implement real-time price push (WebSocket) — use polling snapshots.
- Do NOT implement automated trading/order placement — the project principle is informational assistance, never auto-trading.
- Do NOT modify the Prediction Kernel, learning tables, learning dashboard, or existing prediction-market event pipeline.

## Architecture

```
Sports match (match_id)         Prediction market contract (contract_id)
    │                                    │
    │  TeamIdentity + match date         │  market question text
    │                                    │  + price/lastTradePrice
    ▼                                    ▼
┌─────────────────────────────────────────────────┐
│  SportMarketBridgeService (new)                  │
│  ├─ Rule layer: TeamNameMatcher                  │
│  │  └─ team name normalization + date window     │
│  │     + sport filter                            │
│  ├─ LLM layer: MarketQuestionResolver            │
│  │  └─ LLM semantic matching on rule miss        │
│  └─ Persistence: SportMarketLinkStore (new)      │
│     └─ match_id ↔ contract_id + confidence       │
└─────────────────────────────────────────────────┘
                    │
                    ▼ verified=true (fail-closed)
┌─────────────────────────────────────────────────┐
│  ImpliedProbabilityExtractor                     │
│  ├─ Polymarket: price → implied_prob             │
│  └─ TheOddsAPI: decimal_odds → implied_prob      │
│     (normalize to remove vigorish)               │
└─────────────────────────────────────────────────┘
                    │
                    ▼
         MarketImpliedProbabilities
         (per outcome: home_win/draw/away_win)
```

### Data flow

1. **Collection**: Scheduled tasks fetch Polymarket sports markets + The Odds API traditional sportsbook odds.
2. **Matching**: `SportMarketBridgeService` links markets to `match_id` via three layers (rule → LLM → manual verification).
3. **Extraction**: Implied probabilities are extracted from linked markets (Polymarket price or traditional odds inverse, normalized).
4. **Storage**: `SportMarketLinkStore` persists link relations; `MarketSnapshotStore` appends price time-series snapshots.
5. **Consumption**: Sub-project B (Edge Detector) consumes verified links to compute model-vs-market divergence.

### Hard constraints

- **Fail-closed**: Unverified links are never exposed to downstream consumers. `get_verified_links(match_id)` returns only `verified=True` links. This mirrors the existing `event_market_link_store.get_verified_link` pattern.
- **Zero-invasion on sports kernel**: `PredictionKernel`, `PredictionEngine`, `FeatureSet`, `domain.py`, `LearningService`, the 3 learning tables, and the learning dashboard components are NOT modified.
- **Zero-invasion on existing prediction-market pipeline**: `event_market_link_store`, `event_intelligence_service`, `polymarket_event_source`, `kalshi_event_source` are NOT modified. New `polymarket_sports_source` exists in parallel.
- **Feature flag**: `PHASE7_SPORT_MARKET_BRIDGE_ENABLED` defaults to OFF. When false, all new endpoints return 503 and collection tasks are not scheduled.
- **New tables use `kernel_` prefix**: `kernel_sport_market_links`, `kernel_market_snapshots`. Existing tables are not modified.
- **Regression protection**: Existing World Cup odds logic (`odds_api_service` with `SPORT = "soccer_fifa_world_cup"`) must pass tests with zero modifications.

## Data Model

### Table: `kernel_sport_market_links`

Link relation between a sports match and a prediction-market contract (relatively stable, occasionally updated).

```python
@dataclass(frozen=True)
class SportMarketLink:
    # Primary identity
    match_id: str                    # e.g., "nba-20250101-LAL-BOS"
    contract_id: str                 # Polymarket token_id / The Odds API event_key

    # Market metadata
    source: str                      # "polymarket" | "the_odds_api"
    market_question: str             # "Will Lakers beat Celtics on Jan 1?"
    market_type: str                 # "single_match_binary" | "traditional_odds"
    outcome_label: str               # "YES" / "NO" / "home" / "away" / "draw"

    # Implied probability snapshot (captured at link time)
    implied_probability: float       # 0.0 - 1.0
    raw_price: float                 # Polymarket price (0-1) or decimal_odds
    liquidity_usd: Optional[float]   # Polymarket liquidity; None for traditional sportsbook
    volume_usd: Optional[float]      # Polymarket volume; None for traditional sportsbook

    # Link metadata
    link_method: str                 # "rule" | "llm" | "manual"
    link_confidence: float           # 0.0 - 1.0
    verified: bool                   # fail-closed gate
    linked_at: datetime
    verified_at: Optional[datetime]

    # Mapping to sports kernel outcome namespace
    mapped_outcome: str              # "home_win" / "draw" / "away_win"
```

**Unique key**: `(match_id, contract_id, outcome_label)` — one match can have multiple contracts (e.g., Polymarket YES + NO both map to `home_win`; traditional sportsbook has home/draw/away as separate rows).

**`mapped_outcome` rationale**: Unifies heterogeneous market outcome labels (YES/NO, home/away/draw) into the sports kernel's outcome namespace (`home_win`/`draw`/`away_win`). Sub-project B aligns model probabilities with market probabilities by `mapped_outcome`.

### Table: `kernel_market_snapshots`

Price time-series (frequently appended, never overwritten).

```python
@dataclass(frozen=True)
class MarketSnapshot:
    snapshot_id: str                 # UUID
    match_id: str
    contract_id: str
    captured_at: datetime
    implied_probability: float
    raw_price: float
    liquidity_usd: Optional[float]
    volume_usd: Optional[float]
    source: str
```

**Separation rationale**: Links are relatively stable (a match-market association rarely changes); prices fluctuate constantly. Separating avoids rewriting link rows on every price tick and preserves a time-series for Sub-project D (market calibration feedback) and the snapshot chart UI.

### Implied probability calculation

```python
def polymarket_to_implied(yes_price: float, no_price: float) -> tuple[float, float, float]:
    """Returns (yes_implied, no_implied, spread). YES+NO > 1.0 portion is spread."""
    spread = max(0.0, yes_price + no_price - 1.0)
    return yes_price, no_price, spread

def odds_api_to_implied(decimal_odds_list: list[float]) -> list[float]:
    """Traditional sportsbook decimal odds → normalized implied probabilities (vigorish removed)."""
    raw = [1.0 / odds for odds in decimal_odds_list]
    total = sum(raw)
    return [p / total for p in raw]  # normalize so sum == 1.0
```

- **Polymarket**: `price` is already a 0-1 probability expression. `implied_probability = price`. YES+NO sum > 1.0 indicates spread (recorded but not adjusted — Sub-project B decides whether to adjust).
- **The Odds API**: `implied_probability = 1 / decimal_odds`. Raw probabilities sum > 1.0 due to vigorish; normalize by dividing each by the sum to remove overround.

## Three-Layer Matching Engine

### Layer 1: Rule matcher (`TeamNameMatcher`)

Deterministic, zero LLM cost, handles ~80% of clear cases.

**Input**: `match_id` (contains team info) + `market_question` text
**Output**: `Optional[MatchResult(confidence, mapped_outcome, reasoning)]`

**Matching rules** (by priority):

1. **Team name normalization** via `TeamAliasRegistry`:
   - Each league has a canonical team ID with alias variants
   - Example: `"Lakers"` / `"Los Angeles Lakers"` / `"LAL"` / `"洛杉矶湖人"` → canonical `los_angeles_lakers`
   - Sport type is inferred from `COMPETITION_SPORT` to constrain the search space

2. **Date window matching**:
   - Extract match date from `match_id` (e.g., `nba-20250101-LAL-BOS` → 2025-01-01)
   - Extract date expression from `market_question` ("tonight" / "Jan 1" / "January 1st" / "1月1日")
   - Tolerance: ±1 day (timezone differences)

3. **Outcome direction inference**:
   - `"Will [Home] beat [Away]?"` → YES maps to `home_win`
   - `"Will [Away] beat [Home]?"` → YES maps to `away_win`
   - Traditional sportsbook home/draw/away maps directly

4. **Confidence scoring**:
   - Both team names + date match → `confidence = 0.95`
   - One team name + date match → `confidence = 0.75` (escalate to LLM)
   - Date only → `confidence = 0.3` (escalate to LLM)

**Output routing**:
- `confidence >= 0.9` → auto-verified
- `0.6 <= confidence < 0.9` → escalate to LLM layer
- `< 0.6` → escalate to LLM layer

### Layer 2: LLM semantic matcher (`MarketQuestionResolver`)

Triggered on rule-layer miss or low confidence. Reuses existing `llm_gateway_service`.

**Input**: `match_id` (structured match info) + `market_question` + partial match clues from rule layer
**Output**: `MatchResult(confidence, mapped_outcome, reasoning)`

**Prompt design** (structured JSON output):
```
Given sports match information:
- Sport: basketball
- Competition: nba
- Home team: Los Angeles Lakers (LAL)
- Away team: Boston Celtics (BOS)
- Date: 2025-01-01

Prediction market question: "{market_question}"

Determine whether this market question is about the above match, and which outcome the YES result corresponds to.
Output JSON: {"is_match": bool, "confidence": 0.0-1.0, "mapped_outcome": "home_win"|"away_win"|"draw"|"none", "reasoning": str}
```

**Output routing**:
- `confidence >= 0.85` → auto-verified
- `0.6 <= confidence < 0.85` → pending manual verification
- `< 0.6` → no link created

### Layer 3: Manual verification gate

Mirrors the existing `event_market_link_store` `verified` pattern, applied to `SportMarketLinkStore`.

**Entry points**:
- CLI tool `scripts/sport_market_bridge_cli.py` (follows `domain_reliability_cli.py` pattern)
  - Lists `verified=False AND confidence >= 0.6` pending links
  - Human confirms or rejects each
- Frontend `PendingReviewQueue` component (same operations via API)

**Auto-verification rules** (no human needed):
- Rule layer `confidence >= 0.9` → auto-verified
- LLM layer `confidence >= 0.85` → auto-verified
- All others → pending manual verification

**Fail-closed**: `get_verified_links(match_id)` returns only `verified=True` links. Sub-project B (Edge Detector) consumes verified links only.

### Team alias registry (`TeamAliasRegistry`)

**Location**: `backend/app/sports/_shared/team_aliases.py`

**Data structure**:
```python
TEAM_ALIASES: dict[str, dict[str, str]] = {
    "nba": {
        "LAL": "los_angeles_lakers",
        "Lakers": "los_angeles_lakers",
        "Los Angeles Lakers": "los_angeles_lakers",
        "洛杉矶湖人": "los_angeles_lakers",
        "BOS": "boston_celtics",
        "Celtics": "boston_celtics",
        # ...
    },
    # epl, mlb, nhl, ucl, laliga, bundesliga, seriea, ligue1, wc
}
```

**Initial coverage**: 10 competitions' team aliases (NBA 30, MLB 30, NHL 32, EPL 20, UCL 32, La Liga 20, Bundesliga 18, Serie A 20, Ligue 1 18, World Cup 32).

**Maintenance**: Static dict file, extended as needed. Team names are relatively stable; no external API dependency. The file is large but trivially correct — each entry is a string-to-string mapping.

## Data Collection

### Polymarket sports market collection

**New file**: `backend/app/services/polymarket_sports_source.py`

The existing `polymarket_event_source.py` is a general-purpose collector (by volume) with no sports branch. The new source exists in parallel — the existing source is NOT modified.

**Collection strategy**:
1. Call Polymarket API `/markets` (reuse existing `polymarket_service.py` HTTP client)
2. Filter sports candidate markets by keywords:
   - Team name keywords (all aliases flattened from `TeamAliasRegistry`)
   - Sport type keywords ("NBA" / "NFL" / "EPL" / "Premier League" / "MLB" / "NHL" / "World Cup" etc.)
   - Sports action keywords ("beat" / "win" / "defeat" / "vs" / "play")
3. Exclude obvious non-single-match markets (futures/season keywords: "championship" / "season" / "MVP" / "playoffs bracket" → tag `market_type = "futures"`, skip for now)
4. Output candidate market list: `{contract_id, question, price, liquidity, volume, end_date}`

**Scheduling**: Reuse existing `scheduler.py`. New scheduled task `discover_sport_markets`, hourly. Runs in parallel with existing `discover_events`, does not block it.

### Traditional sportsbook odds extension

**Modified file**: `backend/app/services/odds_api_service.py`

Currently hardcoded `SPORT = "soccer_fifa_world_cup"`. Extend to multi-league.

**Extension mapping** (`COMPETITION_TO_ODDS_API_SPORT`):
```python
COMPETITION_TO_ODDS_API_SPORT = {
    "wc": "soccer_fifa_world_cup",
    "epl": "soccer_epl",
    "ucl": "soccer_uefa_champs_league",
    "laliga": "soccer_spain_la_liga",
    "bundesliga": "soccer_germany_bundesliga",
    "seriea": "soccer_italy_serie_a",
    "ligue1": "soccer_france_ligue_one",
    "nba": "basketball_nba",
    "mlb": "baseball_mlb",
    "nhl": "icehockey_nhl",
}
```

**Function signature change**:
```python
# From
def get_odds(home: str, away: str) -> Optional[OddsResult]:
# To
def get_odds(home: str, away: str, competition: str = "wc") -> Optional[OddsResult]:
```

**Constraints**:
- The Odds API free tier: 500 req/month — aggressive caching required (reuse existing `odds_cache_service.py`, raise TTL from 1 hour to 6 hours, only collect within 24 hours before kickoff)
- Preserve `soccer_fifa_world_cup` default to ensure World Cup existing logic has zero regression

**Caller updates**:
- `football/adapters/_shared.py:52-59` — pass `competition` parameter
- NBA/MLB/NHL `feature_builder` — enable odds input (previously hardcoded `odds_home=None`)
- `SportMarketBridgeService` — call `get_odds` for traditional sportsbook implied probabilities

### Market detector (`SportMarketDetector`)

**New file**: `backend/app/services/sport_market_detector.py`

Determines whether a candidate market is a single-match market and extracts structured info.

**Input**: `{contract_id, question, source}`
**Output**: `Optional[SportMarketInfo]`

```python
@dataclass(frozen=True)
class SportMarketInfo:
    contract_id: str
    source: str                    # "polymarket" | "the_odds_api"
    market_question: str
    market_type: str               # "single_match_binary" | "traditional_odds"
    detected_sport: Optional[str]  # "football" | "basketball" | ...
    detected_competition: Optional[str]  # "nba" | "epl" | ...
    detected_teams: list[str]      # canonical team IDs
    detected_date: Optional[date]
    outcome_label: str             # "YES" | "home" | "away" | "draw"
```

**Detection logic** (deterministic, no LLM):
1. Reverse-lookup team names in `market_question` via `TeamAliasRegistry`
2. Infer `detected_sport`/`detected_competition` via sport/league keywords
3. Extract `detected_date` via date parser (reuse existing `market_semantics_service` date extraction)
4. Traditional sportsbook source is directly tagged `market_type = "traditional_odds"`, no text parsing needed

### Scheduling and deduplication

**Deduplication**: `SportMarketLinkStore` unique key `(match_id, contract_id, outcome_label)` prevents duplicate links.

**Schedule timeline**:
```
Hourly:   discover_sport_markets (Polymarket)
          → SportMarketDetector detection
          → TeamNameMatcher rule matching
          → LLM fallback (on miss where confidence >= 0.6)
          → write to SportMarketLinkStore

Every 6h: fetch_traditional_odds (The Odds API)
          → directly structured (no text parsing)
          → write to SportMarketLinkStore (link_method="rule", confidence=1.0, verified=True)

Every 1m: capture_market_snapshots
          → for all verified links, fetch latest price
          → append to kernel_market_snapshots
```

## API Endpoints

**New route file**: `backend/app/api/routes/sport_markets.py`

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/sport-markets/links` | GET | List links (supports `match_id`/`source`/`verified` filters) |
| `/api/sport-markets/links/{match_id}` | GET | All market links for a single match |
| `/api/sport-markets/links/{match_id}/latest` | GET | Latest verified links for a match, joined with each link's newest snapshot row (consumed by Edge Detector) |
| `/api/sport-markets/pending` | GET | Pending manual-verification links |
| `/api/sport-markets/links/{match_id}/{contract_id}/verify` | POST | Manual verification (body: `{"verified": bool, "note": str}`) |
| `/api/sport-markets/snapshots/{match_id}` | GET | Market price time-series for a match |

**Constraints**:
- First 3 GET endpoints are gated by `PHASE7_SPORT_MARKET_BRIDGE_ENABLED`; return 503 when false.
- `/pending` and `/verify` are gated by `PHASE7_SPORT_MARKET_BRIDGE_ENABLED` + admin permission (reuse existing `security.py`).
- `/latest` returns only `verified=True` links (fail-closed).
- POST `/verify` is the only write operation in this sub-project (besides collection tasks).

## Frontend

**New route**: `/sports/markets` (independent of the learning dashboard)

**New components** (in `frontend/src/components/sports/markets/`):
1. `MarketLinksTable.tsx` — link list table (match_id / source / market_question / implied_prob / verified badge)
2. `PendingReviewQueue.tsx` — pending verification queue (card layout; each card shows market question + rule/LLM match confidence + confirm/reject buttons)
3. `MarketSnapshotChart.tsx` — price time-series (reuse recharts LineChart, same pattern as `prediction-trajectory.tsx`)
4. `MatchMarketPanel.tsx` — single match market link panel (embeddable in a future `/sports/matches/[id]` detail page)

**Navigation entry**: `app-nav.tsx` inserts `体育市场 → /sports/markets` after `/sports/learning`, before `/world-cup`.

**API client**: `frontend/src/lib/sport-markets-api.ts` (independent of `learning-api.ts` and `sports-api.ts`).

## Integration Points

### Zero-invasion modules (NOT modified in Phase 7)

- `PredictionKernel` / `PredictionEngine` / `FeatureSet` / `domain.py`
- `LearningService` / 3 learning tables (`KernelPredictionHistory`, `KernelCalibration`, `KernelEngineScore`)
- Learning dashboard components (all 6 + pages)
- `event_market_link_store` / `event_intelligence_service`
- `polymarket_event_source` / `kalshi_event_source` (new `polymarket_sports_source` exists in parallel)

### Modified modules

- `odds_api_service.py` — extend to multi-league support (Section "Traditional sportsbook odds extension")
- `app-nav.tsx` — add 1 navigation entry
- `config.py` — add Phase 7 configuration items
- `scheduler.py` — register 3 new scheduled tasks
- `kernel_db.py` — append new table class definitions and query functions (separate from learning tables)
- NBA/MLB/NHL `feature_builder` — enable odds input (previously hardcoded `odds_home=None`)
- `football/adapters/_shared.py` — pass `competition` parameter to `get_odds`

### New module manifest

```
backend/app/
├── api/routes/sport_markets.py
├── kernel/
│   ├── sport_market_bridge_service.py    # three-layer matching orchestration
│   ├── sport_market_link_store.py        # link persistence
│   └── market_snapshot_store.py          # snapshot persistence
├── services/
│   ├── polymarket_sports_source.py       # Polymarket sports collection
│   └── sport_market_detector.py          # market detector
├── sports/_shared/
│   └── team_aliases.py                   # TeamAliasRegistry
└── utils/
    └── implied_prob.py                   # implied probability calculation (unified)

frontend/src/
├── app/sports/markets/
│   ├── page.tsx                          # bridge management main page
│   └── loading.tsx
├── components/sports/markets/
│   ├── MarketLinksTable.tsx
│   ├── PendingReviewQueue.tsx
│   ├── MarketSnapshotChart.tsx
│   └── MatchMarketPanel.tsx
└── lib/sport-markets-api.ts

scripts/
└── sport_market_bridge_cli.py            # manual verification CLI

backend/tests/
├── test_team_aliases.py
├── test_implied_prob.py
├── test_sport_market_detector.py
├── test_polymarket_sports_source.py
├── test_sport_market_bridge.py
├── test_sport_market_routes.py
└── test_odds_api_multi_league.py
```

## Configuration (`config.py`)

```python
PHASE7_SPORT_MARKET_BRIDGE_ENABLED: bool = False
POLYMARKET_SPORTS_DISCOVERY_INTERVAL_MIN: int = 60
ODDS_API_FETCH_INTERVAL_HOURS: int = 6
ODDS_API_CACHE_TTL_HOURS: int = 6
MARKET_SNAPSHOT_INTERVAL_MIN: int = 1
SPORT_MARKET_LLM_CONFIDENCE_THRESHOLD: float = 0.85
SPORT_MARKET_RULE_CONFIDENCE_THRESHOLD: float = 0.9
```

All default to OFF / conservative values to maintain backward compatibility.

## Testing Strategy

### Backend TDD (strict RED → GREEN)

| Test file | Coverage | Key scenarios |
|-----------|----------|---------------|
| `test_team_aliases.py` | TeamAliasRegistry | 10-league coverage, EN/CN name variants, unknown name returns None |
| `test_implied_prob.py` | Implied probability calc | Polymarket spread calculation, traditional sportsbook vigorish removal normalization, edge case (odds=0 raises) |
| `test_sport_market_detector.py` | Market detector | Team name detection, sport/league inference, date parsing, futures exclusion, traditional sportsbook passthrough |
| `test_polymarket_sports_source.py` | Polymarket sports collection | Keyword filtering, futures exclusion, deduplication, API failure graceful degradation |
| `test_sport_market_bridge.py` | Three-layer matching orchestration | Rule-layer high-confidence auto-verified, LLM fallback, confidence boundaries (0.9/0.85/0.6), fail-closed on unverified |
| `test_sport_market_routes.py` | API endpoints | 503 gating, verified filtering, pending list, verify write op, snapshots time-series |
| `test_odds_api_multi_league.py` | The Odds API extension | 10-league mapping, cache hit, uncovered league graceful degradation, World Cup regression |

**Test constraints**:
- Backend DB tests use `tmp_path` real SQLite, no mocks (inherit learning-dashboard pattern)
- LLM layer tests use mock `llm_gateway_service` (no real API calls, but verify prompt construction and JSON parsing)
- Polymarket/The Odds API collection tests use `responses` or `httpx_mock` recorded playback
- Existing World Cup odds tests must pass with zero modifications (regression protection)

### Frontend tests

- `MarketLinksTable` / `PendingReviewQueue` / `MarketSnapshotChart` each get 4-6 tests
- `next/link` mock (inherit `trades/page.test.tsx` pattern)
- recharts mock (inherit `reliability-chart.test.tsx` pattern)

**Estimate**: ~55 backend tests + ~20 frontend tests = ~75 new tests

## Phase Boundaries

### In scope (Sub-project A)

- `match_id ↔ contract_id` mapping (three-layer matching)
- Polymarket sports market collection (single-match class only)
- The Odds API multi-league extension
- Implied probability extraction and snapshotting
- Bridge management frontend (link list + pending queue + price chart)
- Manual verification CLI + API

### Out of scope (deferred to B/C/D and later)

- Edge calculation (model vs market divergence) → Sub-project B
- `ActionableRecommendation` extension for sports → Sub-project C
- Market settlement price feedback into learning loop → Sub-project D
- Futures/championship markets (e.g., NBA Championship) → future extension
- Kalshi sports markets (CFTC regulatory complexity) → future extension
- Real-time price push (WebSocket) → current design uses polling snapshots
- Automated trading/order placement → never (project principle: informational assistance only)

## Success Criteria

1. **Functional completeness**:
   - 10-league team alias registry fully covered
   - Polymarket sports single-match markets can be collected and identified
   - The Odds API 10-league odds can be fetched
   - Three-layer matching engine: coverage ≥ 80% (rule + LLM combined), accuracy ≥ 95% (correct links among verified)

2. **Zero regression**:
   - World Cup existing odds logic passes tests with zero modifications
   - `PredictionKernel` / `LearningService` / learning dashboard zero modifications
   - Existing `polymarket_event_source` / `event_intelligence_service` zero modifications

3. **Safety gating**:
   - `PHASE7_SPORT_MARKET_BRIDGE_ENABLED=false` → all new endpoints return 503
   - `get_verified_links` returns only verified links
   - Collection tasks are not scheduled when feature flag is off

4. **Operability**:
   - Manual verification CLI can list pending links and confirm/reject
   - Frontend pending queue is operable
   - Price snapshot time-series is traceable

## Estimate

- **New files**: 14 backend + 7 frontend + 6 tests + 1 CLI = 28 files
- **Modified files**: 5 (config / scheduler / kernel_db / odds_api_service / app-nav) + 4 feature_builders + 1 _shared = 10
- **Code volume**: +~3,500 lines
- **Tests**: ~75 new tests
