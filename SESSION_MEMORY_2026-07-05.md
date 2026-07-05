# Session Memory - 2026-07-05

This document is for quickly restoring context after Codex context compaction, session handoff, or switching agents.

Repository: `E:\Github\Prediction Market Reality Filter`

## Current Focus

The user had modified many frontend layouts and asked Codex to inspect for newly added redundant/dead code, then asked to fix, commit, and write this memory document.

## Work Completed In This Session

Frontend cleanup was kept intentionally surgical:

- `frontend/src/app/decisions/page.tsx`
  - Removed unused `EdgeMetric` component.
- `frontend/src/app/edges/page.tsx`
  - Removed duplicated inline `EdgeTimelinePoint` / `EdgeTimelineChart` implementation.
  - Switched to the reusable `EdgeTimelineChart` from `@/components/edges/edge-timeline-chart`.
  - Removed now-unused `recharts`, `ChartFrame`, and `DarkTooltip` imports.
  - Removed unused `EdgeTrajectory` and `fmtSignedPct` imports.
- `frontend/src/components/edges/edge-timeline-chart.tsx`
  - Reusable chart supports optional `height?: number`, so the compact edge timeline can reuse it.
- `frontend/src/app/page.tsx`
  - Removed unused `ChevronDown` import.
  - Removed stale inline comment that still described `discoveryStatus` as `Record<string, unknown>`.

`eventsApi.translateAll` was left in place because it may be an intentionally retained API client surface for future/manual batch translation UI; it was not layout-generated dead code.

## Verification Evidence

Commands run from `frontend/` unless otherwise noted:

- `npm.cmd run typecheck`
  - Passed: `tsc --noEmit` exit 0.
- `npm.cmd test -- src/components/app-nav.test.tsx src/app/navigation-shell.test.ts src/app/page.test.tsx src/lib/dashboard-cache.test.ts src/lib/api.test.ts`
  - Passed: 5 test files, 20 tests.
- From repo root: `git diff --check -- frontend`
  - Passed with only LF/CRLF working-copy warnings.
- `npm.cmd exec eslint -- src/app/decisions/page.tsx src/app/edges/page.tsx src/app/page.tsx src/components/edges/edge-timeline-chart.tsx`
  - Passed for the touched cleanup files.
- `npm.cmd run lint`
  - Still fails due unrelated existing lint debt outside this cleanup scope:
    - `frontend/src/app/trades/page.tsx`
    - `frontend/src/app/world-cup/page.tsx`
    - `frontend/src/components/world-cup/analytics-dashboard.tsx`
    - `frontend/src/components/world-cup/engine-auto-tune-dashboard.tsx`
  - Remaining warnings include unused items in world-cup/dashboard generated files.
  - The targeted dead-code warnings for `EdgeMetric`, `EdgeTrajectory`, `fmtSignedPct`, and `ChevronDown` no longer appear.

## Commit Scope Notes

The working tree had many pre-existing modified files before the commit request, including backend files, docs, start script, many frontend pages/components, and new tests.

To avoid accidentally committing unrelated user work, the intended commit scope for this session is limited to:

- `frontend/src/app/decisions/page.tsx`
- `frontend/src/app/edges/page.tsx`
- `frontend/src/app/page.tsx`
- `frontend/src/components/edges/edge-timeline-chart.tsx`
- `SESSION_MEMORY_2026-07-05.md`

Other uncommitted changes should be left untouched unless the user explicitly asks to include them.

## Recommended Next Steps

If the user wants a fully clean frontend lint run, address the remaining unrelated `react-hooks/set-state-in-effect` errors and unused warnings listed above in a separate focused pass.

---

# Session Memory Update - Settlement Fix + Win Rate Diagnosis

Time: 2026-07-05 early morning, Asia/Shanghai.

This section captures the later event-settlement and prediction-engine work so a
fresh agent can continue tomorrow without re-deriving context.

## User Context

The user reported several event settlement and simulated-trading inconsistencies:

- expired/unsettled market events were being marked resolved in our system;
- settled trades were missing entry time in the closed-trades UI;
- current/open and closed simulated trades displayed system-inferred probability
  where actual market/event probability was expected;
- some events that were not settled on market platforms were already settled in
  our system;
- after repair, some truly settled events became unsettled again;
- after settlement behavior became normal, win rate looked very poor:
  roughly 20 events, only 4 correct.

The user now asked to stop before implementation and write memory. Tomorrow's
next task is to improve the prediction engine and simulated-trade gating.

## Important Process / Safety Notes

