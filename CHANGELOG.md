# Changelog

### Fix: a market-less event was graded on whichever estimate the model wrote last

- **P2-W2 — "非市场类体育事件的 commitment / 校准路径（不出 prediction_store 时）".** First confirmed that *not* reaching `prediction_store` is deliberate, not the gap: `freeze_prediction` returns `None` for any `source.type != "prediction_market"` (`prediction_store.py:259`), and its docstring states the rule — no market, no edge, no committed prediction. Giving sports events a prediction row would compute `raw_edge` / `trust` / `adjusted_edge` against a **curated baseline standing in for a market price**, manufacturing exactly the kind of number this codebase keeps deleting. So the commitment machinery stays market-gated.
- **The real defect is which estimate gets graded.** Sports events run through `analyze_event` → `analyze_market` like everything else, so `record["probability"]["estimated"]` is an LLM number (`event_intelligence_service.py:827-839`). `record_event` appends a probability snapshot for **every** record with no source-type gate (`event_audit_service.py:48-64`, called at `:1434`), so each re-scan lands a fresh estimate. And `resolve_with_calibration` graded `trend["latest_probability"]`. For a market-derived event that is right — the trajectory tracks a live price. For a market-less event the trajectory is only the model changing its mind, and a re-scan late in a tournament runs **after the outcome has begun leaking into the news context**, so the score could be earned by an estimate that already half-knew the answer. Nothing had ever frozen the verdict it was being graded against.
- **"Latest" was not even stable.** The audit log is compacted to `EVENT_AUDIT_MAX_PER_EVENT` snapshots per event (default 200, `config.py:696`), keeping the most recent — so the first-sight estimate can be deleted outright, and nothing preserved it.
- New `_scored_estimate(record, trend)` grades a `sports_event` on its **first-sight** estimate, applying by hand the same commitment rule `freeze_prediction` applies to market events: one event, one verdict, fixed when first analyzed. `analyze_trend` already returns `first_probability`, so no snapshot re-sorting was needed and the existing helper's meaning is untouched. Market events keep `latest_probability` exactly as before.
- **Both fallbacks are deliberate, and neither falls back to "latest".** Once `observations >= EVENT_AUDIT_MAX_PER_EVENT` the oldest surviving snapshot is no longer provably the first, so grading it would grade a drifted number under the commitment's name; it falls back to the curated baseline, the one estimate that cannot have drifted. A market-less event with no trajectory at all does the same. Neither path defaults to a bare `50.0`.
- **`estimate_basis` is recorded next to the score**, because the number alone cannot say which estimate was graded — a baseline and a first-sight estimate can coincide. `score_event` takes it as a parameter (single production caller) and `Calibration` declares it with a default, so calibration rows written before this field still validate. Values: `first_sight`, `trajectory_latest`, `baseline_trajectory_compacted`, `baseline_no_trajectory`.
- 5 tests added, **each using two snapshots that disagree** — with a single snapshot the first and the latest coincide and the assertion passes either way, the same vacuous shape as the pre-existing threshold tests in the previous increment. One test pins that a sports event still writes **no** prediction row, so a future change cannot quietly start fabricating an edge from a baseline.
- Verified by four byte-level injections, each restored byte-identically: grading the latest estimate for market-less events (the original bug), applying the first-sight rule to *every* source type, trusting the oldest surviving snapshot after compaction, and falling back to a bare `50.0` instead of the curated baseline each fail exactly the intended test.
- No schema, engine, formula, weight or flag-default change; no learning, scheduler, market write or prediction write enabled.

### Fix: settlement counted the same red card twice, once per grain that reported it

- **P2-W3 — "出线/晋级/纪律类确定性结算规则覆盖率".** Rule coverage turned out not to be the gap. Checked against the curated corpus in `world_cup_event_source.py`, `_parse_threshold`'s literal `at least N <phrase>` pattern matches all three threshold-bearing titles that actually exist (`at least 8 red cards`, `at least 140 total goals`, `at least 7 goals`), and goal difference, group rank, penalty shootouts, extra time and the golden boot each have their own branch. No title falls through. The defect is in the counting.
- **The same red card is reported at up to three grains, and settlement added them all.** One data-source bundle produces both a `match_result` fact carrying that match's `home_red_cards + away_red_cards` total *and* one `discipline` fact per card event; an operator may additionally attach a tournament-wide total to `tournament_status`. Their `fact_id` values differ by construction, so the fact store's upsert-by-`fact_id` cannot merge them, and `_red_card_resolution` summed `red_cards` across every kind that carries the field. Measured end to end: **four real red cards read as eight**, so `at least 8 red cards` resolved YES with `actual_outcome=100` at `confidence=1.0` and fed `resolve_with_calibration`.
- `_total_goals_resolution` had the same shape at one grain. `_make_fact_id` seeds on `source` and `observed_at`, so one match imported from two feeds — or re-imported at a later timestamp — persists as two `match_result` facts, and both were summed. Measured through the real store: **five goals read as ten**.
- New `app/services/sports_fact_aggregation.py` is the single place both settlement and the operator signal get an occurrence count from. `red_card_total` / `total_goals` bucket by `match_id` and **pick a grain per match instead of adding grains**: the sum of that match's per-card rows, or the per-match aggregate, whichever is larger. Repeated observations of one match reduce with `max` rather than `+` — cards and goals are monotonic within a match, never taken back — which collapses duplicate imports and successive live snapshots without depending on `observed_at` being present or ordered. A tournament-wide total is treated as already including every match and taken against the per-match tally the same way. Facts with no `match_id` cannot be reconciled against anything, so they are counted once each, preserving the existing manual-import semantics.
- **`suspension` facts are deliberately not counted**, even though an operator could attach `red_cards` to one: a suspension is the consequence of a card that another fact already reports, so counting it is the same double count wearing a different kind.
- Both resolution rules now pass **only the facts they actually counted** as the decision's evidence, instead of every fact that passed the relevance filter. That also tightens `confidence`, which is the minimum over the cited facts.
- `sports_signal_service._discipline_signal` carried the identical sum and is fixed with the same helper. That number is rendered to the operator as `threshold_progress`, so it was reporting twice the real progress toward the threshold.
- **Two zero-value bugs of my own, both the same mistake — absence of a fact is not a zero-valued fact.** A genuine 0-0 match was treated as missing by the `home_goals or … or 0` falsy chain (now explicit `is not None` checks), and `tournament_total` starting at `0.0` won the final tie via `0.0 >= 0.0` when no tournament fact existed at all, returning an empty evidence list. Both guards now test *whether the fact is present*, not *whether its value is >= 0*.
- Verified by eight byte-level injections, each restored byte-identically: reverting each of the three sums to adding every grain, adding the grains instead of reconciling them, re-breaking the zero tie-break, letting repeated observations add instead of `max`, counting `suspension` as a per-card kind, and restoring the falsy-zero fallback chain each fail exactly the intended test.
- 27 tests added — 18 in a new `tests/test_sports_fact_aggregation.py`, 7 resolution regressions, 2 signal. Note the pre-existing threshold tests could not have caught this: each fed a **single** contributing fact, and the goals tests used facts with no `match_id` at all, so they were green either way. Full suite 4570 passed, 11 skipped (up from 4543). No schema, engine, formula, weight or flag-default change; no learning, scheduler, market write or prediction write enabled.
- **P2-W1** (结构化 facts 定时 bundle import) is marked done in the backlog in the same commit with no code change — `_job_world_cup_source_bundle_import` was already implemented, double flag-gated in both the job body and at registration, cron-configurable at 05:20 UTC by default, run-tracked, tested, and effectively default-off because `_world_cup_import_enabled_default()` only returns true when a `FOOTBALL_DATA_API_KEY` is set.

### Fix: the analyst prompt asked the model about facts it was never handed

- **P2-W4 — "AI 只解释结构化事实，禁止模型「猜」红黄牌计数".** The analysis prompt instructed the model to judge "Prediction reasonableness based on probabilities and **Elo/data**", while its only caller (`POST /world-cup/predictions/matches/{id}/analyze`) passed no `elo_ratings` at all — the parameter existed and stayed `None`, so `elo_info` was always empty. It then asked for "key factors most likely to affect the result" with nothing forbidding the model from naming card counts, injuries, lineups or head-to-head records it had never received. The reply is rendered verbatim to the operator by `prediction-analysis-card.tsx`, so an invented statistic reaches a human looking as authoritative as the score.
- New `app/services/llm_fact_grounding.py` builds both halves of the boundary in one place. `build_fact_grounding_section` renders only the facts the caller actually supplied, then lists every one of eleven inventable fact kinds still missing (红黄牌, 伤停名单, 首发阵容, 近期战绩, 历史对战, 控球/射门数, xG, Elo 评分, 赔率/盘口, 天气/场地, 球员个人数据) as "you were NOT given, and therefore do not know", followed by three hard rules: every count/rating/record must appear above; a missing fact must be written 「该项数据未提供」 rather than estimated or derived from the probabilities; and the model must not claim the prediction came from a source not named. A supplied fact leaves the missing list automatically, so a caller cannot render a value and still have the model told it lacks it.
- **An empty value counts as not supplied.** `{}`, `[]`, `""` and `None` all keep their fact kind on the missing list — `world_cup_prediction_pipeline` can store `elo_ratings` as `gbm_pred.get("elo_ratings", {})`, and an empty dict must not take Elo off the missing list while putting no number in its place.
- **The `/optimize` route was handing over five structured facts that the service dropped on the floor.** `optimize_prediction_with_ai` read `match_context` for `injuries`, `recent_form` and `head_to_head` only — three keys no caller has ever passed — while the route sends `stage`, `group`, `venue`, `data_quality` and `key_factors`. `context_info` was therefore empty at every call site, and the prompt still demanded exactly "2 blind spots". Blind spots are now capped rather than required ("return a shorter list, or an empty one, rather than naming a blind spot you cannot ground"), and the whole context mapping reaches the prompt. The three legacy keys still work if anything ever supplies them.
- **Two provenance claims of our own were removed.** The no-route and exception fallbacks told the user the prediction was computed "包括 Elo 评分、历史对战记录和赔率数据" — but that branch never reads `prediction.factors`, and which sources an engine used varies. They now say only 由数据模型计算得出, which is the same rule 3 the model is held to.
- The `/analyze` route now forwards `elo_ratings` and `data_quality` from the stored prediction, so the prompt's questions about them are answerable. A source-scan wiring test pins that, since a service-level test passes whether or not the route supplies them.
- Verified by eight byte-level injections, each restored byte-identically: dropping the `cards` kind, treating an empty dict as supplied, restoring the "Elo/data" question, removing the 「该项数据未提供」 escape hatch, reverting `match_context` to the three unused keys, dropping `data_quality=` from the route, restoring the enumerated-source fallback, and forcing exactly two blind spots each fail exactly the intended test.
- 19 tests added (4543 passed, up from 4524). No schema, engine, formula, weight or flag-default change; no learning, scheduler or market write enabled. Note that `AIAnalysisHistory` rows cached before this change are still reused until the underlying prediction moves — the cache key is the predicted score, confidence and method, not the prompt.

### Test: two green tests that could not fail, and two routes that should stay unmounted

- **`test_registry_altitude_and_conditional_api.py` ended in two `hasattr` assertions**, which pass against any stub — the previous entry noted this file was the only "test" the conditional-calibration API had. Its other assertion was worse than vacuous: `assert abs(soft - 0.44) < 1e-6 or soft > 0.3` over the football soft-factor seed. The exact arm has been **false** since the seed grew to 0.46, so the disjunction silently degraded to `soft > 0.3` and hid the drift it was written to catch. A tolerance joined by `or` to a loose bound is not a tolerance.
- Replaced with 31 assertions over values the code actually produces: the full seed list pinned entry by entry with an exact sum, unique ids and categories; `confidence_bucket` at both bin boundaries (0.45 and 0.70 belong to the *upper* bin) and its fallback to `mid` for null/garbage confidence, which is what `get_conditional_calibration_row` passes it; `stage_bucket` over the nine stage strings the adapters actually emit (`regular_season`, `regular`, `playoff`, `group_stage`, `final`, `semifinal`, `quarterfinal`, `round_of_16`, `unknown`), gathered from the adapters and `world_cup_match_service` rather than imagined; and both composite-key builders against the prefixes the frontend parser splits back apart.
- **Two hazards in `stage_bucket` are now pinned rather than left to luck.** `postseason` contains the *regular* token `season` and only resolves to `knockout` because the knockout scan runs first — swapping the two blocks would file every playoff sample under `regular` with nothing failing. And the regular-token list contains the two-character `rs`, which is harmless only because every adapter builds `<prefix>-<digits>` match ids: an id carrying a team nickname (`dodgers`, `lakers`) would be bucketed `regular` by accident. A test now asserts the match-id fallback returns `unknown`.
- The two fit methods are now tested for the behaviour the operator panel renders: with no prediction/outcome pairs, each must report **every** bucket as 0 and write no calibration row. A missing key would read as "this bucket was not attempted" instead of 样本不足.
- **Three `expect(typeof x).toBe("function")` tests in `use-optimization.test.ts`** replaced. `triggerOptimization` had no other coverage, so it now pins the wire field name `n_trials` (a `nTrials` key would be dropped and the backend would substitute its own default), the 150 default, and the explicit pass-through. Its cache invalidation is asserted against the key `useOptimizationParams` itself writes rather than a literal — the inert-`mutate` trap from the previous increment, where a hand-written key was never in the cache. Two more added: `triggerIngest` must *not* invalidate the params view (it writes no params row), and `applyParams` must carry its id in the path, not a body.
- **`GET /sports/world-cup/analytics/prediction-timeline` and `GET /world-cup/predictions/today` stay unmounted, deliberately, and §10 of the backlog now says why.** The timeline route reads the same `prediction_history` rows for the same `match_id` as the already-mounted `/world-cup/predictions/matches/{id}/prediction-history` behind `PredictionHistoryCard`, but it **omits** the `%_comparison` filter that both live readers apply via `_is_applied_history`, so wiring it would present engine-comparison snapshots that were never adopted as real predictions. The two fields it adds — `match_minute` and `actual_score` — come from three columns **no writer ever sets**: the sole writer at `world_cup_prediction_pipeline.py:1413` omits all three, so they are permanently NULL. Mounting it would be a regression, not a fix.
- `/today` is `/matches` plus a UTC calendar-day window, same serializer and same in_play→scheduled→finished sort; the frontend already reads `/matches` and can filter by date client-side. Noted in passing for whoever does wire it: its `today_start`/`today_end` are naive datetimes compared against a timezone-aware `kickoff_utc`.
- Verified by eight injections, each restored byte-identically: swapping the knockout/regular scan order, omitting a thin bucket’s key, and moving the confidence boundary each fail exactly one backend test; renaming `n_trials`, changing the trial default, pointing the invalidation at a key the hook never writes, adding an invalidation to `triggerIngest`, and moving the apply id into a body each fail exactly the intended frontend test.
- Net counts: 2 backend tests → 31, 6 frontend tests → 8 (560 passed, up from 558). No production code changed in this increment — no schema, route, engine, formula, weight or flag-default change.

### Feat: conditional calibration had a producer nothing called, and rows nothing could read

