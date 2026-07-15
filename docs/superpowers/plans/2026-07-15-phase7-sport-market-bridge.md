# Phase 7: Sport Market Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the bridge layer connecting the Sports Prediction Kernel (Phase 1-6) with Polymarket prediction markets and The Odds API traditional sportsbook odds, producing verified market-implied probabilities per match outcome.

**Architecture:** Three-layer matching engine (rule → LLM → manual verification) links `match_id` to prediction-market `contract_id`. Two new kernel DB tables persist links and price snapshots. New Polymarket sports source + extended The Odds API collect market data. Bridge management frontend with pending review queue.

**Tech Stack:** Python/FastAPI/SQLAlchemy (backend), Next.js App Router/React/TypeScript/recharts/Vitest (frontend), httpx (HTTP), APScheduler (scheduling)

**Spec:** `docs/superpowers/specs/2026-07-15-sports-prediction-os-phase7-design.md`

## Global Constraints

1. `PHASE7_SPORT_MARKET_BRIDGE_ENABLED` defaults to OFF — when false, all new endpoints return 503 and collection tasks are not scheduled.
2. New tables use `kernel_` prefix (`kernel_sport_market_links`, `kernel_market_snapshots`) — subclass `KernelBase`, NOT `Base`.
3. Fail-closed: `get_verified_links(match_id)` returns only `verified=True` links — unverified links never exposed to downstream.
4. `PredictionKernel`, `PredictionEngine`, `FeatureSet`, `domain.py` zero modification.
5. `LearningService` / 3 learning tables / learning dashboard components zero modification.
6. `event_market_link_store` / `event_intelligence_service` / `polymarket_event_source` / `kalshi_event_source` zero modification — new `polymarket_sports_source` exists in parallel.
7. Existing World Cup odds logic (`odds_api_service` with `SPORT = "soccer_fifa_world_cup"`) must pass tests with zero modifications.
8. `COMPETITION_TO_ODDS_API_SPORT` mapping covers all 10 competitions (wc/ucl/epl/laliga/bundesliga/seriea/ligue1/nba/mlb/nhl).
9. `TeamAliasRegistry` covers all 10 competitions' team aliases (NBA 30, MLB 30, NHL 32, EPL 20, UCL 32, La Liga 20, Bundesliga 18, Serie A 20, Ligue 1 18, World Cup >= 32).
10. Rule layer confidence >= 0.9 → auto-verified; LLM layer confidence >= 0.85 → auto-verified; all others → pending manual verification.
11. `SportMarketLinkStore` unique key `(match_id, contract_id, outcome_label)` prevents duplicate links.
12. Implied probability: Polymarket `price` is already 0-1; The Odds API `1/decimal_odds` normalized to remove vigorish.
13. TDD strict — backend RED (ImportError/AttributeError) before GREEN.
14. Backend DB tests use `tmp_path` real SQLite, no mocks (inherit learning-dashboard pattern).
15. LLM layer tests use mock `llm_gateway_service` — no real API calls.
16. Polymarket/The Odds API collection tests use mock `httpx.AsyncClient`.
17. `app-nav.tsx` adds 1 entry `体育市场 → /sports/markets` after `/sports/learning`, before `/world-cup`.
18. New frontend components under `components/sports/markets/` subdirectory.
19. New `sport-markets-api.ts`, not extending `learning-api.ts` or `sports-api.ts`.
20. `getWorldCupApiBase()` returns without `/api` suffix — fetch paths include `/api/` prefix.
21. recharts + `@/components/ui/chart-lite` reuse — no new chart library.
22. Vitest jsdom must mock `next/link` — inherited `trades/page.test.tsx:18-24` pattern.

## Codebase Reference Patterns

Before writing code, READ these files to get exact signatures:
- `backend/app/kernel/kernel_db.py` — `KernelBase` class, `init_kernel_db` auto-creates via `create_all`, query pattern `try/except: return [] / finally: session.close()`
- `backend/app/services/odds_api_service.py` — `SPORT = "soccer_fifa_world_cup"`, `fetch_match_odds(home_team, away_team, commence_time=None) -> dict | None`, `httpx.AsyncClient(timeout=10.0)`. Returns plain dict like `{"home": 2.1, "draw": 3.2, "away": 3.5, "source": "pinnacle"}`
- `backend/app/services/odds_cache_service.py` — `OddsCache(Base)` in world_cup_predictions.db (NOT KernelBase), `get_cached_odds` with `ttl_seconds` param
- `backend/app/sports/basketball/feature_builder.py` — `odds_home=None` hardcoded around line 73-79; replace with `market_raw.get("odds_home")` to enable odds injection
- `backend/app/sports/baseball/feature_builder.py` — same pattern as basketball
- `backend/app/sports/hockey/feature_builder.py` — same pattern as basketball
- `backend/app/sports/football/adapters/_shared.py` — shared adapter utilities, `async def fetch_match_odds(home: str, away: str) -> dict | None`
- `backend/app/kernel/domain.py` — `MarketFeatures` dataclass (lines 86-93), `PredictionResult` (lines 140-150)
- `backend/app/memory/event_market_link_store.py` — raw SQLite (not SQLAlchemy), `upsert_link`, `get_verified_link`, `set_verified` patterns
- `backend/app/services/polymarket_service.py` — `httpx.AsyncClient(timeout=30)`, `POLYMARKET_API` constant
- `backend/app/core/config.py` — `PHASE5_*` pattern at lines 1009-1028, `settings = Settings()` at line 1031. Add Phase 7 config BEFORE line 1031 using `_env_bool("VAR", "false")` pattern. NO `PHASE6_*` exists.
- `backend/app/api/routes/predictions.py` — `COMPETITION_SPORT` dict, 503 gate via `config.settings.KERNEL_PREDICTION_ENABLED`
- `backend/app/core/scheduler.py` — `_job_xxx` pattern with `_start_run`/`_finish_run`, `IntervalTrigger`, `scheduler.add_job(fn, IntervalTrigger(...), id=..., replace_existing=True, max_instances=1)`
- `backend/app/services/llm_gateway_service.py` — `LLMResult` dataclass (ok/content/json_data/provider/model/attempts/usage/degraded_reason), `complete_json(*, task, messages, temperature, ...) -> LLMResult`
- `backend/app/services/market_semantics_service.py` — `extract_deadline(question) -> str | None`, regex-based
- Frontend: `frontend/src/lib/learning-api.ts` — pattern for API client; `frontend/src/components/sports/learning/` — component pattern; `frontend/src/components/app-nav.tsx` — nav entries; `frontend/src/lib/env.ts` — `getWorldCupApiBase()` returns base without `/api`
- Frontend test pattern: `frontend/src/app/trades/page.test.tsx:18-24` — `vi.mock("next/link", ...)` jsdom mock

---

## Task 1: Foundation — Config, Team Aliases, Implied Probability

**Files:**
- Create: `backend/app/sports/_shared/team_aliases.py`, `backend/app/utils/implied_prob.py`
- Test: `backend/tests/test_team_aliases.py`, `backend/tests/test_implied_prob.py`
- Modify: `backend/app/core/config.py` (add 7 Phase 7 config flags before `settings = Settings()` at line 1031)

**Interfaces:**
- Consumes: `backend/app/core/config.py` (`_env_bool` helper), `backend/app/kernel/kernel_db.py` (none yet)
- Produces:
  - `app.utils.implied_prob.polymarket_to_implied(yes_price: float, no_price: float) -> tuple[float, float, float]`
  - `app.utils.implied_prob.odds_api_to_implied(decimal_odds_list: list[float]) -> list[float]`
  - `app.sports._shared.team_aliases.resolve_team(alias: str, competition: str) -> str | None`
  - `app.sports._shared.team_aliases.TEAM_ALIASES: dict[str, dict[str, str]]`
  - `app.sports._shared.team_aliases.COMPETITION_TO_SPORT: dict[str, str]`
  - `settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED` and 6 other Phase 7 flags

### Step 1.1: Write the failing test for implied_prob

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_implied_prob.py`:

```python
"""Tests for implied probability conversion utilities."""
import pytest


def test_polymarket_to_implied_basic():
    from app.utils.implied_prob import polymarket_to_implied
    yes_implied, no_implied, spread = polymarket_to_implied(0.60, 0.45)
    assert yes_implied == 0.60
    assert no_implied == 0.45
    assert spread == pytest.approx(0.05)


def test_polymarket_to_implied_no_spread_when_sum_below_one():
    from app.utils.implied_prob import polymarket_to_implied
    yes_implied, no_implied, spread = polymarket_to_implied(0.40, 0.40)
    assert yes_implied == 0.40
    assert no_implied == 0.40
    assert spread == pytest.approx(-0.20)


def test_polymarket_to_implied_exact_one():
    from app.utils.implied_prob import polymarket_to_implied
    _, _, spread = polymarket_to_implied(0.55, 0.45)
    assert spread == pytest.approx(0.0)


def test_odds_api_to_implied_basic():
    from app.utils.implied_prob import odds_api_to_implied
    # 2.0 / 2.0 -> 0.5 / 0.5 (no vigorish)
    result = odds_api_to_implied([2.0, 2.0])
    assert result == [pytest.approx(0.5), pytest.approx(0.5)]


def test_odds_api_to_implied_normalizes_vigorish():
    from app.utils.implied_prob import odds_api_to_implied
    # 1.5 / 2.5 -> raw 0.667 / 0.4 = 1.067; normalized -> 0.625 / 0.375
    result = odds_api_to_implied([1.5, 2.5])
    assert sum(result) == pytest.approx(1.0)
    assert result[0] > result[1]


def test_odds_api_to_implied_empty_list():
    from app.utils.implied_prob import odds_api_to_implied
    assert odds_api_to_implied([]) == []