- Repository: `E:\Github\Prediction Market Reality Filter`
- `.codegraph/` exists. Use CodeGraph before grep/read when locating code.
- Many unrelated uncommitted changes exist. Do not `git add -A`.
- Do not blindly kill Python/Node processes. The user explicitly asked whether a
  stopped process could be Codex/tooling itself; avoid stopping unknown processes.
- If live market APIs are needed, expect escalation because network is restricted.
- Settlement fixes below should not be reverted.

## Settlement Root Cause Fixed

Root cause:

- `POST /events/resolve-expired` was treating expired/deadline-past events as
  resolved:
  - wrote `outcome.status = "resolved"`;
  - wrote `actual_outcome = 50`;
  - wrote `source = "auto_expired"`;
  - closed predictions/trades incorrectly.

Fix:

- `backend/app/api/routes/events.py`
  - `resolve_expired_events()` now archives only.
  - It no longer writes `outcome` or `calibration`.
  - Returns `resolved: 0`, `archived: count`.
- `frontend/src/app/page.tsx`
  - Button text changed from `结算过期` to `归档过期`.
  - API result uses `archived`.
- `frontend/src/lib/api.ts`
  - `resolveExpired()` response type includes `archived`.
- Tests added/updated:
  - `backend/tests/test_events_routes.py::ResolveExpiredRouteTests`
  - `frontend/src/app/page.test.tsx`

## Data Repair Already Performed

Initial bad-auto-expired repair:

- Removed `outcome` / `calibration` for 16 `auto_expired` events.
- Reopened affected predictions/trades.
- Backups:
  - `backend/event_store.json.bak-auto-expired-20260705-034917`
  - `backend/v2_loop.db.bak-auto-expired-20260705-034917`

Then user noticed some truly settled events became unsettled. The real gap was
that Polymarket direct-by-id settlement was missing.

## Polymarket Direct Settlement Fixed

Root cause:

- Auto-resolve fetched bulk Polymarket resolved markets sorted/capped.
- Linked low-volume/older markets could be missed.
- Manifold had a direct-by-id fallback; Polymarket did not.

Fix:

- `backend/app/services/polymarket_history_service.py`
  - Added `fetch_markets_by_ids(ids)`.
  - Uses `https://gamma-api.polymarket.com/markets/{id}`.
  - Only returns a market when `closed is True`.
  - Parses `outcomePrices`.
  - First/YES-side price >= `0.5` => `actual_outcome=100`, else `0`.
  - Added helper `_to_resolved_market()`.
- `backend/app/services/event_resolve_service.py`
  - Before building contract index, for verified existing links:
    - direct-fetch linked Polymarket/Manifold ids missing from the bulk resolved
      pool;
    - merge actually resolved direct results into `resolved_markets`.
  - Contract-first path then resolves them.
- Tests added/updated:
  - `backend/tests/test_event_resolve_service.py`
  - `backend/tests/test_polymarket_history_service.py`

Live runs already done:

- Live dry-run found 18 truly resolved events.
- Live auto-resolve resolved 18 events.

Additional backups:

- `backend/event_store.json.bak-before-real-auto-resolve-20260705-042459`
- `backend/v2_loop.db.bak-before-real-auto-resolve-20260705-042459`
- `backend/event_store.json.bak-before-polymarket-direct-resolve-20260705-043821`
- `backend/v2_loop.db.bak-before-polymarket-direct-resolve-20260705-043821`

Current settlement state after repair:

- `total_outcome_rows = 22`
- `outcome_sources = {'auto_market': 22}`
- `auto_expired_remaining = []`

Original 16 bad `auto_expired` events now split as:

Truly resolved by market:

- `f85c718470cc542d`
- `165c976e647b2953`
- `8b34f29db6b0b7da`
- `0395da3729bbbddc`
- `de847ca89d7b9632`
- `53d7bb7778da2fad`
- `5b041e5533dab0b0`
- `a2637b0c19da4a27`
- `e8a6dec8854cea56`
- `af780a7274ae1ca6`

Still unresolved/open/archived because the market source is not actually
resolved:

- `74bbd06dd2363f20` Tampa Bay Rays vs Boston Red Sox
- `eb1b693e6fb1b879` Strait of Hormuz ships
- `d1b7614a0c6d8185` Kash Patel remains FBI Director
- `1473aa1a80924484` Department of Education abolished
- `b0e4fa5d56255cb5` Mitch McConnell alive all of July
- `625fca75ee5446af` nuclear reactor buildout goals

## Verification Already Run For Settlement Work

