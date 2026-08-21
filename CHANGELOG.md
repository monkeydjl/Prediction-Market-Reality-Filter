# Changelog

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