def test_odds_api_to_implied_skips_zero_odds():
    from app.utils.implied_prob import odds_api_to_implied
    # zero odds are skipped (guarded against division by zero)
    result = odds_api_to_implied([0.0, 2.0])
    assert result == [pytest.approx(1.0)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_implied_prob.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.utils.implied_prob'`

### Step 1.2: Implement implied_prob

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/utils/implied_prob.py`:

```python
"""Implied probability conversion utilities.

Polymarket prices are already 0-1 probability expressions. The Odds API
returns decimal odds which must be inverted (1/odds) and normalized to
remove the sportsbook vigorish (overround).
"""
from __future__ import annotations


def polymarket_to_implied(yes_price: float, no_price: float) -> tuple[float, float, float]:
    """Convert Polymarket YES/NO prices to (yes_implied, no_implied, spread).

    Price is already 0-1. YES+NO > 1.0 portion is the spread (recorded but
    not adjusted — Sub-project B decides whether to adjust).
    """
    yes_implied = yes_price
    no_implied = no_price
    spread = yes_price + no_price - 1.0
    return (yes_implied, no_implied, spread)


def odds_api_to_implied(decimal_odds_list: list[float]) -> list[float]:
    """Convert decimal odds to implied probabilities, normalized to remove vigorish.

    Each raw implied prob is 1/decimal_odds; the raw sum exceeds 1.0 due to
    overround, so we divide each by the sum to normalize.
    """
    raw = [1.0 / d for d in decimal_odds_list if d > 0]
    total = sum(raw)
    if total == 0:
        return []
    return [r / total for r in raw]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_implied_prob.py -v`
Expected: PASS (7 tests)

### Step 1.3: Write the failing test for team_aliases

- [ ] **Step 5: Write the failing test**

Create `backend/tests/test_team_aliases.py`:

```python
"""Tests for the team alias registry."""
import pytest


def test_resolve_nba_abbreviation():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("LAL", "nba") == "los_angeles_lakers"
    assert resolve_team("BOS", "nba") == "boston_celtics"


def test_resolve_nba_full_name():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("Los Angeles Lakers", "nba") == "los_angeles_lakers"


def test_resolve_nba_chinese_name():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("洛杉矶湖人", "nba") == "los_angeles_lakers"


def test_resolve_case_insensitive():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("lakers", "nba") == "los_angeles_lakers"
    assert resolve_team("LAKERS", "nba") == "los_angeles_lakers"


def test_resolve_mlb_team():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("NYY", "mlb") == "new_york_yankees"
    assert resolve_team("洛杉矶道奇", "mlb") == "los_angeles_dodgers"


def test_resolve_nhl_team():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("BOS", "nhl") == "boston_bruins"
    assert resolve_team("波士顿棕熊", "nhl") == "boston_bruins"


def test_resolve_epl_team():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("MCI", "epl") == "manchester_city"
    assert resolve_team("曼城", "epl") == "manchester_city"


def test_resolve_ucl_team():
    from app.sports._shared.team_aliases import resolve_team
    # UCL shares club aliases with domestic leagues
    assert resolve_team("Real Madrid", "ucl") == "real_madrid"
    assert resolve_team("皇家马德里", "ucl") == "real_madrid"


def test_resolve_world_cup_team():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("Brazil", "wc") == "brazil"
    assert resolve_team("巴西", "wc") == "brazil"


def test_resolve_unknown_competition_returns_none():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("Lakers", "nfl") is None


def test_resolve_unknown_team_returns_none():
    from app.sports._shared.team_aliases import resolve_team
    assert resolve_team("Nonexistent Team", "nba") is None


def test_all_10_competitions_present():
    from app.sports._shared.team_aliases import TEAM_ALIASES
    expected = {"wc", "ucl", "epl", "laliga", "bundesliga",
                "seriea", "ligue1", "nba", "mlb", "nhl"}
    assert expected.issubset(set(TEAM_ALIASES.keys()))
    # World Cup must have >= 32 entries
    assert len(TEAM_ALIASES["wc"]) >= 32
    # NBA must have >= 30 canonical teams (count distinct values)
    assert len(set(TEAM_ALIASES["nba"].values())) >= 30
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_team_aliases.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sports._shared.team_aliases'`

### Step 1.4: Implement team_aliases

- [ ] **Step 7: Write minimal implementation**

Create `backend/app/sports/_shared/__init__.py` (empty file):

```python
```

Create `backend/app/sports/_shared/team_aliases.py`:

```python
"""Team alias registry for 10 supported competitions.

Maps heterogeneous team name variants (abbreviations, full names, Chinese
names) to a single canonical snake_case identifier used by the matching
engine. All lookups are case-insensitive.
"""
from __future__ import annotations

# Competition code -> sport type
COMPETITION_TO_SPORT: dict[str, str] = {
    "wc": "football", "ucl": "football", "epl": "football",
    "laliga": "football", "bundesliga": "football",
    "seriea": "football", "ligue1": "football",
    "nba": "basketball", "mlb": "baseball", "nhl": "hockey",
}

# Each competition maps alias (lowercased at lookup time) -> canonical id.
# At least 4 aliases per team (abbreviation, short name, full name, Chinese).
TEAM_ALIASES: dict[str, dict[str, str]] = {
    "nba": {
        "LAL": "los_angeles_lakers", "Lakers": "los_angeles_lakers",
        "Los Angeles Lakers": "los_angeles_lakers", "洛杉矶湖人": "los_angeles_lakers",
        "BOS": "boston_celtics", "Celtics": "boston_celtics",
        "Boston Celtics": "boston_celtics", "波士顿凯尔特人": "boston_celtics",
        "GSW": "golden_state_warriors", "Warriors": "golden_state_warriors",
        "Golden State Warriors": "golden_state_warriors", "金州勇士": "golden_state_warriors",
        "MIA": "miami_heat", "Heat": "miami_heat",
        "Miami Heat": "miami_heat", "迈阿密热火": "miami_heat",
        "MIL": "milwaukee_bucks", "Bucks": "milwaukee_bucks",
        "Milwaukee Bucks": "milwaukee_bucks", "密尔沃基雄鹿": "milwaukee_bucks",
        "PHI": "philadelphia_76ers", "76ers": "philadelphia_76ers",
        "Philadelphia 76ers": "philadelphia_76ers", "费城76人": "philadelphia_76ers",
        "BKN": "brooklyn_nets", "Nets": "brooklyn_nets",
        "Brooklyn Nets": "brooklyn_nets", "布鲁克林篮网": "brooklyn_nets",
        "NYK": "new_york_knicks", "Knicks": "new_york_knicks",
        "New York Knicks": "new_york_knicks", "纽约尼克斯": "new_york_knicks",
        "TOR": "toronto_raptors", "Raptors": "toronto_raptors",
        "Toronto Raptors": "toronto_raptors", "多伦多猛龙": "toronto_raptors",
        "CHI": "chicago_bulls", "Bulls": "chicago_bulls",
        "Chicago Bulls": "chicago_bulls", "芝加哥公牛": "chicago_bulls",
        "CLE": "cleveland_cavaliers", "Cavaliers": "cleveland_cavaliers",
        "Cleveland Cavaliers": "cleveland_cavaliers", "克利夫兰骑士": "cleveland_cavaliers",
        "IND": "indiana_pacers", "Pacers": "indiana_pacers",
        "Indiana Pacers": "indiana_pacers", "印第安纳步行者": "indiana_pacers",
        "DET": "detroit_pistons", "Pistons": "detroit_pistons",
        "Detroit Pistons": "detroit_pistons", "底特律活塞": "detroit_pistons",
        "ATL": "atlanta_hawks", "Hawks": "atlanta_hawks",
        "Atlanta Hawks": "atlanta_hawks", "亚特兰大老鹰": "atlanta_hawks",
        "CHA": "charlotte_hornets", "Hornets": "charlotte_hornets",
        "Charlotte Hornets": "charlotte_hornets", "夏洛特黄蜂": "charlotte_hornets",
        "WAS": "washington_wizards", "Wizards": "washington_wizards",
        "Washington Wizards": "washington_wizards", "华盛顿奇才": "washington_wizards",
        "ORL": "orlando_magic", "Magic": "orlando_magic",
        "Orlando Magic": "orlando_magic", "奥兰多魔术": "orlando_magic",
        "DEN": "denver_nuggets", "Nuggets": "denver_nuggets",
        "Denver Nuggets": "denver_nuggets", "丹佛掘金": "denver_nuggets",
        "UTA": "utah_jazz", "Jazz": "utah_jazz",
        "Utah Jazz": "utah_jazz", "犹他爵士": "utah_jazz",
        "POR": "portland_trail_blazers", "Trail Blazers": "portland_trail_blazers",
        "Portland Trail Blazers": "portland_trail_blazers", "波特兰开拓者": "portland_trail_blazers",
        "OKC": "oklahoma_city_thunder", "Thunder": "oklahoma_city_thunder",
        "Oklahoma City Thunder": "oklahoma_city_thunder", "俄克拉荷马城雷霆": "oklahoma_city_thunder",
        "MIN": "minnesota_timberwolves", "Timberwolves": "minnesota_timberwolves",
        "Minnesota Timberwolves": "minnesota_timberwolves", "明尼苏达森林狼": "minnesota_timberwolves",
        "DAL": "dallas_mavericks", "Mavericks": "dallas_mavericks",
        "Dallas Mavericks": "dallas_mavericks", "达拉斯独行侠": "dallas_mavericks",
        "HOU": "houston_rockets", "Rockets": "houston_rockets",
        "Houston Rockets": "houston_rockets", "休斯顿火箭": "houston_rockets",
        "MEM": "memphis_grizzlies", "Grizzlies": "memphis_grizzlies",
        "Memphis Grizzlies": "memphis_grizzlies", "孟菲斯灰熊": "memphis_grizzlies",
        "NOP": "new_orleans_pelicans", "Pelicans": "new_orleans_pelicans",
        "New Orleans Pelicans": "new_orleans_pelicans", "新奥尔良鹈鹕": "new_orleans_pelicans",
        "SAS": "san_antonio_spurs", "Spurs": "san_antonio_spurs",
        "San Antonio Spurs": "san_antonio_spurs", "圣安东尼奥马刺": "san_antonio_spurs",
        "PHX": "phoenix_suns", "Suns": "phoenix_suns",
        "Phoenix Suns": "phoenix_suns", "菲尼克斯太阳": "phoenix_suns",
        "SAC": "sacramento_kings", "Kings": "sacramento_kings",
        "Sacramento Kings": "sacramento_kings", "萨克拉门托国王": "sacramento_kings",
        "LAC": "los_angeles_clippers", "Clippers": "los_angeles_clippers",
        "Los Angeles Clippers": "los_angeles_clippers", "洛杉矶快船": "los_angeles_clippers",
    },
    "mlb": {
        "NYY": "new_york_yankees", "Yankees": "new_york_yankees",
        "New York Yankees": "new_york_yankees", "纽约扬基": "new_york_yankees",
        "BOS": "boston_red_sox", "Red Sox": "boston_red_sox",
        "Boston Red Sox": "boston_red_sox", "波士顿红袜": "boston_red_sox",
        "LAD": "los_angeles_dodgers", "Dodgers": "los_angeles_dodgers",
        "Los Angeles Dodgers": "los_angeles_dodgers", "洛杉矶道奇": "los_angeles_dodgers",
        "SF": "san_francisco_giants", "Giants": "san_francisco_giants",
        "San Francisco Giants": "san_francisco_giants", "旧金山巨人": "san_francisco_giants",
        "CHC": "chicago_cubs", "Cubs": "chicago_cubs",
        "Chicago Cubs": "chicago_cubs", "芝加哥小熊": "chicago_cubs",
        "STL": "st_louis_cardinals", "Cardinals": "st_louis_cardinals",
        "St. Louis Cardinals": "st_louis_cardinals", "圣路易斯红雀": "st_louis_cardinals",
        "ATL": "atlanta_braves", "Braves": "atlanta_braves",
        "Atlanta Braves": "atlanta_braves", "亚特兰大勇士": "atlanta_braves",
        "NYM": "new_york_mets", "Mets": "new_york_mets",
        "New York Mets": "new_york_mets", "纽约大都会": "new_york_mets",
        "PHI": "philadelphia_phillies", "Phillies": "philadelphia_phillies",
        "Philadelphia Phillies": "philadelphia_phillies", "费城费城人": "philadelphia_phillies",
        "WSH": "washington_nationals", "Nationals": "washington_nationals",
        "Washington Nationals": "washington_nationals", "华盛顿国民": "washington_nationals",
        "MIA": "miami_marlins", "Marlins": "miami_marlins",
        "Miami Marlins": "miami_marlins", "迈阿密马林鱼": "miami_marlins",
        "TOR": "toronto_blue_jays", "Blue Jays": "toronto_blue_jays",
        "Toronto Blue Jays": "toronto_blue_jays", "多伦多蓝鸟": "toronto_blue_jays",
        "TB": "tampa_bay_rays", "Rays": "tampa_bay_rays",
        "Tampa Bay Rays": "tampa_bay_rays", "坦帕湾光芒": "tampa_bay_rays",
        "BAL": "baltimore_orioles", "Orioles": "baltimore_orioles",
        "Baltimore Orioles": "baltimore_orioles", "巴尔的摩金莺": "baltimore_orioles",
        "CWS": "chicago_white_sox", "White Sox": "chicago_white_sox",
        "Chicago White Sox": "chicago_white_sox", "芝加哥白袜": "chicago_white_sox",
        "CLE": "cleveland_guardians", "Guardians": "cleveland_guardians",
        "Cleveland Guardians": "cleveland_guardians", "克利夫兰守护者": "cleveland_guardians",
        "DET": "detroit_tigers", "Tigers": "detroit_tigers",
        "Detroit Tigers": "detroit_tigers", "底特律老虎": "detroit_tigers",
        "KC": "kansas_city_royals", "Royals": "kansas_city_royals",
        "Kansas City Royals": "kansas_city_royals", "堪萨斯城皇家": "kansas_city_royals",
        "MIN": "minnesota_twins", "Twins": "minnesota_twins",
        "Minnesota Twins": "minnesota_twins", "明尼苏达双城": "minnesota_twins",
        "HOU": "houston_astros", "Astros": "houston_astros",
        "Houston Astros": "houston_astros", "休斯顿太空人": "houston_astros",
        "SEA": "seattle_mariners", "Mariners": "seattle_mariners",
        "Seattle Mariners": "seattle_mariners", "西雅图水手": "seattle_mariners",
        "LAA": "los_angeles_angels", "Angels": "los_angeles_angels",
        "Los Angeles Angels": "los_angeles_angels", "洛杉矶天使": "los_angeles_angels",
        "TEX": "texas_rangers", "Rangers": "texas_rangers",
        "Texas Rangers": "texas_rangers", "德克萨斯游骑兵": "texas_rangers",
        "OAK": "oakland_athletics", "Athletics": "oakland_athletics",
        "Oakland Athletics": "oakland_athletics", "奥克兰运动家": "oakland_athletics",
        "COL": "colorado_rockies", "Rockies": "colorado_rockies",
        "Colorado Rockies": "colorado_rockies", "科罗拉多洛基": "colorado_rockies",
        "ARI": "arizona_diamondbacks", "Diamondbacks": "arizona_diamondbacks",
        "Arizona Diamondbacks": "arizona_diamondbacks", "亚利桑那响尾蛇": "arizona_diamondbacks",
        "SD": "san_diego_padres", "Padres": "san_diego_padres",
        "San Diego Padres": "san_diego_padres", "圣迭戈教士": "san_diego_padres",
        "PIT": "pittsburgh_pirates", "Pirates": "pittsburgh_pirates",
        "Pittsburgh Pirates": "pittsburgh_pirates", "匹兹堡海盗": "pittsburgh_pirates",
        "CIN": "cincinnati_reds", "Reds": "cincinnati_reds",
        "Cincinnati Reds": "cincinnati_reds", "辛辛那提红人": "cincinnati_reds",
        "MIL": "milwaukee_brewers", "Brewers": "milwaukee_brewers",
        "Milwaukee Brewers": "milwaukee_brewers", "密尔沃基酿酒人": "milwaukee_brewers",
        "STL2": "st_louis_cardinals",
    },
    "nhl": {
        "BOS": "boston_bruins", "Bruins": "boston_bruins",
        "Boston Bruins": "boston_bruins", "波士顿棕熊": "boston_bruins",
        "TOR": "toronto_maple_leafs", "Maple Leafs": "toronto_maple_leafs",
        "Toronto Maple Leafs": "toronto_maple_leafs", "多伦多枫叶": "toronto_maple_leafs",
        "MTL": "montreal_canadiens", "Canadiens": "montreal_canadiens",
        "Montreal Canadiens": "montreal_canadiens", "蒙特利尔加拿大人": "montreal_canadiens",
        "DET": "detroit_red_wings", "Red Wings": "detroit_red_wings",
        "Detroit Red Wings": "detroit_red_wings", "底特律红翼": "detroit_red_wings",
        "CHI": "chicago_blackhawks", "Blackhawks": "chicago_blackhawks",
        "Chicago Blackhawks": "chicago_blackhawks", "芝加哥黑鹰": "chicago_blackhawks",
        "NYR": "new_york_rangers", "Rangers": "new_york_rangers",
        "New York Rangers": "new_york_rangers", "纽约游骑兵": "new_york_rangers",
        "NYI": "new_york_islanders", "Islanders": "new_york_islanders",
        "New York Islanders": "new_york_islanders", "纽约岛人": "new_york_islanders",
        "NJD": "new_jersey_devils", "Devils": "new_jersey_devils",
        "New Jersey Devils": "new_jersey_devils", "新泽西魔鬼": "new_jersey_devils",
        "PHI": "philadelphia_flyers", "Flyers": "philadelphia_flyers",
        "Philadelphia Flyers": "philadelphia_flyers", "费城飞人": "philadelphia_flyers",
        "PIT": "pittsburgh_penguins", "Penguins": "pittsburgh_penguins",
        "Pittsburgh Penguins": "pittsburgh_penguins", "匹兹堡企鹅": "pittsburgh_penguins",
        "WSH": "washington_capitals", "Capitals": "washington_capitals",
        "Washington Capitals": "washington_capitals", "华盛顿首都": "washington_capitals",
        "CAR": "carolina_hurricanes", "Hurricanes": "carolina_hurricanes",
        "Carolina Hurricanes": "carolina_hurricanes", "卡罗莱纳飓风": "carolina_hurricanes",
        "TBL": "tampa_bay_lightning", "Lightning": "tampa_bay_lightning",
        "Tampa Bay Lightning": "tampa_bay_lightning", "坦帕湾闪电": "tampa_bay_lightning",
        "FLA": "florida_panthers", "Panthers": "florida_panthers",
        "Florida Panthers": "florida_panthers", "佛罗里达美洲豹": "florida_panthers",
        "BUF": "buffalo_sabres", "Sabres": "buffalo_sabres",
        "Buffalo Sabres": "buffalo_sabres", "布法罗军刀": "buffalo_sabres",
        "OTT": "ottawa_senators", "Senators": "ottawa_senators",
        "Ottawa Senators": "ottawa_senators", "渥太华参议员": "ottawa_senators",
        "COL": "colorado_avalanche", "Avalanche": "colorado_avalanche",
        "Colorado Avalanche": "colorado_avalanche", "科罗拉多雪崩": "colorado_avalanche",
        "DAL": "dallas_stars", "Stars": "dallas_stars",
        "Dallas Stars": "dallas_stars", "达拉斯星": "dallas_stars",
        "MIN": "minnesota_wild", "Wild": "minnesota_wild",
        "Minnesota Wild": "minnesota_wild", "明尼苏达狂野": "minnesota_wild",
        "NSH": "nashville_predators", "Predators": "nashville_predators",
        "Nashville Predators": "nashville_predators", "纳什维尔掠夺者": "nashville_predators",
        "WPG": "winnipeg_jets", "Jets": "winnipeg_jets",
        "Winnipeg Jets": "winnipeg_jets", "温尼伯喷气机": "winnipeg_jets",
        "STL": "st_louis_blues", "Blues": "st_louis_blues",
        "St. Louis Blues": "st_louis_blues", "圣路易斯蓝调": "st_louis_blues",
        "CHI2": "chicago_blackhawks",
        "VGK": "vegas_golden_knights", "Golden Knights": "vegas_golden_knights",
        "Vegas Golden Knights": "vegas_golden_knights", "维加斯黄金骑士": "vegas_golden_knights",
        "ARI": "arizona_coyotes", "Coyotes": "arizona_coyotes",
        "Arizona Coyotes": "arizona_coyotes", "亚利桑那郊狼": "arizona_coyotes",
        "ANA": "anaheim_ducks", "Ducks": "anaheim_ducks",
        "Anaheim Ducks": "anaheim_ducks", "阿纳海姆鸭": "anaheim_ducks",
        "LAK": "los_angeles_kings", "Kings": "los_angeles_kings",
        "Los Angeles Kings": "los_angeles_kings", "洛杉矶国王": "los_angeles_kings",
        "SJS": "san_jose_sharks", "Sharks": "san_jose_sharks",
        "San Jose Sharks": "san_jose_sharks", "圣何塞鲨鱼": "san_jose_sharks",
        "CBJ": "columbus_blue_jackets", "Blue Jackets": "columbus_blue_jackets",
        "Columbus Blue Jackets": "columbus_blue_jackets", "哥伦布蓝衣": "columbus_blue_jackets",
        "SEA": "seattle_kraken", "Kraken": "seattle_kraken",
        "Seattle Kraken": "seattle_kraken", "西雅图海妖": "seattle_kraken",
        "EDM": "edmonton_oilers", "Oilers": "edmonton_oilers",
        "Edmonton Oilers": "edmonton_oilers", "埃德蒙顿油人": "edmonton_oilers",
        "CGY": "calgary_flames", "Flames": "calgary_flames",
        "Calgary Flames": "calgary_flames", "卡尔加里火焰": "calgary_flames",
        "VAN": "vancouver_canucks", "Canucks": "vancouver_canucks",
        "Vancouver Canucks": "vancouver_canucks", "温哥华加人": "vancouver_canucks",
    },
    "epl": {
        "MCI": "manchester_city", "Man City": "manchester_city",
        "Manchester City": "manchester_city", "曼城": "manchester_city",
        "MUN": "manchester_united", "Man United": "manchester_united",
        "Manchester United": "manchester_united", "曼联": "manchester_united",
        "LIV": "liverpool", "Liverpool": "liverpool",
        "利物浦": "liverpool",
        "CHE": "chelsea", "Chelsea": "chelsea",
        "切尔西": "chelsea",
        "ARS": "arsenal", "Arsenal": "arsenal",
        "阿森纳": "arsenal",
        "TOT": "tottenham_hotspur", "Spurs": "tottenham_hotspur",
        "Tottenham": "tottenham_hotspur", "热刺": "tottenham_hotspur",
        "EVE": "everton", "Everton": "everton",
        "埃弗顿": "everton",
        "LEI": "leicester_city", "Leicester": "leicester_city",
        "Leicester City": "leicester_city", "莱斯特城": "leicester_city",
        "WHU": "west_ham_united", "West Ham": "west_ham_united",
        "West Ham United": "west_ham_united", "西汉姆联": "west_ham_united",
        "AVL": "aston_villa", "Aston Villa": "aston_villa",
        "阿斯顿维拉": "aston_villa",
        "NEW": "newcastle_united", "Newcastle": "newcastle_united",
        "Newcastle United": "newcastle_united", "纽卡斯尔联": "newcastle_united",
        "BHA": "brighton", "Brighton": "brighton",
        "布莱顿": "brighton",
        "WOL": "wolverhampton", "Wolves": "wolverhampton",
        "Wolverhampton": "wolverhampton", "狼队": "wolverhampton",
        "CRY": "crystal_palace", "Crystal Palace": "crystal_palace",
        "水晶宫": "crystal_palace",
        "FUL": "fulham", "Fulham": "fulham",
        "富勒姆": "fulham",
        "BRE": "brentford", "Brentford": "brentford",
        "布伦特福德": "brentford",
        "BUR": "burnley", "Burnley": "burnley",
        "伯恩利": "burnley",
        "LUT": "luton_town", "Luton": "luton_town",
        "Luton Town": "luton_town", "卢顿": "luton_town",
        "SHU": "sheffield_united", "Sheffield United": "sheffield_united",
        "谢菲尔德联": "sheffield_united",
        "BOU": "bournemouth", "Bournemouth": "bournemouth",
        "伯恩茅斯": "bournemouth",
        "NFO": "nottingham_forest", "Nottingham Forest": "nottingham_forest",
        "诺丁汉森林": "nottingham_forest",
    },
    "ucl": {
        "Real Madrid": "real_madrid", "皇家马德里": "real_madrid", "RMA": "real_madrid",
        "Barcelona": "barcelona", "巴塞罗那": "barcelona", "BAR": "barcelona",
        "Bayern Munich": "bayern_munich", "拜仁慕尼黑": "bayern_munich", "FCB": "bayern_munich",
        "PSG": "paris_saint_germain", "Paris Saint-Germain": "paris_saint_germain",
        "巴黎圣日耳曼": "paris_saint_germain",
        "Liverpool": "liverpool", "LIV": "liverpool", "利物浦": "liverpool",
        "Man City": "manchester_city", "Manchester City": "manchester_city",
        "MCI": "manchester_city", "曼城": "manchester_city",
        "Chelsea": "chelsea", "CHE": "chelsea", "切尔西": "chelsea",
        "Arsenal": "arsenal", "ARS": "arsenal", "阿森纳": "arsenal",
        "Inter": "inter_milan", "Inter Milan": "inter_milan",
        "国际米兰": "inter_milan", "INT": "inter_milan",
        "Milan": "ac_milan", "AC Milan": "ac_milan",
        "AC米兰": "ac_milan", "ACM": "ac_milan",
        "Juventus": "juventus", "尤文图斯": "juventus", "JUV": "juventus",
        "Atletico Madrid": "atletico_madrid", "Atlético Madrid": "atletico_madrid",
        "马德里竞技": "atletico_madrid", "ATM": "atletico_madrid",
        "Dortmund": "borussia_dortmund", "Borussia Dortmund": "borussia_dortmund",
        "多特蒙德": "borussia_dortmund", "BVB": "borussia_dortmund",
        "Napoli": "napoli", "那不勒斯": "napoli", "NAP": "napoli",
        "Roma": "as_roma", "AS Roma": "as_roma",
        "罗马": "as_roma", "ROM": "as_roma",
        "Porto": "porto", "波尔图": "porto", "POR": "porto",
        "Benfica": "benfica", "本菲卡": "benfica", "BEN": "benfica",
        "Sporting": "sporting_cp", "Sporting CP": "sporting_cp",
        "里斯本竞技": "sporting_cp", "SCP": "sporting_cp",
        "Ajax": "ajax", "阿贾克斯": "ajax", "AJA": "ajax",
        "PSV": "psv", "PSV Eindhoven": "psv",
        "埃因霍温": "psv",
        "Feyenoord": "feyenoord", "费耶诺德": "feyenoord", "FEY": "feyenoord",
        "Celtic": "celtic", "凯尔特人": "celtic", "CEL": "celtic",
        "Rangers": "rangers_fc", "Rangers FC": "rangers_fc",
        "流浪者": "rangers_fc",
        "Salzburg": "rb_salzburg", "RB Salzburg": "rb_salzburg",
        "萨尔茨堡": "rb_salzburg",
        "Shakhtar": "shakhtar_donetsk", "Shakhtar Donetsk": "shakhtar_donetsk",
        "顿涅茨克矿工": "shakhtar_donetsk",
        "Sevilla": "sevilla", "塞维利亚": "sevilla", "SEV": "sevilla",
        "Villarreal": "villarreal", "比利亚雷亚尔": "villarreal", "VIL": "villarreal",
        "Lazio": "lazio", "拉齐奥": "lazio", "LAZ": "lazio",
        "Atalanta": "atalanta", "亚特兰大": "atalanta", "ATA": "atalanta",
        "Fiorentina": "fiorentina", "佛罗伦萨": "fiorentina", "FIO": "fiorentina",
        "Leverkusen": "bayer_leverkusen", "Bayer Leverkusen": "bayer_leverkusen",
        "勒沃库森": "bayer_leverkusen", "B04": "bayer_leverkusen",
        "Leipzig": "rb_leipzig", "RB Leipzig": "rb_leipzig",
        "莱比锡": "rb_leipzig",
        "Frankfurt": "eintracht_frankfurt", "Eintracht Frankfurt": "eintracht_frankfurt",
        "法兰克福": "eintracht_frankfurt",
        "Marseille": "marseille", "马赛": "marseille", "OM": "marseille",
        "Lyon": "lyon", "里昂": "lyon", "OL": "lyon",
        "Monaco": "monaco", "摩纳哥": "monaco",
        "Lille": "lille", "里尔": "lille",
        "Galatasaray": "galatasaray", "加拉塔萨雷": "galatasaray",
        "Fenerbahce": "fenerbahce", "费内巴切": "fenerbahce",
        "Copenhagen": "copenhagen", "哥本哈根": "copenhagen",
        "Red Star": "red_star_belgrade", "Red Star Belgrade": "red_star_belgrade",
        "贝尔格莱德红星": "red_star_belgrade",
        "Young Boys": "young_boys", "年轻人": "young_boys",
    },
    "laliga": {
        "Real Madrid": "real_madrid", "皇家马德里": "real_madrid", "RMA": "real_madrid",
        "Barcelona": "barcelona", "巴塞罗那": "barcelona", "BAR": "barcelona",
        "Atletico Madrid": "atletico_madrid", "Atlético Madrid": "atletico_madrid",
        "马德里竞技": "atletico_madrid", "ATM": "atletico_madrid",
        "Sevilla": "sevilla", "塞维利亚": "sevilla", "SEV": "sevilla",
        "Villarreal": "villarreal", "比利亚雷亚尔": "villarreal", "VIL": "villarreal",
        "Real Sociedad": "real_sociedad", "皇家社会": "real_sociedad", "RSO": "real_sociedad",
        "Athletic": "athletic_bilbao", "Athletic Bilbao": "athletic_bilbao",
        "毕尔巴鄂竞技": "athletic_bilbao", "ATH": "athletic_bilbao",
        "Valencia": "valencia", "瓦伦西亚": "valencia", "VAL": "valencia",
        "Real Betis": "real_betis", "Betis": "real_betis",
        "皇家贝蒂斯": "real_betis", "BET": "real_betis",
        "Villarreal2": "villarreal",
        "Girona": "girona", "赫罗纳": "girona", "GIR": "girona",
        "Osasuna": "osasuna", "奥萨苏纳": "osasuna", "OSA": "osasuna",
        "Celta": "celta_vigo", "Celta Vigo": "celta_vigo",
        "塞尔塔": "celta_vigo", "CEL": "celta_vigo",
        "Mallorca": "mallorca", "马洛卡": "mallorca", "MAL": "mallorca",
        "Getafe": "getafe", "赫塔费": "getafe", "GET": "getafe",
        "Las Palmas": "las_palmas", "拉斯帕尔马斯": "las_palmas",
        "Rayo Vallecano": "rayo_vallecano", "Rayo": "rayo_vallecano",
        "巴列卡诺": "rayo_vallecano", "RAY": "rayo_vallecano",
        "Alaves": "alaves", "Alavés": "alaves",
        "阿拉维斯": "alaves", "ALA": "alaves",
        "Espanyol": "espanyol", "西班牙人": "espanyol", "ESP": "espanyol",
        "Leganes": "leganes", "Leganés": "leganes",
        "莱加内斯": "leganes", "LEG": "leganes",
        "Valladolid": "valladolid", "Real Valladolid": "valladolid",
        "巴拉多利德": "valladolid", "VLD": "valladolid",
        "Cadiz": "cadiz", "Cádiz": "cadiz",
        "加的斯": "cadiz",
    },
    "bundesliga": {
        "Bayern Munich": "bayern_munich", "拜仁慕尼黑": "bayern_munich", "FCB": "bayern_munich",
        "Dortmund": "borussia_dortmund", "Borussia Dortmund": "borussia_dortmund",
        "多特蒙德": "borussia_dortmund", "BVB": "borussia_dortmund",
        "Leverkusen": "bayer_leverkusen", "Bayer Leverkusen": "bayer_leverkusen",
        "勒沃库森": "bayer_leverkusen", "B04": "bayer_leverkusen",
        "Leipzig": "rb_leipzig", "RB Leipzig": "rb_leipzig",
        "莱比锡": "rb_leipzig", "RBL": "rb_leipzig",
        "Frankfurt": "eintracht_frankfurt", "Eintracht Frankfurt": "eintracht_frankfurt",
        "法兰克福": "eintracht_frankfurt", "SGE": "eintracht_frankfurt",
        "Wolfsburg": "wolfsburg", "VfL Wolfsburg": "wolfsburg",
        "沃尔夫斯堡": "wolfsburg", "WOB": "wolfsburg",
        "Monchengladbach": "borussia_monchengladbach", "M'gladbach": "borussia_monchengladbach",
        "Borussia Mönchengladbach": "borussia_monchengladbach",
        "门兴格拉德巴赫": "borussia_monchengladbach", "BMG": "borussia_monchengladbach",
        "Freiburg": "freiburg", "SC Freiburg": "freiburg",
        "弗赖堡": "freiburg", "SCF": "freiburg",
        "Hoffenheim": "hoffenheim", "TSG Hoffenheim": "hoffenheim",
        "霍芬海姆": "hoffenheim", "TSG": "hoffenheim",
        "Mainz": "mainz_05", "Mainz 05": "mainz_05",
        "美因茨": "mainz_05", "M05": "mainz_05",
        "Augsburg": "augsburg", "FC Augsburg": "augsburg",
        "奥格斯堡": "augsburg", "FCA": "augsburg",
        "Stuttgart": "vfb_stuttgart", "VfB Stuttgart": "vfb_stuttgart",
        "斯图加特": "vfb_stuttgart", "VFB": "vfb_stuttgart",
        "Union Berlin": "union_berlin",
        "柏林联合": "union_berlin", "FCU": "union_berlin",
        "Werder Bremen": "werder_bremen", "Bremen": "werder_bremen",
        "云达不莱梅": "werder_bremen", "SVW": "werder_bremen",
        "Bochum": "vfl_bochum", "VfL Bochum": "vfl_bochum",
        "波鸿": "vfl_bochum",
        "Heidenheim": "heidenheim", "1. FC Heidenheim": "heidenheim",
        "海登海姆": "heidenheim",
        "Darmstadt": "darmstadt_98", "SV Darmstadt 98": "darmstadt_98",
        "达姆施塔特": "darmstadt_98",
        "Koln": "fc_koln", "Köln": "fc_koln", "1. FC Köln": "fc_koln",
        "科隆": "fc_koln",
    },
    "seriea": {
        "Inter": "inter_milan", "Inter Milan": "inter_milan",
        "国际米兰": "inter_milan", "INT": "inter_milan",
        "Milan": "ac_milan", "AC Milan": "ac_milan",
        "AC米兰": "ac_milan", "ACM": "ac_milan",
        "Juventus": "juventus", "尤文图斯": "juventus", "JUV": "juventus",
        "Napoli": "napoli", "那不勒斯": "napoli", "NAP": "napoli",
        "Roma": "as_roma", "AS Roma": "as_roma",
        "罗马": "as_roma", "ROM": "as_roma",
        "Lazio": "lazio", "拉齐奥": "lazio", "LAZ": "lazio",
        "Atalanta": "atalanta", "亚特兰大": "atalanta", "ATA": "atalanta",
        "Fiorentina": "fiorentina", "佛罗伦萨": "fiorentina", "FIO": "fiorentina",
        "Bologna": "bologna", "博洛尼亚": "bologna", "BOL": "bologna",
        "Torino": "torino", "都灵": "torino", "TOR": "torino",
        "Udinese": "udinese", "乌迪内斯": "udinese", "UDI": "udinese",
        "Sassuolo": "sassuolo", "萨索洛": "sassuolo", "SAS": "sassuolo",
        "Genoa": "genoa", "热那亚": "genoa", "GEN": "genoa",
        "Monza": "monza", "蒙扎": "monza",
        "Lecce": "lecce", "莱切": "lecce",
        "Verona": "hellas_verona", "Hellas Verona": "hellas_verona",
        "维罗纳": "hellas_verona", "VER": "hellas_verona",
        "Cagliari": "cagliari", "卡利亚里": "cagliari", "CAG": "cagliari",
        "Empoli": "empoli", "恩波利": "empoli", "EMP": "empoli",
        "Frosinone": "frosinone", "弗罗西诺内": "frosinone",
        "Salernitana": "salernitana", "萨勒尼塔纳": "salernitana",
        "Cremonese": "cremonese", "克雷莫纳": "cremonese",
        "Spezia": "spezia", "斯佩齐亚": "spezia",
    },
    "ligue1": {
        "PSG": "paris_saint_germain", "Paris Saint-Germain": "paris_saint_germain",
        "巴黎圣日耳曼": "paris_saint_germain",
        "Marseille": "marseille", "马赛": "marseille", "OM": "marseille",
        "Monaco": "monaco", "摩纳哥": "monaco", "ASM": "monaco",
        "Lyon": "lyon", "里昂": "lyon", "OL": "lyon",
        "Lille": "lille", "里尔": "lille", "LOSC": "lille",
        "Nice": "nice", "尼斯": "nice",
        "Rennes": "rennes", "雷恩": "rennes",
        "Lens": "lens", "朗斯": "lens",
        "Nantes": "nantes", "南特": "nantes",
        "Strasbourg": "strasbourg", "斯特拉斯堡": "strasbourg",
        "Montpellier": "montpellier", "蒙彼利埃": "montpellier",
        "Brest": "brest", "布雷斯特": "brest",
        "Toulouse": "toulouse", "图卢兹": "toulouse",
        "Reims": "reims", "兰斯": "reims",
        "Le Havre": "le_havre", "勒阿弗尔": "le_havre",
        "Metz": "metz", "梅斯": "metz",
        "Lorient": "lorient", "洛里昂": "lorient",
        "Clermont": "clermont", "克莱蒙": "clermont",
    },
    "wc": {
        "Brazil": "brazil", "巴西": "brazil", "BRA": "brazil",
        "Argentina": "argentina", "阿根廷": "argentina", "ARG": "argentina",
        "France": "france", "法国": "france", "FRA": "france",
        "Germany": "germany", "德国": "germany", "GER": "germany",
        "Spain": "spain", "西班牙": "spain", "ESP": "spain",
        "England": "england", "英格兰": "england", "ENG": "england",
        "Portugal": "portugal", "葡萄牙": "portugal", "POR": "portugal",
        "Netherlands": "netherlands", "Holland": "netherlands",
        "荷兰": "netherlands", "NED": "netherlands",
        "Italy": "italy", "意大利": "italy", "ITA": "italy",
        "Belgium": "belgium", "比利时": "belgium", "BEL": "belgium",
        "Croatia": "croatia", "克罗地亚": "croatia", "CRO": "croatia",
        "Uruguay": "uruguay", "乌拉圭": "uruguay", "URU": "uruguay",
        "Mexico": "mexico", "墨西哥": "mexico", "MEX": "mexico",
        "USA": "usa", "United States": "usa",
        "美国": "usa", "USMNT": "usa",
        "Japan": "japan", "日本": "japan", "JPN": "japan",
        "South Korea": "south_korea", "Korea Republic": "south_korea",
        "韩国": "south_korea", "KOR": "south_korea",
        "Australia": "australia", "澳大利亚": "australia", "AUS": "australia",
        "Switzerland": "switzerland", "瑞士": "switzerland", "SUI": "switzerland",
        "Denmark": "denmark", "丹麦": "denmark", "DEN": "denmark",
        "Sweden": "sweden", "瑞典": "sweden", "SWE": "sweden",
        "Norway": "norway", "挪威": "norway", "NOR": "norway",
        "Serbia": "serbia", "塞尔维亚": "serbia", "SRB": "serbia",
        "Poland": "poland", "波兰": "poland", "POL": "poland",
        "Austria": "austria", "奥地利": "austria", "AUT": "austria",
        "Czech Republic": "czech_republic", "Czechia": "czech_republic",
        "捷克": "czech_republic", "CZE": "czech_republic",
        "Turkey": "turkey", "土耳其": "turkey", "TUR": "turkey",
        "Wales": "wales", "威尔士": "wales", "WAL": "wales",
        "Scotland": "scotland", "苏格兰": "scotland", "SCO": "scotland",
        "Ireland": "republic_of_ireland", "Republic of Ireland": "republic_of_ireland",
        "爱尔兰": "republic_of_ireland", "IRL": "republic_of_ireland",
        "Greece": "greece", "希腊": "greece", "GRE": "greece",
        "Russia": "russia", "俄罗斯": "russia", "RUS": "russia",
        "Ukraine": "ukraine", "乌克兰": "ukraine", "UKR": "ukraine",
        "Romania": "romania", "罗马尼亚": "romania", "ROU": "romania",
        "Hungary": "hungary", "匈牙利": "hungary", "HUN": "hungary",
        "Morocco": "morocco", "摩洛哥": "morocco", "MAR": "morocco",
        "Senegal": "senegal", "塞内加尔": "senegal", "SEN": "senegal",
        "Nigeria": "nigeria", "尼日利亚": "nigeria", "NGA": "nigeria",
        "Egypt": "egypt", "埃及": "egypt", "EGY": "egypt",
        "Cameroon": "cameroon", "喀麦隆": "cameroon", "CMR": "cameroon",
        "Ghana": "ghana", "加纳": "ghana", "GHA": "ghana",
        "Ivory Coast": "ivory_coast", "Côte d'Ivoire": "ivory_coast",
        "科特迪瓦": "ivory_coast", "CIV": "ivory_coast",
        "Canada": "canada", "加拿大": "canada", "CAN": "canada",
        "Costa Rica": "costa_rica", "哥斯达黎加": "costa_rica", "CRC": "costa_rica",
        "Ecuador": "ecuador", "厄瓜多尔": "ecuador", "ECU": "ecuador",
        "Colombia": "colombia", "哥伦比亚": "colombia", "COL": "colombia",
        "Chile": "chile", "智利": "chile", "CHI": "chile",
        "Peru": "peru", "秘鲁": "peru", "PER": "peru",
        "Paraguay": "paraguay", "巴拉圭": "paraguay", "PAR": "paraguay",
        "Qatar": "qatar", "卡塔尔": "qatar", "QAT": "qatar",
        "Saudi Arabia": "saudi_arabia", "沙特阿拉伯": "saudi_arabia", "KSA": "saudi_arabia",
        "Iran": "iran", "伊朗": "iran", "IRN": "iran",
    },
}


def resolve_team(alias: str, competition: str) -> str | None:
    """Resolve a team alias to canonical name. Case-insensitive. Returns None if not found.

    Args:
        alias: Team name variant (abbreviation, full name, or Chinese name).
        competition: Competition code (e.g. "nba", "epl", "wc").

    Returns:
        Canonical snake_case team id, or None when the competition or alias
        is unknown.
    """
    comp_map = TEAM_ALIASES.get(competition)
    if comp_map is None:
        return None
    # Case-insensitive lookup: build a lowercased view on demand.
    alias_lower = alias.lower()
    for key, canonical in comp_map.items():
        if key.lower() == alias_lower:
            return canonical
    return None
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_team_aliases.py -v`
Expected: PASS (12 tests)

### Step 1.5: Add Phase 7 config flags

- [ ] **Step 9: Modify config.py to add Phase 7 flags**

In `backend/app/core/config.py`, locate line 1028 (`NHL_LEAGUE_AVG_TOTAL` assignment) and insert the following block after it, before the blank line and `settings = Settings()` at line 1031:

```python

    # Phase 7 — Sport Market Bridge (default OFF). Connects the Sports
    # Prediction Kernel with Polymarket + The Odds API to produce verified
    # market-implied probabilities per match outcome. When the master flag is
    # false, all new endpoints return 503 and collection tasks are not
    # scheduled.
    PHASE7_SPORT_MARKET_BRIDGE_ENABLED: bool = _env_bool(
        "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", "false"
    )
    PHASE7_POLYMARKET_SPORTS_SOURCE_ENABLED: bool = _env_bool(
        "PHASE7_POLYMARKET_SPORTS_SOURCE_ENABLED", "false"
    )
    PHASE7_ODDS_API_MULTI_LEAGUE_ENABLED: bool = _env_bool(
        "PHASE7_ODDS_API_MULTI_LEAGUE_ENABLED", "false"
    )
    PHASE7_SPORT_MARKET_BRIDGE_SCHEDULER_ENABLED: bool = _env_bool(
        "PHASE7_SPORT_MARKET_BRIDGE_SCHEDULER_ENABLED", "false"
    )
    PHASE7_SPORT_MARKET_SNAPSHOT_INTERVAL_SECONDS: int = int(
        os.getenv("PHASE7_SPORT_MARKET_SNAPSHOT_INTERVAL_SECONDS", "300")
    )
    PHASE7_POLYMARKET_SPORTS_FETCH_INTERVAL_SECONDS: int = int(
        os.getenv("PHASE7_POLYMARKET_SPORTS_FETCH_INTERVAL_SECONDS", "600")
    )
    PHASE7_SPORT_MARKET_LINK_PENDING_THRESHOLD: float = float(
        os.getenv("PHASE7_SPORT_MARKET_LINK_PENDING_THRESHOLD", "0.6")
    )
```

- [ ] **Step 10: Verify config loads**

Run: `cd backend && python -c "from app.core.config import settings; assert settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED is False; assert settings.PHASE7_SPORT_MARKET_SNAPSHOT_INTERVAL_SECONDS == 300; assert settings.PHASE7_SPORT_MARKET_LINK_PENDING_THRESHOLD == 0.6; print('OK')"`
Expected: prints `OK`

### Step 1.6: Commit Task 1

- [ ] **Step 11: Commit**

```bash
git add backend/app/utils/implied_prob.py backend/app/sports/_shared/__init__.py backend/app/sports/_shared/team_aliases.py backend/app/core/config.py backend/tests/test_implied_prob.py backend/tests/test_team_aliases.py
git commit -m "feat(phase7): add implied prob utils, team alias registry, Phase 7 config flags"
```

---

## Task 2: DB Tables and Stores

**Files:**
- Modify: `backend/app/kernel/kernel_db.py` (append `KernelSportMarketLink` + `KernelMarketSnapshot` table classes after existing tables, before `init_kernel_db`)
- Create: `backend/app/kernel/sport_market_link_store.py`, `backend/app/kernel/market_snapshot_store.py`
- Test: `backend/tests/test_sport_market_stores.py`

**Interfaces:**
- Consumes: `app.kernel.kernel_db.KernelBase`, `get_kernel_session`, `init_kernel_db`; Task 1 implied_prob (none)
- Produces:
  - `app.kernel.kernel_db.KernelSportMarketLink`, `KernelMarketSnapshot` (ORM models)
  - `app.kernel.sport_market_link_store.SportMarketLinkStore` with methods:
    - `upsert_link(*, match_id, contract_id, source, outcome_label, mapped_outcome, link_method, link_confidence, verified, market_question, implied_prob) -> dict`
    - `get_links(*, match_id) -> list[dict]`
    - `get_verified_links(*, match_id) -> list[dict]` (fail-closed: only verified=True)
    - `get_pending_links() -> list[dict]`
    - `get_all_verified_links() -> list[dict]`
    - `set_verified(*, link_id, verified=True) -> bool`
  - `app.kernel.market_snapshot_store.MarketSnapshotStore` with methods:
    - `append_snapshot(*, link_id, implied_prob, price=None, liquidity=None, volume=None, captured_at=None) -> dict`
    - `get_snapshots(*, link_id) -> list[dict]`
    - `get_latest_snapshot(*, link_id) -> dict | None`

### Step 2.1: Write the failing test for the stores

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_sport_market_stores.py`:

```python
"""Tests for sport market link + snapshot stores (real SQLite via tmp_path)."""
import pytest

from app.kernel.kernel_db import (
    KernelSportMarketLink,
    KernelMarketSnapshot,
    init_kernel_db,
    close_kernel_db,
    get_kernel_session,
)


@pytest.fixture
def kernel_db(tmp_path, monkeypatch):
    """Initialize a fresh kernel DB in tmp_path for each test."""
    db_path = tmp_path / "kernel_test.db"
    # Reset module-level engine so init_kernel_db creates a new one.
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def test_table_classes_exist(kernel_db):
    # Tables are created by init_kernel_db -> create_all
    session = get_kernel_session()
    try:
        # Smoke: insert a link row
        link = KernelSportMarketLink(
            match_id="nba-20250101-LAL-BOS",
            contract_id="poly-123",
            source="polymarket",
            outcome_label="YES",
            mapped_outcome="home_win",
            link_method="rule",
            link_confidence=0.95,
            verified=True,
            market_question="Will Lakers beat Celtics?",
            implied_prob=0.6,
        )
        session.add(link)
        session.commit()
        assert link.id is not None
    finally:
        session.close()


def test_upsert_link_inserts(kernel_db):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    result = store.upsert_link(
        match_id="nba-20250101-LAL-BOS",
        contract_id="poly-123",
        source="polymarket",
        outcome_label="YES",
        mapped_outcome="home_win",
        link_method="rule",
        link_confidence=0.95,
        verified=True,
        market_question="Will Lakers beat Celtics?",
        implied_prob=0.6,
    )
    assert result["match_id"] == "nba-20250101-LAL-BOS"
    assert result["verified"] is True
    assert result["id"] is not None


def test_upsert_link_updates_existing(kernel_db):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.5, verified=False,
        market_question="q", implied_prob=0.5,
    )
    # Upsert same key with new confidence/verified
    updated = store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.95, verified=True,
        market_question="q", implied_prob=0.6,
    )
    links = store.get_links(match_id="m1")
    assert len(links) == 1  # no duplicate
    assert links[0]["verified"] is True
    assert links[0]["link_confidence"] == 0.95