Passed:

```powershell
python -m unittest tests.test_event_resolve_service tests.test_polymarket_history_service tests.test_events_routes.ResolveExpiredRouteTests tests.test_simulated_trade_store -v
python -m compileall app/services/event_resolve_service.py app/services/polymarket_history_service.py app/api/routes/events.py
```

Earlier frontend/backend targeted checks also passed. Note: `npm run build`
previously reached compile/static generation but failed at final
`frontend/out` delete due an `EBUSY` lock. Do not claim full build pass unless
rerun cleanly.

## Win Rate Diagnosis - Current Evidence

User reported that settlement is now normal but win rate is terrible: about 20
events, only 4 correct.

Diagnostic result from current data:

- resolved rows: `22`
- by platform: `{'Manifold': 11, 'Polymarket': 11}`
- by prediction status: `{'observed': 22}`
- by decision: `{'provisional_act': 8, 'watch': 11, 'skip': 3}`
- edge-direction correct: `4/22 = 18.18%`
- simulated-trade win rate where closed: `3/19 = 15.79%`
- `direction_correct` in DB: `0 usable rows` because current resolved rows have
  `direction_correct = NULL` when `PREDICTION_CALIBRATION_ENABLED` was off or
  snapshot fields were absent.

Definition used for edge-direction correctness:

```python
edge_correct = sign(raw_edge) * (actual_outcome - market_probability) > 0
```

This asks whether the system's AI-vs-market divergence beat the market's
direction.

Important finding:

- All 22 resolved predictions are `observed`.
- There are no `act/scored` samples.
- The bad "win rate" is mostly measuring `watch` and `provisional_act`
  exploratory/cold-start rows, not calibrated action rows.

## Platform / Bucket Findings

Manifold:

- 11 resolved.
- Direction correct: `0/11`.
- Pattern: AI/model pushed low-probability events far upward and they resolved
  NO.
- Examples:
  - Fable Americans: AI `33.67` vs market `7.90`, actual `0`.
  - Fable Europeans: AI `32.73` vs market `5.30`, actual `0`.
  - WTI crude above $76: AI `30.70` vs market `3.84`, actual `0`.
  - US average gas prices move 10c in one day: AI `30.12` vs market `1.00`,
    actual `0`.

Polymarket:

- 11 resolved.
- Direction correct: `4/11`.
- Better than Manifold but still poor.
- Several wrong cases were model underestimating YES/favorites:
  - Bitcoin above $60k July 3: AI `45.70` vs market `59.50`, actual `100`.
  - Spain win: AI `53.52` vs market `74.50`, actual `100`.
  - Morocco win: AI `34.57` vs market `54.50`, actual `100`.
- Correct cases included:
  - Germany vs Paraguay O/U 2.5: AI lower than market, actual `0`.
  - Spain spread -2.5: AI higher than market, actual `100`.
  - England/DR Congo total corners: AI lower than market, actual `0`.
  - Austria corners O/U: AI slightly lower than market, actual `0`.

Low market-probability bucket:

- Market 0-10% bucket: `0/8` direction correct.
- This is the most obvious failure mode.

## Root-Cause Hypotheses To Test Tomorrow

Do not implement blindly. Start with failing tests.

Likely root causes:

1. Paper-trade gate is too permissive.
   - Current code opens trades for:
     - `act`
     - `provisional_act`
     - `watch` when `PAPER_TRADE_WATCH_ENABLED=true`
   - Current defaults:
     - `PAPER_TRADE_ENABLED=true`
     - `PAPER_TRADE_WATCH_ENABLED=true`
     - `COLD_START_BYPASS_ENABLED=true`
   - Source:
     - `backend/app/services/event_intelligence_service.py::_persist_events`

2. Cold-start bypass is dangerous in current data.
   - `provisional_act`: 8 samples, only 1 correct.
   - `watch`: 11 samples, only 2 correct.
   - No segment was qualified (`qualified=0` for all 22).

3. Unknown category anchoring is distorting probabilities.
   - `backend/app/services/base_rate_service.py`
   - `_DEFAULT = BaseRate("unknown", 20, 80, 50, "无法分类，使用最大熵先验")`
   - `anchor_probability()` pulls toward the category prior.
   - 20/22 current resolved rows are `unknown`.
   - For market-derived events, this pushes low market-probability Manifold
     events toward 50%, creating fake YES edges.