- `POST /predictions/calibration/conditional` is the **only** producer of P1-V5 bucket calibration rows, and it reached no caller anywhere: no frontend, no scheduler, no CLI, no test. Meanwhile `edge_detector_service` (two call sites) reads exactly those rows through `get_conditional_calibration_row`. The read path was live and the only way to fill it was a hand-written curl — the sixth instance of the "capability exists but reaches no caller" class, and the second where the read side was wired and the write side was not.
- **The rows were also unreadable when they did exist.** Bucket rows reuse the existing `KernelCalibration` unique constraint by storing a composite competition key (`epl#c_high`, `epl#s_knockout`) instead of adding a column — deliberate, to avoid a migration. But `GET /predictions/calibration` filters `competition` by exact equality and the panel's 赛事 dropdown offers plain codes, so selecting `epl` hid every `epl#c_*` row, while 全部 printed the composite key raw with nothing to say what a bucket was.
- The calibration table now drops `competition` from its request and filters client-side on the parsed base, so a selected competition shows its base row **and** its bucket rows; a new 分桶 column renders 基准 / 置信度·低|中|高 / 阶段·常规赛|淘汰赛|未知, keeping the raw key in the cell's title. The two reliability charts keep the server-side filter — they aggregate predictions, not calibration rows, so no composite key is involved.
- `parseCalibrationKey` is a separate pure module because both the filter and the column depend on the same parse, and a disagreement between them would silently hide rows. Unknown bucket tokens keep their raw value rather than get an invented label, and an empty suffix (`epl#c_`) is reported as a base row rather than claimed as a bucket the backend never wrote.
- The 拟合分桶校准 button is operator-gated the same way as every other mutation (write key, `window.confirm`), is disabled unless a specific engine *and* competition are selected — the route takes `competition` as a required param and fits one engine at a time — and reports per-bucket counts, with `0` rendered as 样本不足 rather than `0 条` so a thin bucket is not read as a fit that produced empty parameters. The panel states in place that fitting the rows does **not** switch conditional calibration on: applying them stays behind `KERNEL_CONDITIONAL_CALIBRATION_ENABLED`, which this path does not touch.
- **The route was missing the master kernel-flag guard** that every neighbouring route in the file has, so with `KERNEL_PREDICTION_ENABLED` off it still wrote `KernelCalibration` rows that `GET /calibration` would then refuse to read back. Added, and the test asserts the refusal happens *before* the fit runs, not merely that the status is 503.
- The route had **no test at all** (`tests/test_registry_altitude_and_conditional_api.py` only asserts `hasattr` on a method name). Four route tests added: both bucket maps returned with the filters forwarded, `competition` required, 503-without-writing, and the write key enforced (401 without the header, 200 with it).
- Verified discriminating by four frontend injections, each restored: restoring the server-side competition filter fails the bucket-visibility test, printing the raw composite key fails the 分桶 test, dropping the confirm dialog fails the cancel test, and removing the cache invalidation fails the prefix test. Plus one backend injection: removing the kernel-flag guard fails the 503 test.
- 18 frontend tests added across 1 new file (558 passed, up from 540) and 4 backend tests. No schema, engine, formula, weight or flag-default change; nothing is enabled by this increment. `npm run build` still exports every route statically.

### Feat: the settlement button had no read side, so its result was invisible on the match page

- `/sports/match` already mounted `ProcessSettlementButton` under a 结算反馈 heading. An operator could trigger settlement for that match and then had nowhere to see what came out: `GET /sport-settlements/{match_id}`, the `useSettlement` hook and the `SettlementList` type all existed with **no caller anywhere in the app**, and the only rendering of settlement rows was the global 50-row history table on `/sports/settlements`, which cannot be filtered by match. The write half of the feature was wired and the read half was not — the same "capability exists but reaches no caller" class as the previous four increments.
- `MatchSettlementPanel` renders that match's rows beside the button: 结果 / 模型概率 / 结算概率 / Brier / 有向误差 / 方向 / 状态, with `skip_reason` inline so a `skipped_no_links` row explains itself rather than looking like a blank.
- **The 方向 column is tri-state on purpose.** `direction_correct === null` means the model landed on the market price, so there was no directional call — the previous entry stopped counting those as misses upstream. Rendering `—` (with a title explaining it is excluded from `direction_accuracy`) keeps this panel from contradicting the number the calibration panel publishes.
- **404 is the ordinary state, not an error.** The route raises 404 rather than returning an empty list, so the panel shows 尚无结算记录; 503 (phase flag off, the default) is one muted line instead of an amber banner that would otherwise sit on every match page; any other status still surfaces as an error.
- **`processSettlement` invalidated a cache key that never existed.** It called `mutate(".../sport-settlements/history")`, but `useSettlementHistory` always appends `?limit=…`, so that key was never in the cache and the refresh did nothing — the history table only appeared to refresh because it passes its own local `mutate()` through `onDone`. Replaced with a prefix filter, which also covers this match's rows and the engine/competition calibration that `_update_market_calibration` upserts on the same call.
- **`useMatchAudit` had no caller either, because `MarketPriceAuditPanel` rebuilt its URL inline.** The panel now calls the hook, removing the duplicated key. Behavior is unchanged; the capability was already reachable, just not through the exported hook.
- Verified discriminating by four injections, each restored: rendering a no-call as `✗` fails the tri-state test, unmounting the panel fails the wiring test, restoring the literal history key fails the invalidation test, and rebuilding the audit URL inline fails all three audit-panel tests.
- 11 tests added across 3 new files, and the vacuous `processSettlement` test (`expect(typeof processSettlement).toBe("function")`) replaced with the invalidation assertion (540 passed, up from 529). No backend, schema, flag or type change; `npm run build` still exports every route statically.

### Fix: a no-call was scored as a wrong call, and the miss became engine trust

- `market_settlement_service._compute_direction_correct` returned `0` — *wrong* — when `raw_edge == 0`. That is not a wrong call, it is **no call**: the model landed on the market price, so the closing line has no direction to confirm or contradict. `_update_market_calibration` then divided by every processed row, so each such row pulled `direction_accuracy` down, and `calibration_fusion_service._compute_market_trust` reads that field back **verbatim as engine trust** — which `edge_detector_service` multiplies into `adjusted_edge`. A measurement artefact was being spent as a model penalty.
- **Nothing stops those rows from existing.** `raw_edge = model_prob - market_prob` with no threshold (`edge_detector_service.py:170`) and every outcome with market data is persisted, so an engine that tracks the market produces them in bulk. The `0.5 / 0.5` degenerate fallback several engines use against a market at `0.5` is the same shape.
- **The fix needed no schema change, which is why it is landing now.** `kernel_market_settlements.direction_correct` is already `Integer, nullable=True`; the two `skipped_*` paths already write `None` into it. So the tri-state the rest of the repo already speaks — `quality_metrics_report_service.slice_metrics`, `prediction_store.get_calibration_buckets` and `calibration_drift_service._cell_metrics` all exclude `None` from the mean — was expressible here all along. The frontend needed no change either: `types.ts` already declares `direction_correct: number | null` and `SettlementHistoryTable` already renders the third case as `—` rather than `✗`.
- **A zero *market* move deliberately still scores 0.** An unmoved closing line is not a confirmation of the edge; that is the ordinary CLV reading, a definition rather than an oversight. Only the zero-*edge* half is unambiguous, so only that half moved, and the docstring says not to fold the other in without settling the question.
- **When no row in the window is directional, no calibration row is written at all.** `kernel_market_calibrations.direction_accuracy` is `nullable=False`, so there is no in-schema way to say "not measured", and writing `0.0` would tell `_compute_market_trust` the engine's direction had been measured and had failed every single time — collapsing trust to `DIAGNOSIS_TRUST_FLOOR` on the strength of no measurement. Skipping keeps the slope/intercept update from that one degenerate window, which is the cheaper loss; the case is logged.
- **Known and documented residual:** `direction_accuracy` is now averaged over the directional rows while `sample_count` remains the regression's `n`, so the two can differ. Publishing both would need a `directional_count` column, and kernel tables still have no ALTER TABLE path here.
- Verified discriminating by two injections, each restored: scoring a zero edge as a miss again fails 2 tests (the unit case and the end-to-end settlement row), and dropping the aggregate's `None` filter fails the other 2 (dilution and the all-no-calls case).
- 3 tests added, 2 rewritten. No schema, engine, formula, weight, flag or frontend change.

### Guard: the hand-written sports-api types had no checker on either side