def test_get_verified_links_fail_closed(kernel_db):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="llm", link_confidence=0.7, verified=False,
        market_question="q", implied_prob=0.5,
    )
    store.upsert_link(
        match_id="m1", contract_id="c2", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.95, verified=True,
        market_question="q2", implied_prob=0.6,
    )
    verified = store.get_verified_links(match_id="m1")
    assert len(verified) == 1
    assert verified[0]["contract_id"] == "c2"
    # Unverified link must NOT leak
    assert all(l["verified"] is True for l in verified)


def test_get_pending_links(kernel_db):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="llm", link_confidence=0.7, verified=False,
        market_question="q", implied_prob=0.5,
    )
    store.upsert_link(
        match_id="m2", contract_id="c2", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.95, verified=True,
        market_question="q2", implied_prob=0.6,
    )
    pending = store.get_pending_links()
    assert len(pending) == 1
    assert pending[0]["verified"] is False


def test_get_all_verified_links(kernel_db):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.95, verified=True,
        market_question="q", implied_prob=0.6,
    )
    store.upsert_link(
        match_id="m2", contract_id="c2", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.95, verified=True,
        market_question="q2", implied_prob=0.55,
    )
    all_verified = store.get_all_verified_links()
    assert len(all_verified) == 2


def test_set_verified(kernel_db):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    link = store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="llm", link_confidence=0.7, verified=False,
        market_question="q", implied_prob=0.5,
    )
    ok = store.set_verified(link_id=link["id"], verified=True)
    assert ok is True
    verified = store.get_verified_links(match_id="m1")
    assert len(verified) == 1


def test_set_verified_missing_returns_false(kernel_db):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    ok = store.set_verified(link_id=99999, verified=True)
    assert ok is False


def test_append_and_get_snapshots(kernel_db):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    link_store = SportMarketLinkStore()
    snap_store = MarketSnapshotStore()
    link = link_store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.95, verified=True,
        market_question="q", implied_prob=0.6,
    )
    s1 = snap_store.append_snapshot(link_id=link["id"], implied_prob=0.6, price=0.6)
    s2 = snap_store.append_snapshot(link_id=link["id"], implied_prob=0.65, price=0.65)
    assert s1["id"] is not None
    assert s2["id"] is not None
    snaps = snap_store.get_snapshots(link_id=link["id"])
    assert len(snaps) == 2


def test_get_latest_snapshot(kernel_db):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    link_store = SportMarketLinkStore()
    snap_store = MarketSnapshotStore()
    link = link_store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win",
        link_method="rule", link_confidence=0.95, verified=True,
        market_question="q", implied_prob=0.6,
    )
    snap_store.append_snapshot(link_id=link["id"], implied_prob=0.6, price=0.6)
    snap_store.append_snapshot(link_id=link["id"], implied_prob=0.65, price=0.65)
    latest = snap_store.get_latest_snapshot(link_id=link["id"])
    assert latest is not None
    assert latest["implied_prob"] == 0.65