4. `segment_skill` currently measures AI vs random, not AI vs market.
   - Source:
     - `backend/app/memory/prediction_store.py::segment_skill`
   - It aggregates AI Brier only.
   - Current aggregate:
     - AI avg Brier about `0.2020`
     - market avg Brier about `0.1282`
   - AI is worse than market, but because it is better than random, the current
     skill formula can still look positive. This can incorrectly unlock future
     action.

## Recommended Tomorrow Plan

Use these skills/processes:

- `systematic-debugging`
- `test-driven-development`
- `verification-before-completion`
- CodeGraph before code search/reads

Suggested implementation direction:

1. Add failing tests first.
   - Simulated trades should not open for `watch`.
   - Strongly consider not opening for `provisional_act` by default.
   - Only true `act` should create paper trades unless an explicit exploration
     mode is enabled.

2. Make cold-start safer.
   - Disable `COLD_START_BYPASS_ENABLED` by default, or keep the decision label
     but block trade creation for `provisional_act`.
   - Keep observation data, but do not count it as a tradable win-rate signal.

3. Fix segment trust to be market-relative.
   - Segment skill should require AI to beat the market baseline, not just random.
   - A simple metric: compare mean AI Brier vs mean market Brier for the segment.
   - If AI does not beat market, trust should be 0/floored and must not unlock
     `act`.

4. Fix unknown/longshot behavior.
   - For prediction-market-sourced events with category `unknown`, do not anchor
     toward static 50%.
   - Prefer market baseline as the anchor until category is known or learned
     calibration exists.
   - Add a specific guard for low market probability (<10%) where model wants to
     lift probability by a large amount without strong direct evidence.

5. Backfill diagnostics only after code behavior is locked by tests.
   - Existing 22 rows have `direction_correct = NULL`.
   - If UI needs it, add a safe backfill script/function with backup first.

## Files Most Likely To Edit Tomorrow

- `backend/app/services/event_intelligence_service.py`
  - `_persist_events()` paper-trade creation gate.
- `backend/app/core/config.py`
  - defaults for `PAPER_TRADE_WATCH_ENABLED`, `COLD_START_BYPASS_ENABLED`, or new
    explicit exploratory-trade flag.
- `backend/app/services/diagnosis_service.py`
  - `decide()` / `diagnose()` cold-start decision behavior if needed.
- `backend/app/memory/prediction_store.py`
  - `segment_skill()` and possibly calibration summaries.
- `backend/app/services/base_rate_service.py`
  - `unknown` prior / market-source anchoring behavior.
- Tests likely:
  - `backend/tests/test_diagnosis_service.py`
  - `backend/tests/test_prediction_store.py`
  - `backend/tests/test_event_intelligence_service.py`
  - possibly `backend/tests/test_base_rate_service.py`

## Do Not Forget

- Current untracked backup files should not be staged unless the user explicitly
  asks:
  - `backend/event_store.json.bak-auto-expired-20260705-034917`
  - `backend/event_store.json.bak-before-real-auto-resolve-20260705-042459`
  - `backend/event_store.json.bak-before-polymarket-direct-resolve-20260705-043821`
- The working tree contains many unrelated modified frontend/backend files from
  prior tasks. Keep tomorrow's change surgical.
---

# Session Memory Update - Prediction Engine Safety Evidence Boost v1

Time: 2026-07-05, Asia/Shanghai.

User decision: keep `watch` simulated trades enabled for exploration data. This work optimized the prediction engine rather than disabling watch trades.

Implemented:

- Added deterministic evidence quality scoring with buckets `weak`, `mixed`, `solid`, and `strong`.
- Added a low-probability longshot guardrail so weak evidence cannot lift sub-10% market probabilities into large artificial YES edges.
- Added confidence caps for weak/mixed evidence, unknown categories, and low-probability weak-evidence situations.
- Changed unknown-category anchoring in `analyze_market()` to use the market probability as effective prior instead of static 50%.
- Added diagnostics to analysis output: evidence quality factor/bucket/reasons, guardrail status/reason, confidence cap reasons, and base-rate effective prior.

Verification:

- `python -m unittest tests.test_ai_analysis_service -v`
- `python -m unittest tests.test_event_resolve_service tests.test_polymarket_history_service tests.test_events_routes.ResolveExpiredRouteTests tests.test_simulated_trade_store -v`
- `python -m compileall app/services/probability_engine_service.py app/services/ai_analysis_service.py`

Remaining follow-up:

- Segment skill should be made market-relative in a separate focused pass if prediction-store fields are sufficient or after adding explicit market baseline fields.
- UI/reporting should eventually separate `act`, `provisional_act`, and `watch` simulated-trade performance.