- The previous entry fixed one declared-but-never-sent field. This closes the gap that let it rot for as long as it did. CI's `Type Sync Check` runs `scripts.generate_types --check`, which covers only the 14 Pydantic root models in `app/models/_frontend_export.py` — the events domain. Every sports route returns a bare `dict[str, Any]`, so `frontend/src/lib/sports-api/types.ts` is a hand-maintained mirror with **no checker at all** on either side.
- `tests/test_sports_api_type_contract.py` parses the `PredictionResult` interface out of the `.ts` file and compares it against the two real producers: the `POST /predictions/matches/{id}/predict` response (built through `TestClient` with a stub kernel) must equal *every* declared field, required and optional; the persisted-row serializer behind `GET /matches/{id}` must equal exactly the required ones. Reading a frontend file from a backend test follows `test_generate_types.py`, which resolves `frontend/src/lib` the same way — no Node is involved, only text.
- **Both directions fail, not just the one that bit us.** A field declared in TypeScript and not sent fails, and a key the backend adds without declaring it fails too. The second half matters because an undeclared field is invisible to every frontend consumer, which is the same defect facing the other way.
- **The exemption list is asserted to be exact, and each entry carries its reason.** `match_id` is absent from the stored row because the serializer takes it from the caller's URL; `betting_analysis` because `kernel_predictions` has no column for it and kernel tables have no ALTER TABLE path here. A subset check would let a third field go missing quietly, so the test asserts set equality and separately asserts every exemption still names a real interface field — an exemption for a renamed field would otherwise hide the rename.
- **Guarded against being vacuous.** These tests are only as good as the regex that feeds them; a parse that silently matched nothing would make every assertion trivially true. `_interface_fields` raises if it finds no fields, and `test_the_parse_actually_finds_the_fields` pins three specific names, one required and two optional.
- Verified discriminating by four injections, each failing the right tests and each restored: dropping `betting_analysis` from the route (1 failure — i.e. this test would have caught the previous entry's defect), adding an undeclared key to the response (1), dropping `feature_version` from the stored-row serializer (1), and renaming the field in `types.ts` (3, including the exemption check).
- **Deliberately scoped to `PredictionResult`.** `types.ts` holds 42 interfaces and 30 optional fields; all 30 were audited by hand against the backend files that emit them and none is unsatisfiable, so generalizing the test to all of them would be pinning a contract no defect has been found against. `_interface_fields` takes the interface name, so extending it later is a one-line addition.
- No production code changed. 5 tests added (4488 passed, up from 4483).

### Fix: the entire `betting_analysis` audit trail reached no caller (P1-X3 / P1-O1 follow-up)

- Nine engines build `betting_analysis` and two deliberately return `None`; `prediction_kernel` then appends `conditional_calibration` to whatever the engine wrote. `POST /predictions/matches/{match_id}/predict` returned eight fields and this was not one of them. The dict was computed on every prediction and read by nobody.
- **Three frontend panels already read it, from this exact endpoint.** `triggerPrediction` in `use-matches.ts` posts to `/predictions/matches/{id}/predict` and types the reply as `PredictionResult`, whose declared `betting_analysis` was therefore always `undefined`:
  - `SoftTotalsPanel` opens with `if (!soft || soft.available !== true) return null`, so the soft O/U + BTTS panel has never rendered once — and neither has P1-O1's `真实盘口线` / `联赛均值线` badge, which is the entire consumer of the `line_source` plumbing added for it.
  - `SportConfidencePanel` is documented "prefers API `confidence_breakdown`" (P1-X3) but fell through to its own re-derivations of decision strength, completeness and agreement on every render. It was not blank, it was quietly showing different numbers, and `market_damp` — which only the kernel computes — had no path to the screen at all.
  - the situational block in `match-detail-panel.tsx` is gated on `betting_analysis.situational_applied`, so it never opened.
- **Two probability rewrites were made invisible, which is the serious half.** Both replace the numbers being returned and record the before/after in `betting_analysis`:
  - `prediction_kernel.py:89-103` swaps in the calibrated probabilities and records `slope`, `intercept`, `sample_count`, `bucket`, `raw_home_win` and `calibrated_home_win`. Without the field a calibrated prediction is indistinguishable in shape from an uncalibrated one — nothing tells a caller its number was adjusted, or how thin the sample behind the adjustment was.
  - `situational_engine.py:89-95` swaps in `adjusted` and records `base_probs`, `adjusted_probs` and `situational_notes`.
- **The write was already tested; the readability never was.** `tests/test_kernel_prediction_kernel.py:298` asserts `out.betting_analysis["conditional_calibration"]["applied"] is True` on the kernel's own return value, and passed throughout. A field can be correct on every object inside the process and still not exist for anyone outside it. This is the seventh instance of the unreachable-capability class.
- **Persistence stays out of scope and is documented as blocked.** `kernel_predictions` (`kernel_db.py:54-68`) has no `betting_analysis` column and this repo has no ALTER TABLE path for kernel tables — the same wall that blocks the `direction_accuracy` fix. So `_prediction_to_dict`, which serializes the ORM row for `GET /predictions/matches/{id}`, still cannot carry it, and the field exists only on the POST response. The route docstring says so, rather than leaving the next reader to rediscover it.
- Three tests, each pinning something different: the `confidence_breakdown` + `line_source` pair, the calibration record with its raw-vs-calibrated values and sample count, and that `None` arrives as an explicit `null` rather than a missing key (the LoL market-only engine really does return `None`, and a schema tells those apart even where a careless client does not). Verified discriminating — removing the one added line fails all three.
- Nothing else changed: no engine, no formula, no weight, no schema, no flag, no new dependency. `betting_analysis` was already a free-form dict on `PredictionResult`, and every producer's payload was checked to be plain JSON scalars, so no encoder can be surprised.
- 3 tests added (4483 passed, up from 4480).

### Fix: the style table already had the rows; the lookup could not reach them (P1-F6)

- `football_style._normalize` only lowercased and collapsed whitespace, so the static style table — keyed on short club names (`arsenal`, `lazio`, `villarreal`) — could not answer the names the adapters actually pass, which are Football-Data.org spellings (`Arsenal FC`, `SS Lazio`, `Villarreal CF`). This is the root cause behind the previous two entries: the form-derived possession proxy existed to fill a gap that was mostly a **lookup** failure, not a data gap. The rows were already in the table.
- `stats_for_team` now goes through `_lookup_key`, which tries both exact spellings first, then affix-stripped and accent-folded candidates, and requires every candidate to land on a **real table key**. Nothing is guessed, there is no fuzzy scoring, and a club the table does not carry still returns `None` so the engine marks the factor unavailable and redistributes its weight — the existing contract is unchanged.
- Measured on each track's own alias table, as the share of team names the table can resolve, and the square of that as a proxy for both sides of a fixture resolving:

  | track | teams | resolved before | resolved after | pair proxy before | pair proxy after |
  | --- | --- | --- | --- | --- | --- |
  | epl | 48 | 20 | 40 | 17.4% | 69.4% |
  | ucl | 72 | 37 | 55 | 26.4% | 58.4% |
  | laliga | 20 | 5 | 9 | 6.2% | 20.3% |
  | bundesliga | 18 | 4 | 6 | 4.9% | 11.1% |
  | seriea | 20 | 2 | 7 | 1.0% | 12.2% |
  | ligue1 | 18 | 3 | 5 | 2.8% | 7.7% |

  The pair column is `(resolved/teams)²`, which assumes both sides are drawn uniformly from the alias table. Real fixture lists are not uniform — bigger clubs recur — so treat it as table coverage, not an observed per-fixture rate. It is the same estimator the two entries below used, kept for comparability.
- **A loosened lookup has to be audited for wrong hits, not just for misses**, because a wrong club's possession is worse than no possession: the engine cannot tell it apart from the right club's. Three audits, all run against the real corpus (167 names from the league, UCL and EPL alias tables) and all pinned as tests:
  - *collision* — group every table key by its fully stripped+folded form, fail if any group holds differing stats. Empty.
  - *no-invention* — every token of the matched key must be a token of the input, so stripping may drop a legal-form token but never add an identifying one. Zero violations.
  - *whitelist* — assert `_AFFIX_TOKENS` contains no identifying token, since the future hazard is somebody adding one.
- All 39 newly-resolved mappings were listed and checked by eye; every one is the same club (`AFC Bournemouth → bournemouth`, `SSC Napoli → napoli`, `Atlético de Madrid → atletico de madrid`, `Borussia Mönchengladbach → borussia monchengladbach`). Residual misses are genuine data gaps — no row for Getafe, Valencia, Burnley, Stuttgart, Bochum, Freiburg, Augsburg — and correctly leave the factor unavailable.
- `_AFFIX_TOKENS` is grounded in a census of that corpus, not in imagination: it holds exactly the legal-form tokens observed leading or trailing a real fixture name. Deliberate exclusions, with reasons in the source: `sg` (Paris SG → a bare "paris" Paris FC could later claim), `rb` (adds zero resolutions — the table already carries both spellings), and every identifying token (`city`, `united`, `real`, `club`, `town`, squad years like `05` / `1909`).
- Accent folding was priced before inclusion: exactly three extra resolutions, zero collisions. It is carried by a built-once `lru_cache` index over a static constant operators edit by PR, so there is nothing to invalidate.
- **Engine effect, measured on Arsenal FC vs Manchester City FC** with elo/form/h2h/odds/injury present. The factor flips from unavailable to available, and confidence goes **down**: 0.6234 → 0.6126 (**−1.08pp**). p(home_win) 0.4648 → 0.4601, p(away_win) 0.2657 → 0.2719. That direction is the point — real possession favours the away side here (65% vs 57%), so it *disagrees* with the elo/form/odds consensus and `factor_agreement` correctly falls. The removed proxy could only ever agree with form, which is why it inflated confidence.
- Verified discriminating by four injections, each failing the right tests and each restored: adding `"city"` to the whitelist (4 failures), moving the exact-match pass after stripping (1), adding a colliding row (1), and reverting `stats_for_team` to the normalize-only lookup (30).
- One test was vacuous as first written and is worth recording. `test_exact_key_wins_over_any_weakened_candidate` compared *stats*, and today's table gives `ac milan` and `milan` the same row, so it stayed green with the exact pass moved after stripping. Rewritten to assert `_lookup_key(key) == key` — identity, not equal values — it fails immediately.
- Not fixed here, still engine-formula territory: the possession factor is not neutral at `share=0.5` (0.381/0.238/0.381, suppressing draw), `shots_on_target_home`/`_away` are read and written nowhere, and `style_source` is written and never read.
- 65 tests added net (4480 passed, up from 4415). Most of that count is parametrization, not 65 independent behaviours: two tests run across the 29-entry `_FIXTURE_SPELLINGS` list and the no-invention audit runs across the same, so the new *behaviours* are roughly a dozen — the spelling-resolution pair, the three audits, exact-match precedence, and the bare-legal-form / partial-name / unknown-club negatives.

### Correction: the possession proxy never touched the World Cup (P1-F6 follow-up)

- The entry below claimed the World Cup was the permanently-affected track. **That is wrong.** `WorldCupAdapter.fetch_all_data` builds its own `raw` dict and calls **zero** `enrich_*` functions — it never goes through `fetch_elo_and_odds`, which is the only path to the removed proxy. The reasoning that produced the claim (`is_world_cup` skips the live provider, and `stats_for_team("Brazil") is None`) is true of `enrich_style_features` and irrelevant, because no World Cup fixture reaches that function.
- **The affected tracks are the six callers of `fetch_elo_and_odds`** — epl, ucl, laliga, bundesliga, seriea, ligue1 — and the blast radius is considerably larger than one tournament. The static style table is keyed on short club names (`arsenal`, `chelsea`) while the adapters feed Football-Data.org names (`Arsenal FC`, `Chelsea FC`), and `_normalize` only lowercases and collapses whitespace, so most lookups miss. Both sides must resolve for the factor to be real, giving these pair-hit rates against each track's own alias table:

  | track | FD names | resolve | pair-hit | fixtures given fabricated possession |
  | --- | --- | --- | --- | --- |
  | epl | 48 | 20 | 17.4% | ~83% |
  | ucl | 72 | 37 | 26.4% | ~74% |
  | laliga | 20 | 5 | 6.2% | ~94% |
  | bundesliga | 18 | 4 | 4.9% | ~95% |
  | seriea | 20 | 2 | 1.0% | ~99% |
  | ligue1 | 18 | 3 | 2.8% | ~97% |

  The live style provider is default-off, so in the shipped configuration it is always the static table that decides.
- The measured magnitudes in that entry are **unchanged and correct** — they were computed from the engine's own arithmetic, not from a track. Re-measured across every available-factor profile: the weight ratio is 1.357×–1.374× and the confidence inflation is +1.22pp to +2.03pp, with +2.03pp / +3.51% at the elo+odds+form floor that the affected league fixtures actually sit at. Only the label "World Cup fixture" was wrong.
- Pinned by `test_world_cup_adapter_never_reaches_the_style_enricher`, which drives the real `WorldCupAdapter.fetch_all_data` with the enrichers patched rather than reading the source. Verified discriminating: routing that adapter through `fetch_elo_and_odds` makes it fail.
- **Removed dead machinery the proxy left behind.** `custom.pop("possession_proxy", None)` in `enrich_style_features` could never fire — `raw` always arrives fresh from `fetch_elo_and_odds`, nothing upstream writes the key, and the AST guard now prevents a new producer. It was kept last increment as "cheap to clear", which was a defence of a line that cannot run.
- **Rewrote five tests that pinned an unreachable state.** `TestLiveStyleOverwrite._raw`, `test_live_and_static_incomplete_preserve_form_proxy`, `test_both_static_hits_overwrite_proxy`, `test_one_side_unknown_keeps_proxy`, and `test_both_unknown_no_static_source` all seeded `possession_home`/`possession_away`/`possession_proxy` into `custom` before calling the enricher and asserted what became of them. No production call can produce that input, so "keeps proxy" and "overwrites proxy" described a fiction, and one test name advertised the removed defect as intended behaviour. They now start from the empty `custom` the enricher is actually handed and assert the reachable contract: a full pair writes all six keys plus `style_source`, a half pair writes nothing. Verified discriminating — injecting half-pair completion fails two of them.
- **Fixed the misnamed World Cup style test**, the fourth item reported-but-unfixed below. It used club names under a `wc` code, so the static table answered and it asserted `style_source == "static_table"` — it could not see the national-team path it was named for. Split in two: one drives Brazil/Argentina and asserts nothing is written, the other keeps club names to isolate "the provider is skipped because of the competition code" from "the lookup missed".
- `enrich_style_features`' docstring and the `is_world_cup` guard now state that no current caller passes a World Cup fixture, so the branch is not re-read as evidence about that track. The guard stays: it is cheap, and a national team has no club style row.
- 2 tests added net (4415 passed, up from 4413).

### Fix: form was voting twice, the second time labelled "possession" (P1-F6)

- `fetch_elo_and_odds` wrote form share into `custom["possession_home"/"possession_away"]`, with a comment saying it was there "so multi-factor soft path is non-null". But the engine's `form` factor reads the *same two numbers* — `feature_builder.py` passes `team_raw["form_home"]` straight through to `TeamFeatures.form_home` — so one piece of evidence was counted twice under two names. The engine cannot tell the difference: it reads `custom.get("possession_home")` and has no way to know the value came from form.
- The `possession_proxy: "form_share"` marker recorded the substitution and **nothing read it**, in `app/` or in the frontend. A provenance marker no consumer checks does not make a substituted value honest; it just records the substitution where nobody looks.
- Three separate inflations, measured:

  | channel | mechanism | effect |
  | --- | --- | --- |
  | fused weight | `possession` 0.04 added to `form` 0.09 | form-derived share 0.145 → 0.197 (**1.357×**) |
  | `data_completeness` | counted as a 4th available factor | 3/8 → 4/8 |
  | `factor_agreement` | cast a vote agreeing with form by construction | +1 agreeing vote |
  | **net confidence** | all three | **+1.22pp to +2.03pp (+1.85% to +3.51% relative)**, by how many other factors resolve |
  | `p(home_win)` | opposing signs partly cancel | ≤ 0.71pp |

- The confidence channel is the one that matters, and its sign is backwards: the fixtures with the least real data reported the *highest* completeness, because the proxy filled the slot precisely when nothing else could. The frontend labels this factor 控球/射门, so a form number was reaching users under a possession heading. **The affected tracks are the six callers of `fetch_elo_and_odds`** — epl, ucl, laliga, bundesliga, seriea, ligue1 — see the correction entry above for the sizing; an earlier version of this entry named the World Cup, which never reaches the proxy at all.
- Removed the write. The engine already has the right mechanism for a factor with no data — `available=False` plus weight redistribution, the same path every other missing factor takes — and the proxy existed specifically to defeat it. No engine formula, weight, or contract changed; only the input stopped lying. Real possession, live or static, still switches the factor on.
- **Falsifiability caught four near-vacuous tests before they shipped.** Re-injecting the proxy failed only the source-level AST guard; all four behavioural tests stayed green. Each for a different reason: the unknown-club fixture had no form resolved, so the proxy's own `fh is not None` guard skipped it; the World Cup test drove `enrich_style_features` directly and never ran the proxy block; the double-count test used a fixture that *hits* the static table, where style enrichment pops the marker and overwrites possession, erasing the evidence. The production condition is "form resolved, style missing", and no test reproduced it. Rewritten to seed form explicitly and use a fixture both style sources miss, two behavioural tests now fail on re-injection alongside the guard.
- The AST guard earns its place because this defect was a *write*: no behavioural test could have caught the proxy being **added**, since the engine consumes whatever is under the key without complaint. Pinning the set of functions allowed to write possession is what makes the next such addition visible.
- Also worth recording: no test covered the removed writer at all. A behaviour worth up to 2.03pp of confidence and a 1.357× weight distortion was held in place by nothing.
- Audited clean in the same sweep: all 13 live-provider entry points have production call sites, and all five `market_totals` injectors reach a `resolve_totals_line` consumer (World Cup maps to `sport=football`, so it lands in `football_multi_factor_engine`). Four findings left unfixed because they are engine-formula territory: `shots_on_target_home`/`_away` are read at `football_multi_factor_engine.py:557,560` and written nowhere in `app/`; the possession factor is not neutral at `share=0.5`, returning 0.381/0.238/0.381 with a draw mass of 0.238 against ~0.28 for its siblings, so it suppresses draw regardless of input; `style_source` is written and never read; and `test_world_cup_does_not_call_live_style_provider` uses club names for a World Cup fixture, so it hits the static table and cannot observe the national-team path it is named for (fixed in the entry above).
- 8 tests added (4413 passed, up from 4405).

### Fix: one liquidity rule instead of two that had drifted (P1-V3)

- `market_liquidity.compute_match_liquidity_factor` carried a **second copy** of the defect fixed in the previous entry, and its docstring said "Semantics mirror `EdgeDetectorService._compute_liquidity_factor`" while nothing checked that claim. Fixing the edge path first therefore *created* a measurable contradiction: the same `[unmeasured, $100]` group scored **1.0** in the edge detector and **0.01** here, a 100× disagreement between two functions documented as mirrors.
- Three drifts, not one. The mixed case (both took `max` over the *measured* subset, so one unmeasured venue beside a $100 market scored as though the group were a $100 market); a link with **no snapshot at all** (the edge path read that as unmeasured and declined to penalize, this path `continue`d and dropped the link, letting a measured venue decide alone); and after the first fix, the outright disagreement above.
- This copy matters more than the first: it is **not** behind a default-off flag. Every sport's feature builder injects it — football, basketball, baseball, hockey, LoL, World Cup — and it feeds `compute_confidence`'s `market_quality_damp` and `odds_quality`, so it lands in `KernelPrediction.confidence`. Measured on the damp term, at the 10k floor:

  | group | old factor | old damp | fixed damp | confidence effect |
  | --- | --- | --- | --- | --- |
  | unmeasured book + $100 market | 0.0100 | 0.9020 | 1.0000 | +10.86% |
  | unmeasured book + $1k market | 0.1000 | 0.9200 | 1.0000 | +8.70% |
  | unmeasured book + $4.9k market | 0.4900 | 0.9980 | 1.0000 | +0.20% |
  | all measured (regression) | unchanged | — | — | 0.00% |

  Per the previous entry, traditional-odds links never receive a snapshot at all, so the top rows are the common shape rather than a rare one.
- The rule now lives in one place, `market_liquidity.group_liquidity_factor(liquidities, *, floor)`, called by both. It returns `None` meaning "do not penalize" whenever **any** venue publishes no usable depth, including the all-unmeasured case both functions already treated that way. Callers differ only in how they *render* that verdict: the edge detector multiplies its factor, so it renders as `1.0`; the feed omits the key, so `odds_quality` and `market_quality_damp` skip the term. The `floor` stays a parameter rather than being read from config inside the helper, because the edge detector deliberately keeps its own (5000) decoupled from `DIAGNOSIS_LIQUIDITY_FLOOR` (10000) — coupling them once let a config change in the diagnosis pipeline silently flatten every edge's liquidity factor. The rule is shared; the scale is not.
- The consistency test took two attempts, and the first one is worth recording as a near-miss. It called the shared helper twice with different floors and asserted the two results agreed — which is close to tautological: it stays green even when a caller stops using the helper altogether, which is exactly the failure it claimed to cover. Verified by re-introducing the real drift and watching it pass. Rewritten to drive the two *entry points*, it now fails on that same injected drift.
- A second trap in the same test: "declined to penalize" cannot be read off the factor value, because at the edge floor of 5000 a genuinely deep group saturates the ramp at `1.0` — the same number the edge side uses to render "no penalty". The test asks the rule for its verdict and then asserts each caller rendered *that*, instead of comparing numbers.
- Seven tests added. Reverting the shared helper fails six of them across **both** test files from a single edit, which is the structural guarantee the extraction buys: there is no longer a copy that can be fixed alone.

### Fix: unmeasured venue depth was spent as a measurement of zero (P1-V3)

- Three defects in the sport-edge path, all one question: what does it mean that a venue publishes no liquidity? Every other liquidity site in this repo answers it the same way — `diagnosis_service.liquidity_factor` says "do not penalize what we cannot measure", `market_liquidity` omits the key rather than defaulting it, and `market_quality_service` excludes a missing sub-score from its average. The edge path answered it two other ways, neither of them that one.
- **`_aggregate_market_prob` treated unknown depth as a one-dollar market.** The weight was `max(liquidity, 1.0)` for a venue that publishes depth and `1.0` for one that does not — a sentinel spent on the same numeric scale as real dollars, making the unmeasured venue the *most distrusted* member of the group by a factor of thousands. Measured: a book quoting 0.50 with no published depth beside a $100 market quoting 0.20 produced a consensus of **0.2030**, of which the book held **0.99%**. Three such books all quoting 0.50 held **2.91%** between them. `raw_edge` inflated from 0.30 to **0.4470**, +49%, in the direction that manufactures edge.
- The weight for an unmeasured venue is now the **median of the published weights** — "assume this venue is typical of the venues that do publish", the minimum-assumption reading, and the only one that neither penalizes nor favours it. It preserves the real depth ordering among published venues, which an unweighted mean would discard: with an unmeasured book at 0.50, a $5k market at 0.20 and a $50k market at 0.60, the old rule gave 0.5636, an unweighted mean would give 0.4333, and the median imputation gives **0.5424** — three different numbers, so one test rules out both alternatives. Note that case corrects *downward*; the fix is not a one-directional thumb on the scale.
- **`_compute_liquidity_factor` contradicted itself.** Its own two documented rules are "an unmeasured venue is not penalized" (the all-unmeasured branch returned 1.0) and "the most liquid source dominates". Taking the max over only the *measured* subset honoured neither: it penalized a venue precisely because its depth could not be measured, and let the group's fate be decided by a member that is not the max once the unpenalized member is counted. One unmeasured venue alone gave 1.0; that same venue beside a $100 market gave **0.02** — learning that some *other* venue is thin cut the factor 50×, having learned nothing about the first. The factor is now 1.0 whenever any venue is unmeasured. This is a policy choice, the one the function already made for the all-unmeasured case, not a measurement: a venue that publishes no depth has an *unknowable* factor and arithmetic cannot supply one.
- **`fetch_link_price` sent traditional-odds links to the wrong venue**, which is why the mixed measured/unmeasured case is the *normal* case rather than an edge case. The Kalshi branch's own docstring diagnoses the failure — "sending it to gamma matches nothing and the link silently never gets a snapshot" — and the fallback then left it in place for the other source this class creates itself: `link_traditional_odds` stores a synthetic `odds_api::<match_id>::<outcome_label>` in `contract_id`, which gamma cannot match, so every poll spent an outbound request to learn nothing and the link never got a snapshot. It now returns None and names the reason. That does not create the gap — the gap already existed — it stops querying the wrong venue. Wiring `TraditionalOddsStore` into the snapshot path would be a market write and is deliberately not done.
- The two arithmetic fixes reduce **exactly** to the previous behavior when every venue publishes depth (nothing to impute, no unmeasured member) and when none does (no published weights to take a median of, so all weights stay 1.0 and the result is the unweighted mean it already was). Only the mixed case moves; two regression tests assert those endpoints and pass under both the old and the new rule, which is what makes them anchors rather than discriminators.
- The mixed case had **no test coverage at all**, which is how this survived. The one test whose name claimed to cover it — `test_detect_edges_traditional_odds_no_liquidity_uses_weight_1` — seeded a *single* link, and with one link the weight cancels out of the weighted mean entirely, so its assertions could not see the weight it named. Renamed, and six discriminating tests added; all six were confirmed to fail against the previous behavior before it was restored.
- Reported honestly: in the mixed case the two old defects pushed in opposite directions, so their product could look small by accident rather than by prudence (`adjusted_edge` 0.0089 where the corrected value is 0.3000). `PHASE7_EDGE_DETECTOR_ENABLED` remains **false**; correcting the arithmetic of an existing default-off path enables no learning, scheduling, market write, or prediction write.

### Fix: calibration fusion weighted a dormant source's sentinel as if it were an estimate (P1-V5)

- `calibration_fusion_service.compute_trust` Case 4 fused Phase 3 and market calibration by sample count, but a source below its MIN reports `DIAGNOSIS_DORMANT_TRUST` (0.5) — a **sentinel meaning "no usable estimate"**, not a measurement of 0.5. The old arithmetic weighted that sentinel by the very sample count that carries no estimate, so a row saying "I don't know" pulled the composite toward 0.5 in proportion to how loudly it said it.
- Measured with defaults (`CALIBRATION_FEEDBACK_MIN_SAMPLES=8`, `MIN_SAMPLES_FOR_MARKET_CALIBRATION=10`), Phase 3 at accuracy 0.72 over 20 samples beside a market channel whose real direction accuracy is 0.95:

  | market samples | state | composite trust |
  | --- | --- | --- |
  | 0 | no row | 0.7200 |
  | 1 | dormant | 0.7095 |
  | 7 | dormant | 0.6630 |
  | 9 | dormant | 0.6517 |
  | 10 | qualified | 0.7967 |

  Trust **fell** as evidence about a *good* channel accumulated, then jumped 0.145 at the threshold. That cannot be read as shrinkage toward a prior: under shrinkage more data means *less* pull to the prior; here it meant more.
- Two further manifestations. The presence of a dormant row moved the answer while the **absence** of that row did not (Cases 2/3 give a missing source zero weight), even though both carry identical information — none. And the distortion ran upward too, which is the worse half: an engine measured at 0.20 over 20 samples was flattered to **0.2931** by a 9-sample dormant row, a 47% relative inflation of the trust that gates its edges.
- Only *qualified* sources now carry weight. If neither qualifies the result is `dormant` rather than an average of two sentinels, and `source` reports `phase3_only` / `market_only` when only one channel qualified — labelling that `fusion` would claim corroboration that never happened. The dormant sentinel and sample counts are still reported so a zero weight stays observable. Cases 1–3 are deliberately untouched: with one source there is nothing to fuse and the dormant 0.5 is the correct answer.
- The threshold rule now lives in one `_source_trust` helper returning `(trust, qualified)`, used by both `_compute_phase3_trust` and `_compute_market_trust`, so the qualification threshold and the trust value cannot drift apart.
- The single existing test pinned the defect (`test_compute_trust_fusion_with_one_dormant_source` asserted the 0.6913 dilution as correct) and was replaced by six tests: zero weight for a dormant source, dormant-row-equals-no-row, a monotonicity sweep across the threshold, the bad-engine inflation case, both-dormant, and the market-only mirror. All six were confirmed to fail against the previous arithmetic before the fix was restored.
- `PHASE8_CALIBRATION_FUSION_ENABLED` remains **false** and `EdgeDetectorService._compute_trust` still bypasses the service entirely when off. Correcting the arithmetic of an existing default-off path enables no learning, scheduling, market write, or prediction write.

### Confidence-reliability curve and signed calibration gap (P1-X1)

- The reliability curve that already existed bins `max(outcome_probabilities)`. `KernelPrediction.confidence` is a **different quantity** — built by `engines/confidence.compute_confidence` from decision strength, data completeness, factor agreement, and a market damper — and nothing had ever compared it to outcomes. So the engine's own stated confidence was rendered in the learning panel, consumed as a trust input, and never once checked against whether matches at that confidence actually resolved that often.
- `compute_confidence_reliability_bins` adds that curve, exposed as `GET /predictions/calibration/confidence-reliability` with the same bin shape as the probability route so the frontend chart is reused unchanged.
- Alongside ECE it publishes `signed_gap = mean_confidence − mean_accuracy`. ECE is unsigned and therefore cannot say which way to move the formula; the sign can. Positive is overconfident (rendered 过度自信), negative is conservative (保守). Both means are published so the gap can be read rather than trusted.
- The binning rule was extracted into one shared `_reliability_curve` used by both curves, so they cannot drift apart in bin edges, rounding, or ECE weighting. Two behaviors are preserved exactly: ECE accumulates from the **unrounded** bin means while `max_calibration_error` reads the **rounded** per-bin values the caller sees, and the bin index uses `min(int(predicted * bins), bins - 1)` rather than dividing by `bin_width` — `0.3 / 0.1` is `2.9999...` and put 0.3 in the wrong bin.
- Note the scale: `compute_confidence` maps its blend into `0.30..0.95`, so the lowest and highest bins are expected to be empty rather than missing. The panel says so, otherwise a correct chart reads as a bug.
- Every new fixture makes confidence and `max(outcome_probabilities)` different numbers (0.90 versus 0.55), so the two curves land in different bins and report different ECE (0.65 versus 0.30) on identical rows. Substituting the wrong column was confirmed to fail 8 of 17 backend assertions and 3 frontend ones before the correct binding was restored — the pre-existing reliability tests could not have caught it, since they asserted only bin counts and `total_samples`.
- Replaced a vacuous test in the process: `test_reliability_source_has_ece` asserted `"ece" in inspect.getsource(compute_reliability_bins)`. It pinned no value, and broke the moment the arithmetic moved into a shared helper with no behavior change.
- Read-only throughout. The endpoint writes nothing, no learning path is enabled, and no engine formula, weight, or output key changes.

### Fix: calibration trust was the league's home-win rate, not the engine's accuracy (P1-V5)

- `KernelCalibration.avg_accuracy` was written as `mean(1[outcome == "home_win"])` — the share of fixtures that ended in a home win, a property of the league that is **entirely independent of what the engine predicted**. `avg_confidence` was written as the mean predicted home-win probability, while `KernelPrediction.confidence` existed and was ignored. All three producers were affected: `update_calibration`, `update_calibration_by_confidence`, and `update_calibration_by_stage`.
- Neither field is decoration. `edge_detector_service._compute_trust_phase3` and `calibration_fusion_service._compute_phase3_trust` read `avg_accuracy` as engine **trust** (`clamp(avg_accuracy, DIAGNOSIS_TRUST_FLOOR, 1.0)`), `engine_score` divides accuracy by confidence to publish `confidence_calibration`, and `GET /predictions/calibration` plus the frontend learning panel render both verbatim as 平均置信度 / 平均准确率. So an engine that never predicts a home win scored trust equal to the league's home-win rate — 0.46 in a 46%-home league, the same as a perfect engine — and `confidence_calibration` was really a home-bias ratio.
- Accuracy is now the share of fixtures whose called outcome actually happened, counted by `argmax(outcome_probabilities) == outcome`, and confidence is the engine's own value. The rule lives in one new `predicted_outcome` helper that `compute_error` now also uses, so the summary cannot drift from the per-match `outcome_correct` it summarizes. The linear calibration regression is deliberately untouched: it maps the home-win probability, so `x`/`y` are correctly that column — only the two summary fields read them wrongly, and a comment now says so at the point of temptation.
- The bucket rows were self-contradicting: a `#c_high` row (confidence ≥ 0.70 by definition of its own key) reported an `avg_confidence` of 0.1875.
- The defect survived because the only test covering it asserted `avg_confidence > 0` and `avg_accuracy >= 0`, and because the existing seeder cannot distinguish the two quantities — its argmax is always `home_win` and its home-win rate happens to equal its accuracy (both 2/3). The new seeder makes them 0.75 and 0.25 by construction; all six new semantic assertions were confirmed to fail against the previous arithmetic before the fix was restored. A further test drives two engines with opposite calls over identical results and shows trust now separates them where it previously gave both the same value.
- Every consumer test already hand-seeded `avg_accuracy` with values read as accuracy (0.72 / 0.75 / 0.90 / 0.10), so the intended semantics were never in doubt — only the producer was wrong. `PHASE3_LEARNING` and conditional apply remain OFF; correcting the arithmetic of an existing default-off, write-key-gated path does not enable learning, scheduling, or any write.

### Real market over/under totals-line provider (P1-O1 真盘口)

- `market_totals_service`: opt-in configured over/under snapshots that replace the league-average placeholder line in the soft-totals diagnostic for football, NBA, NHL, and MLB — with bearer authentication, `http`/`https`-only endpoints, sport+date query resolution, bounded responses, strict validation, duplicate- and self-paired-fixture rejection, per-URL caching of valid snapshots only, and no-request behavior when disabled or unconfigured.
- The placeholder it replaces was not merely approximate; for the three North American sports it made the diagnostic vacuous. Those engines derive scores as `league_avg/2 ± margin/2`, so home + away equals `league_avg` exactly, and the soft-totals call is passed `line=league_avg`. Line and expected total were therefore identical by construction and **`p_over` was a per-sport constant** — measured at exactly `0.4821` for basketball at margins of 0, +5, +15, and −12 alike. Football escapes this only because `_probabilities_to_scores` applies a draw factor, so its expected total does move against its fixed 2.5 line.
- The provider must publish the line together with **both decimal prices**. A line on its own is a number with nothing behind it; a two-sided quote is what makes it a market. Prices are de-vigged locally, the overround must sit in `(1.00, 1.30]`, and the de-vigged over probability must be within `MARKET_TOTALS_MAX_PRICE_SKEW` of even — a book's posted total is by definition the level it has balanced, so a heavily skewed price means the number is not that level. The line must also fall in `[0.5×, 2.0×]` of the baseline it replaces, which catches a cross-sport unit error such as a basketball spread of 5.5 arriving as a total.
- Unpriced markets and malformed data are handled differently on purpose: a structurally broken row rejects the whole snapshot, while a row whose `over_odds` and `under_odds` are both explicitly `null` is a real fixture with a suspended or unopened market and empties only that fixture. `available=True, total=None` stays distinct from `available=False`; neither asserts the market is even.
- The request date is required as exactly `YYYY-MM-DD` and re-emitted canonically. Validating it loosely was a real defect found while testing: a whitespace-only date passed the original falsy check and was sent verbatim. A provider handed a timestamp or a partial date is free to ignore it and return another day's board, and a wrong-day snapshot looks perfectly valid while quoting lines for the wrong fixtures.
- Wired at **five** adapter call sites — `mlb_adapter`, `nhl_adapter`, `nba_adapter`, the football composition root `adapters/_shared.fetch_elo_and_odds` (EPL/UCL/league), and `world_cup_adapter._build_custom`, which builds `custom` itself and would otherwise have silently kept the 2.5 placeholder. Each site has a wiring test asserting the `(sport, kickoff-date, home, away)` call and that an unavailable or raising provider writes nothing, because a provider nobody calls is a capability that exists and is unreachable.
- Provenance is recorded as `soft_totals_btts["line_source"]` (`market_provider` / `league_average`), with the de-vigged book probability published verbatim as `market_p_over` alongside the model's own `p_over`. A malformed line in `custom` degrades silently to the baseline; a usable line with an unusable companion probability keeps the line. Engine formulas, weights, output keys, and default behavior are unchanged.
- The frontend panel badges the line source, shows a model-versus-book comparison tile when a book probability is present, and renders a caveat when the line and the expected total coincide — checked numerically (`|expected − line| < 0.05`) rather than by sport, so football's fixed 2.5 placeholder is not mislabelled.
- Caveat carried in the contract and in the panel: for basketball, baseball, and hockey the **expected total remains the league average**, so even with a real line the comparison measures the line's distance from that baseline rather than a team-specific scoring forecast. A team-specific expected total needs a scoring model those engines do not have.
- 亚盘 (Asian handicap) stays open on P1-O1: a spread line needs a margin distribution (Skellam or equivalent) that none of these engines expose, plus a frontend consumer that does not exist. Publishing a handicap with no margin model behind it would repeat the defect this provider was written to close.
- Production activation requires a licensed provider returning the documented contract; see `docs/dev/market-totals-provider-contract.md`.

### Fix: soft over/under was wrong at basketball scale (P1-O1)

- `soft_totals_btts_analysis` summed the match total over a fixed `0..10`-per-side score grid. At NBA scale (~110 points per side) essentially none of the probability mass falls inside that grid, so **`p_over` returned `0.0` and `p_under` returned `1.0` on every basketball match** — rendered directly to users by the soft-totals panel as `大 220 → 0.0% / 小 220 → 100.0%`. Hockey and baseball were also truncated, less visibly.
- The sum of two independent Poisson score counts is itself Poisson, so the over/under needs only a one-dimensional distribution over the total, not a two-dimensional score grid. The new `_poisson_total_pmf` builds that distribution in log space (`math.lgamma`) with a bound scaled to the mean (`lam + 10*sqrt(lam)`), so tail accuracy is constant across sports instead of degrading as the total grows. BTTS keeps its existing closed form.
- Measured effect: basketball at a 220 line goes from `0.0 / 1.0` to `0.4821 / 0.5179`; baseball moves `0.4674 → 0.4769`; hockey `0.4707 → 0.4711`; football is unchanged to four decimals, since the old grid was already wide enough there. All output keys, rounding, line semantics, engine formulas, and weights are untouched.
- Removed the `max_g` parameter from `soft_totals_from_scores`: it was declared with a default of `30` but never forwarded, so no caller could ever widen the grid.
- Regression coverage in `tests/test_soft_totals_distribution.py` pins the distribution properties — including a cross-check against an explicit two-dimensional convolution — and the previously vacuous `test_basketball_soft_totals` now asserts the probabilities instead of only the envelope. Both fail against the old implementation.
- Known limitation, unchanged and left to the real-market-line work: an exact total on an integer line still counts as under, which at a 220 line is ≈2.7% of the mass. Real push handling belongs with real market lines and an Asian-handicap feed, so `真盘口/亚盘` stays open on P1-O1.

### MLB measured park-factor provider (P1-M2)

- `mlb_live_park_service`: opt-in configured park snapshots that replace the static 30-team `park_factor` table, with bearer authentication, `http`/`https`-only endpoints, season-year query resolution, bounded responses, strict validation, duplicate-team rejection, per-URL caching of valid snapshots only, and no-request behavior when disabled or unconfigured.
- The provider must publish **home and road game counts with the combined runs scored in those games**; the factor is computed as `(home_runs / home_games) / (road_runs / road_games)` rather than read from the payload, so a pre-computed factor with no game sample is rejected instead of trusted. Dividing each side by its own game count also means unequal home/road windows do not skew the result. A computed factor outside `[0.70, 1.40]` rejects the whole snapshot as a unit mismatch.
- Sample size and malformed data are handled differently on purpose: a structurally broken row rejects the whole snapshot, while a well-formed row with either game count below `MLB_LIVE_PARK_MIN_GAMES` drops only that park so the static table covers it. The road count is checked too because it is the baseline the home rate divides by.
- There is deliberately **no pair rule** here, unlike the team-strength providers: a park factor is a property of one venue both teams play in, so only the home team is looked up and no home-vs-away comparison can be distorted by a mixed source. Provenance is recorded as `custom.park_source` (`live_provider` / `static_table`). The `BaseballEngine` `park` formula and weight are unchanged.
- HR park factors and batter-handedness park splits remain open: the first needs a new engine factor and weight, and the second needs lineup batter handedness the adapter does not have — only the probable starter's `pitchHand`.
- Production activation requires a licensed provider returning the documented contract; see `docs/dev/mlb-live-park-provider-contract.md`.

### NHL true 5v5 shot-quality provider (P1-H1)

- `nhl_live_xg_service`: opt-in configured 5v5 snapshots that replace the club-stats shot proxies, with bearer authentication, `http`/`https`-only endpoints, season-year query resolution, bounded responses, strict validation, duplicate-team rejection, per-URL caching of valid snapshots only, and no-request behavior when disabled or unconfigured.
- The provider must publish 5v5 ice time plus **actual expected goals and/or actual corsi event counts**; xGF/60 and CF% are computed from those inputs rather than read from the payload, so a pre-computed rate with no sample is rejected instead of trusted. Each metric group is optional but must arrive complete — a half-supplied corsi pair or a row with no measurement rejects the snapshot. A computed `xgf_per_60` outside `[1.0, 4.5]` or corsi share outside `[0.30, 0.70]` rejects the whole snapshot.
- Sample size and malformed data are handled differently on purpose: a structurally broken row rejects the whole snapshot, while a well-formed row below `NHL_LIVE_XG_MIN_TOI_MINUTES` drops only that team so the club-stats proxies cover it.
- The pair rule applies per metric — a metric is written only when both sides carry it live, because `HockeyEngine` consumes a home-vs-away share and pairing a measured 5v5 rate against a shots-on-goal proxy would manufacture a spurious edge. Because the engine prefers corsi over expected goals, a measured-xG-only pair clears `corsi_pct_{home,away}` so the proxy cannot shadow real data; the `attack_share` formula, coefficients, and weight are unchanged. Provenance is recorded as `custom.skating_source` (`live_provider` / `club_stats_proxy` / `soft_form`).
- Production activation requires a licensed provider returning the documented contract; see `docs/dev/nhl-live-5v5-provider-contract.md`.

### NBA dynamic-season efficiency provider (P1-B4)

- `nba_live_ratings_service`: opt-in configured season efficiency snapshots that replace the static 30-team ORtg/DRtg table, with bearer authentication, `http`/`https`-only endpoints, season-year query resolution, bounded responses, strict validation, duplicate-team rejection, per-URL caching of valid snapshots only, and no-request behavior when disabled or unconfigured.
- The provider must publish points, points allowed, and **true possession counts**; ORtg/DRtg are computed from those counts rather than read from the payload, so a pre-computed rating with no possession sample is rejected instead of trusted. A computed value outside `[80, 140]` points per 100 rejects the whole snapshot as a unit mismatch.
- Sample size and malformed data are handled differently on purpose: a structurally broken row rejects the whole snapshot, while a well-formed row below `NBA_LIVE_RATINGS_MIN_POSSESSIONS` drops only that team so the static table covers it.
- Both sides always come from one source, recorded as `custom.ratings_source`. `BasketballEngine` consumes the ORtg−DRtg differential, so pairing a live season level against a static multi-year level would manufacture a spurious edge; one live side therefore falls back to static for both. The `net_rating` formula and weight are unchanged.
- Production activation requires a licensed provider returning the documented contract; see `docs/dev/nba-live-ratings-provider-contract.md`.

### NBA live availability provider (P1-B1)

- `nba_live_injury_service`: opt-in configured NBA availability snapshots with bearer authentication, `http`/`https`-only endpoints, bounded responses, strict team/absence validation, duplicate-team rejection, per-URL in-memory caching of valid snapshots only, and no-request behavior when disabled or unconfigured.
- Only `out`, `inactive`, and `suspended` count as absent; `questionable`, `probable`, and `day-to-day` describe a player expected to feature and are ignored. Role tiers and the impact formula stay in `app/sports/basketball/nba_injury.py`, so live and static values come from the same arithmetic, and an unrecognized tier falls through to that module's documented bench default.
- NBA injury enrichment now prefers a reached provider and records `custom.injury_source_{home,away}` as `live_provider` or `static_table`. A disabled provider, transport failure, rejected snapshot, service exception, or a provider silent on that team all degrade to the static Out table; when neither source has a value no injury key is written.
- Production activation requires a licensed provider returning the documented contract; see `docs/dev/nba-live-injury-provider-contract.md`.

### Football international match-day schedule density (P1-F2)

- National-team schedule density now folds in the real international match days recorded by the repository's international results CSV — qualifiers, friendlies, and continental fixtures — which the kernel never carries because it holds tournament fixtures only.
- Cross-source duplicate protection uses the calendar date alone: a national team plays at most once a day, so a date already present in kernel history is the same match and is skipped. No fixture-ID compatibility is assumed between the two sources, and the fixture's own date is never counted as a prior match.
- The existing `matches_merged_7d_*` / `matches_merged_3d_*` keys are filled, with `custom.matches_intl_7d_*` and `custom.schedule_intl_source` added as provenance only. Club fixtures never consult the CSV, a failed lookup preserves the kernel counts, and MultiFactor formulas plus the default-OFF `FOOTBALL_SCHEDULE_MERGE_ENABLED` gate are unchanged.

### Football multi-source weather consensus (P1-F7)

- `football_live_weather_service`: opt-in second, independently configured weather source with bearer authentication, bounded responses, strict temperature/condition validation against the shared vocabulary, its own in-memory cache, and no-request behavior when disabled or unconfigured.
- The live weather layer now reads every configured source best-effort and merges them deterministically: a single source is passed through unchanged, two agreeing sources average the temperature, and a temperature gap beyond the tolerance keeps the primary reading. Either source failing degrades to the other; both failing still degrades to static climate.
- `custom.weather_source_count` and `custom.weather_agreement` are provenance-only additions; the `weather_temp_c` / `weather_condition` feature contract, the fill order, the clamp band, the horizon gate, and MultiFactor formulas/weights are unchanged.
- Production activation requires a licensed provider returning the documented contract; see `docs/dev/football-live-weather-secondary-provider-contract.md`.

### Football combined H2H sources (P1-F4)

- Historical CSV and kernel H2H now expose current-fixture-home meeting records, merge them before aggregation, and retain valid data when either source is unavailable.
- Cross-source duplicate protection intentionally uses only the mutually available date, current-home scoreline, and hosting designation; it makes no fixture-ID compatibility assumption. Deduplication runs before the existing 20-match cap.
- Neutral CSV fixtures do not enter the same-venue H2H subset. Existing rate fields, venue-split flag behavior, and MultiFactor formulas/weights are unchanged.

### Football live availability impact weighting (P1-F3)

- `football_live_availability_service`: opt-in configured player-availability snapshots with strict team/absence validation, actual minutes/value-share inputs, bounded bearer-authenticated requests, in-memory cache, and no-request behavior when disabled or unconfigured.
- Football injury enrichment now tries a complete contextual availability impact before API-Football, static-table, and World Cup-fact fallbacks. The existing role-based impact remains the baseline and the MultiFactor injury formula/weights are unchanged.
- Production activation requires a licensed provider returning the documented contract; see `docs/dev/football-live-availability-provider-contract.md`.

### Football live schedule density provider (P1-F2)

- `football_live_schedule_service`: opt-in configured, read-only fixture-history snapshots with bearer authentication, bounded responses, strict fixture validation, historical cutoff filtering, in-memory caching, and no-request behavior when disabled or unconfigured.
- Schedule density keeps kernel fixtures authoritative and falls back to the live provider only when kernel history is empty or unavailable. Cross-competition fallback preserves competition-scoped alias resolution; no fetched fixtures or predictions are written to the database.
- Production activation requires a licensed conforming provider URL/key; see `docs/dev/football-live-schedule-provider-contract.md`. Existing 7-day/3-day windows, current-match exclusion, and MultiFactor formulas remain unchanged.

### Football live schedule density provider (P1-F2)

- `football_live_schedule_service`: opt-in configured, read-only fixture-history snapshots with bearer authentication, bounded responses, strict fixture validation, historical cutoff filtering, in-memory caching, and no-request behavior when disabled or unconfigured.
- Schedule density keeps kernel fixtures authoritative and falls back to the live provider only when kernel history is empty or unavailable. Cross-competition fallback preserves competition-scoped alias resolution; no fetched fixtures or predictions are written to the database.
- Production activation requires a licensed conforming provider URL/key; see `docs/dev/football-live-schedule-provider-contract.md`. Existing 7-day/3-day windows, current-match exclusion, and MultiFactor formulas remain unchanged.

### Football live referee statistics (P1-F8)

- `football_live_referee_service`: opt-in configured provider for genuine referee season home-win rates, with bounded responses, strict validation, normalized referee matching, in-memory cache, and no-request behavior when disabled or unconfigured.
- Referee enrichment now preserves explicit fields first, then uses a named live row (`referee_source=live_provider`), then the static bias map. World Cup does not call the configured club provider; MultiFactor formulas and weights remain unchanged.
- Production activation still requires a licensed conforming provider URL/key; see `docs/dev/football-live-referee-provider-contract.md`.

### Football live style statistics (P1-F6)

- `football_live_style_service`: opt-in configured provider for genuine possession, shots/90, and PPDA season snapshots, with bounded responses, strict validation, normalized club matching, in-memory cache, and no-request behavior when disabled or unconfigured.
- Football enrichment now uses a complete live pair first (`style_source=live_provider`), then the existing complete static pair, then the form-share possession proxy. World Cup does not call the configured club provider; MultiFactor formulas and weights remain unchanged.
- Production activation still requires a licensed conforming provider URL/key; see `docs/dev/football-live-style-provider-contract.md`.

### Football live injury and true-xG sources (P1-F3 / P1-F5)

- `football_live_injury_service`: opt-in API-Football league/season injury snapshots with bounded responses, normalized club matching, valid-snapshot cache, and `injury_source_*` provenance. Live unavailability degrades to the existing static table, then World Cup facts; MultiFactor injury formula/weight remains unchanged.
- `football_live_xg_service`: opt-in, configured true-xG provider with a strict normalized season-snapshot envelope; rejects provider errors, malformed/duplicate teams, non-finite/out-of-range values, and proxy metrics. Both club sides must resolve from one valid live snapshot before enrich writes `xg_source=live_provider`.
- xG fallback remains live complete pair → static complete pair → goals-per-game proxy. World Cup does not call the configured club xG source. No provider URL/key is enabled by default; production activation requires a licensed conforming endpoint, documented in `docs/dev/football-live-xg-provider-contract.md`.

### Football schedule density across competitions (P1-F2)

- `team_aliases.comparison_key(name, competition)`: the alias-resolved comparison key P1-F1/F4 kept private to `club_form` is now shared, so the density path and the form path cannot drift apart. `club_form` delegates; behaviour unchanged
- `_merged_fixture_history`: fixtures across all of `FactorRegistry._FOOTBALL_COMPETITIONS`, not just the current one. Density counts were previously scoped to a single competition, so a club playing UCL midweek and league at the weekend measured 1 match in 7 days instead of 2 — a directional bias, since the clubs with extra fixtures are the strong ones
- Each row resolves against **its own** competition, because a few abbreviations collide across tables (`CEL` is laliga's Celta Vigo but ucl's Celtic; likewise `ESP`, `POR`). Names outside the tables fall back to the existing string compare, so coverage never regresses
- New `custom.matches_merged_7d_{home,away}` and `matches_merged_3d_{home,away}`; `matches_last_7d_*` and `schedule_congested_*` keep their current values
- MultiFactor rest factor reads congestion from the merged 7-day counts and adds a 3-day short-turnaround tier at the back-to-back magnitude (0.03) rather than the congestion one (0.015); tiers stay mutually exclusive. New `FOOTBALL_SCHEDULE_MERGE_ENABLED` (default OFF) reproduces the previous output bit-for-bit. Data-side keys are written regardless so the distribution can be inspected before enabling
- `rest_form.py` untouched — it is shared with the nba/mlb/nhl adapters and the backtest loader, so resolution lives on the history side where `matches_in_window_as_of` has exactly one caller

### Football form / H2H deepening (P1-F1 / P1-F4)

- `club_form`: alias-aware name matching. Both sides resolve through `TEAM_ALIASES` scoped to one competition (`BOS` is nba's Celtics and mlb's Red Sox, so cross-competition resolution is not attempted); either side failing to resolve falls back to the old normalized-string compare, so the change is purely additive. An absent or unregistered competition disables the layer entirely. Previously `Man City` never matched a stored `Manchester City`, and the miss was silent — lookup returned None, enrich skipped the write, and the engine reweighted the absent factor
- `h2h_from_kernel`: self-pairing check moved after alias resolution, so `("Spurs", "Tottenham")` is rejected instead of counting one club against itself
- `weighted_points_form_rate(results, half_life=5.0)`: recency-weighted points rate on the same [0,1] scale as `points_form_rate`; weight `0.5 ** (i / half_life)`, per-match W=1.0 / D=1/3 / L=0.0, non-W/D/L entries dropped rather than scored as losses. `points_form_rate` untouched
- `team_form_from_kernel` additionally returns `recent_results` + `form_rate_weighted`; enrich prefers the weighted rate and falls back to flat. The world-cup CSV path has no per-match sequence, so it keeps the flat rate
- `h2h_from_kernel` additionally returns `home_venue_{matches,home_wins,draws,away_wins}` — the subset the current home team also hosted; enrich writes `custom.h2h_home_venue_{matches,win_rate,draw_rate}`
- MultiFactor h2h factor blends overall with same-venue by `alpha = min(1, n/4)`; new `FOOTBALL_H2H_VENUE_SPLIT_ENABLED` (default OFF) reproduces the previous output bit-for-bit. Data-side keys are written regardless so the distribution can be inspected before enabling

### Football static referee home-bias (P1-F8)

- `football_referee.bias_for_referee`: code-local soft home_bias by normalized referee name (top leagues + UCL-common)
- Adapter `enrich_referee_features`: pass-through rate/bias first; static fill writes `referee_home_bias` + `referee_source=static_map`
- MultiFactor referee formula/weight unchanged; true referee stats API/DB still pending

### Football club geo travel + venue altitude + static climate (P1-F7)

- `team_geo`: club city table first for football leagues, national fallback; sparse `altitude_m_for_team` (≥1500 m venues)
- Adapter `enrich_altitude_features`: pass-through first, static fill-only when missing (`altitude_source=static_table`); existing travel_between_teams picks up clubs
- Adapter `enrich_weather_features`: pass-through first, static climate fill-only when temp and condition both missing (`weather_source=static_climate`); `football_weather.climate_for_home` city×month soft priors
- `football_weather.live_weather_for_match`: Open-Meteo-style keyless JSON forecast; horizon gate (default 72 h), in-memory TTL cache keyed (lat, lon, date), httpx with configurable timeout; returns None on any failure (never raises)
- Adapter weather selection order: env explicit (zero-safe) → `live_forecast` → `static_climate`; 5 new optional config keys `FOOTBALL_LIVE_WEATHER_*` (off by default — unset == today's behavior)
- MultiFactor travel/altitude formulas, ≥1500 m gate, weights unchanged

### Football static style stats (P1-F6)

- Soft code-local per-club possession / shots / PPDA table (`football_style.stats_for_team`); when **both** sides resolve, overwrite form-share possession proxy and write shots/ppda with `custom.style_source=static_table`. MultiFactor soft possession path unchanged. True stats API still pending.

### Football static xG table (P1-F5)
- `football_xg`: code-local attack xG/90 by normalized club name (big-five + UCL-ish)
- Enrich: goals_per_game proxy first; both-sides static hit overwrites `xg_*` + `xg_source=static_table`
- MultiFactor xG formula/weight unchanged; true xG API still pending

### Football club H2H from kernel (P1-F4)
- `h2h_from_kernel`: pairwise meetings, current-home perspective, as-of + competition filter
- Enrich: historical CSV first; kernel only when historical empty; same `h2h_*` rate fields
- MultiFactor h2h formula/weight unchanged

### Football form points rate (P1-F1)
- `points_form_rate`: form_* = (3W+D)/(3N) in [0,1] when played > 0
- Adapter enrich single write site; historical CSV + club kernel both benefit
- MultiFactor form weight/formula unchanged; US-sport rest_form stays win-rate

### Football static injury impact (P1-F3)
- `football_injury`: Out-only role weights (star/starter/rotation/bench) → `injury_impact` in [0,1]
- Enrich dual-writes `player` + `custom` when static table has Out rows; missing → None
- WC player-status source remains fallback only when static is None; MultiFactor formula/weight unchanged

### Football schedule density window counts (P1-F2)
- `matches_in_window_as_of`: prior fixtures in 7-day window (includes unfinished)
- Football enrich injects `matches_last_7d_*`; `schedule_congested_*` from count≥2 when known
- Rest ≤ 2 remains fallback only when count unknown; b2b still rest ≤ 1; MultiFactor weights unchanged
- MultiFactor rest: schedule_congested_* key present is authoritative (count <2 no longer OR'd with rest<=2)

### NBA static team ORtg/DRtg for net_rating (P1-B4)
- `nba_team_ratings`: 30-franchise static ORtg/DRtg (+ Clippers alias)
- Adapter injects four custom fields only when both sides resolve; omits otherwise
- Removes match-invariant ortg/drtg/pace/tpct stubs; BasketballEngine formula/weight unchanged

### NBA static injury impact (P1-B1)
- `nba_injury`: Out-only role weights (star/starter/rotation/bench) → `injury_impact` in [0,1]
- Adapter dual-writes `player` + `custom` when static table has Out rows; missing → None
- FeatureBuilder passthrough; BasketballEngine formula/weight unchanged

### MLB full 30-team static park factors (P1-M2)
- Expand `_PARK_FACTORS` to all 30 franchises (+ Athletics alias)
- Runs-only soft signal; engine park formula/weight unchanged
- Coverage + range + Coors/low-park direction unit tests

### MLB platoon splits vs starter hand (P1-M4)
- Team hitting `statSplits` sitCodes `vl,vr` → season OPS vs LHP/RHP
- Probable SP `pitchHand` from `/people` → `custom.pitcher_hand_*`
- Home hitters use away SP hand; away hitters use home SP hand
- Inject `platoon_ops_*` + clamped `platoon_advantage_home` for BaseballEngine soft factor
- Spot check 2026-07-24 COL@MIL: Sugano(R)/Drohan(L) → OPS 0.753 vs 0.706, adv +0.047

### MLB outdoor weather from game feed (P1-M3)
- `parse_mlb_weather` / `parse_wind_mph` / F→C from v1.1 `gameData.weather`
- Single `_fetch_game_context` call reuses feed for SP + weather + venue
- Inject `weather_temp_c` / `weather_wind_mph` / condition into custom + environment
- Open-roof only for engine soft factor; dome/closed drops temp/wind

### MLB real starting pitcher + bullpen ERA (P1-M1)
- Game feed uses official `/api/v1.1/game/{pk}/feed/live` (v1 404s)
- Probable pitchers from feed `gameData.probablePitchers` (+ schedule hydrate fallback)
- `parse_pitcher_person` → starter name/ERA/WHIP into `custom.pitcher_*`
- Team pitching totals → `team_era_*`; relief-only IP-weighted ERA → `bullpen_era_*`
- Team-id map for all 30 franchises; graceful fallback to league-avg 4.10

### NHL club-stats attack rates for attack_share (P1-H1)
- `summarize_club_rates`: GF/GA/SF/SA per game + shot_share (corsi-like)
- Adapter one club-stats fetch per side → goalie + rates (no double HTTP)
- `team_gf_*` / `team_ga_*` / soft `xg_for_*` (0.09×SF) / `corsi_pct_*` from live stats
- Form-shaped GF/GA remains fallback when club-stats empty

### NHL primary goalie save% from club-stats
- `fetch_nhl_club_stats` + `pick_primary_goalie` (most `gamesStarted`)
- `NHLAdapter._fetch_starting_goalies` no longer stubbed; maps team → abbrev
- Roster path prefers abbrev (`/v1/roster/UTA/current`); HTTP `trust_env=False`
- Goalie factor now receives real save% when club-stats available

### NHL Utah franchise team-name canonicalization
- Collapse `Arizona Coyotes` / `Utah Hockey Club` / `Utah Utah Hockey Club` → `Utah Mammoth`
- Fix placeName+commonName double-prefix (`Utah` + `Utah Hockey Club`)
- Aliases + geo for Mammoth variants; re-seeded NHL Elo (**33** teams, continuous franchise key)
- Spot-check: Utah fixtures resolve Elo both sides; hockey engine quality `real`

### Elo HFA/K runtime wiring (Phase 9 follow-up)
- `kernel/elo_params_resolve.py`: applied Optuna `elo_params` overlay settings
- Engines (NBA/MLB/NHL) use resolved HFA; NBA playoff split only when no applied
- `_elo_params_for_sport` / `seed_elo_ratings` use applied K/carry/initial
- `apply(..., reseed_elo=True)` re-seeds Elo + `reset_kernel_singleton()`
- Re-seeded ratings for applied NBA **5** / MLB **6** / NHL **7**
- Holdout: applied Elo beats settings Elo by **+3.0 / +1.2 / +3.3pp** (NBA/MLB/NHL)
- `scripts/eval_applied_params.py` for applied vs settings Elo comparison
- Re-applied NBA **5** so registry `elo`/`form` match candidate (were stale 0.50/0.15)
- `EngineRegistry.select(auto)` prefers sport-specific engines over `elo_odds` `*`

### Rest/form as-of features (Phase 9 follow-up)
- `sports/_shared/rest_form.py`: leakage-safe form L10 + rest days
- `match_loader` uses enrich (no flat defaults)
- NBA/MLB/NHL adapters: unknown rest → None; form as-of kickoff
- Re-ran Optuna 80 trials on real rest/form; **applied** NBA **5** / MLB **6** / NHL **7**
  (acc 70.24% / 54.22% / 62.35%); prior flat-feature applied rows archived
- API restarted so FactorRegistry loads new `source=optimized` weights

### Phase 9 Optuna offline optimize + apply (P1-A3 / P1-A4)
- Ran 80-trial TPE for NBA/MLB/NHL on chronological 80/20 split
- First pass (flat rest/form): NBA **69.95%**, NHL **63.02%**, MLB **53.27%** → applied 4/3/2
- `save_candidate` upserts the existing `candidate` row in place (UNIQUE sport/competition/status)
- Apply writes factor weights + (after Elo wiring) re-seeds HFA/K from applied row;
  engine extra factors (net_rating/injury/park/etc.) keep prior defaults and join fusion
- CLI: `python scripts/run_phase9_optimize.py --sport all --n-trials 80`

### NBA/MLB historical ingest + MLB competitive filter (Phase 9 / P1-A1)
- NBA ingest **2023-24** (1319) + **2024-25** (1321); scored/results **3962**; Elo **30** teams
- MLB ingest **2024** (2473) + **2025** (2477) competitive only; scored/results **6803**; Elo **30** teams
- `parse_mlb_game`: skip non-competitive `gameType` (S/A/E…); keep R + postseason
- Canonicalize `Oakland Athletics` → `Athletics` for stable multi-season Elo keys

### NHL historical ingest + Elo seed (Phase 9 / P1-A1)
- Ingested NHL **2023-24** (1511) + **2024-25** (1504) via club-schedule season keys
- `kernel_match_results` scored/results **3014**; `kernel_elo_ratings` **34** teams
- Season label `"YYYY-YY"` → NHL API `YYYYYYYY` in HistoricalDataIngestor
- RUNBOOK: Phase 9 ingest + `/backfill-seed` + `scripts/seed_sport_elo.py`

### NHL real sync verified (local)
- Club-season bulk + nested name parse → **1409** fixtures (2026-27)
- d45 empty mid-summer OK; d60 shows Sep preseason openers
- Transient SSL/timeout retries on api-web.nhle.com

### NHL real sync path (Phase 5 / api-web.nhle.com)
- Club-season bulk fetch (`/v1/club-schedule-season/{abbrev}/{season}`) + dedupe
- Parse official nested `placeName`/`commonName` + `team.score`; `OFF` finished
- Prefer upcoming season key then fallback; follow redirects; RUNBOOK steps

### MLB real sync verified (local)
- Nested team parse fix → `synced=2814`; d7/d45 show live 2026 games

### MLB parse fix for official schedule shape
- Read `teams.{home,away}.team.name` (+ score) from statsapi.mlb.com
- Flat name shape still works for unit fixtures

### MLB Phase 5 prep + real sync path
- Default season label → 2026; sync uses current calendar Mar–Nov window
- RUNBOOK steps (no vendor key); early-spring empty → prior year fallback

### NBA real sync verified (local)
- With key + PHASE4: `synced=1322` (2025-26 fallback); d45 empty mid-summer OK
- 429 backoff fix required for free-tier full pagination

### balldontlie 429 resilience for NBA sync
- Page client: longer interval, 429 backoff retries, partial page return
- Adapter treats rate-limit as season fallback trigger

### NBA season roll + fallback (Phase 4)
- Preferred balldontlie season **2026** (2026-27); empty → try **2025**
- RUNBOOK: PHASE4_NBA + BALLDONTLIE sync/list steps
- Real sync still requires local `BALLDONTLIE_API_KEY`

### RUNBOOK: multi-league sync interpretation
- Table for synced vs days_ahead empty; BL1/UCL vendor lag notes

### Phase2 multi-league real sync fixes
- Bundesliga + Ligue 1 season roll had stayed on 2025 — fixed to **2026**
- UCL: preferred 2026 404 → fall back one season (often still finished campaign)
- Live sync verified: epl/laliga/seriea/ligue1 have 2026-27 openers; BL1/UCL finished-only until vendor

### Football season roll + matches days_ahead
- EPL/UCL/five-league Football-Data season → **2026** (`2026-27`)
- `GET /api/predictions/matches?days_ahead=0..60` (default 0 = today only)
- Betting landings poll with `daysAhead: 45` so openers appear mid-summer break

### Hub flag strip / runtime status tests
- Vitest for static catalog fallback, LoL dry-run flags, blocked vendor runtime line

### Fix betting status `lol` on Kernel ON + hub runtime
- `build_status_payload` always includes `lol` (was dropped when Kernel ready)
- Hub wires `useBettingStatus`: prefixes + LoL vendor effective/blocked
- `verify_local_stack` summarizes catalog/status without secrets
- ESPORTS_BOUNDARY links ADR-005; RUNBOOK drops fake `EPL_DATA_ENABLED`

### RUNBOOK: LoL D4 env + status `lol` diagnostics
- Ops table for PHASE_LOL / dry-run / vendor shell / grace
- Resolver matrix + smoke curls for catalog flags and status.lol
- Document no secrets in status responses

### LoL schedule source resolver guard
- `resolve_lol_schedule_source()`: only `null`/`dry_run` runtime; `grid`/`pandascore`/unknown → Null + blocked
- `LolAdapter()` uses resolver by default; status exposes effective vendor + blocked reason

### ADR-005 D4 LoL vendor config shell
- Env: `LOL_SCHEDULE_VENDOR`, `LOL_VENDOR_API_BASE`, `LOL_VENDOR_API_KEY`, `LOL_SETTLE_GRACE_HOURS` (defaults null/empty/6)
- `GET /api/betting/status` includes `lol` diagnostics (no secrets, `production_http_client_ready=false`)
- No PartnerHttp client; commercial vendor ids are config-only until GATES

### LoL dry-run observability (status + hub + landing)
- Catalog/status flags: `lol_dry_run_import`, `lol_dry_run_path_configured` (no path/secrets)
- Status hint mentions `lol-` when PHASE_LOL on/off
- Hub flag strip shows LoL + dry-run; `/sports/betting/lol` ops panel (no fake markets)

### ADR-005 LoL vendor selection + GATES fill
- Preferred production source: GRID-class official partner (not OA CS2/Dota)
- PandaScore-class optional for odds enrichment only
- GATES: P1/P4/P5/P7 closed; P2/P3 partial; P6 legal open
- competition code `lol_msi` added


### EngineRegistry sport-aware auto select
- `select("auto", sport=…)` filters by `supported_sports` (and resolves sport from competition)
- `PredictionKernel.predict` passes match sport into registry
- LoL no longer loses auto to football engines


### LoL Kernel dry-run stack (ADR-004 Tasks 0-9)
- `PHASE_LOL_ENABLED` / `LOL_DRY_RUN_*` flags
- LolAdapter, market-only engine, catalog entry
- `docs/dev/lol/GATES.md` + RUNBOOK LoL section

### LoL esports implementation plan (ADR-004)
- Plan: `docs/superpowers/plans/2026-07-22-lol-esports-adapter.md`
- Dry-run stack + market-only engine; production HTTP blocked on GATES


### ADR-004 Esports/LoL Accepted
- First title: LoL; sport `lol`, prefix `lol-`
- Production adapter blocked on official/partner API gates
- Design note: `docs/superpowers/specs/2026-07-22-esports-lol-adapter-design.md`
- ESPORTS_BOUNDARY synced; no adapter code in this change


### Kernel list ↔ 竞猜联赛闭环
- `/sports` CompetitionChips for kernel leagues (sport-scoped)
- Match list badge deep-links to `/sports/betting/{id}` when catalog known
- Catalog helpers: normalizeCompetitionCode, getCompetitionByCode, kernelCompetitionChips


### Operator key deep-link for 竞猜落地页
- `#operator-key` opens nav OperatorKeyControl edit form
- Landing listens for credentials event so 同步赛程 appears after authorize
- Link「打开顶部授权」from landing when write key missing


### 竞猜落地页：同步按钮补全 + 今日赛程预览
- Wire operator 同步赛程 button (was imported but incomplete)
- Match preview list (up to 5) with deep links
- Runtime adapter prefix from GET /betting/status


### GET /api/betting/status diagnostic
- Read-only flags + MultiAdapter `registered_prefixes` (no write key)
- Hub shows runtime adapter list when status is available
- verify_local_stack + RUNBOOK smoke include `/betting/status`


### Operator schedule sync + esports ADR-004
- `POST /api/predictions/schedule/sync` (write key; optional sport/competition)
- MultiAdapter.sync_schedule accepts ScheduleFilter short-circuit
- Landing: 同步赛程 button when operator key present
- Catalog flags aligned to PHASE2 / PHASE4_NBA / PHASE5_*
- ADR-004 esports data adapter (Proposed)


### 竞猜落地页 match count + RUNBOOK 联赛启用
- Client `CompetitionLanding`: live catalog adapter status + today's match count via `useMatches`
- `useMatches(null)` skips fetch (coming_soon / non-kernel)
- RUNBOOK: Betting / 联赛赛程 flags + smoke curls


### 竞猜 hub live catalog merge + adapter badges
- Hub is client: merges static catalog with `GET /api/betting/catalog`
- Cards show adapter_likely badges; flag strip (Kernel / EPL / 五大联赛)
- Offline fallback keeps static catalog when API is down


### MultiAdapter competition/sport schedule short-circuit
- Shared `kernel/competition_codes.py` (aliases + prefix maps)
- `ScheduleFilter.sport` optional field
- `MultiAdapter.fetch_schedule` only hits matching league adapters
- Catalog payload: `flags` + per-competition `adapter_likely`
- list_matches passes sport/competition into ScheduleFilter early


### 竞猜 competition 过滤 + catalog API
- `GET /api/predictions/matches?competition=` (+ aliases: pl→epl, wc→world_cup, serie-a→serie_a)
- `GET /api/betting/catalog` and `/catalog/{id}` from `kernel/betting_catalog.py`
- FE: `useMatches({ sport, competition })`, Kernel list `?competition=` chip, landing/kernel links
- `docs/dev/ESPORTS_BOUNDARY.md` — no fake esports markets until data sources exist


### 竞猜模块 IA 起步
- `lib/betting/competition-catalog.ts`: static catalog (world cup, big-five aliases, NBA/MLB/NHL, esports placeholder, tool links)
- `/sports/betting` hub: sectioned cards + dual-track note
- `/sports/betting/[competitionId]`: competition landing (kernel link / world-cup / coming_soon, no fake odds)
- Nav: 竞猜中心 first in Sports group; world-cup nav entry removed (route kept)
- Kernel list: `?sport=` query sync; banners link 竞猜中心


### Ops runbook + local smoke + CI typecheck
- RUNBOOK: Prometheus series table, Grafana import path, DRIFT_ALERT_* /
  SCHEDULER_FAILURE_ALERT_* dispatch notes, matching-eval CLI snippet
- `verify_local_stack.py`: probe `/metrics`, sport-markets pending,
  quality-metrics summary/drift/alerts; hint lines for bridge + alert flags
- CI frontend job: run `npm run typecheck` before tests (catches casing/import drift)


### Frontend typecheck fixes + alert env docs
- World Cup page: import missing `SportTrackBanner`
- FuturesDashboard: import missing `ScrollableTable`
- predictions-api: import `getOperatorApiKey` / `getOperatorId`
- match-detail-panel: use `prediction.engine` (not non-existent `engine_name`)
- app-nav HotNewsTicker: annotate `display`/`loop` as `TickerItem[]` so fallback items keep optional `delta`/`href`
- Align edge/settlement/realtime imports to on-disk lowercase filenames (TS1261 casing)
- `.env.example`: document `DRIFT_ALERT_*` and `SCHEDULER_FAILURE_ALERT_*` (default OFF)


### Three-layer matching eval set + scheduler failure alerts (P1-SB1 / E8)
- `scripts/eval_sport_market_matching.py`: rule / LLM / manual matcher eval against labeled JSONL with precision / recall / F1
- `data/eval/sport_market_link_eval.sample.jsonl`: 6-case sample covering rule hit / partial / LLM hit / reject / pending / manual
- `app/services/scheduler_failure_alert_dispatcher.py`: webhook + Sentry breadcrumb + log, gated by `SCHEDULER_FAILURE_ALERT_ENABLED` (default OFF), per-job_name cooldown
- `scheduler.py._finish_run` calls dispatcher on failed status (metrics + Sentry capture unchanged)
- `docs/ops/grafana/pmrf-overview.json`: Grafana dashboard for scheduler health / drift / overlay latency / LLM cost / decision quality
- Config: `SCHEDULER_FAILURE_ALERT_ENABLED`, `SCHEDULER_FAILURE_ALERT_WEBHOOK_URL`, `SCHEDULER_FAILURE_ALERT_COOLDOWN_SECONDS`
- Tests: `test_eval_sport_market_matching.py` (16/16), `test_scheduler_failure_alert_dispatcher.py` (7/7)


### Pending auto-verify UI + registry altitude + conditional cal API
- PendingReviewQueue: dry-run / apply auto-verify buttons
- Football factor registry seeds `altitude` (soft-only list)
- `POST /predictions/calibration/conditional` fits conf+stage buckets


### PPDA soft + stage calibration + auto-verify (P1-F6 / V5 / V2)
- MultiFactor possession channel accepts `ppda_home/away` (lower = better press)
- Stage-bucket calibration keys `{comp}#s_{regular|knockout}`; meta.stage in explanation
- `POST /sport-markets/pending/auto-verify` + store helper (flag OFF by default)


### Edge factor attribution (P1-V3)
- `extract_factor_drivers` ranks prediction explanation by outcome impact
- Edge detect/latest attach `factor_drivers` + `factor_attribution`
- Edge detail FE chips; recommendation rationale may include 主导因子


### Reliability ECE + market price audit (P1-X1 / P1-V1)
- `compute_reliability_bins` returns `ece`, `max_calibration_error`, `sample_count`
- FE ReliabilityChart shows ECE badge
- `GET /sport-markets/links/{id}/audit` and `/matches/{id}/audit` price-path summary
- Match detail `MarketPriceAuditPanel`


### Soft totals all sports + rec guardrails + altitude (P1-O1/SB3/F7)
- Basketball/Hockey/Baseball engines emit soft O/U via `soft_totals_from_scores`
- Sport recommendations: `guardrail_flags` / `policy_notes` (stale/liquidity/trust demote)
- Football multi-factor soft `altitude` when `venue_altitude_m` ≥ 1500


### Soft totals/BTTS + baseball platoon (P1-O1 / P1-M4)
- `soft_totals_btts_analysis` (independent Poisson) in multi-factor + EloOdds `betting_analysis`
- FE `SoftTotalsPanel` on match detail
- BaseballEngine soft `platoon` from `platoon_ops_*` / `platoon_advantage_home` (weight 0.05)


### Model vs market disagreement diagnosis (P1-V3)
- Sport recommendation rationale appends 分歧诊断 (gap / trust / liquidity / stale)
- Edge detail FE shows diagnosis line when |Δ| ≥ 3pp (or high priority)


### Football rest density + injury custom (P1-F2 / P1-F3)
- Adapter flags `b2b_*` (rest≤1) and `schedule_congested_*` (rest≤2)
- MultiFactor rest: extra edge penalty for b2b / midweek congestion asymmetry
- Injury factor reads `custom.injury_impact_*` when player fields empty


### Referee feed + conditional calibration apply (P1-F8 / P1-V5)
- Football adapter `enrich_referee_features`: pass-through rate/bias + optional static map
- `KERNEL_CONDITIONAL_CALIBRATION_ENABLED` (default OFF): Kernel applies bucket slope/intercept to home_win then renormalizes
- Annotation in `betting_analysis.conditional_calibration`


### Football referee soft factor (P1-F8)
- FootballMultiFactorEngine: soft `referee` from `custom.referee_home_win_rate` / `referee_home_bias`
- Weight 0.02 (profiles rebalanced); unavailable when no referee stats
- FactorRegistry seed + FE label 裁判倾向


### Conditional calibration by confidence (P1-V5)
- `confidence_bucket` low/mid/high; store rows as `{competition}#c_{bucket}`
- `KernelLearningService.update_calibration_by_confidence` + `get_conditional_calibration`
- Global `update_calibration` best-effort refreshes buckets after fit


### NBA playoff stage HFA (P1-B5)
- BasketballEngine: playoff/postseason uses `NBA_ELO_HFA_PLAYOFF` (default 90) and
  `NBA_HOME_COURT_PLAYOFF` (default 0.55) vs regular 100 / 0.58
- Config env vars: `NBA_ELO_HFA_PLAYOFF`, `NBA_HOME_COURT_PLAYOFF`


### Confidence breakdown API + FE (P1-X3)
- confidence_breakdown() returns decision_strength / completeness / agreement / market_damp
- Injected into FootballMultiFactor / EloOdds / Basketball / Baseball / Hockey via betting_analysis
- FE SportConfidencePanel prefers API breakdown; shows market damp when <1


### Odds traditional vs market summary (P1-O4)
- TraditionalOddsChart: latest implied-prob divergence table (传统−市场, ≥5pp highlight)
- Chinese outcome labels on series titles

### Football possession form proxy (P1-F6 feed)
- Adapter soft-fills `possession_*` from form share when true stats missing


### FactorRegistry soft seeds + sport confidence UI (P1-E2 / P1-X3)
- Seed football multi-factor soft factors: travel/xg/market_value/possession (no global elo/odds)
- NBA/MLB/NHL seeds include travel/injury/net_rating/park/bullpen/weather/attack_share
- Competition aliases: seriea↔serie_a, ligue1↔ligue_1
- NHL adapter: form-scaled GF + xg_for proxy for attack_share
- FE `SportConfidencePanel` on match detail (strength / completeness / agreement)


### Football possession soft factor (P1-F6)
- FootballMultiFactorEngine: soft `possession` from custom possession_* or shots_*
- Weights/profiles rebalanced to include possession (sum ≈ 1.0)

### Hockey attack share (P1-H1)
- HockeyEngine: soft `attack_share` factor (corsi% preferred, else xg_for, else GF proxy)
- Default weights rebalanced; FE label 进攻份额

### Football xG + market value soft factors (P1-F5)
- FootballMultiFactorEngine: soft `xg` (custom.xg_* attack-rate share) and `market_value` (log squad-value ratio)
- Default weights + competition profiles include xg/market_value (sum ≈ 1.0)
- Adapter: cache-only Transfermarkt `get_cached_market_value` → team/custom market_value_*
- FE FactorBreakdownTable labels for xg / market_value
- Tests updated for 9-factor explanation + xG/MV soft behaviour

### Edge review priority soft feedback (P1-O5)
- SportRecommendationService: compute `review_priority` from edge signals; demote act tiers (critical/high); raise risk; rationale prefix
- API `_rec_to_dict` includes `review_priority`; RecommendationCard shows priority badge when non-normal
- Tests: `test_recommendation_priority_soft.py`

### Multi-sport soft factors (P1-B1/B3, P1-M1–M3, P1-H3, P1-F7)
- Shared `app/sports/_shared/team_geo.py`: NBA/NHL/MLB + national football city coords, haversine km, timezone offset, `travel_prob_home`
- NBA/NHL/MLB/football adapters inject `travel_km_away` / `timezone_offset_hours_away` into FeatureSet custom
- BasketballEngine: `travel` + soft `injury` factors (weight redistribute when missing)
- BaseballEngine: wire `park` + `bullpen` (+ soft `weather` temp/wind); MLB adapter bullpen_era proxy from team ERA
- HockeyEngine: `travel` factor for cross-zone (incl. Canada) fatigue
- FootballMultiFactorEngine: new `travel` factor + competition profile weights
- Feature builders pass through `travel_distance_km`; FE factor labels for travel/bullpen/weather
- Edge discrepancies list API: `review_priority` / trust / liquidity + sort by priority then |edge|
- FE Edge table priority column; history payload includes review fields
- Tests: `test_team_geo.py`, `test_sport_factors_travel_park.py`

### Football multi-factor engine (P1-E1)
- Added `FootballMultiFactorEngine` (`football_multi_factor`): fuses elo, odds, form, rest, injury, h2h with missing-factor weight redistribution
- Feature flag `FOOTBALL_MULTI_FACTOR_ENGINE_ENABLED` (default OFF); requires `KERNEL_PREDICTION_ENABLED`
- `FactorRegistry.ensure_competition_factors` seeds form/rest/injury/h2h for football competitions without changing global elo/odds 0.30/0.70
- New `get_competition_weight()` avoids multi-factor fusion picking up EloOddsEngine global weights
- Docs: `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` tracks remaining backlog

### Dixon-Coles / GBM / Ensemble into Kernel (P1-E5–E7)
- `DixonColesEngine` (`dixon_coles`) + `DIXON_COLES_ENGINE_ENABLED`
- `GbmEngine` (`gbm`) wraps legacy LightGBM path + `GBM_ENGINE_ENABLED`
- `EnsembleEngine` (`ensemble`) inverse-Brier fusion + `FOOTBALL_ENSEMBLE_ENGINE_ENABLED`
- All default OFF; register only when Kernel is enabled

### Phase 9 optimization wiring (P1-A3)
- `POST /api/sport-optimization/run` now loads historical matches from kernel DB,
  time-series splits, and runs `ParameterOptimizer.optimize_sync` in a background task
- `app/kernel/backtest/match_loader.py` for DB → backtest match dicts
- CLI: `backend/scripts/run_phase9_optimize.py`

### Situational feature feed + Phase 15 UX (P1-F* / P1-FE*)
- Football adapters enrich form / h2h / rest / xG-proxy after Elo+odds fetch
- Match detail: engine selector, 503 Kernel disabled banner, predict error alert
- Optimization dashboard: NBA/MLB/NHL/all only; Phase 9 disabled banner on 503

### WebSocket URL + Edge detect + club form
- `buildWsUrl`: Next dev (:3000) connects WS to backend :8000; prod same-origin
- RealtimePriceTable: richer columns, disabled/error empty states
- `POST /api/sport-edges/{match_id}/detect` on-demand edge compute (write key)
- EdgeDetailPanel: "重新计算 Edge" button
- Club form fallback from `kernel_match_results` when international CSV misses

### Recommendations / settlements UX + local verify
- Shared `FeatureDisabledBanner` for Phase7 503 states
- OpenDecisionsList: act/watch filters, links to match, empty-path guidance
- SettlementHistoryTable: manual match_id reprocess; row actions
- Match detail: inline recommendation panel + process settlement
- `scripts/verify_local_stack.py` smoke-checks health + key Phase endpoints

### Event decision diagnosis UI (P2-FE10)
- `DecisionReport` types include quality overlays + final_displayed_direction
- DecisionCard expand: calibration status, decision/market/source quality downgrades
- verify script also probes `/api/events/decisions/open` and `/api/events/calibration`

### Calibration copy + quality alert deep links (P2-FE11)
- History AccuracySummary: labels event-layer (EIP) vs Kernel / settlements
- Learning tabs: Kernel scope banner + link to /history
- Anomalies API attaches sample `event_ids` + `href`; AnomalyBanner links to events
- Quality report errors: event_id → /events/{id}

### History segment compare (P2-FE12)
- `GET /api/events/predictions/calibration/buckets` exposes edge×confidence diagnostics
- History page: SegmentComparePanel (领域 / act 类目 / Edge 桶 / 置信度桶 + 交叉表)
- Chart of skill by category remains; table enables sort and side-by-side comparison

### Hot news ticker from real movers (P3-FE13)
- AppNav ticker fetches `/events/movers` and shows live titles + delta (links to event)
- Falls back to placeholder copy when empty/unavailable; label 异动快讯 vs 示例新闻

### Event vs Sport Edge IA (P2-FE4)
- `DomainScopeBanner` cross-links `/edges` ↔ `/sports/edges` with scope copy
- Nav labels: 事件 Edge / 体育 Edge; page titles clarify EIP vs Kernel

### Apply weight diff + learning weights tab (P2-FE5)
- `OptimizedParamsStore.apply` returns `previous_applied` + `weight_diff`
- OptimizationDashboard: last-apply before/after table + per-row weight preview
- Learning: tab「已应用权重」lists status=applied factor/elo weights

### World Cup vs Kernel track (P2-FE6)
- `SportTrackBanner` on `/sports` and `/sports/world-cup` (API path + cross-links)
- Nav: 世界杯 → `/sports/world-cup`; Kernel list active state excludes named hubs

### Trades edge aligned with EIP raw_edge (P2-SB6)
- Simulated trade rows expose `raw_edge` + `directional_edge` (YES→raw, NO→−raw)
- `trade_stats` adds directional mean + documented `edge_definition` (legacy `|raw|` key kept)
- `/trades` UI: definition banner, raw/方向 columns, links to event vs sport edges

### Futures multi-leg coverage (P2-SB5)
- Expanded Kalshi series prefixes (NFL / EPL / NCAAB / conf); longest-prefix match
- `multi_leg_integrity` (legs, Σp, dupes) on fetch + store APIs
- `GET /futures/meta/series` + `/meta/coverage`; FuturesDashboard coverage panel

### A11y + shared form/table styles (P3-FE7)
- `lib/ui-classes` (`inputCls` / `selectCls`) shared by analyze + manual resolve + optimization
- `ScrollableTable` for mobile horizontal scroll; factor/edge/futures/optimization tables
- Sport filter `aria-pressed` / group label; form controls `htmlFor` + `aria-label`

### Backtest results visualization (P3-FE8)
- `ParameterOptimizer.optimize_sync` returns accuracy/Brier/MAE/sample/train-test + weights
- `BacktestResultsPanel`: bar chart (recharts) + metrics table from task.result or candidates
- OptimizationDashboard shows last-run results and candidate comparison

### Operator credentials hardening (P2-FE9 partial)
- `lib/operator-credentials`: sessionStorage-only key/id, mask, clear, change event
- OperatorKeyControl: clear button, session-only copy, masked tooltip
- api/sports client use shared auth header builder; Runbook documents browser key model + BFF residual

### Odds quality weight dampening (P1-E4) + competition profiles (P1-E3 partial)
- `kernel/engines/odds_quality.py`: multiplier from freshness, overround, `custom.liquidity_factor`
- Applied in `EloOddsEngine` and `FootballMultiFactorEngine` (odds weight only; redistributes via fusion)
- Multi-factor competition profiles for epl / laliga / ucl / wc (registry override still wins)
- `kernel/market_liquidity.py`: injects `liquidity_factor` from sport-market links/snapshots into FeatureSet.custom via MultiFeatureBuilder + football adapter
- NBA/MLB/NHL FeatureBuilders + adapters also inject (same helper; MultiFeatureBuilder remains the common path)

### Situational engine (P1-E8)
- `situational_adjust.py`: soft renormalized lifts for knockout / must-win / group pressure (capped)
- `SituationalEngine`: wraps EloOdds (or other base); explainable `situational` contribution
- Flag `SITUATIONAL_ENGINE_ENABLED` (default off); registered before ensemble
- `group_context_bridge`: WorldCupAdapter + football `_shared` inject must_win/pressure into custom

### Confidence + B2B + odds dispersion (P1-X1 / B2 / H2 / O2)
- `confidence.compute_confidence`: strength + completeness + agreement + market damp
- Wired into EloOdds, MultiFactor, Basketball, Baseball, Hockey engines
- NBA/NHL `b2b_*` + rest-factor penalties
- `odds_dispersion_from_books` + TraditionalOddsStore inject via MultiFeatureBuilder
- `GET /predictions/engines/meta` + frontend `useEnginesMeta`
- Multi-factor profiles: serie_a / bundesliga / ligue_1
- MLB park factor (soft) in BaseballEngine + coarse park map in MLB adapter
- Edge `review_priority` (low/normal/high/critical) + API field
- Match detail: engine ZH labels + situational notes banner
- BasketballEngine: net_rating factor (ORtg/DRtg) + restored b2b rest penalty
- Edge UI: review_priority column + detail badges

## v0.4.0 (2026-07-16)

### Sports Prediction OS — Phase 1-13

13-phase architectural evolution from a World Cup-only prediction module into a
multi-sport Prediction OS with a Protocol-based kernel, market bridge, learning
loop, and real-time push. ~134 commits, ~6,872 matches/year coverage (10
competitions: World Cup + 6 football leagues + NBA + MLB + NHL), accuracy
target lifted from ~67% toward 72-75%+.

#### Phase 1 — Prediction Kernel Extraction
- Extracted `backend/app/kernel/` with Protocol interfaces (DataAdapter,
  FeatureBuilder, PredictionEngine, LearningService) and frozen dataclass
  domain model (`domain.py`)
- `prediction_kernel.py` is sport-agnostic — zero imports of `world_cup_*` /
  `epl_*` / `nba_*`
- WorldCupAdapter bridges to existing `world_cup_*` modules internally
- `KERNEL_PREDICTION_ENABLED` feature flag defaults OFF; falls back to legacy
  `world_cup_prediction_pipeline` when false
- New `kernel_` prefix DB tables; existing `world_cup_predictions.db` untouched

#### Phase 2/2b — Football Leagues (UCL → EPL → La Liga → Bundesliga → Serie A → Ligue 1)
- `PHASE2_LEAGUES_ENABLED` flag (OFF); MultiAdapter routes by match_id prefix
  (`wc-` / `ucl-` / `epl-` / `laliga-` / `bundesliga-` / `seriea-` / `ligue1-`)
- Football-Data.org client parameterized for competition codes
- ClubElo.com service with 7-day cache TTL + 1s request interval
- Adapter shared utilities as stateless functions in `_shared.py`

#### Phase 3 — Unified Learning Loop
- Closed loop: outcome → error → calibration → weight update → engine score → next prediction
- `PHASE3_LEARNING_ENABLED` flag (OFF); `FactorRegistry` DB-backed
- `learning_service.py` `compute_error()` and `update_weights()` dynamically
  iterate factor keys (no hardcoded `elo`/`odds`)

#### Phase 4 — NBA Integration + BasketballEngine
- `PHASE4_NBA_ENABLED` flag (OFF); `BALLDONTLIE_API_KEY` empty by default
  (NBAAdapter auto-disabled when not configured)
- NBA Elo self-computed (HFA=100, K_regular=20, K_playoff=30, regression=0.75)
- BasketballEngine: Bradley-Terry binary model, 4 factors
  (elo 0.45 / home_court 0.15 / rest 0.15 / form 0.25)
- MultiFeatureBuilder mirrors MultiAdapter prefix-dispatch pattern

#### Phase 5 — MLB / NHL Integration
- `PHASE5_MLB_ENABLED` / `PHASE5_NHL_ENABLED` flags (OFF)
- MLB: `statsapi.mlb.com` (official free API, no key, 1 req/s)
- NHL: `api-web.nhle.com` (official free API, no key, 1 req/s)
- BaseballEngine: 5 factors (elo 0.30 / home_court 0.10 / rest 0.15 / form 0.20 / starting_pitcher 0.25)
- HockeyEngine: 5 factors (elo 0.35 / home_court 0.15 / rest 0.15 / form 0.20 / goalie 0.15)
- Weight redistribution when factor unavailable
- NHL overtime/shootout stored in `raw["custom"]` (binary outcome preserved)

#### Phase 6 — Learning Dashboard
- 7 tasks via Subagent-Driven Development; 22 files (+2,603 lines)
- 74 tests pass (42 backend + 32 frontend)
- Reliability chart, prediction history, calibration panel, engine scores
- `bins` parameter clamped to 5-20 range
- Tab state syncs with URL `?tab=` parameter

#### Phase 7 — Sport Market Bridge Layer (4 Subprojects)
- **A**: Three-layer matching engine (rule ≥0.9 → LLM ≥0.85 → manual gate);
  fail-closed `/latest` at 2 layers; 40 files (+3,577 lines)
- **B**: Edge Detector — aligns model probs with market-implied probs to
  compute trust-weighted `adjusted_edge`; writes to `kernel_sport_edges` table
- **C**: Sport Recommendation Engine — `SportRecommendationService` + 3 GET endpoints
- **D**: Market Settlement Feedback Loop — parallel-channel design writes only
  to `kernel_market_settlements` + `kernel_market_calibrations` tables (never
  touches Phase 3's `KernelCalibration`); Brier-style error signals;
  calibration regression clamps slope [0.0, 2.0] + intercept [-0.5, 0.5]

#### Phase 8 — Pipeline Completion + Calibration Fusion
- `PHASE8_CALIBRATION_FUSION_ENABLED` flag (OFF); EdgeDetectorService delegates
  to CalibrationFusionService when on
- New `kernel_traditional_odds_snapshots` table (separate from `kernel_market_snapshots`)
- Filled `_job_capture_market_snapshots` + `_job_fetch_traditional_odds` stubs
- Frontend chart upgraded to recharts LineChart with Polymarket comparison

#### Phase 9 — Accuracy Sprint
- `PHASE9_ACCURACY_SPRINT_ENABLED` flag (OFF)
- Backtesting framework (`backtest/runner.py` + `elo_time_machine.py`)
- Bayesian parameter optimization via Optuna TPE (`parameter_optimizer.py`)
- Multi-objective weighted scoring (accuracy + brier + mae)

#### Phase 10 — WebSocket Real-time Price Push
- `PHASE10_REALTIME_PUSH_ENABLED` flag (OFF)
- `ConnectionManager` singleton manages per-match WebSocket subscriber sets
- `/ws/*` endpoints return 503 when disabled

#### Phase 11 — Kalshi Sports Market Integration
- `PHASE11_KALSHI_SPORTS_ENABLED` flag (OFF)
- Kalshi public read-only API at `settings.KALSHI_API_URL` (no auth)
- Fail-closed source; polite 1s request interval

#### Phase 12 — Futures / Championship Markets
- `PHASE12_FUTURES_MARKETS_ENABLED` flag (OFF)
- Multi-leg N-outcome championship markets (one event → N contracts, one per team)
- 2 new DB tables (`kernel_futures_links` + `kernel_futures_snapshots`)
- 3 GET endpoints + 2 scheduler jobs
- Kalshi futures series prefixes (KXNBACHAMP / KXMLBCHAMP / KXNHLCHAMP /
  KXSOCCERWCS / KXSOCCERUCL) → (competition, championship_type) tuples

#### Phase 13 — Pre-existing Test Isolation Fix
- `backend/tests/conftest.py` autouse fixture resets `kernel_db` and
  `connection_manager` module-level singletons before AND after each test
- Purely additive (no existing test files modified)
- 101 tests pass forward AND reverse order

### Project Review Fixes
- **C1**: WebSocket `broadcast_to_match` snapshots set before iterating
  (prevents `RuntimeError: Set changed size during iteration`)
- **C2**: kernel_db 3 query functions missing `finally: session.close()`
  (resource leak)
- **C3/C4**: MLB/NHL adapter pitcher ERA and goalie save_pct defaults changed
  from hardcoded values to None (engine now correctly detects unavailable
  data and redistributes weight per Phase 5 constraint)
- **C5**: `futures_link_store.get_latest_snapshots` N+1 query replaced with
  single GROUP BY + JOIN
- **C7**: kernel_db 8 `except Exception` blocks now log warnings with
  `exc_info=True` (was silently swallowing DB errors)
- **C8**: `_parse_kalshi_price` returns None instead of 0.5 when no price data
  (callers skip contracts instead of injecting synthetic 50% probability)
- **C11/C12/C13**: `.env.example` — added missing `KERNEL_PREDICTION_ENABLED`
  master switch; aligned `EVENT_DISCOVER_LIMIT` (10→100) and
  `WORLD_CUP_SOURCE_ENABLED` (true→false) with `config.py` defaults
- Dead code cleanup: `_FACTOR_NAMES` dict, unused `seed_elo_from_games` imports

### Hard Constraints Honored
- PredictionKernel / domain.py / LearningService / engines/*.py — zero
  modifications across all phases (Protocol-based decoupling)
- All feature flags default OFF (backward compatibility)
- New DB tables use `kernel_` prefix; existing tables untouched
- TDD strictly followed for backend DB functions (RED → GREEN)

---

## v0.3.0 (2026-06-20)

### Production Hardening
- **Security**: CORS origins configurable via `CORS_ALLOWED_ORIGINS`, default localhost-only
- **Security**: API write key authentication (`require_write_key` middleware, `X-API-Key` header)
- **Security**: Rate limiting added (`InMemoryRateLimitMiddleware`, 120 req/60s per client+path)
- **Ops**: `/api/health` endpoint (returns scheduler status, loop health, failed runs)
- **Ops**: Rotating file logging (`RotatingFileHandler`, 10MB×5, configurable)
- **Ops**: `misfire_grace_time` extended to 86400s (24h), configurable
- **Ops**: systemd unit with `Restart=always`
- **Ops**: Daily backup script + systemd timer (`scripts/backup_stores.py`)
- **Ops**: Health check systemd timer (pings `/api/health` every 5 min)
- **Ops**: Dockerfile + docker-compose.yml with healthcheck
- **CI**: GitHub Actions workflow (compileall + unittest)

### Reality Feedback Loop
- Resolve write order hardened: score_prediction before resolve_event (crash-safe)
- Orphan prediction reconciliation before each auto-resolve
- Verified link seeding on prediction freeze
- Trust floor (`DIAGNOSIS_TRUST_FLOOR`, default 0.1) prevents absorbing state
- Loop run ledger (`loop_run_store.py` + `/api/events/loop/status`)

### Refactoring
- DRY: `_now()` unified to `utc_now()` in `utils/helpers.py` (4→1 definitions)
- DRY: `_clamp01` unified to `clamp01()` in `utils/helpers.py` (2→1 definitions)
- Config: `LLM_CONCURRENCY` replaces hardcoded `asyncio.Semaphore(4)`
- Scoring functions extracted to `scoring_service.py`
- Legacy `openai_service.py` client now has `timeout=60.0, max_retries=2`
- Dependencies pinned with `>=lower,<upper` constraints

### Documentation
- New: `docs/dev/ARCHITECTURE.md` (C4 diagrams, data flow, deployment)
- New: `docs/dev/adr/001-json-file-store.md`
- New: `docs/dev/adr/002-nextjs-static-export.md`
- New: `docs/dev/adr/003-fastapi-over-flask.md`
- New: `docs/ops/RUNBOOK.md` (monitoring, backup, process supervision)
- Cleanup: 6 code review files moved from `docs/user/` → `docs/archive/`
- Updated: test count in `Event Intelligence Platform.md` (141→503)

---

## v0.2.0 (2026-Q2)

- Multi-source event discovery (Polymarket + Manifold + Kalshi)
- AI probability analysis pipeline
- Multi-source auto-resolution (contract-first settlement)
- Calibration feedback loop (opt-in)
- Next.js dashboard

---

## v0.1.0 (2025-Q4)

- Initial FastAPI backend
- JSON file event store
- DeepSeek LLM integration
- Polymarket event source