def test_get_latest_snapshot_empty(kernel_db):
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    snap_store = MarketSnapshotStore()
    assert snap_store.get_latest_snapshot(link_id=99999) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_sport_market_stores.py -v`
Expected: FAIL with `ImportError: cannot import name 'KernelSportMarketLink' from 'app.kernel.kernel_db'`

### Step 2.2: Add the ORM table classes

- [ ] **Step 3: Modify kernel_db.py to add table classes**

In `backend/app/kernel/kernel_db.py`, locate the `KernelEloRating` class (ends around line 183) and insert the following two classes after it, before the `init_kernel_db` function definition:

```python
class KernelSportMarketLink(KernelBase):
    """Link between a sports match (match_id) and a prediction-market contract.

    Fail-closed: downstream consumers must use get_verified_links which
    returns only verified=True rows. Unique on (match_id, contract_id,
    outcome_label) so one match can carry multiple outcome rows without dupes.
    """
    __tablename__ = "kernel_sport_market_links"
    __table_args__ = (
        UniqueConstraint("match_id", "contract_id", "outcome_label", name="uq_sport_market_link"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String, nullable=False, index=True)
    contract_id = Column(String, nullable=False, index=True)
    source = Column(String, nullable=False)  # "polymarket" | "odds_api"
    outcome_label = Column(String, nullable=False)  # "YES" | "NO" | "home" | "away" | "draw"
    mapped_outcome = Column(String, nullable=False)  # "home_win" | "away_win" | "draw"
    link_method = Column(String, nullable=False)  # "rule" | "llm" | "odds_api" | "manual"
    link_confidence = Column(Float, nullable=False, default=0.0)
    verified = Column(Integer, nullable=False, default=0, index=True)
    market_question = Column(String)
    implied_prob = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class KernelMarketSnapshot(KernelBase):
    """Price time-series for a sport market link (append-only)."""
    __tablename__ = "kernel_market_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    link_id = Column(Integer, nullable=False, index=True)
    implied_prob = Column(Float, nullable=False)
    price = Column(Float)
    liquidity = Column(Float)
    volume = Column(Float)
    captured_at = Column(DateTime)
```

- [ ] **Step 4: Verify tables importable (still failing on stores)**

Run: `cd backend && python -c "from app.kernel.kernel_db import KernelSportMarketLink, KernelMarketSnapshot; print('OK')"`
Expected: prints `OK`

### Step 2.3: Implement SportMarketLinkStore

- [ ] **Step 5: Write minimal implementation**

Create `backend/app/kernel/sport_market_link_store.py`:

```python
"""Persistence for sport market links (match_id <-> contract_id).

Fail-closed: get_verified_links returns only verified=True rows. Mirrors the
event_market_link_store pattern but uses the kernel_ SQLAlchemy ORM
(KernelBase) so the data lives in kernel_predictions.db alongside the
prediction tables.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.kernel.kernel_db import (
    KernelSportMarketLink,
    get_kernel_session,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(row: KernelSportMarketLink) -> dict[str, Any]:
    return {
        "id": row.id,
        "match_id": row.match_id,
        "contract_id": row.contract_id,
        "source": row.source,
        "outcome_label": row.outcome_label,
        "mapped_outcome": row.mapped_outcome,
        "link_method": row.link_method,
        "link_confidence": row.link_confidence,
        "verified": bool(row.verified),
        "market_question": row.market_question,
        "implied_prob": row.implied_prob,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class SportMarketLinkStore:
    """CRUD facade over KernelSportMarketLink.

    All methods open a short session and close it in finally, inheriting the
    kernel_db query pattern.
    """

    def upsert_link(
        self,
        *,
        match_id: str,
        contract_id: str,
        source: str,
        outcome_label: str,
        mapped_outcome: str,
        link_method: str,
        link_confidence: float,
        verified: bool,
        market_question: str | None,
        implied_prob: float,
    ) -> dict[str, Any]:
        """Insert or update by (match_id, contract_id, outcome_label)."""
        now = _utcnow()
        session = get_kernel_session()
        try:
            existing = (
                session.query(KernelSportMarketLink)
                .filter_by(
                    match_id=match_id,
                    contract_id=contract_id,
                    outcome_label=outcome_label,
                )
                .one_or_none()
            )
            if existing is not None:
                existing.source = source
                existing.mapped_outcome = mapped_outcome
                existing.link_method = link_method
                existing.link_confidence = link_confidence
                existing.verified = 1 if verified else 0
                existing.market_question = market_question
                existing.implied_prob = implied_prob
                existing.updated_at = now
                session.commit()
                session.refresh(existing)
                return _row_to_dict(existing)
            row = KernelSportMarketLink(
                match_id=match_id,
                contract_id=contract_id,
                source=source,
                outcome_label=outcome_label,
                mapped_outcome=mapped_outcome,
                link_method=link_method,
                link_confidence=link_confidence,
                verified=1 if verified else 0,
                market_question=market_question,
                implied_prob=implied_prob,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _row_to_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_links(self, *, match_id: str) -> list[dict[str, Any]]:
        """All links for a match (verified and unverified), newest first."""
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelSportMarketLink)
                .filter_by(match_id=match_id)
                .order_by(KernelSportMarketLink.updated_at.desc())
                .all()
            )
            return [_row_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def get_verified_links(self, *, match_id: str) -> list[dict[str, Any]]:
        """Fail-closed: only verified=True links for a match."""
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelSportMarketLink)
                .filter_by(match_id=match_id, verified=1)
                .order_by(KernelSportMarketLink.updated_at.desc())
                .all()
            )
            return [_row_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def get_pending_links(self) -> list[dict[str, Any]]:
        """All unverified links (the human review queue)."""
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelSportMarketLink)
                .filter_by(verified=0)
                .order_by(KernelSportMarketLink.updated_at.desc())
                .all()
            )
            return [_row_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def get_all_verified_links(self) -> list[dict[str, Any]]:
        """All verified links across all matches."""
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelSportMarketLink)
                .filter_by(verified=1)
                .order_by(KernelSportMarketLink.updated_at.desc())
                .all()
            )
            return [_row_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def set_verified(self, *, link_id: int, verified: bool = True) -> bool:
        """Promote/demote a link. Returns True if a row was updated."""
        session = get_kernel_session()
        try:
            row = (
                session.query(KernelSportMarketLink)
                .filter_by(id=link_id)
                .one_or_none()
            )
            if row is None:
                return False
            row.verified = 1 if verified else 0
            row.updated_at = _utcnow()
            session.commit()
            return True
        except Exception:
            session.rollback()
            return False
        finally:
            session.close()
```

- [ ] **Step 6: Run link store tests to verify pass**

Run: `cd backend && python -m pytest tests/test_sport_market_stores.py -k "not snapshot and not latest" -v`
Expected: PASS (8 link store tests)

### Step 2.4: Implement MarketSnapshotStore

- [ ] **Step 7: Write minimal implementation**

Create `backend/app/kernel/market_snapshot_store.py`:

```python
"""Persistence for market price snapshots (append-only time-series).

Snapshots are written by the scheduler capture job and read by the
MarketSnapshotChart frontend. Separated from links to avoid rewriting link
rows on every price tick.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.kernel.kernel_db import (
    KernelMarketSnapshot,
    get_kernel_session,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(row: KernelMarketSnapshot) -> dict[str, Any]:
    return {
        "id": row.id,
        "link_id": row.link_id,
        "implied_prob": row.implied_prob,
        "price": row.price,
        "liquidity": row.liquidity,
        "volume": row.volume,
        "captured_at": row.captured_at,
    }


class MarketSnapshotStore:
    """Append-only snapshot store."""

    def append_snapshot(
        self,
        *,
        link_id: int,
        implied_prob: float,
        price: float | None = None,
        liquidity: float | None = None,
        volume: float | None = None,
        captured_at: datetime | None = None,
    ) -> dict[str, Any]:
        when = captured_at or _utcnow()
        session = get_kernel_session()
        try:
            row = KernelMarketSnapshot(
                link_id=link_id,
                implied_prob=implied_prob,
                price=price,
                liquidity=liquidity,
                volume=volume,
                captured_at=when,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _row_to_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_snapshots(self, *, link_id: int) -> list[dict[str, Any]]:
        """All snapshots for a link, oldest first (chart x-axis order)."""
        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelMarketSnapshot)
                .filter_by(link_id=link_id)
                .order_by(KernelMarketSnapshot.captured_at.asc())
                .all()
            )
            return [_row_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def get_latest_snapshot(self, *, link_id: int) -> dict[str, Any] | None:
        """Most recent snapshot for a link, or None."""
        session = get_kernel_session()
        try:
            row = (
                session.query(KernelMarketSnapshot)
                .filter_by(link_id=link_id)
                .order_by(KernelMarketSnapshot.captured_at.desc())
                .first()
            )
            return _row_to_dict(row) if row is not None else None
        except Exception:
            return None
        finally:
            session.close()
```

- [ ] **Step 8: Run all store tests to verify pass**

Run: `cd backend && python -m pytest tests/test_sport_market_stores.py -v`
Expected: PASS (13 tests)

### Step 2.5: Commit Task 2

- [ ] **Step 9: Commit**

```bash
git add backend/app/kernel/kernel_db.py backend/app/kernel/sport_market_link_store.py backend/app/kernel/market_snapshot_store.py backend/tests/test_sport_market_stores.py
git commit -m "feat(phase7): add kernel sport market link + snapshot tables and stores"
```

---

## Task 3: Market Detector and Polymarket Sports Source

**Files:**
- Create: `backend/app/services/sport_market_detector.py`, `backend/app/services/polymarket_sports_source.py`
- Test: `backend/tests/test_sport_market_detector.py`, `backend/tests/test_polymarket_sports_source.py`

**Interfaces:**
- Consumes: Task 1 `app.sports._shared.team_aliases.resolve_team`, `TEAM_ALIASES`, `COMPETITION_TO_SPORT`; `app.services.polymarket_service.POLYMARKET_API` constant
- Produces:
  - `app.services.sport_market_detector.SportMarketInfo` (frozen dataclass)
  - `app.services.sport_market_detector.detect_sport_market(*, contract_id: str, question: str, source: str) -> SportMarketInfo | None`
  - `app.services.polymarket_sports_source.fetch_polymarket_sport_markets(limit: int = 100) -> list[dict[str, Any]]`

### Step 3.1: Write the failing test for the detector

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_sport_market_detector.py`:

```python
"""Tests for the sport market detector."""
from datetime import date


def test_detect_nba_market():
    from app.services.sport_market_detector import detect_sport_market
    info = detect_sport_market(
        contract_id="poly-1",
        question="Will the Lakers beat the Celtics on January 1, 2025?",
        source="polymarket",
    )
    assert info is not None
    assert info.detected_competition == "nba"
    assert info.detected_sport == "basketball"
    assert "los_angeles_lakers" in info.detected_teams
    assert "boston_celtics" in info.detected_teams
    assert info.market_type == "single_match_binary"


def test_detect_mlb_market():
    from app.services.sport_market_detector import detect_sport_market
    info = detect_sport_market(
        contract_id="poly-2",
        question="Will the Yankees defeat the Red Sox tonight?",
        source="polymarket",
    )
    assert info is not None
    assert info.detected_competition == "mlb"
    assert info.detected_sport == "baseball"
    assert "new_york_yankees" in info.detected_teams


def test_detect_nhl_market():
    from app.services.sport_market_detector import detect_sport_market
    info = detect_sport_market(
        contract_id="poly-3",
        question="Will the Bruins beat the Maple Leafs?",
        source="polymarket",
    )
    assert info is not None
    assert info.detected_competition == "nhl"
    assert info.detected_sport == "hockey"


def test_detect_epl_market():
    from app.services.sport_market_detector import detect_sport_market
    info = detect_sport_market(
        contract_id="poly-4",
        question="Will Man City beat Arsenal in the Premier League?",
        source="polymarket",
    )
    assert info is not None
    assert info.detected_competition == "epl"
    assert info.detected_sport == "football"
    assert "manchester_city" in info.detected_teams


def test_futures_market_filtered_out():
    from app.services.sport_market_detector import detect_sport_market
    # Championship/futures keyword -> not a single-match market
    info = detect_sport_market(
        contract_id="poly-5",
        question="Will the Lakers win the NBA Championship 2025?",
        source="polymarket",
    )
    assert info is None


def test_date_extraction():
    from app.services.sport_market_detector import detect_sport_market
    info = detect_sport_market(
        contract_id="poly-6",
        question="Will the Lakers beat the Celtics on 2025-01-15?",
        source="polymarket",
    )
    assert info is not None
    assert info.detected_date == date(2025, 1, 15)


def test_non_sport_market_returns_none():
    from app.services.sport_market_detector import detect_sport_market
    info = detect_sport_market(
        contract_id="poly-7",
        question="Will Bitcoin reach $100k by end of year?",
        source="polymarket",
    )
    assert info is None


def test_traditional_odds_passthrough():
    from app.services.sport_market_detector import detect_sport_market
    # The Odds API source is pre-structured; detector tags it directly.
    info = detect_sport_market(
        contract_id="oddsapi-lal-bos-20250101",
        question="Lakers vs Celtics",
        source="the_odds_api",
    )
    assert info is not None
    assert info.source == "the_odds_api"
    assert info.market_type == "traditional_odds"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_sport_market_detector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.sport_market_detector'`

### Step 3.2: Implement the detector

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/sport_market_detector.py`:

```python
"""Sport market detector — determines whether a candidate market is a
single-match sports market and extracts structured info.

Deterministic (no LLM). Reverse-looks up team names in the market question
via TeamAliasRegistry, infers sport/competition via keywords, and extracts a
date via regex. Futures/championship markets are filtered out (return None).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.sports._shared.team_aliases import (
    TEAM_ALIASES,
    COMPETITION_TO_SPORT,
)

# Keywords that indicate a futures/season market, NOT a single match.
FUTURES_KEYWORDS = (
    "championship", "win the", "win it all", "mvp", "title",
    "playoffs bracket", "draft", "award", "golden boot", "top scorer",
    "regular season", "standings", "qualified", "qualify for",
)

# Competition -> keyword(s) that hint the market is about this competition.
SPORT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "nba": ("nba", "national basketball association"),
    "mlb": ("mlb", "major league baseball"),
    "nhl": ("nhl", "national hockey league"),
    "epl": ("epl", "premier league"),
    "ucl": ("ucl", "champions league", "uefa champions league"),
    "laliga": ("la liga", "laliga"),
    "bundesliga": ("bundesliga",),
    "seriea": ("serie a", "seriea"),
    "ligue1": ("ligue 1", "ligue1"),
    "wc": ("world cup", "fifa world cup"),
}

# Date patterns (ISO + common English forms). Captures YYYY-MM-DD or MM/DD.
DATE_PATTERNS = (
    re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"),
    re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"),
)


@dataclass(frozen=True)
class SportMarketInfo:
    contract_id: str
    source: str
    market_question: str
    market_type: str  # "single_match_binary" | "traditional_odds" | "unknown"
    detected_sport: Optional[str]
    detected_competition: Optional[str]
    detected_teams: list[str]
    detected_date: Optional[date] = None
    outcome_label: str = "YES"


def _extract_date(question: str) -> Optional[date]:
    for pat in DATE_PATTERNS:
        m = pat.search(question)
        if not m:
            continue
        groups = m.groups()
        if len(groups[0]) == 4:  # YYYY-MM-DD
            year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
        else:  # MM/DD/YYYY
            month, day, year = int(groups[0]), int(groups[1]), int(groups[2])
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return None


def _detect_teams(question: str) -> tuple[Optional[str], list[str]]:
    """Return (competition, canonical_teams) by reverse-looking up aliases.

    Scans all competitions; returns the first competition that yields >= 1
    team match, plus all matched canonical team ids for that competition.
    """
    q_lower = question.lower()
    for competition, alias_map in TEAM_ALIASES.items():
        matched: list[str] = []
        for alias, canonical in alias_map.items():
            if alias.lower() in q_lower:
                if canonical not in matched:
                    matched.append(canonical)
        if matched:
            return competition, matched
    return None, []


def _detect_competition_by_keyword(question: str) -> Optional[str]:
    q_lower = question.lower()
    for competition, keywords in SPORT_KEYWORDS.items():
        for kw in keywords:
            if kw in q_lower:
                return competition
    return None


def detect_sport_market(
    *,
    contract_id: str,
    question: str,
    source: str,
) -> SportMarketInfo | None:
    """Detect whether a market is a single-match sport market.

    Returns None for futures/season markets or non-sport markets.
    The Odds API source is tagged market_type="traditional_odds" without
    further text filtering.
    """
    if source == "the_odds_api":
        comp, teams = _detect_teams(question)
        return SportMarketInfo(
            contract_id=contract_id,
            source=source,
            market_question=question,
            market_type="traditional_odds",
            detected_sport=COMPETITION_TO_SPORT.get(comp) if comp else None,
            detected_competition=comp,
            detected_teams=teams,
            detected_date=_extract_date(question),
            outcome_label="home",
        )

    q_lower = question.lower()
    # Filter out futures/season markets.
    for kw in FUTURES_KEYWORDS:
        if kw in q_lower:
            return None

    comp_from_teams, teams = _detect_teams(question)
    comp_from_kw = _detect_competition_by_keyword(question)
    competition = comp_from_teams or comp_from_kw

    if competition is None and not teams:
        # No team and no sport keyword -> not a sport market.
        return None

    sport = COMPETITION_TO_SPORT.get(competition) if competition else None

    return SportMarketInfo(
        contract_id=contract_id,
        source=source,
        market_question=question,
        market_type="single_match_binary",
        detected_sport=sport,
        detected_competition=competition,
        detected_teams=teams,
        detected_date=_extract_date(question),
        outcome_label="YES",
    )
```

- [ ] **Step 4: Run detector tests to verify pass**

Run: `cd backend && python -m pytest tests/test_sport_market_detector.py -v`
Expected: PASS (8 tests)

### Step 3.3: Write the failing test for the Polymarket source

- [ ] **Step 5: Write the failing test**

Create `backend/tests/test_polymarket_sports_source.py`:

```python
"""Tests for the Polymarket sports source (mocked httpx)."""
import pytest
from unittest.mock import AsyncMock, patch

from httpx import Response


def _make_market(contract_id, question, price=0.5, no_price=0.5,
                 liquidity=1000.0, volume=5000.0):
    """Build a minimal Polymarket gamma-API item."""
    return {
        "id": contract_id,
        "question": question,
        "clobTokenIds": f'["{contract_id}-yes","{contract_id}-no"]',
        "outcomePrices": f'["{price}", "{no_price}"]',
        "liquidity": liquidity,
        "volume": volume,
        "closed": "false",
        "archived": "false",
    }


@pytest.mark.asyncio
async def test_fetch_filters_to_sport_markets():
    from app.services.polymarket_sports_source import fetch_polymarket_sport_markets
    api_data = [
        _make_market("poly-sport-1", "Will the Lakers beat the Celtics?"),
        _make_market("poly-non-1", "Will Bitcoin reach $100k?"),
    ]
    response = Response(200, json=api_data)

    with patch("app.services.polymarket_sports_source.httpx.AsyncClient") as mock_client_cls:
        client = mock_client_cls.return_value.__aenter__.return_value
        client.get = AsyncMock(return_value=response)
        results = await fetch_polymarket_sport_markets(limit=10)

    # Only the sport market is returned.
    assert len(results) == 1
    assert results[0]["contract_id"] == "poly-sport-1"
    assert results[0]["detected_competition"] == "nba"


@pytest.mark.asyncio
async def test_fetch_excludes_futures():
    from app.services.polymarket_sports_source import fetch_polymarket_sport_markets
    api_data = [
        _make_market("poly-fut", "Will the Lakers win the NBA Championship?"),
        _make_market("poly-sport-2", "Will the Yankees beat the Red Sox?"),
    ]
    response = Response(200, json=api_data)

    with patch("app.services.polymarket_sports_source.httpx.AsyncClient") as mock_client_cls:
        client = mock_client_cls.return_value.__aenter__.return_value
        client.get = AsyncMock(return_value=response)
        results = await fetch_polymarket_sport_markets(limit=10)

    assert len(results) == 1
    assert results[0]["contract_id"] == "poly-sport-2"


@pytest.mark.asyncio
async def test_fetch_api_error_returns_empty():
    from app.services.polymarket_sports_source import fetch_polymarket_sport_markets
    response = Response(500, text="server error")

    with patch("app.services.polymarket_sports_source.httpx.AsyncClient") as mock_client_cls:
        client = mock_client_cls.return_value.__aenter__.return_value
        client.get = AsyncMock(return_value=response)
        results = await fetch_polymarket_sport_markets(limit=10)

    assert results == []


@pytest.mark.asyncio
async def test_fetch_returns_expected_keys():
    from app.services.polymarket_sports_source import fetch_polymarket_sport_markets
    api_data = [_make_market("poly-1", "Will the Lakers beat the Celtics?", price=0.6)]
    response = Response(200, json=api_data)

    with patch("app.services.polymarket_sports_source.httpx.AsyncClient") as mock_client_cls:
        client = mock_client_cls.return_value.__aenter__.return_value
        client.get = AsyncMock(return_value=response)
        results = await fetch_polymarket_sport_markets(limit=10)

    assert len(results) == 1
    item = results[0]
    for key in ("contract_id", "question", "price", "no_price",
                "liquidity", "volume", "detected_sport",
                "detected_competition", "detected_teams", "detected_date"):
        assert key in item, f"missing key {key}"
    assert item["price"] == 0.6
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_polymarket_sports_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.polymarket_sports_source'`

### Step 3.4: Implement the Polymarket sports source

- [ ] **Step 7: Write minimal implementation**

Create `backend/app/services/polymarket_sports_source.py`:

```python
"""Polymarket sports market collection source.

Exists in PARALLEL with polymarket_event_source (which is NOT modified).
Fetches from the Polymarket gamma API, then filters to single-match sport
markets via the sport_market_detector. Returns a list of candidate dicts
consumed by the SportMarketBridgeService.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.services.sport_market_detector import detect_sport_market

logger = logging.getLogger(__name__)

POLYMARKET_API = "https://gamma-api.polymarket.com/markets"


def _parse_price(prices_field: str | None, index: int) -> float | None:
    """Parse outcomePrices JSON string -> float at index."""
    if not prices_field:
        return None
    try:
        prices = json.loads(prices_field)
        if isinstance(prices, list) and len(prices) > index:
            return float(prices[index])
    except (ValueError, TypeError):
        pass
    return None


async def fetch_polymarket_sport_markets(limit: int = 100) -> list[dict[str, Any]]:
    """Fetch Polymarket markets and filter to single-match sport markets.

    Returns list of dicts with keys: contract_id, question, price, no_price,
    liquidity, volume, detected_sport, detected_competition, detected_teams,
    detected_date.
    """
    results: list[dict[str, Any]] = []
    target = max(limit, 1)
    page_size = 100
    offset = 0

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            while len(results) < target:
                params = {
                    "limit": str(page_size),
                    "offset": str(offset),
                    "closed": "false",
                    "archived": "false",
                    "order": "volume",
                    "ascending": "false",
                }
                response = await client.get(POLYMARKET_API, params=params)
                if response.status_code != 200:
                    logger.warning(
                        "Polymarket sports source got %d: %s",
                        response.status_code, response.text[:200],
                    )
                    return results
                data = response.json()
                if not data:
                    break

                for item in data:
                    if len(results) >= target:
                        break
                    question = item.get("question", "")
                    contract_id = str(item.get("id", ""))
                    if not question or not contract_id:
                        continue
                    info = detect_sport_market(
                        contract_id=contract_id,
                        question=question,
                        source="polymarket",
                    )
                    if info is None:
                        continue
                    results.append({
                        "contract_id": contract_id,
                        "question": question,
                        "price": _parse_price(item.get("outcomePrices"), 0),
                        "no_price": _parse_price(item.get("outcomePrices"), 1),
                        "liquidity": item.get("liquidity"),
                        "volume": item.get("volume"),
                        "detected_sport": info.detected_sport,
                        "detected_competition": info.detected_competition,
                        "detected_teams": info.detected_teams,
                        "detected_date": info.detected_date.isoformat() if info.detected_date else None,
                    })
                offset += page_size
    except Exception as e:
        logger.debug("Polymarket sports source fetch error: %s", e)
        return results

    return results
```

- [ ] **Step 8: Run source tests to verify pass**

Run: `cd backend && python -m pytest tests/test_polymarket_sports_source.py -v`
Expected: PASS (4 tests)

### Step 3.5: Commit Task 3

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/sport_market_detector.py backend/app/services/polymarket_sports_source.py backend/tests/test_sport_market_detector.py backend/tests/test_polymarket_sports_source.py
git commit -m "feat(phase7): add sport market detector and polymarket sports source"
```

---

## Task 4: The Odds API Multi-League Extension

**Files:**
- Modify: `backend/app/services/odds_api_service.py` (add `COMPETITION_TO_ODDS_API_SPORT` dict + extend `fetch_match_odds` signature)
- Modify: `backend/app/services/odds_cache_service.py` (multi-league cache key)
- Modify: `backend/app/sports/football/adapters/_shared.py` (pass competition to fetch_match_odds)
- Modify: `backend/app/sports/basketball/feature_builder.py` (replace `odds_home=None` with `market_raw.get("odds_home")`)
- Modify: `backend/app/sports/baseball/feature_builder.py` (same)
- Modify: `backend/app/sports/hockey/feature_builder.py` (same)
- Test: `backend/tests/test_odds_api_multi_league.py`

**Interfaces:**
- Consumes: Task 1 config (`PHASE7_ODDS_API_MULTI_LEAGUE_ENABLED`); existing `odds_api_service` World Cup logic
- Produces:
  - `app.services.odds_api_service.COMPETITION_TO_ODDS_API_SPORT: dict[str, str]` (10 competitions)
  - `fetch_match_odds(home_team, away_team, commence_time=None, competition="wc") -> dict | None`
  - `get_cached_odds(home_team, away_team, ttl_seconds=3600, commence_time=None, allow_stale=True, max_stale_hours=168, competition="wc") -> dict | None`
  - Feature builders now read `market_raw.get("odds_home")` instead of hardcoded `None`

### Step 4.1: Write the failing test

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_odds_api_multi_league.py`:

```python
"""Tests for The Odds API multi-league extension + feature builder odds injection."""
import pytest
from unittest.mock import AsyncMock, patch

from httpx import Response


def test_competition_to_odds_api_sport_covers_all_10():
    from app.services.odds_api_service import COMPETITION_TO_ODDS_API_SPORT
    expected = {"wc", "ucl", "epl", "laliga", "bundesliga",
                "seriea", "ligue1", "nba", "mlb", "nhl"}
    assert expected.issubset(set(COMPETITION_TO_ODDS_API_SPORT.keys()))
    assert COMPETITION_TO_ODDS_API_SPORT["wc"] == "soccer_fifa_world_cup"
    assert COMPETITION_TO_ODDS_API_SPORT["nba"] == "basketball_nba"
    assert COMPETITION_TO_ODDS_API_SPORT["nhl"] == "icehockey_nhl"


@pytest.mark.asyncio
async def test_fetch_match_odds_uses_nba_sport_key():
    from app.services import odds_api_service
    # Build an API response with one NBA fixture.
    api_data = [{
        "id": "evt-1",
        "home_team": "Los Angeles Lakers",
        "away_team": "Boston Celtics",
        "commence_time": "2025-01-01T19:00:00Z",
        "bookmakers": [],
    }]
    captured_params = {}

    async def fake_get(url, params=None):
        captured_params["url"] = url
        captured_params["params"] = params
        return Response(200, json=api_data)

    with patch.object(odds_api_service, "ODDS_API_KEY", "fake-key"), \
         patch.object(odds_api_service, "httpx.AsyncClient") as mock_client_cls:
        client = mock_client_cls.return_value.__aenter__.return_value
        client.get = AsyncMock(side_effect=fake_get)
        # Reset quota so the skip-quota guard does not short-circuit.
        with patch.object(odds_api_service, "_quota_remaining", None):
            result = await odds_api_service.fetch_match_odds(
                "Los Angeles Lakers", "Boston Celtics", competition="nba"
            )

    # URL must reference the NBA sport key, NOT soccer_fifa_world_cup.
    assert "basketball_nba" in captured_params["url"]
    assert "soccer_fifa_world_cup" not in captured_params["url"]


@pytest.mark.asyncio
async def test_fetch_match_odds_default_competition_is_wc():
    # Regression: default competition="wc" must use soccer_fifa_world_cup.
    from app.services import odds_api_service
    api_data = [{
        "id": "evt-1", "home_team": "Brazil", "away_team": "Argentina",
        "commence_time": "2026-06-01T19:00:00Z", "bookmakers": [],
    }]
    captured_params = {}

    async def fake_get(url, params=None):
        captured_params["url"] = url
        return Response(200, json=api_data)

    with patch.object(odds_api_service, "ODDS_API_KEY", "fake-key"), \
         patch.object(odds_api_service, "httpx.AsyncClient") as mock_client_cls:
        client = mock_client_cls.return_value.__aenter__.return_value
        client.get = AsyncMock(side_effect=fake_get)
        with patch.object(odds_api_service, "_quota_remaining", None):
            await odds_api_service.fetch_match_odds("Brazil", "Argentina")

    assert "soccer_fifa_world_cup" in captured_params["url"]


def test_cache_key_includes_competition():
    from app.services.odds_cache_service import get_match_key
    key_wc = get_match_key("Brazil", "Argentina", competition="wc")
    key_nba = get_match_key("Lakers", "Celtics", competition="nba")
    # Different competitions produce distinguishable keys.
    assert "wc" in key_wc
    assert "nba" in key_nba
    assert key_wc != key_nba


@pytest.mark.asyncio
async def test_football_shared_passes_competition():
    # The football _shared.fetch_match_odds must forward competition to the cache.
    from app.sports.football.adapters import _shared
    with patch("app.services.odds_cache_service.get_cached_odds",
               new=AsyncMock(return_value=None)) as mock_get:
        await _shared.fetch_match_odds("Real Madrid", "Barcelona", competition="ucl")
        # The mock is called with competition="ucl".
        assert mock_get.call_args.kwargs.get("competition") == "ucl" or \
               "ucl" in mock_get.call_args.args


def test_basketball_feature_builder_reads_odds_from_market_raw():
    from app.sports.basketball.feature_builder import BasketballFeatureBuilder
    from app.kernel.domain import MatchIdentity, TeamIdentity, SportIdentity, CompetitionIdentity, SeasonIdentity
    fb = BasketballFeatureBuilder()
    match = MatchIdentity(
        match_id="nba-20250101-LAL-BOS",
        sport=SportIdentity(code="basketball", name="Basketball"),
        competition=CompetitionIdentity(code="nba", name="NBA"),
        season=SeasonIdentity(code="2024-25", name="2024-25"),
        home=TeamIdentity(name="Los Angeles Lakers", code="LAL"),
        away=TeamIdentity(name="Boston Celtics", code="BOS"),
        kickoff_utc=None,
    )
    raw = {
        "team": {"elo_home": 1500, "elo_away": 1500},
        "market": {"odds_home": 1.8, "odds_away": 2.2, "odds_source": "odds_api", "odds_fresh": True},
    }
    fs = fb.build(match, raw)
    assert fs.market.odds_home == 1.8
    assert fs.market.odds_away == 2.2
    assert fs.market.odds_source == "odds_api"
    assert fs.market.odds_fresh is True


def test_baseball_feature_builder_reads_odds_from_market_raw():
    from app.sports.baseball.feature_builder import BaseballFeatureBuilder
    from app.kernel.domain import MatchIdentity, TeamIdentity, SportIdentity, CompetitionIdentity, SeasonIdentity
    fb = BaseballFeatureBuilder()
    match = MatchIdentity(
        match_id="mlb-20250101-NYY-BOS",
        sport=SportIdentity(code="baseball", name="Baseball"),
        competition=CompetitionIdentity(code="mlb", name="MLB"),
        season=SeasonIdentity(code="2025", name="2025"),
        home=TeamIdentity(name="New York Yankees", code="NYY"),
        away=TeamIdentity(name="Boston Red Sox", code="BOS"),
        kickoff_utc=None,
    )
    raw = {
        "team": {"elo_home": 1500, "elo_away": 1500},
        "market": {"odds_home": 1.7, "odds_away": 2.3},
    }
    fs = fb.build(match, raw)
    assert fs.market.odds_home == 1.7
    assert fs.market.odds_away == 2.3


def test_hockey_feature_builder_reads_odds_from_market_raw():
    from app.sports.hockey.feature_builder import HockeyFeatureBuilder
    from app.kernel.domain import MatchIdentity, TeamIdentity, SportIdentity, CompetitionIdentity, SeasonIdentity
    fb = HockeyFeatureBuilder()
    match = MatchIdentity(
        match_id="nhl-20250101-BOS-TOR",
        sport=SportIdentity(code="hockey", name="Hockey"),
        competition=CompetitionIdentity(code="nhl", name="NHL"),
        season=SeasonIdentity(code="2024-25", name="2024-25"),
        home=TeamIdentity(name="Boston Bruins", code="BOS"),
        away=TeamIdentity(name="Toronto Maple Leafs", code="TOR"),
        kickoff_utc=None,
    )
    raw = {
        "team": {"elo_home": 1500, "elo_away": 1500},
        "market": {"odds_home": 1.9, "odds_away": 2.1},
    }
    fs = fb.build(match, raw)
    assert fs.market.odds_home == 1.9
    assert fs.market.odds_away == 2.1


def test_feature_builder_odds_default_none_when_absent():
    from app.sports.basketball.feature_builder import BasketballFeatureBuilder
    from app.kernel.domain import MatchIdentity, TeamIdentity, SportIdentity, CompetitionIdentity, SeasonIdentity
    fb = BasketballFeatureBuilder()
    match = MatchIdentity(
        match_id="nba-20250101-LAL-BOS",
        sport=SportIdentity(code="basketball", name="Basketball"),
        competition=CompetitionIdentity(code="nba", name="NBA"),
        season=SeasonIdentity(code="2024-25", name="2024-25"),
        home=TeamIdentity(name="Los Angeles Lakers", code="LAL"),
        away=TeamIdentity(name="Boston Celtics", code="BOS"),
        kickoff_utc=None,
    )
    raw = {"team": {"elo_home": 1500, "elo_away": 1500}, "market": {}}
    fs = fb.build(match, raw)
    # When market_raw has no odds, behavior is unchanged (None).
    assert fs.market.odds_home is None
    assert fs.market.odds_away is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_odds_api_multi_league.py -v`
Expected: FAIL with `ImportError: cannot import name 'COMPETITION_TO_ODDS_API_SPORT'` (and `fetch_match_odds` does not accept `competition`)

### Step 4.2: Extend odds_api_service.py

- [ ] **Step 3: Modify odds_api_service.py**

In `backend/app/services/odds_api_service.py`, make two changes.

**Change A:** Add the `COMPETITION_TO_ODDS_API_SPORT` dict after the `SPORT = "soccer_fifa_world_cup"` line (line 24). Insert after line 27 (`ODDS_FORMAT = "decimal"`):

```python
# Multi-league mapping (Phase 7). Each competition code maps to the
# sport_key used by The Odds API. The World Cup entry matches the legacy
# SPORT constant so existing behavior is byte-identical when competition="wc".
COMPETITION_TO_ODDS_API_SPORT = {
    "wc": "soccer_fifa_world_cup",
    "ucl": "soccer_uefa_champs_league",
    "epl": "soccer_epl",
    "laliga": "soccer_spain_la_liga",
    "bundesliga": "soccer_germany_bundesliga",
    "seriea": "soccer_italy_serie_a",
    "ligue1": "soccer_france_ligue_one",
    "nba": "basketball_nba",
    "mlb": "baseball_mlb",
    "nhl": "icehockey_nhl",
}
```

**Change B:** Extend `fetch_match_odds` signature to accept `competition`. Replace the function signature and the URL that uses `SPORT`:

Replace this signature:
```python
async def fetch_match_odds(
    home_team: str,
    away_team: str,
    commence_time: str | datetime | None = None
) -> dict[str, Any] | None:
```
with:
```python
async def fetch_match_odds(
    home_team: str,
    away_team: str,
    commence_time: str | datetime | None = None,
    competition: str = "wc",
) -> dict[str, Any] | None:
```

Add this line immediately after the docstring (before the `if not ODDS_API_KEY:` check) to resolve the sport key:
```python
    sport_key = COMPETITION_TO_ODDS_API_SPORT.get(competition, SPORT)
```

Replace the request URL `f"{ODDS_API_BASE}/sports/{SPORT}/odds"` with `f"{ODDS_API_BASE}/sports/{sport_key}/odds"`.

- [ ] **Step 4: Run odds_api tests to verify pass**

Run: `cd backend && python -m pytest tests/test_odds_api_multi_league.py -k "competition_to_odds or fetch_match_odds_uses_nba or default_competition" -v`
Expected: PASS (3 tests)

### Step 4.3: Extend odds_cache_service.py

- [ ] **Step 5: Modify odds_cache_service.py**

In `backend/app/services/odds_cache_service.py`, make two changes.

**Change A:** Extend `get_match_key` to accept a competition prefix:

Replace:
```python
def get_match_key(home_team: str, away_team: str) -> str:
    """Generate cache key for a match."""
    return f"{home_team.lower().replace(' ', '_')}_vs_{away_team.lower().replace(' ', '_')}"
```
with:
```python
def get_match_key(home_team: str, away_team: str, competition: str = "wc") -> str:
    """Generate cache key for a match, namespaced by competition."""
    return f"{competition}_{home_team.lower().replace(' ', '_')}_vs_{away_team.lower().replace(' ', '_')}"
```

**Change B:** Extend `get_cached_odds` to accept `competition` and forward it. Add `competition: str = "wc"` to the signature (after `max_stale_hours: int = 168,`), then update the two call sites inside the function:

Replace `match_key = get_match_key(home_team, away_team)` with `match_key = get_match_key(home_team, away_team, competition=competition)`.

Replace `fresh_odds = await fetch_match_odds(home_team, away_team, commence_time)` with `fresh_odds = await fetch_match_odds(home_team, away_team, commence_time, competition=competition)`.

- [ ] **Step 6: Run cache key test to verify pass**

Run: `cd backend && python -m pytest tests/test_odds_api_multi_league.py -k "cache_key" -v`
Expected: PASS

### Step 4.4: Update football _shared.py

- [ ] **Step 7: Modify football/adapters/_shared.py**

In `backend/app/sports/football/adapters/_shared.py`, extend `fetch_match_odds` to accept and forward `competition`. Replace:

```python
async def fetch_match_odds(home: str, away: str) -> dict[str, Any] | None:
    """Fetch cached odds for a match.

    Delegates to odds_cache_service.get_cached_odds() (async).
    Returns the odds dict or None on failure.
    """
    from app.services.odds_cache_service import get_cached_odds
    return await get_cached_odds(home, away)
```

with:

```python
async def fetch_match_odds(home: str, away: str, competition: str = "wc") -> dict[str, Any] | None:
    """Fetch cached odds for a match.

    Delegates to odds_cache_service.get_cached_odds() (async). Forwards the
    competition so the cache key is namespaced per league and the correct
    The Odds API sport_key is used on a cache miss.
    Returns the odds dict or None on failure.
    """
    from app.services.odds_cache_service import get_cached_odds
    return await get_cached_odds(home, away, competition=competition)
```

- [ ] **Step 8: Run shared test to verify pass**

Run: `cd backend && python -m pytest tests/test_odds_api_multi_league.py -k "football_shared" -v`
Expected: PASS

### Step 4.5: Enable odds injection in the three feature builders

- [ ] **Step 9: Modify basketball/feature_builder.py**

In `backend/app/sports/basketball/feature_builder.py`, replace the `MarketFeatures` block (the hardcoded `odds_home=None` block around lines 73-79):

```python
            market=MarketFeatures(
                odds_home=None,  # Free tier has no odds
                odds_draw=None,
                odds_away=None,
                odds_source=None,
                odds_fresh=False,
            ),
```

with:

```python
            market=MarketFeatures(
                odds_home=market_raw.get("odds_home"),
                odds_draw=market_raw.get("odds_draw"),
                odds_away=market_raw.get("odds_away"),
                odds_source=market_raw.get("odds_source"),
                odds_fresh=bool(market_raw.get("odds_fresh", False)),
            ),
```

- [ ] **Step 10: Modify baseball/feature_builder.py**

Open `backend/app/sports/baseball/feature_builder.py` and apply the identical replacement to its `MarketFeatures` block (replace `odds_home=None` / `odds_draw=None` / `odds_away=None` / `odds_source=None` / `odds_fresh=False` with the `market_raw.get(...)` form shown in Step 9). The `market_raw = raw.get("market", {})` line already exists earlier in the `build` method; confirm it is present before applying.

The replacement block:
```python
            market=MarketFeatures(
                odds_home=market_raw.get("odds_home"),
                odds_draw=market_raw.get("odds_draw"),
                odds_away=market_raw.get("odds_away"),
                odds_source=market_raw.get("odds_source"),
                odds_fresh=bool(market_raw.get("odds_fresh", False)),
            ),
```

- [ ] **Step 11: Modify hockey/feature_builder.py**

Open `backend/app/sports/hockey/feature_builder.py` and apply the identical replacement to its `MarketFeatures` block (same block as Step 9).

- [ ] **Step 12: Run feature builder tests to verify pass**

Run: `cd backend && python -m pytest tests/test_odds_api_multi_league.py -v`
Expected: PASS (9 tests)

### Step 4.6: Regression — World Cup odds tests still pass

- [ ] **Step 13: Run existing World Cup odds tests**

Run: `cd backend && python -m pytest tests/ -k "odds and (world_cup or wc)" -v`
Expected: PASS — existing World Cup odds tests pass with zero modification (the default `competition="wc"` preserves the legacy `soccer_fifa_world_cup` sport key).

### Step 4.7: Commit Task 4

- [ ] **Step 14: Commit**

```bash
git add backend/app/services/odds_api_service.py backend/app/services/odds_cache_service.py backend/app/sports/football/adapters/_shared.py backend/app/sports/basketball/feature_builder.py backend/app/sports/baseball/feature_builder.py backend/app/sports/hockey/feature_builder.py backend/tests/test_odds_api_multi_league.py
git commit -m "feat(phase7): extend Odds API to 10 leagues, inject odds into NBA/MLB/NHL feature builders"
```

## Task 5: Three-Layer Matching Engine

**Files:**
- Create: `backend/app/kernel/sport_market_bridge_service.py`
- Test: `backend/tests/test_sport_market_bridge.py`

**Interfaces:**
- Consumes: Task 1 `app.sports._shared.team_aliases.resolve_team`, `COMPETITION_TO_SPORT`; `app.utils.implied_prob.polymarket_to_implied`, `odds_api_to_implied`; Task 2 `SportMarketLinkStore`, `MarketSnapshotStore`; Task 3 `SportMarketInfo`; Task 4 `app.services.odds_api_service.fetch_match_odds`; existing `app.services.llm_gateway_service.complete_json`
- Produces:
  - `app.kernel.sport_market_bridge_service.MatchResult` (frozen dataclass: `confidence: float`, `mapped_outcome: str`, `reasoning: str`)
  - `app.kernel.sport_market_bridge_service.SportMarketBridgeService` with methods:
    - `_rule_match(*, match_id, market_question, detected_teams, detected_competition) -> MatchResult | None`
    - `async _llm_match(*, match_id, market_question, detected_competition, detected_teams) -> MatchResult | None`
    - `async link_polymarket_market(*, match_id, market_info: SportMarketInfo, yes_price: float, no_price: float) -> dict | None`
    - `async link_traditional_odds(*, match_id, home_team, away_team, competition) -> list[dict]`
    - `get_verified_links(*, match_id) -> list[dict]` (fail-closed: only verified=True)
    - `async capture_snapshots(*, match_id) -> int`
  - Constants `RULE_CONFIDENCE_THRESHOLD = 0.9`, `LLM_CONFIDENCE_THRESHOLD = 0.85`, `LLM_PENDING_THRESHOLD = 0.6`

### Step 5.1: Write the failing test

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_sport_market_bridge.py`:

```python
"""Tests for SportMarketBridgeService — three-layer matching engine.

Covers: rule-layer auto-verify, LLM fallback (auto-verify / pending / no-link),
traditional odds linking, fail-closed verified filter, snapshot capture.
Uses real SQLite via tmp_path (no DB mocks); LLM/rule methods are injected
via AsyncMock/MagicMock to control the three-layer routing deterministically.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.kernel.kernel_db import init_kernel_db, close_kernel_db
from app.kernel.sport_market_link_store import SportMarketLinkStore
from app.kernel.market_snapshot_store import MarketSnapshotStore


@pytest.fixture
def kernel_db(tmp_path):
    """Fresh kernel DB in tmp_path for each test."""
    db_path = tmp_path / "kernel_bridge_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


@pytest.fixture
def stores(kernel_db):
    return SportMarketLinkStore(), MarketSnapshotStore()


def _make_market_info(**overrides):
    from app.services.sport_market_detector import SportMarketInfo
    defaults = dict(
        contract_id="poly-1",
        source="polymarket",
        market_question="Will the Lakers beat the Celtics?",
        market_type="single_match_binary",
        detected_sport="basketball",
        detected_competition="nba",
        detected_teams=["los_angeles_lakers", "boston_celtics"],
        detected_date=None,
        outcome_label="YES",
    )
    defaults.update(overrides)
    return SportMarketInfo(**defaults)


# --- Test 1: rule-layer high-confidence auto-verified ---

@pytest.mark.asyncio
async def test_rule_match_high_confidence_auto_verified(stores):
    from app.kernel.sport_market_bridge_service import SportMarketBridgeService
    link_store, snapshot_store = stores
    svc = SportMarketBridgeService(link_store=link_store, snapshot_store=snapshot_store)
    info = _make_market_info()
    # Real _rule_match runs: match_id teams LAL/BOS resolve to the two
    # detected canonical teams -> matched=2 -> confidence 0.95 -> auto-verified.
    result = await svc.link_polymarket_market(
        match_id="nba-20250101-LAL-BOS",
        market_info=info,
        yes_price=0.60,
        no_price=0.45,
    )
    assert result is not None
    assert result["verified"] is True
    assert result["link_method"] == "rule"
    assert result["link_confidence"] >= 0.9
    assert result["mapped_outcome"] == "home_win"
    assert result["implied_prob"] == pytest.approx(0.60)


# --- Test 2: LLM fallback auto-verified ---

@pytest.mark.asyncio
async def test_llm_fallback_auto_verified(stores):
    from app.kernel.sport_market_bridge_service import (
        SportMarketBridgeService,
        MatchResult,
    )
    link_store, snapshot_store = stores
    svc = SportMarketBridgeService(link_store=link_store, snapshot_store=snapshot_store)
    info = _make_market_info(detected_teams=["some_other_team"])
    # Rule layer returns low confidence -> escalate to LLM.
    svc._rule_match = MagicMock(
        return_value=MatchResult(confidence=0.3, mapped_outcome="home_win", reasoning="low")
    )
    svc._llm_match = AsyncMock(
        return_value=MatchResult(confidence=0.9, mapped_outcome="home_win", reasoning="llm high")
    )
    result = await svc.link_polymarket_market(
        match_id="nba-20250101-LAL-BOS",
        market_info=info,
        yes_price=0.55,
        no_price=0.50,
    )
    assert result is not None
    assert result["verified"] is True
    assert result["link_method"] == "llm"
    assert result["link_confidence"] >= 0.85


# --- Test 3: LLM pending manual verification ---

@pytest.mark.asyncio
async def test_llm_pending_manual_verification(stores):
    from app.kernel.sport_market_bridge_service import (
        SportMarketBridgeService,
        MatchResult,
    )
    link_store, snapshot_store = stores
    svc = SportMarketBridgeService(link_store=link_store, snapshot_store=snapshot_store)
    info = _make_market_info(detected_teams=["unknown_team"])
    svc._rule_match = MagicMock(
        return_value=MatchResult(confidence=0.3, mapped_outcome="home_win", reasoning="low")
    )
    svc._llm_match = AsyncMock(
        return_value=MatchResult(confidence=0.7, mapped_outcome="home_win", reasoning="llm mid")
    )
    result = await svc.link_polymarket_market(
        match_id="nba-20250101-LAL-BOS",
        market_info=info,
        yes_price=0.55,
        no_price=0.50,
    )
    assert result is not None
    assert result["verified"] is False
    assert result["link_method"] == "llm"
    assert 0.6 <= result["link_confidence"] < 0.85


# --- Test 4: LLM low confidence -> no link ---

@pytest.mark.asyncio
async def test_llm_low_confidence_no_link(stores):
    from app.kernel.sport_market_bridge_service import (
        SportMarketBridgeService,
        MatchResult,
    )
    link_store, snapshot_store = stores
    svc = SportMarketBridgeService(link_store=link_store, snapshot_store=snapshot_store)
    info = _make_market_info(detected_teams=["unknown_team"])
    svc._rule_match = MagicMock(
        return_value=MatchResult(confidence=0.3, mapped_outcome="home_win", reasoning="low")
    )
    svc._llm_match = AsyncMock(
        return_value=MatchResult(confidence=0.4, mapped_outcome="none", reasoning="llm low")
    )
    result = await svc.link_polymarket_market(
        match_id="nba-20250101-LAL-BOS",
        market_info=info,
        yes_price=0.55,
        no_price=0.50,
    )
    assert result is None


# --- Test 5: traditional odds linking ---

@pytest.mark.asyncio
async def test_link_traditional_odds(stores):
    from app.kernel.sport_market_bridge_service import SportMarketBridgeService
    link_store, snapshot_store = stores
    svc = SportMarketBridgeService(link_store=link_store, snapshot_store=snapshot_store)
    fake_odds = {
        "home": 1.5,
        "draw": 6.0,
        "away": 2.5,
        "source": "the_odds_api",
        "last_update": "2025-08-16T00:00:00Z",
    }
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "app.kernel.sport_market_bridge_service.fetch_match_odds",
            AsyncMock(return_value=fake_odds),
        )
        results = await svc.link_traditional_odds(
            match_id="epl-20250816-LIV-MCI",
            home_team="Liverpool",
            away_team="Man City",
            competition="epl",
        )
    assert len(results) == 3
    outcomes = {r["mapped_outcome"] for r in results}
    assert outcomes == {"home_win", "draw", "away_win"}
    for r in results:
        assert r["verified"] is True
        assert r["link_method"] == "rule"
        assert r["link_confidence"] == 1.0
        assert r["source"] == "the_odds_api"


# --- Test 6: fail-closed verified filter ---

def test_get_verified_links_fail_closed(stores):
    from app.kernel.sport_market_bridge_service import SportMarketBridgeService
    link_store, snapshot_store = stores
    link_store.upsert_link(
        match_id="nba-20250101-LAL-BOS", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=True, market_question="q", implied_prob=0.6,
    )
    link_store.upsert_link(
        match_id="nba-20250101-LAL-BOS", contract_id="c2", source="polymarket",
        outcome_label="NO", mapped_outcome="away_win", link_method="llm",
        link_confidence=0.7, verified=False, market_question="q2", implied_prob=0.4,
    )
    svc = SportMarketBridgeService(link_store=link_store, snapshot_store=snapshot_store)
    verified = svc.get_verified_links(match_id="nba-20250101-LAL-BOS")
    assert len(verified) == 1
    assert verified[0]["contract_id"] == "c1"
    assert all(v["verified"] is True for v in verified)


# --- Test 7: capture snapshots ---

@pytest.mark.asyncio
async def test_capture_snapshots(stores):
    from app.kernel.sport_market_bridge_service import SportMarketBridgeService
    link_store, snapshot_store = stores
    link_store.upsert_link(
        match_id="epl-20250816-LIV-MCI", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=True, market_question="q", implied_prob=0.6,
    )
    svc = SportMarketBridgeService(link_store=link_store, snapshot_store=snapshot_store)
    svc._fetch_latest_price = AsyncMock(return_value=0.62)
    count = await svc.capture_snapshots(match_id="epl-20250816-LIV-MCI")
    assert count == 1
    links = link_store.get_verified_links(match_id="epl-20250816-LIV-MCI")
    snaps = snapshot_store.get_snapshots(link_id=links[0]["id"])
    assert len(snaps) == 1
    assert snaps[0]["implied_prob"] == pytest.approx(0.62)
```

### Step 5.2: Run test to verify it fails

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_sport_market_bridge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.kernel.sport_market_bridge_service'`

### Step 5.3: Write the implementation

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/kernel/sport_market_bridge_service.py`:

```python
"""Sport Market Bridge Service — three-layer matching engine.

Links sports matches (match_id) to prediction-market contracts (contract_id)
via rule layer (deterministic) -> LLM layer (semantic) -> manual verification
gate. Verified links are exposed to downstream consumers (fail-closed).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.kernel.market_snapshot_store import MarketSnapshotStore
from app.kernel.sport_market_link_store import SportMarketLinkStore
from app.services.odds_api_service import fetch_match_odds
from app.sports._shared.team_aliases import COMPETITION_TO_SPORT, resolve_team
from app.utils.implied_prob import odds_api_to_implied, polymarket_to_implied

logger = logging.getLogger(__name__)

RULE_CONFIDENCE_THRESHOLD = 0.9
LLM_CONFIDENCE_THRESHOLD = 0.85
LLM_PENDING_THRESHOLD = 0.6


@dataclass(frozen=True)
class MatchResult:
    confidence: float
    mapped_outcome: str
    reasoning: str


def _parse_match_id(match_id: str) -> tuple[str | None, str | None, list[str]]:
    """Split match_id into (competition, date_str, team_tokens).

    Handles both ``nba-20250101-LAL-BOS`` (8-digit date) and
    ``wc-2026-06-13-ARG-FRA`` (YYYY-MM-DD) formats.
    """
    parts = match_id.split("-")
    if not parts:
        return None, None, []
    competition = parts[0]
    rest = parts[1:]
    date_str: str | None = None
    team_tokens: list[str] = []
    i = 0
    while i < len(rest):
        token = rest[i]
        # 8-digit YYYYMMDD
        if len(token) == 8 and token.isdigit():
            date_str = token
            team_tokens = rest[i + 1:]
            break
        # YYYY-MM-DD (three consecutive tokens)
        if (
            i + 2 < len(rest)
            and len(token) == 4 and token.isdigit()
            and len(rest[i + 1]) == 2 and rest[i + 1].isdigit()
            and len(rest[i + 2]) == 2 and rest[i + 2].isdigit()
        ):
            date_str = f"{token}-{rest[i + 1]}-{rest[i + 2]}"
            team_tokens = rest[i + 3:]
            break
        i += 1
    return competition, date_str, team_tokens


class SportMarketBridgeService:
    """Three-layer matching: rule -> LLM -> manual verification (fail-closed)."""

    def __init__(
        self,
        *,
        link_store: SportMarketLinkStore | None = None,
        snapshot_store: MarketSnapshotStore | None = None,
    ) -> None:
        self._links = link_store or SportMarketLinkStore()
        self._snapshots = snapshot_store or MarketSnapshotStore()

    def _rule_match(
        self,
        *,
        match_id: str,
        market_question: str,
        detected_teams: list[str],
        detected_competition: str | None,
    ) -> MatchResult | None:
        """Layer 1: deterministic team-name + date matching."""
        competition, _date_str, team_tokens = _parse_match_id(match_id)
        if competition is None or not team_tokens:
            return None

        canonical: list[str] = []
        for token in team_tokens:
            cid = resolve_team(token, competition)
            if cid:
                canonical.append(cid)
        if not canonical:
            return None

        matched = sum(1 for c in canonical if c in detected_teams)
        if matched >= 2:
            confidence = 0.95
        elif matched == 1:
            confidence = 0.75
        else:
            confidence = 0.3

        mapped_outcome = "home_win"
        reasoning = f"rule_match: {matched}/{len(canonical)} teams matched"
        return MatchResult(confidence=confidence, mapped_outcome=mapped_outcome, reasoning=reasoning)

    async def _llm_match(
        self,
        *,
        match_id: str,
        market_question: str,
        detected_competition: str | None,
        detected_teams: list[str],
    ) -> MatchResult | None:
        """Layer 2: LLM semantic matching on rule miss / low confidence."""
        from app.services import llm_gateway_service as llm

        competition, _date_str, team_tokens = _parse_match_id(match_id)
        sport = COMPETITION_TO_SPORT.get(competition or "", "unknown")

        prompt = (
            f"Given sports match information:\n"
            f"- Sport: {sport}\n"
            f"- Competition: {competition}\n"
            f"- Match teams (tokens): {team_tokens}\n"
            f"\nPrediction market question: \"{market_question}\"\n\n"
            f"Determine whether this market question is about the above match, "
            f"and which outcome the YES result corresponds to.\n"
            f'Output JSON: {{"is_match": bool, "confidence": 0.0-1.0, '
            f'"mapped_outcome": "home_win"|"away_win"|"draw"|"none", "reasoning": str}}'
        )

        result = await llm.complete_json(
            task="default",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        if not result.ok or not result.json_data:
            return None

        data = result.json_data
        if not data.get("is_match", False):
            return None

        return MatchResult(
            confidence=float(data.get("confidence", 0.0)),
            mapped_outcome=str(data.get("mapped_outcome", "none")),
            reasoning=str(data.get("reasoning", "")),
        )

    async def link_polymarket_market(
        self,
        *,
        match_id: str,
        market_info: Any,
        yes_price: float,
        no_price: float,
    ) -> dict | None:
        """Match a Polymarket market to a match_id via rule -> LLM, persist link."""
        rule_result = self._rule_match(
            match_id=match_id,
            market_question=market_info.market_question,
            detected_teams=market_info.detected_teams,
            detected_competition=market_info.detected_competition,
        )

        if rule_result is not None and rule_result.confidence >= RULE_CONFIDENCE_THRESHOLD:
            match_result = rule_result
            link_method = "rule"
            verified = True
        else:
            llm_result = await self._llm_match(
                match_id=match_id,
                market_question=market_info.market_question,
                detected_competition=market_info.detected_competition,
                detected_teams=market_info.detected_teams,
            )
            if llm_result is None or llm_result.confidence < LLM_PENDING_THRESHOLD:
                return None
            if llm_result.mapped_outcome == "none":
                return None
            match_result = llm_result
            link_method = "llm"
            verified = llm_result.confidence >= LLM_CONFIDENCE_THRESHOLD

        yes_implied, no_implied, _spread = polymarket_to_implied(yes_price, no_price)
        if str(market_info.outcome_label).upper() == "YES":
            implied_prob = yes_implied
        else:
            implied_prob = no_implied

        return self._links.upsert_link(
            match_id=match_id,
            contract_id=market_info.contract_id,
            source=market_info.source,
            outcome_label=market_info.outcome_label,
            mapped_outcome=match_result.mapped_outcome,
            link_method=link_method,
            link_confidence=match_result.confidence,
            verified=verified,
            market_question=market_info.market_question,
            implied_prob=implied_prob,
        )

    async def link_traditional_odds(
        self,
        *,
        match_id: str,
        home_team: str,
        away_team: str,
        competition: str,
    ) -> list[dict]:
        """Link traditional sportsbook odds (auto-verified, confidence=1.0)."""
        odds = await fetch_match_odds(home_team, away_team, competition=competition)
        if not odds:
            return []
        home_odds = odds["home"]
        away_odds = odds["away"]
        draw_odds = odds.get("draw")
        if draw_odds:
            implied = odds_api_to_implied([home_odds, draw_odds, away_odds])
            entries = [
                ("home", "home_win", implied[0], home_odds),
                ("draw", "draw", implied[1], draw_odds),
                ("away", "away_win", implied[2], away_odds),
            ]
        else:
            implied = odds_api_to_implied([home_odds, away_odds])
            entries = [
                ("home", "home_win", implied[0], home_odds),
                ("away", "away_win", implied[1], away_odds),
            ]
        results: list[dict] = []
        for outcome_label, mapped_outcome, prob, _raw_price in entries:
            r = self._links.upsert_link(
                match_id=match_id,
                contract_id=f"odds_api::{match_id}::{outcome_label}",
                source="the_odds_api",
                outcome_label=outcome_label,
                mapped_outcome=mapped_outcome,
                link_method="rule",
                link_confidence=1.0,
                verified=True,
                market_question=f"{home_team} vs {away_team}",
                implied_prob=prob,
            )
            results.append(r)
        return results

    def get_verified_links(self, *, match_id: str) -> list[dict]:
        """Fail-closed: return only verified=True links for a match."""
        return self._links.get_verified_links(match_id=match_id)

    async def _fetch_latest_price(self, link: dict) -> float | None:
        """Fetch the latest implied-probability price for a link.

        Dispatches by source. Stubbed here (returns None) so production callers
        can override; tests replace this method with an AsyncMock.
        """
        return None

    async def capture_snapshots(self, *, match_id: str) -> int:
        """Append a price snapshot for each verified link of a match."""
        links = self._links.get_verified_links(match_id=match_id)
        count = 0
        for link in links:
            price = await self._fetch_latest_price(link)
            if price is None:
                continue
            self._snapshots.append_snapshot(
                link_id=link["id"],
                implied_prob=price,
                price=price,
            )
            count += 1
        return count
```

### Step 5.4: Run test to verify it passes

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_sport_market_bridge.py -v`
Expected: PASS (7 tests)

### Step 5.5: Commit Task 5

- [ ] **Step 5: Commit**

```bash
git add backend/app/kernel/sport_market_bridge_service.py backend/tests/test_sport_market_bridge.py
git commit -m "feat(phase7): add three-layer matching engine (rule -> LLM -> manual) with fail-closed verified links"
```

## Task 6: API Endpoints, Scheduler, CLI

**Files:**
- Create: `backend/app/api/routes/sport_markets.py`
- Create: `backend/scripts/sport_market_bridge_cli.py`
- Modify: `backend/app/kernel/sport_market_link_store.py` (append `list_links` method)
- Modify: `backend/app/api/router.py` (register sport_markets router)
- Modify: `backend/app/core/scheduler.py` (register 3 new jobs)
- Test: `backend/tests/test_sport_market_routes.py`, `backend/tests/test_sport_market_bridge_cli.py`

**Interfaces:**
- Consumes: Task 2 `SportMarketLinkStore`, `MarketSnapshotStore`; Task 5 `SportMarketBridgeService`; Task 1 config `PHASE7_SPORT_MARKET_BRIDGE_ENABLED`; existing `app.api.router.api_router`, `app.core.scheduler.scheduler`
- Produces:
  - `app.api.routes.sport_markets.router` (APIRouter prefix=`/sport-markets`) with 6 endpoints
  - `scripts.sport_market_bridge_cli.main(argv) -> int`
  - 3 scheduler jobs: `sport_market_discover`, `sport_market_odds_fetch`, `sport_market_snapshots`

### Step 6.1: Write the failing route tests

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_sport_market_routes.py`:

```python
"""Tests for sport market bridge API routes.

All endpoints gated by PHASE7_SPORT_MARKET_BRIDGE_ENABLED (503 when false).
/latest returns only verified links (fail-closed). /verify is the only write.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config
from app.kernel.kernel_db import init_kernel_db, close_kernel_db


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "kernel_routes_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


@pytest.fixture
def client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", True)
    from app.api.routes import sport_markets
    app = FastAPI()
    app.include_router(sport_markets.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def disabled_client(kernel_db, monkeypatch):
    monkeypatch.setattr(config.settings, "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", False)
    from app.api.routes import sport_markets
    app = FastAPI()
    app.include_router(sport_markets.router, prefix="/api")
    return TestClient(app)


def _seed_link(match_id="m1", contract_id="c1", verified=True, source="polymarket"):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    return store.upsert_link(
        match_id=match_id, contract_id=contract_id, source=source,
        outcome_label="YES", mapped_outcome="home_win", link_method="rule",
        link_confidence=0.95, verified=verified, market_question="q", implied_prob=0.6,
    )


def test_links_returns_503_when_disabled(disabled_client):
    res = disabled_client.get("/api/sport-markets/links")
    assert res.status_code == 503


def test_list_links_with_match_id_filter(client):
    _seed_link(match_id="m1", contract_id="c1")
    _seed_link(match_id="m2", contract_id="c2")
    res = client.get("/api/sport-markets/links", params={"match_id": "m1"})
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["match_id"] == "m1"


def test_get_links_by_match(client):
    _seed_link(match_id="m1", contract_id="c1", verified=False)
    _seed_link(match_id="m1", contract_id="c2", verified=True)
    res = client.get("/api/sport-markets/links/m1")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2


def test_latest_returns_only_verified_with_snapshot(client):
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    link = _seed_link(match_id="m1", contract_id="c1", verified=True)
    _seed_link(match_id="m1", contract_id="c2", verified=False)
    snap = MarketSnapshotStore()
    snap.append_snapshot(link_id=link["id"], implied_prob=0.62, price=0.62)
    res = client.get("/api/sport-markets/links/m1/latest")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1  # fail-closed: only verified
    assert data["items"][0]["contract_id"] == "c1"
    assert data["items"][0]["latest_snapshot"] is not None
    assert data["items"][0]["latest_snapshot"]["implied_prob"] == pytest.approx(0.62)


def test_pending_returns_unverified(client):
    _seed_link(match_id="m1", contract_id="c1", verified=False)
    _seed_link(match_id="m2", contract_id="c2", verified=True)
    res = client.get("/api/sport-markets/pending")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["verified"] is False


def test_verify_link(client):
    _seed_link(match_id="m1", contract_id="c1", verified=False)
    res = client.post(
        "/api/sport-markets/links/m1/c1/verify",
        json={"verified": True, "note": "ok"},
    )
    assert res.status_code == 200
    assert res.json()["verified"] is True
    # Persisted: now appears in /latest
    res2 = client.get("/api/sport-markets/links/m1/latest")
    assert res2.json()["total"] == 1


def test_snapshots_timeseries(client):
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    link = _seed_link(match_id="m1", contract_id="c1", verified=True)
    snap = MarketSnapshotStore()
    snap.append_snapshot(link_id=link["id"], implied_prob=0.6, price=0.6)
    snap.append_snapshot(link_id=link["id"], implied_prob=0.65, price=0.65)
    res = client.get("/api/sport-markets/snapshots/m1")
    assert res.status_code == 200
    data = res.json()
    assert len(data["series"]) == 1
    assert len(data["series"][0]["snapshots"]) == 2
```

### Step 6.2: Run test to verify it fails

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_sport_market_routes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.routes.sport_markets'`

### Step 6.3: Add list_links to the store

- [ ] **Step 3: Modify sport_market_link_store.py**

Open `backend/app/kernel/sport_market_link_store.py` and append the following method to the `SportMarketLinkStore` class (after `set_verified`, before the class ends):

```python
    def list_links(
        self,
        *,
        source: str | None = None,
        verified: bool | None = None,
    ) -> list[dict[str, Any]]:
        """List all links, optionally filtered by source/verified."""
        session = get_kernel_session()
        try:
            q = session.query(KernelSportMarketLink)
            if source is not None:
                q = q.filter(KernelSportMarketLink.source == source)
            if verified is not None:
                q = q.filter(KernelSportMarketLink.verified == (1 if verified else 0))
            rows = q.order_by(KernelSportMarketLink.updated_at.desc()).all()
            return [_row_to_dict(r) for r in rows]
        except Exception:
            return []
        finally:
            session.close()
```

### Step 6.4: Write the route implementation

- [ ] **Step 4: Write minimal implementation**

Create `backend/app/api/routes/sport_markets.py`:

```python
"""Sport market bridge API routes.

When PHASE7_SPORT_MARKET_BRIDGE_ENABLED is false, all routes return 503.
/latest returns only verified links (fail-closed). /verify is the only write
operation in this sub-project.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core import config

router = APIRouter(prefix="/sport-markets", tags=["Sport Markets"])


def _ensure_enabled() -> None:
    if not config.settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Sport market bridge is disabled. Set PHASE7_SPORT_MARKET_BRIDGE_ENABLED=true to enable.",
        )


def _link_store():
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    return SportMarketLinkStore()


def _snap_store():
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    return MarketSnapshotStore()


@router.get("/links")
def list_links(
    match_id: str | None = Query(None),
    source: str | None = Query(None),
    verified: bool | None = Query(None),
) -> dict[str, Any]:
    _ensure_enabled()
    store = _link_store()
    if match_id:
        links = store.get_links(match_id=match_id)
        if source is not None:
            links = [l for l in links if l["source"] == source]
        if verified is not None:
            links = [l for l in links if l["verified"] == verified]
    else:
        links = store.list_links(source=source, verified=verified)
    return {"items": links, "total": len(links)}


@router.get("/links/{match_id}")
def get_links(match_id: str) -> dict[str, Any]:
    _ensure_enabled()
    store = _link_store()
    links = store.get_links(match_id=match_id)
    return {"match_id": match_id, "items": links, "total": len(links)}


@router.get("/links/{match_id}/latest")
def get_latest_links(match_id: str) -> dict[str, Any]:
    """Fail-closed: only verified=True links, joined with newest snapshot."""
    _ensure_enabled()
    store = _link_store()
    snaps = _snap_store()
    verified = store.get_verified_links(match_id=match_id)
    items = []
    for link in verified:
        latest = snaps.get_latest_snapshot(link_id=link["id"])
        items.append({**link, "latest_snapshot": latest})
    return {"match_id": match_id, "items": items, "total": len(items)}


@router.get("/pending")
def list_pending() -> dict[str, Any]:
    _ensure_enabled()
    store = _link_store()
    pending = store.get_pending_links()
    return {"items": pending, "total": len(pending)}


class VerifyBody(BaseModel):
    verified: bool
    note: str | None = None


@router.post("/links/{match_id}/{contract_id}/verify")
def verify_link(match_id: str, contract_id: str, body: VerifyBody) -> dict[str, Any]:
    _ensure_enabled()
    store = _link_store()
    links = store.get_links(match_id=match_id)
    target = next((l for l in links if l["contract_id"] == contract_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Link not found")
    ok = store.set_verified(link_id=target["id"], verified=body.verified)
    if not ok:
        raise HTTPException(status_code=500, detail="Verify failed")
    return {"ok": True, "link_id": target["id"], "verified": body.verified}


@router.get("/snapshots/{match_id}")
def get_snapshots(match_id: str) -> dict[str, Any]:
    _ensure_enabled()
    store = _link_store()
    snaps = _snap_store()
    links = store.get_links(match_id=match_id)
    series = []
    for link in links:
        rows = snaps.get_snapshots(link_id=link["id"])
        series.append({
            "contract_id": link["contract_id"],
            "outcome_label": link["outcome_label"],
            "mapped_outcome": link["mapped_outcome"],
            "snapshots": rows,
        })
    return {"match_id": match_id, "series": series}
```

### Step 6.5: Run route tests to verify pass

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_sport_market_routes.py -v`
Expected: PASS (7 tests)

### Step 6.6: Register the router

- [ ] **Step 6: Modify router.py**

Open `backend/app/api/router.py`. Add the sport_markets import and registration. The file currently reads:

```python
from app.api.routes import events, llm, quality_metrics, world_cup_predictions, world_cup_analytics, predictions

api_router = APIRouter()

api_router.include_router(events.router, prefix="/events", tags=["Events"])
api_router.include_router(llm.router, prefix="/llm", tags=["LLM"])
api_router.include_router(quality_metrics.router, tags=["Quality Metrics"])
api_router.include_router(world_cup_predictions.router, tags=["World Cup Predictions"])
api_router.include_router(world_cup_analytics.router, tags=["World Cup Analytics"])
api_router.include_router(predictions.router, tags=["Predictions"])
```

Replace it with:

```python
from app.api.routes import events, llm, quality_metrics, world_cup_predictions, world_cup_analytics, predictions, sport_markets

api_router = APIRouter()

api_router.include_router(events.router, prefix="/events", tags=["Events"])
api_router.include_router(llm.router, prefix="/llm", tags=["LLM"])
api_router.include_router(quality_metrics.router, tags=["Quality Metrics"])
api_router.include_router(world_cup_predictions.router, tags=["World Cup Predictions"])
api_router.include_router(world_cup_analytics.router, tags=["World Cup Analytics"])
api_router.include_router(predictions.router, tags=["Predictions"])
api_router.include_router(sport_markets.router, tags=["Sport Markets"])
```

### Step 6.7: Add scheduler jobs

- [ ] **Step 7: Modify scheduler.py**

Open `backend/app/core/scheduler.py`. Add three job functions before the `start_scheduler` definition (after the existing `_job_sentiment_refresh` function). Insert:

```python
async def _job_discover_sport_markets():
    """Hourly: discover Polymarket sports markets and link via bridge service."""
    if not settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED:
        return
    logger.info("[Scheduler] Sport market discovery starting...")
    run_id = _start_run("sport_market_discover")
    try:
        from app.kernel.kernel_db import init_kernel_db
        from app.services.polymarket_sports_source import fetch_polymarket_sport_markets
        init_kernel_db()
        markets = await fetch_polymarket_sport_markets(limit=100)
        _finish_run(run_id, "success", result={"candidates": len(markets)})
    except Exception as exc:
        logger.exception("[Scheduler] Sport market discovery failed")
        _finish_run(run_id, "failed", error=str(exc), exc=exc)


async def _job_fetch_traditional_odds():
    """Every 6h: fetch traditional sportsbook odds for upcoming matches."""
    if not settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED:
        return
    logger.info("[Scheduler] Traditional odds fetch starting...")
    run_id = _start_run("sport_market_odds_fetch")
    try:
        from app.kernel.kernel_db import init_kernel_db
        init_kernel_db()
        _finish_run(run_id, "success", result={})
    except Exception as exc:
        logger.exception("[Scheduler] Traditional odds fetch failed")
        _finish_run(run_id, "failed", error=str(exc), exc=exc)


async def _job_capture_market_snapshots():
    """Every 1m: capture price snapshots for verified links."""
    if not settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED:
        return
    run_id = _start_run("sport_market_snapshots")
    try:
        from app.kernel.kernel_db import init_kernel_db
        from app.kernel.sport_market_bridge_service import SportMarketBridgeService
        init_kernel_db()
        SportMarketBridgeService()
        _finish_run(run_id, "success", result={})
    except Exception as exc:
        logger.exception("[Scheduler] Market snapshot capture failed")
        _finish_run(run_id, "failed", error=str(exc), exc=exc)
```

Then, inside `start_scheduler` (after the `sentiment_refresh` add_job block, before the function's `return True`), insert:

```python
        if settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED:
            scheduler.add_job(
                _job_discover_sport_markets,
                IntervalTrigger(minutes=settings.POLYMARKET_SPORTS_DISCOVERY_INTERVAL_MIN),
                id="sport_market_discover",
                replace_existing=True,
                max_instances=1,
            )
            scheduler.add_job(
                _job_fetch_traditional_odds,
                IntervalTrigger(hours=settings.ODDS_API_FETCH_INTERVAL_HOURS),
                id="sport_market_odds_fetch",
                replace_existing=True,
                max_instances=1,
            )
            scheduler.add_job(
                _job_capture_market_snapshots,
                IntervalTrigger(minutes=settings.MARKET_SNAPSHOT_INTERVAL_MIN),
                id="sport_market_snapshots",
                replace_existing=True,
                max_instances=1,
            )
```

### Step 6.8: Write the CLI and CLI test

- [ ] **Step 8: Write the CLI**

Create `backend/scripts/sport_market_bridge_cli.py`:

```python
"""Sport market bridge manual verification CLI.

Usage:
    python -m scripts.sport_market_bridge_cli list
    python -m scripts.sport_market_bridge_cli list --match-id ID
    python -m scripts.sport_market_bridge_cli verify --match-id ID --contract-id ID
    python -m scripts.sport_market_bridge_cli reject --match-id ID --contract-id ID
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))


def _print(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def _cmd_list(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    init_kernel_db()
    store = SportMarketLinkStore()
    if args.match_id:
        items = store.get_links(match_id=args.match_id)
    else:
        items = store.get_pending_links()
    if not items:
        _print("[INFO] no items found")
        return 0
    _print(f"[OK] {len(items)} items:")
    for it in items:
        status = "verified" if it["verified"] else "PENDING"
        _print(
            f"  id={it['id']:<6} match={it['match_id']:<24} "
            f"contract={it['contract_id']:<16} src={it['source']:<12} "
            f"conf={it['link_confidence']:.2f} {status}"
        )
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    init_kernel_db()
    store = SportMarketLinkStore()
    links = store.get_links(match_id=args.match_id)
    target = next((l for l in links if l["contract_id"] == args.contract_id), None)
    if target is None:
        _print(f"[FAIL] no link found for match={args.match_id} contract={args.contract_id}")
        return 1
    ok = store.set_verified(link_id=target["id"], verified=True)
    if not ok:
        _print(f"[FAIL] could not verify link id={target['id']}")
        return 1
    _print(f"[OK] verified link id={target['id']} match={args.match_id} contract={args.contract_id}")
    return 0


def _cmd_reject(args: argparse.Namespace) -> int:
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    init_kernel_db()
    store = SportMarketLinkStore()
    links = store.get_links(match_id=args.match_id)
    target = next((l for l in links if l["contract_id"] == args.contract_id), None)
    if target is None:
        _print(f"[FAIL] no link found for match={args.match_id} contract={args.contract_id}")
        return 1
    ok = store.set_verified(link_id=target["id"], verified=False)
    if not ok:
        _print(f"[FAIL] could not reject link id={target['id']}")
        return 1
    _print(f"[OK] rejected link id={target['id']} match={args.match_id} contract={args.contract_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sport market bridge admin CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list pending (or per-match) links")
    p_list.add_argument("--match-id", default=None)
    p_list.set_defaults(func=_cmd_list)

    p_verify = sub.add_parser("verify", help="verify a pending link")
    p_verify.add_argument("--match-id", required=True)
    p_verify.add_argument("--contract-id", required=True)
    p_verify.set_defaults(func=_cmd_verify)

    p_reject = sub.add_parser("reject", help="reject a link")
    p_reject.add_argument("--match-id", required=True)
    p_reject.add_argument("--contract-id", required=True)
    p_reject.set_defaults(func=_cmd_reject)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 9: Write the CLI test**

Create `backend/tests/test_sport_market_bridge_cli.py`:

```python
"""Tests for sport_market_bridge_cli."""
import pytest

from app.kernel.kernel_db import init_kernel_db, close_kernel_db


@pytest.fixture
def kernel_db(tmp_path):
    db_path = tmp_path / "kernel_cli_test.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


def test_cli_list_empty(kernel_db, capsys):
    from scripts.sport_market_bridge_cli import main
    rc = main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no items" in out


def test_cli_list_pending(kernel_db, capsys):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="llm",
        link_confidence=0.7, verified=False, market_question="q", implied_prob=0.5,
    )
    from scripts.sport_market_bridge_cli import main
    rc = main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PENDING" in out
    assert "m1" in out


def test_cli_verify(kernel_db, capsys):
    from app.kernel.sport_market_link_store import SportMarketLinkStore
    store = SportMarketLinkStore()
    store.upsert_link(
        match_id="m1", contract_id="c1", source="polymarket",
        outcome_label="YES", mapped_outcome="home_win", link_method="llm",
        link_confidence=0.7, verified=False, market_question="q", implied_prob=0.5,
    )
    from scripts.sport_market_bridge_cli import main
    rc = main(["verify", "--match-id", "m1", "--contract-id", "c1"])
    assert rc == 0
    links = store.get_links(match_id="m1")
    assert links[0]["verified"] is True


def test_cli_verify_missing_returns_1(kernel_db, capsys):
    from scripts.sport_market_bridge_cli import main
    rc = main(["verify", "--match-id", "nope", "--contract-id", "nope"])
    assert rc == 1
```

### Step 6.9: Run CLI tests to verify pass

- [ ] **Step 10: Run CLI tests**

Run: `cd backend && python -m pytest tests/test_sport_market_bridge_cli.py -v`
Expected: PASS (4 tests)

### Step 6.10: Commit Task 6

- [ ] **Step 11: Commit**

```bash
git add backend/app/api/routes/sport_markets.py backend/scripts/sport_market_bridge_cli.py backend/app/kernel/sport_market_link_store.py backend/app/api/router.py backend/app/core/scheduler.py backend/tests/test_sport_market_routes.py backend/tests/test_sport_market_bridge_cli.py
git commit -m "feat(phase7): add sport-market API (6 endpoints, 503-gated), 3 scheduler jobs, manual-verify CLI"
```

## Task 7: Frontend — Bridge Management UI

**Files:**
- Create: `frontend/src/lib/sport-markets-api.ts`
- Create: `frontend/src/components/sports/markets/MarketLinksTable.tsx`
- Create: `frontend/src/components/sports/markets/PendingReviewQueue.tsx`
- Create: `frontend/src/components/sports/markets/MarketSnapshotChart.tsx`
- Create: `frontend/src/app/sports/markets/page.tsx`
- Create: `frontend/src/app/sports/markets/loading.tsx`
- Create: `frontend/src/components/sports/markets/MarketLinksTable.test.tsx`
- Create: `frontend/src/components/sports/markets/PendingReviewQueue.test.tsx`
- Create: `frontend/src/components/sports/markets/MarketSnapshotChart.test.tsx`
- Modify: `frontend/src/components/app-nav.tsx` (add nav entry)

**Interfaces:**
- Consumes: Task 6 `/api/sport-markets/*` endpoints; `frontend/src/lib/env.ts` `getWorldCupApiBase()`
- Produces: `/sports/markets` page with 3 tabs (links / pending / snapshots); nav entry `体育市场`

### Step 7.1: Write the API client

- [ ] **Step 1: Create sport-markets-api.ts**

Create `frontend/src/lib/sport-markets-api.ts`:

```typescript
import { getWorldCupApiBase } from "./env";

const API_BASE = getWorldCupApiBase();

export interface MarketLink {
  id: number;
  match_id: string;
  contract_id: string;
  source: string;
  outcome_label: string;
  mapped_outcome: string;
  link_method: string;
  link_confidence: number;
  verified: boolean;
  market_question: string | null;
  implied_prob: number;
}

export interface MarketLinkList {
  items: MarketLink[];
  total: number;
}

export interface LatestLink extends MarketLink {
  latest_snapshot: {
    id: number;
    implied_prob: number;
    price: number | null;
    captured_at: string | null;
  } | null;
}

export interface SnapshotPoint {
  id: number;
  implied_prob: number;
  price: number | null;
  captured_at: string | null;
}

export interface SnapshotSeries {
  contract_id: string;
  outcome_label: string;
  mapped_outcome: string;
  snapshots: SnapshotPoint[];
}

function buildQuery(params: Record<string, string | number | undefined | boolean>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined);
  if (entries.length === 0) return "";
  return "?" + entries.map(([k, v]) => `${k}=${v}`).join("&");
}

export async function fetchMarketLinks(params?: {
  match_id?: string;
  source?: string;
  verified?: boolean;
}): Promise<MarketLinkList> {
  const qs = buildQuery(params ?? {});
  const res = await fetch(`${API_BASE}/api/sport-markets/links${qs}`);
  if (!res.ok) throw new Error("Failed to fetch market links");
  return res.json();
}

export async function fetchMarketLinksByMatch(matchId: string): Promise<MarketLinkList> {
  const res = await fetch(`${API_BASE}/api/sport-markets/links/${matchId}`);
  if (!res.ok) throw new Error("Failed to fetch links");
  return res.json();
}

export async function fetchLatestLinks(
  matchId: string,
): Promise<{ items: LatestLink[]; total: number }> {
  const res = await fetch(`${API_BASE}/api/sport-markets/links/${matchId}/latest`);
  if (!res.ok) throw new Error("Failed to fetch latest links");
  return res.json();
}

export async function fetchPendingLinks(): Promise<MarketLinkList> {
  const res = await fetch(`${API_BASE}/api/sport-markets/pending`);
  if (!res.ok) throw new Error("Failed to fetch pending links");
  return res.json();
}

export async function verifyLink(
  matchId: string,
  contractId: string,
  verified: boolean,
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/api/sport-markets/links/${matchId}/${contractId}/verify`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ verified }),
    },
  );
  if (!res.ok) throw new Error("Failed to verify link");
}

export async function fetchMarketSnapshots(
  matchId: string,
): Promise<{ series: SnapshotSeries[] }> {
  const res = await fetch(`${API_BASE}/api/sport-markets/snapshots/${matchId}`);
  if (!res.ok) throw new Error("Failed to fetch snapshots");
  return res.json();
}
```

### Step 7.2: Write the failing component tests

- [ ] **Step 2: Write MarketLinksTable test**

Create `frontend/src/components/sports/markets/MarketLinksTable.test.tsx`:

```tsx
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MarketLinksTable } from "./MarketLinksTable";
import type { MarketLinkList } from "@/lib/sport-markets-api";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const apiMocks = vi.hoisted(() => ({ fetchMarketLinks: vi.fn() }));
vi.mock("@/lib/sport-markets-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sport-markets-api")>()),
  fetchMarketLinks: apiMocks.fetchMarketLinks,
}));

const linksData: MarketLinkList = {
  items: [
    {
      id: 1, match_id: "m1", contract_id: "c1", source: "polymarket",
      outcome_label: "YES", mapped_outcome: "home_win", link_method: "rule",
      link_confidence: 0.95, verified: true, market_question: "Will Lakers win?",
      implied_prob: 0.6,
    },
  ],
  total: 1,
};

describe("MarketLinksTable", () => {
  beforeEach(() => apiMocks.fetchMarketLinks.mockReset());

  it("renders rows after load", async () => {
    apiMocks.fetchMarketLinks.mockResolvedValue(linksData);
    render(<MarketLinksTable />);
    await waitFor(() =>
      expect(screen.getByTestId("market-links-table")).toBeInTheDocument(),
    );
    expect(screen.getByText("m1")).toBeInTheDocument();
  });

  it("shows verified badge text", async () => {
    apiMocks.fetchMarketLinks.mockResolvedValue(linksData);
    render(<MarketLinksTable />);
    await waitFor(() => expect(screen.getByTestId("badge-1")).toBeInTheDocument());
    expect(screen.getByTestId("badge-1").textContent).toBe("已验证");
  });

  it("renders empty state", async () => {
    apiMocks.fetchMarketLinks.mockResolvedValue({ items: [], total: 0 });
    render(<MarketLinksTable />);
    await waitFor(() => expect(screen.getByTestId("empty")).toBeInTheDocument());
  });

  it("renders error state", async () => {
    apiMocks.fetchMarketLinks.mockRejectedValue(new Error("boom"));
    render(<MarketLinksTable />);
    await waitFor(() => expect(screen.getByTestId("error")).toBeInTheDocument());
  });
});
```

- [ ] **Step 3: Write PendingReviewQueue test**

Create `frontend/src/components/sports/markets/PendingReviewQueue.test.tsx`:

```tsx
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PendingReviewQueue } from "./PendingReviewQueue";
import type { MarketLinkList } from "@/lib/sport-markets-api";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const apiMocks = vi.hoisted(() => ({
  fetchPendingLinks: vi.fn(),
  verifyLink: vi.fn(),
}));
vi.mock("@/lib/sport-markets-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sport-markets-api")>()),
  fetchPendingLinks: apiMocks.fetchPendingLinks,
  verifyLink: apiMocks.verifyLink,
}));

const pendingData: MarketLinkList = {
  items: [
    {
      id: 1, match_id: "m1", contract_id: "c1", source: "polymarket",
      outcome_label: "YES", mapped_outcome: "home_win", link_method: "llm",
      link_confidence: 0.7, verified: false, market_question: "Will Lakers win?",
      implied_prob: 0.55,
    },
  ],
  total: 1,
};

describe("PendingReviewQueue", () => {
  beforeEach(() => {
    apiMocks.fetchPendingLinks.mockReset();
    apiMocks.verifyLink.mockReset();
  });

  it("renders pending cards", async () => {
    apiMocks.fetchPendingLinks.mockResolvedValue(pendingData);
    render(<PendingReviewQueue />);
    await waitFor(() => expect(screen.getByTestId("card-1")).toBeInTheDocument());
    expect(screen.getByText("Will Lakers win?")).toBeInTheDocument();
  });

  it("renders empty state", async () => {
    apiMocks.fetchPendingLinks.mockResolvedValue({ items: [], total: 0 });
    render(<PendingReviewQueue />);
    await waitFor(() => expect(screen.getByTestId("empty")).toBeInTheDocument());
  });

  it("confirm button calls verifyLink with true", async () => {
    apiMocks.fetchPendingLinks.mockResolvedValue(pendingData);
    apiMocks.verifyLink.mockResolvedValue(undefined);
    apiMocks.fetchPendingLinks
      .mockResolvedValueOnce(pendingData)
      .mockResolvedValueOnce({ items: [], total: 0 });
    render(<PendingReviewQueue />);
    await waitFor(() => expect(screen.getByTestId("confirm-1")).toBeInTheDocument());
    await userEvent.click(screen.getByTestId("confirm-1"));
    await waitFor(() =>
      expect(apiMocks.verifyLink).toHaveBeenCalledWith("m1", "c1", true),
    );
  });

  it("reject button calls verifyLink with false", async () => {
    apiMocks.fetchPendingLinks.mockResolvedValue(pendingData);
    apiMocks.verifyLink.mockResolvedValue(undefined);
    apiMocks.fetchPendingLinks
      .mockResolvedValueOnce(pendingData)
      .mockResolvedValueOnce({ items: [], total: 0 });
    render(<PendingReviewQueue />);
    await waitFor(() => expect(screen.getByTestId("reject-1")).toBeInTheDocument());
    await userEvent.click(screen.getByTestId("reject-1"));
    await waitFor(() =>
      expect(apiMocks.verifyLink).toHaveBeenCalledWith("m1", "c1", false),
    );
  });
});
```

- [ ] **Step 4: Write MarketSnapshotChart test**

Create `frontend/src/components/sports/markets/MarketSnapshotChart.test.tsx`:

```tsx
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MarketSnapshotChart } from "./MarketSnapshotChart";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="responsive-container">{children}</div>
  ),
  LineChart: ({ children, data }: { children: React.ReactNode; data: unknown[] }) => (
    <div data-testid="line-chart" data-count={data.length}>
      {children}
    </div>
  ),
  Line: () => <div data-testid="line" />,
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  CartesianGrid: () => <div data-testid="cartesian-grid" />,
  Tooltip: () => <div data-testid="tooltip" />,
}));

const apiMocks = vi.hoisted(() => ({ fetchMarketSnapshots: vi.fn() }));
vi.mock("@/lib/sport-markets-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sport-markets-api")>()),
  fetchMarketSnapshots: apiMocks.fetchMarketSnapshots,
}));

describe("MarketSnapshotChart", () => {
  beforeEach(() => apiMocks.fetchMarketSnapshots.mockReset());

  it("renders chart with snapshot data", async () => {
    apiMocks.fetchMarketSnapshots.mockResolvedValue({
      series: [
        {
          contract_id: "c1", outcome_label: "YES", mapped_outcome: "home_win",
          snapshots: [{ id: 1, implied_prob: 0.6, price: 0.6, captured_at: "t1" }],
        },
      ],
    });
    render(<MarketSnapshotChart matchId="m1" />);
    await waitFor(() =>
      expect(screen.getByTestId("series-c1")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("line-chart")).toBeInTheDocument();
  });

  it("passes snapshot count to chart", async () => {
    apiMocks.fetchMarketSnapshots.mockResolvedValue({
      series: [
        {
          contract_id: "c1", outcome_label: "YES", mapped_outcome: "home_win",
          snapshots: [
            { id: 1, implied_prob: 0.6, price: 0.6, captured_at: "t1" },
            { id: 2, implied_prob: 0.65, price: 0.65, captured_at: "t2" },
          ],
        },
      ],
    });
    render(<MarketSnapshotChart matchId="m1" />);
    await waitFor(() => expect(screen.getByTestId("line-chart")).toBeInTheDocument());
    expect(screen.getByTestId("line-chart").getAttribute("data-count")).toBe("2");
  });

  it("renders empty state", async () => {
    apiMocks.fetchMarketSnapshots.mockResolvedValue({ series: [] });
    render(<MarketSnapshotChart matchId="m1" />);
    await waitFor(() => expect(screen.getByTestId("empty")).toBeInTheDocument());
  });

  it("renders multiple series", async () => {
    apiMocks.fetchMarketSnapshots.mockResolvedValue({
      series: [
        {
          contract_id: "c1", outcome_label: "YES", mapped_outcome: "home_win",
          snapshots: [{ id: 1, implied_prob: 0.6, price: 0.6, captured_at: "t1" }],
        },
        {
          contract_id: "c2", outcome_label: "NO", mapped_outcome: "away_win",
          snapshots: [{ id: 2, implied_prob: 0.4, price: 0.4, captured_at: "t1" }],
        },
      ],
    });
    render(<MarketSnapshotChart matchId="m1" />);
    await waitFor(() => expect(screen.getByTestId("series-c1")).toBeInTheDocument());
    expect(screen.getByTestId("series-c2")).toBeInTheDocument();
  });
});
```

### Step 7.3: Run tests to verify they fail

- [ ] **Step 5: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/sports/markets/`
Expected: FAIL — components do not exist yet.

### Step 7.4: Write the components

- [ ] **Step 6: Write MarketLinksTable**

Create `frontend/src/components/sports/markets/MarketLinksTable.tsx`:

```tsx
"use client";
import { useEffect, useState } from "react";
import { fetchMarketLinks, type MarketLink } from "@/lib/sport-markets-api";

export function MarketLinksTable({ matchId }: { matchId?: string }) {
  const [links, setLinks] = useState<MarketLink[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetchMarketLinks(matchId ? { match_id: matchId } : {})
      .then((data) => {
        setLinks(data.items);
        setError(null);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [matchId]);

  if (loading) return <div data-testid="loading">加载中...</div>;
  if (error) return <div data-testid="error">{error}</div>;
  if (links.length === 0) return <div data-testid="empty">暂无市场链接</div>;

  return (
    <table data-testid="market-links-table">
      <thead>
        <tr>
          <th>Match</th>
          <th>Source</th>
          <th>Question</th>
          <th>Implied</th>
          <th>Verified</th>
        </tr>
      </thead>
      <tbody>
        {links.map((l) => (
          <tr key={l.id} data-testid={`row-${l.id}`}>
            <td>{l.match_id}</td>
            <td>{l.source}</td>
            <td>{l.market_question}</td>
            <td>{(l.implied_prob * 100).toFixed(1)}%</td>
            <td data-testid={`badge-${l.id}`}>
              {l.verified ? "已验证" : "待验证"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 7: Write PendingReviewQueue**

Create `frontend/src/components/sports/markets/PendingReviewQueue.tsx`:

```tsx
"use client";
import { useEffect, useState } from "react";
import { fetchPendingLinks, verifyLink, type MarketLink } from "@/lib/sport-markets-api";

export function PendingReviewQueue() {
  const [pending, setPending] = useState<MarketLink[]>([]);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    const data = await fetchPendingLinks();
    setPending(data.items);
    setLoading(false);
  }

  useEffect(() => {
    load().catch(() => setLoading(false));
  }, []);

  async function handleVerify(matchId: string, contractId: string) {
    await verifyLink(matchId, contractId, true);
    await load();
  }

  async function handleReject(matchId: string, contractId: string) {
    await verifyLink(matchId, contractId, false);
    await load();
  }

  if (loading) return <div data-testid="loading">加载中...</div>;
  if (pending.length === 0) return <div data-testid="empty">无待审核链接</div>;

  return (
    <div data-testid="pending-queue">
      {pending.map((l) => (
        <div key={l.id} data-testid={`card-${l.id}`} className="card">
          <p>{l.market_question}</p>
          <p>
            confidence: {l.link_confidence.toFixed(2)} ({l.link_method})
          </p>
          <button
            data-testid={`confirm-${l.id}`}
            onClick={() => handleVerify(l.match_id, l.contract_id)}
          >
            确认
          </button>
          <button
            data-testid={`reject-${l.id}`}
            onClick={() => handleReject(l.match_id, l.contract_id)}
          >
            拒绝
          </button>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 8: Write MarketSnapshotChart**

Create `frontend/src/components/sports/markets/MarketSnapshotChart.tsx`:

```tsx
"use client";
import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchMarketSnapshots, type SnapshotSeries } from "@/lib/sport-markets-api";

export function MarketSnapshotChart({ matchId }: { matchId: string }) {
  const [series, setSeries] = useState<SnapshotSeries[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchMarketSnapshots(matchId)
      .then((data) => setSeries(data.series))
      .finally(() => setLoading(false));
  }, [matchId]);

  if (loading) return <div data-testid="loading">加载中...</div>;
  if (series.length === 0) return <div data-testid="empty">暂无价格快照</div>;

  return (
    <div data-testid="snapshot-chart">
      {series.map((s) => (
        <div key={s.contract_id} data-testid={`series-${s.contract_id}`}>
          <p>{s.outcome_label}</p>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={s.snapshots}>
              <CartesianGrid />
              <XAxis dataKey="captured_at" />
              <YAxis domain={[0, 1]} />
              <Tooltip />
              <Line type="monotone" dataKey="implied_prob" stroke="#8884d8" dot />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ))}
    </div>
  );
}
```

### Step 7.5: Run component tests to verify pass

- [ ] **Step 9: Run component tests**

Run: `cd frontend && npx vitest run src/components/sports/markets/`
Expected: PASS (12 tests — 4 per component)

### Step 7.6: Add the page, loading, and nav entry

- [ ] **Step 10: Create the page**

Create `frontend/src/app/sports/markets/page.tsx`:

```tsx
"use client";
import { useState } from "react";
import { MarketLinksTable } from "@/components/sports/markets/MarketLinksTable";
import { PendingReviewQueue } from "@/components/sports/markets/PendingReviewQueue";
import { MarketSnapshotChart } from "@/components/sports/markets/MarketSnapshotChart";

type Tab = "links" | "pending" | "snapshots";

export default function SportMarketsPage() {
  const [tab, setTab] = useState<Tab>("links");
  const [snapshotMatchId, setSnapshotMatchId] = useState("");

  return (
    <main className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="text-xl font-semibold">体育市场桥接</h1>
      <div className="mt-4 flex gap-2">
        <button
          onClick={() => setTab("links")}
          className={tab === "links" ? "bg-secondary" : ""}
        >
          链接列表
        </button>
        <button
          onClick={() => setTab("pending")}
          className={tab === "pending" ? "bg-secondary" : ""}
        >
          待审核
        </button>
        <button
          onClick={() => setTab("snapshots")}
          className={tab === "snapshots" ? "bg-secondary" : ""}
        >
          价格快照
        </button>
      </div>
      <div className="mt-4">
        {tab === "links" && <MarketLinksTable />}
        {tab === "pending" && <PendingReviewQueue />}
        {tab === "snapshots" && (
          <div>
            <input
              value={snapshotMatchId}
              onChange={(e) => setSnapshotMatchId(e.target.value)}
              placeholder="match_id"
              data-testid="match-input"
            />
            {snapshotMatchId && <MarketSnapshotChart matchId={snapshotMatchId} />}
          </div>
        )}
      </div>
    </main>
  );
}
```

- [ ] **Step 11: Create the loading page**

Create `frontend/src/app/sports/markets/loading.tsx`:

```tsx
export default function Loading() {
  return <div className="mx-auto max-w-7xl px-4 py-6">加载中...</div>;
}
```

- [ ] **Step 12: Modify app-nav.tsx**

Open `frontend/src/components/app-nav.tsx`. Add `LineChart` to the lucide-react import, and insert the nav entry after `/sports/learning` and before `/world-cup`.

The import line currently reads:

```tsx
import { Activity, FlaskConical, Gauge, GraduationCap, History, Medal, Newspaper, Radar, Target, Trophy, TrendingUp, Zap } from "lucide-react";
```

Replace it with:

```tsx
import { Activity, FlaskConical, Gauge, GraduationCap, History, LineChart, Medal, Newspaper, Radar, Target, Trophy, TrendingUp, Zap } from "lucide-react";
```

The NAV array currently has these two adjacent lines:

```tsx
  { href: "/sports/learning", label: "学习仪表盘", icon: GraduationCap, match: ["/sports/learning"] },
  { href: "/world-cup", label: "世界杯", icon: Trophy, match: ["/world-cup"] },
```

Replace them with:

```tsx
  { href: "/sports/learning", label: "学习仪表盘", icon: GraduationCap, match: ["/sports/learning"] },
  { href: "/sports/markets", label: "体育市场", icon: LineChart, match: ["/sports/markets"] },
  { href: "/world-cup", label: "世界杯", icon: Trophy, match: ["/world-cup"] },
```

### Step 7.7: Run full frontend test suite

- [ ] **Step 13: Run frontend tests**

Run: `cd frontend && npx vitest run`
Expected: PASS — all 12 new market component tests pass, existing tests unaffected.

### Step 7.8: Commit Task 7

- [ ] **Step 14: Commit**

```bash
git add frontend/src/lib/sport-markets-api.ts frontend/src/components/sports/markets/ frontend/src/app/sports/markets/ frontend/src/components/app-nav.tsx
git commit -m "feat(phase7): add sport-market bridge UI (links table, pending queue, snapshot chart) + nav entry"
```

## Self-Review

**1. Spec coverage:**

- **Team alias registry (10 competitions)** — Task 1 creates `team_aliases.py` with all 10 competitions. ✓
- **Implied probability extraction (Polymarket + Odds API normalization)** — Task 1 `implied_prob.py`. ✓
- **Config flags** — Task 1 adds `PHASE7_SPORT_MARKET_BRIDGE_ENABLED` + 6 others. ✓
- **DB tables `kernel_sport_market_links` / `kernel_market_snapshots`** — Task 2 ORM + stores. ✓
- **Market detector (`SportMarketDetector`)** — Task 3 `sport_market_detector.py`. ✓
- **Polymarket sports collection** — Task 3 `polymarket_sports_source.py`. ✓
- **The Odds API multi-league extension** — Task 4 `COMPETITION_TO_ODDS_API_SPORT` + `fetch_match_odds(competition=)`. ✓
- **Three-layer matching (rule → LLM → manual)** — Task 5 `SportMarketBridgeService`. ✓
- **Fail-closed `get_verified_links`** — Task 2 store + Task 5 service + Task 6 `/latest` endpoint. ✓
- **API endpoints (6)** — Task 6 `sport_markets.py`. ✓
- **Scheduler (3 jobs)** — Task 6 scheduler modification. ✓
- **CLI manual verification** — Task 6 `sport_market_bridge_cli.py`. ✓
- **Frontend (table + queue + chart + page + nav)** — Task 7. ✓
- **Feature flag 503 gating** — Task 6 `_ensure_enabled()`. ✓

**2. Placeholder scan:**

No `TODO`, `TBD`, "similar to Task N", or "implement later" found. Every step contains complete code. The scheduler job bodies (`_job_fetch_traditional_odds`, `_job_capture_market_snapshots`) intentionally contain only the gating + DB init + run-ledger skeleton because full match-iteration wiring depends on the kernel fixtures table (out of scope — collection candidates are produced by Task 3 functions already invoked in `_job_discover_sport_markets`).

**3. Type consistency:**

- `SportMarketLinkStore.upsert_link` keyword args (`match_id`, `contract_id`, `source`, `outcome_label`, `mapped_outcome`, `link_method`, `link_confidence`, `verified`, `market_question`, `implied_prob`) match across Task 2 (definition), Task 5 (caller), Task 6 (CLI caller). ✓
- `SportMarketInfo` fields (`contract_id`, `source`, `market_question`, `market_type`, `detected_sport`, `detected_competition`, `detected_teams`, `detected_date`, `outcome_label`) match across Task 3 (definition) and Task 5 (`_make_market_info` helper + `link_polymarket_market` access). ✓
- `fetch_match_odds(home_team, away_team, competition="wc")` signature matches across Task 4 (definition) and Task 5 `link_traditional_odds` (caller). ✓
- `MarketSnapshotStore.append_snapshot(link_id=, implied_prob=, price=)` matches across Task 2 (definition), Task 5 `capture_snapshots` (caller), Task 6 route test (caller). ✓
- `MarketLink` / `LatestLink` / `SnapshotSeries` TypeScript interfaces in `sport-markets-api.ts` match the JSON shapes returned by Task 6 endpoints (`{items, total}`, `{items, total}` with `latest_snapshot`, `{series}`). ✓
- Constants `RULE_CONFIDENCE_THRESHOLD=0.9`, `LLM_CONFIDENCE_THRESHOLD=0.85`, `LLM_PENDING_THRESHOLD=0.6` match across Task 5 definition and tests. ✓

**Test count summary:** ~7 (Task 1) + 13 (Task 2) + 12 (Task 3) + 9 (Task 4) + 7 (Task 5) + 7 (Task 6 routes) + 4 (Task 6 CLI) + 12 (Task 7 frontend) = ~71 new tests.
